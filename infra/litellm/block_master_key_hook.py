"""
LiteLLM pre-call hook: block master key usage on inference traffic.

The master key (`LITELLM_MASTER_KEY`) is admin-only — it should never be
used for /v1/* proxy calls (chat completions, embeddings, image generation,
etc.). All real clients must use virtual keys (sk-... issued via /key/generate).

When LiteLLM authenticates a request with the master key, it SUBSTITUTES
`UserAPIKeyAuth.api_key` with the stable alias `litellm_proxy_master_key`
(see litellm.proxy.auth.user_api_key_auth L1609 — substitute so the raw key
or its hash never propagates into spend logs / metrics / rate-limit buckets).
This hook checks for that alias.

`async_pre_call_hook` fires ONLY on proxy traffic — admin endpoints
(/key/*, /user/*, /team/*, /admin/*) bypass this hook entirely, so master
key remains valid for them automatically.

Behavior when master key is detected:
  - HTTP 401 with explicit error message ("Use a virtual key instead")
  - Logged at WARNING level for audit (so we can detect scanner attempts
    with the leaked master key)

Registered in utils_patched.py via `_PATCH_BLOCK_MASTER_KEY_HOOK_` block
inside `_init_litellm_callbacks()` method of `ProxyLogging` class.
This is the same mechanism used by `user_agent_hook.py` and
`image_rate_limit_hook.py` — registration via entrypoint.sh subprocess
does NOT work because `exec litellm` starts a fresh Python process.
"""

import os
from typing import Any, Optional

from fastapi import HTTPException
from litellm._logging import verbose_proxy_logger
from litellm.constants import LITELLM_PROXY_MASTER_KEY_ALIAS
from litellm.integrations.custom_logger import CustomLogger


# LiteLLM substitutes the raw master key with this alias on UserAPIKeyAuth.api_key
# (see litellm.proxy.auth.user_api_key_auth.py L1609). Detecting this alias is
# the canonical way to identify master-key-authenticated requests.
MASTER_KEY_ALIAS = LITELLM_PROXY_MASTER_KEY_ALIAS  # "litellm_proxy_master_key"


class BlockMasterKeyLogger(CustomLogger):
    """Rejects /v1/* calls authenticated with the master key."""

    def __init__(self):
        # Resolve master key from env (kept for logging context only — we
        # detect via the alias, not by comparing the raw key).
        mk = os.environ.get("LITELLM_MASTER_KEY", "") or ""
        if mk.startswith("os.environ/"):
            mk = os.environ.get(mk.split("/", 1)[1], "") or ""
        self._master_key = mk
        verbose_proxy_logger.warning(
            "[block_master_key_hook] ACTIVE — will reject master key alias "
            "%r (raw key ending ...%s) on /v1/* traffic",
            MASTER_KEY_ALIAS,
            self._master_key[-6:] if self._master_key else "?",
        )

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: Optional[dict],
        call_type: str,
    ):
        api_key = getattr(user_api_key_dict, "api_key", None) or ""

        if api_key == MASTER_KEY_ALIAS:
            model = data.get("model") if isinstance(data, dict) else None
            verbose_proxy_logger.warning(
                "[block_master_key_hook] REJECTING master key on proxy "
                "call_type=%r model=%r — master key is admin-only. "
                "Use a virtual key (sk-...) issued via /key/generate instead.",
                call_type,
                model,
            )
            raise HTTPException(
                status_code=401,
                detail={
                    "error": {
                        "message": (
                            "Master key cannot be used for inference traffic. "
                            "Use a virtual key (sk-...) instead."
                        ),
                        "type": "master_key_blocked",
                        "code": "master_key_blocked",
                    }
                },
            )

        return data


block_master_key_logger = BlockMasterKeyLogger()
