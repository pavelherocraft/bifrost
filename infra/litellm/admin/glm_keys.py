import json, os, psycopg2
from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value_helper

def dec(v):
    if v is None:
        return None
    try:
        return decrypt_value_helper(str(v), "dbg")
    except Exception as e:
        return f"<dec-err {repr(e)[:30]}>"

dsn = os.environ.get("DATABASE_URL")
conn = psycopg2.connect(dsn)
cur = conn.cursor()
cur.execute(
    'SELECT model_name, litellm_params FROM "LiteLLM_ProxyModelTable" '
    "WHERE model_name ILIKE %s ORDER BY model_name",
    ("%glm%",),
)

cred_cache = {}
def cred_key(cname):
    if cname in cred_cache:
        return cred_cache[cname]
    c2 = conn.cursor()
    c2.execute(
        'SELECT credential_values FROM "LiteLLM_CredentialsTable" WHERE credential_name = %s',
        (cname,),
    )
    row = c2.fetchone()
    c2.close()
    if not row:
        cred_cache[cname] = "<cred not found>"
        return cred_cache[cname]
    cv = row[0]
    cv = json.loads(cv) if isinstance(cv, str) else dict(cv)
    ak = cv.get("api_key")
    cred_cache[cname] = dec(ak) if ak else "<no api_key field>"
    return cred_cache[cname]

for name, lp in cur.fetchall():
    p = json.loads(lp) if isinstance(lp, str) else dict(lp)
    api_base = dec(p.get("api_base"))
    key_enc = p.get("api_key")
    cname = dec(p.get("litellm_credential_name"))
    if key_enc:
        src, key = "inline", dec(key_enc)
    elif cname:
        src, key = f"cred:{cname}", cred_key(cname)
    else:
        src, key = "-", None
    print(f"{name}\t{src}\t{api_base}\t{key}")
cur.close()
conn.close()
