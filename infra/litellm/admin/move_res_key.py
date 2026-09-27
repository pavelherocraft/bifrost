import json, os, psycopg2
from litellm.proxy.common_utils.encrypt_decrypt_utils import encrypt_value_helper

NEW_KEY = "5fd3956b75b548a5af87e4496e861618.Btz946vHzHTyywI4"
MODEL = "GLM-5.3-Flash (res)"

dsn = os.environ.get("DATABASE_URL")
conn = psycopg2.connect(dsn)
cur = conn.cursor()

# backup current row
cur.execute(
    'SELECT litellm_params FROM "LiteLLM_ProxyModelTable" WHERE model_name = %s',
    (MODEL,),
)
row = cur.fetchone()
assert row, f"{MODEL} not found"
with open("/tmp/glm53fres_backup_params.json", "w", encoding="utf-8") as f:
    json.dump({"model_name": MODEL, "litellm_params": row[0]}, f)
print("backup saved to /tmp/glm53fres_backup_params.json")

enc = encrypt_value_helper(NEW_KEY)
cur.execute(
    'UPDATE "LiteLLM_ProxyModelTable" '
    "SET litellm_params = litellm_params || jsonb_build_object('api_key', %s), "
    "updated_at = NOW() WHERE model_name = %s",
    (enc, MODEL),
)
conn.commit()
print("rows updated:", cur.rowcount)

# verify decrypt round-trip
cur.execute(
    'SELECT litellm_params FROM "LiteLLM_ProxyModelTable" WHERE model_name = %s',
    (MODEL,),
)
p = cur.fetchone()[0]
p = json.loads(p) if isinstance(p, str) else dict(p)
from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value_helper
dec = decrypt_value_helper(str(p["api_key"]), "dbg")
print("decrypted key tail:", dec[-8:], "| match:", dec == NEW_KEY)
cur.close()
conn.close()
