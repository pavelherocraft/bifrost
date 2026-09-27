#!/bin/sh
# LiteLLM container entrypoint.
# Patches callback_utils.py to register UserAgentLogger in initialize_callbacks_on_proxy.

set -e

/app/.venv/bin/python3 - <<'PYEOF'
import os, sys
sys.path.insert(0, "/app")

target = "/app/.venv/lib/python3.13/site-packages/litellm/proxy/common_utils/callback_utils.py"
with open(target) as f:
    src = f.read()

if "_PATCH_USER_AGENT_HOOK_" in src:
    print("[entrypoint] callback_utils.py already patched", flush=True)
else:
    needle = 'verbose_proxy_logger.debug(\n        f"{blue_color_code} Initialized Callbacks - {litellm.callbacks} {reset_color_code}"\n    )'
    inject = '''

    # _PATCH_USER_AGENT_HOOK_: ensure UserAgentLogger is always registered.
    try:
        from user_agent_hook import UserAgentLogger as _UA
        _ua_inst = _UA()
        if not any(isinstance(c, _UA) for c in litellm.callbacks):
            litellm.callbacks.insert(0, _ua_inst)
            for _name in ("success_callback", "failure_callback", "_async_success_callback", "_async_failure_callback"):
                _t = getattr(litellm, _name, None)
                if isinstance(_t, list) and not any(isinstance(c, _UA) for c in _t):
                    _t.append(_ua_inst)
            verbose_proxy_logger.warning("__PATCH_DEBUG__ UserAgentLogger ADDED in callback_utils")
    except Exception as _e:
        verbose_proxy_logger.warning(f"__PATCH_DEBUG__ callback_utils WARN: {_e}")
'''
    if needle in src:
        new_src = src.replace(needle, needle + inject, 1)
        with open(target, "w") as f:
            f.write(new_src)
        for d in ["/app/.venv/lib/python3.13/site-packages/litellm/proxy/common_utils/__pycache__", "/app/.venv/lib/python3.13/site-packages/litellm/proxy/__pycache__"]:
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    if "callback_utils" in fn and fn.endswith(".pyc"):
                        os.remove(os.path.join(d, fn))
        print("[entrypoint] callback_utils.py patched in-container", flush=True)
    else:
        print("[entrypoint] WARN: callback_utils.py needle not found", flush=True)
PYEOF

# Patch utils.py too (idempotent)
/app/.venv/bin/python3 - <<'PYEOF2'
import os, sys
sys.path.insert(0, "/app")
target = "/app/.venv/lib/python3.13/site-packages/litellm/proxy/utils.py"
with open(target) as f:
    src = f.read()
if "_PATCH_USER_AGENT_HOOK_" in src:
    print("[entrypoint] utils.py already patched", flush=True)
else:
    old = "                litellm.logging_callback_manager.add_litellm_async_failure_callback(\n                    callback\n                )"
    inject = (
        old + "\n\n"
        "        # _PATCH_USER_AGENT_HOOK_: ensure UserAgentLogger is always registered.\n"
        "        try:\n"
        "            from user_agent_hook import UserAgentLogger as _UA\n"
        "            _ua_inst = _UA()\n"
        "            verbose_proxy_logger.warning(\"__PATCH_DEBUG__ _init_litellm_callbacks ENTERED\")\n"
        "            if not any(isinstance(c, _UA) for c in litellm.callbacks):\n"
        "                litellm.callbacks.insert(0, _ua_inst)\n"
        "                for _name in (\"success_callback\", \"failure_callback\", \"_async_success_callback\", \"_async_failure_callback\"):\n"
        "                    _t = getattr(litellm, _name, None)\n"
        "                    if isinstance(_t, list) and not any(isinstance(c, _UA) for c in _t):\n"
        "                        _t.append(_ua_inst)\n"
        "                verbose_proxy_logger.warning(\"__PATCH_DEBUG__ UserAgentLogger ADDED\")\n"
        "            else:\n"
        "                verbose_proxy_logger.warning(\"__PATCH_DEBUG__ UserAgentLogger ALREADY PRESENT\")\n"
        "        except Exception as _e:\n"
        "            verbose_proxy_logger.warning(f\"__PATCH_DEBUG__ WARN: {_e}\")\n"
    )
    if old in src:
        new_src = src.replace(old, inject, 1)
        with open(target, "w") as f:
            f.write(new_src)
        for d in ["/app/.venv/lib/python3.13/site-packages/litellm/proxy/__pycache__"]:
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    if "utils" in fn and fn.endswith(".pyc"):
                        os.remove(os.path.join(d, fn))
        print("[entrypoint] utils.py patched in-container", flush=True)
    else:
        print("[entrypoint] WARN: utils.py needle not found", flush=True)
PYEOF2

# Ensure psycopg2-binary is available for image_rate_limit_hook.py
# (the hook needs DB access to /litellm-pg on the litellm-net network).
# Idempotent: pip skips reinstall when already present.
echo "[entrypoint] ensuring psycopg2-binary is installed...", flush=True
/app/.venv/bin/python3 - <<'PYEOF3' >/dev/null 2>&1
try:
    import psycopg2  # noqa: F401
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "ensurepip", "--default-pip"])
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "psycopg2-binary"])
    print("[entrypoint] psycopg2-binary installed")
PYEOF3

# ImageRateLimitLogger: same insurance patch as UserAgentLogger — belt-and-suspenders
# so the hook always ends up in litellm.callbacks even if config.yaml registration
# is bypassed by some code path. Also clears the in-process cache so the new
# image_team_limits values are picked up on every restart.
echo "[entrypoint] ensuring ImageRateLimitLogger is registered...", flush=True
/app/.venv/bin/python3 - <<'PYEOF4'
import sys
sys.path.insert(0, "/app")
try:
    import litellm
    from image_rate_limit_hook import ImageRateLimitLogger as _IRL
    _irl_inst = _IRL()
    if not any(isinstance(c, _IRL) for c in litellm.callbacks):
        litellm.callbacks.insert(0, _irl_inst)
        for _name in ("success_callback", "failure_callback", "_async_success_callback", "_async_failure_callback"):
            _t = getattr(litellm, _name, None)
            if isinstance(_t, list) and not any(isinstance(c, _IRL) for c in _t):
                _t.append(_irl_inst)
        litellm._logging.verbose_proxy_logger.warning(
            "[entrypoint] ImageRateLimitLogger ADDED to litellm.callbacks"
        )
    else:
        litellm._logging.verbose_proxy_logger.warning(
            "[entrypoint] ImageRateLimitLogger ALREADY PRESENT"
        )
except Exception as _e:
    import traceback
    traceback.print_exc()
    litellm._logging.verbose_proxy_logger.warning(
        "[entrypoint] ImageRateLimitLogger WARN: %s", _e
    )
PYEOF4

# BlockMasterKeyLogger: reject master key usage on /v1/* proxy traffic.
# Master key is admin-only — see /opt/litellm/block_master_key_hook.py.
# Idempotent: if already in litellm.callbacks, skips insertion.
echo "[entrypoint] ensuring BlockMasterKeyLogger is registered...", flush=True
/app/.venv/bin/python3 - <<'PYEOF_BMK'
import sys
sys.path.insert(0, "/app")
try:
    import litellm
    from block_master_key_hook import BlockMasterKeyLogger as _BMK
    _bmk_inst = _BMK()
    if not any(isinstance(c, _BMK) for c in litellm.callbacks):
        litellm.callbacks.insert(0, _bmk_inst)
        for _name in ("success_callback", "failure_callback", "_async_success_callback", "_async_failure_callback"):
            _t = getattr(litellm, _name, None)
            if isinstance(_t, list) and not any(isinstance(c, _BMK) for c in _t):
                _t.append(_bmk_inst)
        litellm._logging.verbose_proxy_logger.warning(
            "[entrypoint] BlockMasterKeyLogger ADDED to litellm.callbacks"
        )
    else:
        litellm._logging.verbose_proxy_logger.warning(
            "[entrypoint] BlockMasterKeyLogger ALREADY PRESENT"
        )
except Exception as _e:
    import traceback
    traceback.print_exc()
    litellm._logging.verbose_proxy_logger.warning(
        "[entrypoint] BlockMasterKeyLogger WARN: %s", _e
    )
PYEOF_BMK

# GLM credentials swap router: register admin endpoints (/admin/glm/*)
# and the UI page (/admin/glm-swap/). Idempotent — adds include_router()
# after the existing routers in proxy_server.py if not already present.
echo "[entrypoint] ensuring glm_swap_router is registered...", flush=True
/app/.venv/bin/python3 - <<'PYEOF5'
import os, sys
sys.path.insert(0, "/app")
try:
    target = "/app/.venv/lib/python3.13/site-packages/litellm/proxy/proxy_server.py"
    with open(target) as f:
        src = f.read()
    if "_GLM_SWAP_ROUTER_PATCH_" in src:
        print("[entrypoint] proxy_server.py already patched for glm_swap_router", flush=True)
    else:
        # Inject after the last `app.include_router(...)` block. We use a stable
        # pattern that exists in all recent litellm versions: the last line
        # of the chain is `app.include_router(ocr_router)`.
        needle = "app.include_router(ocr_router)\n"
        if needle not in src:
            print("[entrypoint] WARN: ocr_router include_router line not found in proxy_server.py", flush=True)
        else:
            inject = (
                "app.include_router(ocr_router)\n"
                "\n"
                "# _GLM_SWAP_ROUTER_PATCH_: register /admin/glm/* + /admin/glm-swap/ UI page\n"
                "try:\n"
                "    from admin.glm_swap_route import router as _glm_swap_router\n"
                "    app.include_router(_glm_swap_router)\n"
                "    print(\"[entrypoint] glm_swap_router REGISTERED\")\n"
                "except Exception as _e:\n"
                "    import traceback\n"
                "    traceback.print_exc()\n"
                "    print(f\"[entrypoint] glm_swap_router WARN: {_e}\")\n"
            )
            new_src = src.replace(needle, inject, 1)
            with open(target, "w") as f:
                f.write(new_src)
            # clear cache so the patch takes effect
            for d in ["/app/.venv/lib/python3.13/site-packages/litellm/proxy/__pycache__"]:
                if os.path.isdir(d):
                    for fn in os.listdir(d):
                        if "proxy_server" in fn and fn.endswith(".pyc"):
                            os.remove(os.path.join(d, fn))
            print("[entrypoint] proxy_server.py patched for glm_swap_router", flush=True)
except Exception as _e:
    import traceback
    traceback.print_exc()
    print(f"[entrypoint] glm_swap_router WARN: {_e}", flush=True)
PYEOF5

# Payload logs router: register admin endpoints (/admin/payload-logs/*)
# and the UI page (/admin/payload-logs/). Idempotent - adds include_router()
# after the glm_swap_router patch block in proxy_server.py.
echo "[entrypoint] ensuring payload_logs_router is registered..."
/app/.venv/bin/python3 - <<'PYEOF6'
import os, sys
sys.path.insert(0, "/app")
try:
    target = "/app/.venv/lib/python3.13/site-packages/litellm/proxy/proxy_server.py"
    with open(target) as f:
        src = f.read()
    if "_PAYLOAD_LOGS_ROUTER_PATCH_" in src:
        print("[entrypoint] proxy_server.py already patched for payload_logs_router", flush=True)
    else:
        needle = "    print(f\"[entrypoint] glm_swap_router WARN: {_e}\")" + chr(10)
        if needle not in src:
            print("[entrypoint] WARN: glm_swap_router include line not found in proxy_server.py", flush=True)
        else:
            nl = chr(10)
            inject = (
                nl +
                "# _PAYLOAD_LOGS_ROUTER_PATCH_: register /admin/payload-logs/* + UI page" + nl +
                "try:" + nl +
                "    from admin.payload_logs_route import router as _payload_logs_router" + nl +
                "    app.include_router(_payload_logs_router)" + nl +
                "    print(\"[entrypoint] payload_logs_router REGISTERED\")" + nl +
                "except Exception as _e:" + nl +
                "    import traceback" + nl +
                "    traceback.print_exc()" + nl +
                "    print(f\"[entrypoint] payload_logs_router WARN: {_e}\")" + nl
            )
            new_src = src.replace(needle, inject, 1)
            with open(target, "w") as f:
                f.write(new_src)
            for d in ["/app/.venv/lib/python3.13/site-packages/litellm/proxy/__pycache__"]:
                if os.path.isdir(d):
                    for fn in os.listdir(d):
                        if "proxy_server" in fn and fn.endswith(".pyc"):
                            os.remove(os.path.join(d, fn))
            print("[entrypoint] proxy_server.py patched for payload_logs_router", flush=True)
except Exception as _e:
    import traceback
    traceback.print_exc()
    print(f"[entrypoint] payload_logs_router WARN: {_e}", flush=True)
PYEOF6


# _PATCH_XIAOMI_AUDIO_: normalize message.audio (expires_at/transcript) for
# Xiaomi MiMo TTS chat.completions responses. Idempotent; script in admin/ dir.
echo "[entrypoint] applying xiaomi audio patch..."
if [ -f /app/admin/patch_xiaomi_audio.py ]; then
  /app/.venv/bin/python3 /app/admin/patch_xiaomi_audio.py || echo "[entrypoint] WARN: xiaomi audio patch failed"
fi

# _PATCH_MODEL_COST_MERGE_: merge config model_cost into defaults
# (idempotent; script lives in admin/ dir-mount, visible as /app/admin/)
echo "[entrypoint] applying model_cost merge patch..."
if [ -f /app/admin/patch_modelcost.py ]; then
  /app/.venv/bin/python3 /app/admin/patch_modelcost.py || echo "[entrypoint] WARN: model_cost patch failed"
fi

exec litellm "$@"