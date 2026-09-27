"""
LiteLLM callback: UA injection + reasoning streaming normalization + Kimi empty
assistant message filter.

For models whose first streaming chunk is role-only WITHOUT reasoning_content
(Kimi K2.6/K2.7, MiniMax-M2.7), BUFFER the first chunk and merge it with the
next chunk that has actual content/reasoning_content. This produces a first
chunk that opencode's @ai-sdk/openai-compatible parser accepts:

    if (delta.reasoning_content != null && delta.reasoning_content.length > 0) {
        // start reasoning mode
    }

Empty RC="" is ignored (length === 0). We must deliver non-empty RC in the
first chunk OR delay the first chunk until real RC arrives.

Strategy: if first chunk = {role: 'assistant'} only, swallow it. Then on the
next chunk that has reasoning_content (non-empty), inject role: 'assistant'
into it. opencode sees the merged chunk as the first chunk and triggers
reasoning-start correctly.

Additionally, for Kimi/Moonshot models, the upstream API rejects conversation
history containing assistant messages with empty content (no text, no
tool_calls) with HTTP 400:

    litellm.BadRequestError: MoonshotException - the message at position N
    with role 'assistant' must not be empty.

This happens after compaction or in long tool-call cycles. The hook scans
data["messages"] in async_pre_call_hook and replaces any empty assistant
content with a single space " " (Moonshot accepts whitespace-only strings).
Messages with tool_calls are left alone (valid tool-call cycle).
"""
import os
import json
import time
import traceback

from litellm.integrations.custom_logger import CustomLogger
from litellm._logging import verbose_proxy_logger


OPENCODE_UA = os.environ.get(
    "OPENCODE_USER_AGENT",
    "opencode/local ai-sdk/provider-utils/4.0.27 runtime/node.js/24",
)

# Models whose first streaming chunk lacks non-empty reasoning_content.
# Hook will buffer+merge to ensure first yielded chunk has RC.length > 0.
_MERGE_FIRST_CHUNK_MODELS = (
    "Kimi K2.6", "Kimi K2.7", "Kimi K3",
    "Kimi K3-256K", "Kimi K2.8",
    "MiniMax-M2.7",
)

# Moonshot (Kimi) is strict about empty assistant messages: HTTP 400 if
# content is null/empty AND there are no tool_calls. We patch these in
# async_pre_call_hook by replacing empty content with a single space.
_KIMI_EMPTY_MSG_MODELS = (
    "Kimi K2.6", "Kimi K2.7", "Kimi K3",
    "Kimi K3-256K", "Kimi K2.8",
)

_TRACE_LOG = "/tmp/hook-trace.log"
_trace_enabled = os.environ.get("HOOK_TRACE", "0") == "1"


def _trace(model, idx, kind, payload):
    if not _trace_enabled:
        return
    try:
        line = {"ts": time.time(), "model": model, "idx": idx, "kind": kind}
        line.update(payload)
        with open(_TRACE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _get_header(headers, name):
    if not headers:
        return None
    name_l = name.lower()
    for k, v in headers.items():
        if k.lower() == name_l:
            if isinstance(v, list):
                return v[0] if v else None
            return v
    return None


# Substrings that mark the client as a script/admin tool rather than a real
# end-user product. When detected, we forward the opencode UA instead so
# upstream providers (especially those behind Cloudflare WAF) see a
# consistent browser-style signature.
#
# Covers: HTTP CLI tools, scripting language stdlib HTTP clients, and
# popular third-party HTTP libraries. Add new tokens here when a new
# provider flags a different UA pattern.
_SCRIPT_UA_TOKENS = (
    # CLI tools
    "curl", "wget", "powershell",
    # Browser UAs (already replaced before)
    "mozilla",
    # Language stdlib HTTP clients
    "python",        # python-requests, python-httpx
    "go-http-client",
    "java/",         # Java HttpURLConnection, OpenJDK
    "ruby",          # Ruby Net::HTTP
    "libwww-perl",   # Perl LWP
    # Popular HTTP libraries
    "axios",
    "node-fetch",
    "okhttp",        # Java/Kotlin
    "guzzlehttp",    # PHP
    "aiohttp",       # Python aiohttp
)


def _decide_ua(orig_headers):
    client_ua = _get_header(orig_headers, "User-Agent")
    if client_ua:
        client_ua_lower = client_ua.lower()
        if any(tok in client_ua_lower for tok in _SCRIPT_UA_TOKENS):
            return OPENCODE_UA
        return client_ua
    return OPENCODE_UA


def _is_empty_assistant(msg):
    """True if msg is an assistant message with no text content and no tool_calls."""
    if not isinstance(msg, dict):
        return False
    if msg.get("role") != "assistant":
        return False
    if msg.get("tool_calls"):
        return False
    content = msg.get("content")
    if content is None:
        return True
    if isinstance(content, str) and content == "":
        return True
    if isinstance(content, list) and len(content) == 0:
        return True
    return False


def _sanitize_kimi_messages(data, model):
    """For Kimi/Moonshot models: replace empty assistant content with a space.

    Moonshot API rejects assistant messages that have neither content nor
    tool_calls with HTTP 400. This typically happens after compaction or in
    long tool-call cycles where intermediate assistant turns end up empty.

    Returns count of patched messages for trace logging.
    """
    if not any(model.startswith(m) for m in _KIMI_EMPTY_MSG_MODELS):
        return 0
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return 0
    patched = 0
    for i, msg in enumerate(messages):
        if _is_empty_assistant(msg):
            msg["content"] = " "
            patched += 1
            _trace(model, i, "kimi_empty_patched", {
                "role": msg.get("role"),
                "has_tool_calls": bool(msg.get("tool_calls")),
            })
    return patched


class UserAgentLogger(CustomLogger):
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        return None

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        return None

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if (call_type == "image_generation" or call_type == "aimage_generation" or (hasattr(call_type, "value") and call_type.value in ("image_generation", "aimage_generation"))):
            try:
                data.pop("extra_headers", None)
            except Exception:
                pass
            return data
        try:
            psr = (data or {}).get("proxy_server_request") or {}
            orig_headers = psr.get("headers") or {}
            ua = _decide_ua(orig_headers)
            extra = dict(data.get("extra_headers") or {})
            extra["User-Agent"] = ua
            data["extra_headers"] = extra
        except Exception as e:
            verbose_proxy_logger.warning(
                "user_agent_hook: async_pre_call_hook ERROR: %s\n%s",
                e, traceback.format_exc(),
            )
        # GLM/Z.AI + Tencent TokenHub honour OpenAI-style reasoning_effort,
        # but LiteLLM get_optional_params drops it for custom_openai providers
        # unless the request carries allowed_openai_params. Inject the
        # allow-list whenever a client (opencode variants UI) sends an effort.
        try:
            if isinstance(data, dict) and data.get("reasoning_effort"):
                _aop = data.get("allowed_openai_params")
                if isinstance(_aop, list):
                    if "reasoning_effort" not in _aop:
                        _aop.append("reasoning_effort")
                else:
                    data["allowed_openai_params"] = ["reasoning_effort"]
        except Exception as e:
            verbose_proxy_logger.warning(
                "user_agent_hook: reasoning_effort allowlist ERROR: %s\n%s",
                e, traceback.format_exc(),
            )
        # Kimi/Moonshot: filter empty assistant messages (HTTP 400 protection)
        try:
            model = (data or {}).get("model") or ""
            patched = _sanitize_kimi_messages(data, model)
            if patched:
                verbose_proxy_logger.warning(
                    "user_agent_hook: kimi_empty_msg_filter patched %d assistant "
                    "messages for model=%s",
                    patched, model,
                )
        except Exception as e:
            verbose_proxy_logger.warning(
                "user_agent_hook: kimi_empty_msg_filter ERROR: %s\n%s",
                e, traceback.format_exc(),
            )
        # Kimi K2.8: upstream fixes temperature ("only 1 is allowed for
        # this model") and 400s on any other value - drop the param so the
        # provider default applies.
        try:
            _model = ((data or {}).get("model") or "")
            if "k2.8" in _model.lower().replace(" ", "").replace("-", ""):
                if "temperature" in data:
                    data.pop("temperature", None)
                    verbose_proxy_logger.warning(
                        "user_agent_hook: dropped temperature for model=%s "
                        "(upstream requires fixed default)",
                        _model,
                    )
        except Exception as e:
            verbose_proxy_logger.warning(
                "user_agent_hook: kimi drop temperature ERROR: %s\n%s",
                e, traceback.format_exc(),
            )
        # opencode sends `reasoning` (Responses-API style object) to chat
        # completions on openai-compatible providers; the OpenAI SDK rejects
        # it ("unexpected keyword argument 'reasoning'" -> 500). Convert to
        # reasoning_effort when possible, otherwise drop the key.
        try:
            _reasoning = (data or {}).get("reasoning")
            if _reasoning is not None:
                if isinstance(_reasoning, dict) and _reasoning.get("effort"):
                    data.setdefault("reasoning_effort", _reasoning["effort"])
                data.pop("reasoning", None)
                verbose_proxy_logger.warning(
                    "user_agent_hook: converted reasoning -> reasoning_effort for model=%s",
                    (data or {}).get("model"),
                )
        except Exception as e:
            verbose_proxy_logger.warning(
                "user_agent_hook: reasoning convert ERROR: %s\n%s",
                e, traceback.format_exc(),
            )
        return data

    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        if (call_type == "image_generation" or call_type == "aimage_generation" or (hasattr(call_type, "value") and call_type.value in ("image_generation", "aimage_generation"))):
            try:
                kwargs.pop("extra_headers", None)
            except Exception:
                pass
            return kwargs
        try:
            psr = (kwargs or {}).get("proxy_server_request") or {}
            orig_headers = psr.get("headers") or {}
            ua = _decide_ua(orig_headers)
            extra = dict(kwargs.get("extra_headers") or {})
            extra["User-Agent"] = ua
            kwargs["extra_headers"] = extra
        except Exception as e:
            verbose_proxy_logger.warning(
                "user_agent_hook: async_pre_call_deployment_hook ERROR: %s\n%s",
                e, traceback.format_exc(),
            )
        # GLM/Z.AI + Tencent TokenHub honour OpenAI-style reasoning_effort,
        # but LiteLLM get_optional_params drops it for custom_openai providers
        # unless the request carries allowed_openai_params. Inject the
        # allow-list whenever a client (opencode variants UI) sends an effort.
        try:
            if isinstance(kwargs, dict) and kwargs.get("reasoning_effort"):
                _aop = kwargs.get("allowed_openai_params")
                if isinstance(_aop, list):
                    if "reasoning_effort" not in _aop:
                        _aop.append("reasoning_effort")
                else:
                    kwargs["allowed_openai_params"] = ["reasoning_effort"]
        except Exception as e:
            verbose_proxy_logger.warning(
                "user_agent_hook: reasoning_effort allowlist ERROR: %s\n%s",
                e, traceback.format_exc(),
            )
        # Kimi/Moonshot: filter empty assistant messages (backup — in case
        # async_pre_call_hook didn't catch it, e.g. after fallback retry).
        try:
            model = (kwargs or {}).get("model") or ""
            patched = _sanitize_kimi_messages(kwargs, model)
            if patched:
                verbose_proxy_logger.warning(
                    "user_agent_hook: kimi_empty_msg_filter (deployment) patched "
                    "%d assistant messages for model=%s",
                    patched, model,
                )
        except Exception as e:
            verbose_proxy_logger.warning(
                "user_agent_hook: kimi_empty_msg_filter (deployment) ERROR: %s\n%s",
                e, traceback.format_exc(),
            )
        return kwargs

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict, response, request_data
    ):
        """Three responsibilities:
        1. For models in _MERGE_FIRST_CHUNK_MODELS: buffer the first chunk if it
           only contains role='assistant' (no RC or content), and merge it with
           the next chunk that has actual data. Ensures first yielded chunk has
           non-empty reasoning_content.
        2. <think> stripping for models that embed reasoning in content.
        3. Trace logging for debugging.
        """
        think_buf = ""
        past_think = False
        has_think = False
        chunk_idx = 0
        first_chunk_buffered = False

        req_model = ""
        try:
            req_model = (request_data or {}).get("model") or ""
        except Exception:
            pass
        merge_first = any(req_model.startswith(m) for m in _MERGE_FIRST_CHUNK_MODELS)

        if _trace_enabled:
            try:
                with open(_TRACE_LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "ts": time.time(), "event": "stream-start",
                        "model": req_model, "merge_first": merge_first,
                    }, ensure_ascii=False) + "\n")
            except Exception:
                pass

        async def _process_chunk(item):
            """Apply <think> stripping to one chunk in-place. Returns trace data."""
            nonlocal think_buf, has_think, past_think
            choices = getattr(item, "choices", None) or []
            for choice in choices:
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                if not past_think and not has_think:
                    ct = getattr(delta, "content", None)
                    if ct and "<think>" in ct:
                        has_think = True
                        think_buf += ct
                        if "</think>" in think_buf:
                            idx = think_buf.index("</think>") + len("</think>")
                            tail = think_buf[idx:].lstrip("\n")
                            think_buf = ""
                            past_think = True
                            delta.content = tail if tail else None
                        else:
                            delta.content = None
                elif has_think and not past_think:
                    ct = getattr(delta, "content", None)
                    if ct:
                        think_buf += ct
                        if "</think>" in think_buf:
                            idx = think_buf.index("</think>") + len("</think>")
                            tail = think_buf[idx:].lstrip("\n")
                            think_buf = ""
                            past_think = True
                            delta.content = tail if tail else None
                        else:
                            delta.content = None

        async for item in response:
            try:
                # Snapshot delta for trace
                delta_snap = {}
                choices = getattr(item, "choices", None) or []
                for choice in choices:
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue
                    for k in ("role", "content", "reasoning_content"):
                        v = getattr(delta, k, None)
                        if v is not None:
                            if isinstance(v, str) and len(v) > 80:
                                v = v[:80] + "..."
                            delta_snap[k] = v

                # First-chunk merge logic
                if merge_first and chunk_idx == 0 and not first_chunk_buffered:
                    # Inspect first chunk: is it role-only without non-empty RC?
                    first_delta = choices[0].delta if choices else None
                    if first_delta is not None:
                        rc_val = getattr(first_delta, "reasoning_content", None)
                        c_val = getattr(first_delta, "content", None)
                        role_val = getattr(first_delta, "role", None)
                        rc_is_empty = (rc_val is None) or (isinstance(rc_val, str) and rc_val == "")
                        c_is_empty = (c_val is None) or (isinstance(c_val, str) and c_val == "")
                        if role_val == "assistant" and rc_is_empty and c_is_empty:
                            # Buffer this chunk, wait for next
                            first_chunk_buffered = True
                            _trace(req_model, chunk_idx, "buffered_first", delta_snap)
                            chunk_idx += 1
                            continue
                        elif role_val == "assistant" and not rc_is_empty:
                            # First chunk already has non-empty RC — no merge needed
                            _trace(req_model, chunk_idx, "first_ok", delta_snap)
                            await _process_chunk(item)
                            _trace(req_model, chunk_idx - 1, "yielded", delta_snap)
                            yield item
                            chunk_idx += 1
                            continue

                # If we have buffered first chunk, merge role into this one
                if first_chunk_buffered and choices:
                    delta = choices[0].delta if choices[0] else None
                    if delta is not None:
                        existing_role = getattr(delta, "role", None)
                        if existing_role is None:
                            try:
                                delta.role = "assistant"
                            except Exception:
                                pass
                        _trace(req_model, chunk_idx, "merged", delta_snap)
                        first_chunk_buffered = False  # consumed

                await _process_chunk(item)
                _trace(req_model, chunk_idx - 1 if chunk_idx > 0 else 0, "yielded", delta_snap)
            except Exception as e:
                verbose_proxy_logger.warning(
                    "user_agent_hook: stream chunk ERROR: %s\n%s",
                    e, traceback.format_exc(),
                )
            yield item
            chunk_idx += 1

        # If we buffered but stream ended without yielding (edge case), emit it
        if first_chunk_buffered:
            _trace(req_model, chunk_idx, "emit_buffered_at_end", {})
            yield item  # last buffered item


user_agent_logger = UserAgentLogger()

verbose_proxy_logger.warning(
    "user_agent_hook: module loaded, OPENCODE_UA=%s, instance=%s, merge_models=%s, kimi_empty_msg_models=%s, trace=%s",
    OPENCODE_UA, id(user_agent_logger), _MERGE_FIRST_CHUNK_MODELS, _KIMI_EMPTY_MSG_MODELS, _trace_enabled,
)