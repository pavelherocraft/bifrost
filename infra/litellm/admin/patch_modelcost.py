"""Merge patch: litellm_settings.model_cost must MERGE into the default
model_cost map, not replace it. Applied to proxy_server.py load_config().
Idempotent via marker. Run inside the litellm container:
    docker exec -i litellm python3 /app/admin/patch_modelcost.py
"""
import ast
import os

P = "/app/.venv/lib/python3.13/site-packages/litellm/proxy/proxy_server.py"
MARK = "_PATCH_MODEL_COST_MERGE_"

OLD = '''                elif key == "json_logs" and value is True:
                    litellm.json_logs = True
                    litellm._turn_on_json()
                    verbose_proxy_logger.debug(
                        f"{blue_color_code} Enabled JSON logging via config{reset_color_code}"
                    )
                else:
                    verbose_proxy_logger.debug(
                        f"{blue_color_code} setting litellm.{key}={value}{reset_color_code}"
                    )
                    setattr(litellm, key, value)
'''

MERGE_BLOCK = (
    "                    # " + MARK + " (2026-08-29): merge into default map\n"
    "                    # instead of replacing it - config model_cost holds only\n"
    "                    # custom models; default pricing must stay intact.\n"
    "                    if key == \"model_cost\" and isinstance(value, dict):\n"
    "                        litellm.model_cost.update(value)\n"
    "                        verbose_proxy_logger.info(\n"
    "                            \"[patch] model_cost merged from config: %d entries"
    " (total=%d)\",\n"
    "                            len(value),\n"
    "                            len(litellm.model_cost),\n"
    "                        )\n"
    "                    else:\n"
    "                        setattr(litellm, key, value)\n"
)

NEW = OLD.replace(
    "                    setattr(litellm, key, value)\n", MERGE_BLOCK, 1
)

src = open(P, encoding="utf-8").read()

if MARK in src:
    print("[patch_modelcost] already applied, nothing to do")
else:
    n = src.count(OLD)
    if n != 1:
        print(f"[patch_modelcost] WARN: anchor found {n} times (need exactly 1), aborting")
    else:
        new_src = src.replace(OLD, NEW, 1)
        ast.parse(new_src)  # raises SyntaxError if we broke it
        with open(P + ".bak_modelcost", "w", encoding="utf-8") as f:
            f.write(src)
        tmp = P + ".patched_tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_src)
        os.replace(tmp, P)
        print("[patch_modelcost] applied OK (backup: proxy_server.py.bak_modelcost)")
