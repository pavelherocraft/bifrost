import json, os, psycopg2
from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value_helper

dsn = os.environ.get("DATABASE_URL")
conn = psycopg2.connect(dsn)
cur = conn.cursor()
cur.execute(
    'SELECT model_name, litellm_params FROM "LiteLLM_ProxyModelTable" '
    "WHERE model_name ILIKE '%deepseek%' ORDER BY model_name"
)
for name, lp in cur.fetchall():
    p = json.loads(lp) if isinstance(lp, str) else dict(lp)
    out = {}
    for k in ("model", "custom_llm_provider", "litellm_credential_name", "api_base"):
        v = p.get(k)
        if v is None:
            out[k] = None
            continue
        try:
            out[k] = decrypt_value_helper(str(v), "dbg")
        except Exception as e:
            out[k] = f"ERR {repr(e)[:40]}"
    print(f"{name:40s} | provider={out['custom_llm_provider']} | cred={out['litellm_credential_name']} | model={out['model']} | api_base={out['api_base']}")
cur.close()
conn.close()
