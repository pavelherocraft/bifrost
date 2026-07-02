# gpt-image-2 via opencode agent — InternalServerError fix

**Date:** 2026-07-02

## TL;DR

`generate-image-gpt` subagent (model: `bifrost-litellm/gpt-image-2`) was failing
with `litellm.InternalServerError: OpenAIException - The server had an error
while processing your request. Sorry about that!` — followed 30s later by
`No deployments available for selected model` (LiteLLM cooldown).

**Root cause was NOT OpenAI** — it was our own stack: opencode's AI SDK sends
`stream: true` for every model by default, but the OpenAI image API
(`gpt-image-2`, `gpt-image-1.5`) does NOT support streaming. LiteLLM's httpx
client hung on `socket.read` and timed out, which surfaced to the client as a
generic "InternalServerError" from the upstream.

## Reproduction (was)

```bash
curl -X POST https://hcbifrost.herocraft.com/litellm/v1/images/generations \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"gpt-image-2","prompt":"...","size":"1024x1024","n":1,"stream":true}'
# → hangs >60s, eventually 504 (Timeweb) or 500 (OpenAI Exception)
```

Direct (non-streaming) test worked fine:
```bash
# no "stream":true → 200 OK in ~12-15s
```

## Fix (one file)

`/opt/litellm/image_rate_limit_hook.py` — added a `stream = False` override in
`async_pre_call_hook` for image generation calls:

```python
# --- 0.25. Force non-streaming upstream call for image generation ---
# opencode / AI SDK sends stream: true for every model by default.
# But the OpenAI image API (gpt-image-2, gpt-image-1.5) does NOT
# support streaming. The upstream call hangs and LiteLLM hits a
# httpx.ReadTimeout, which surfaces to the client as
# OpenAIException - The server had an error and the deployment
# then goes into cooldown for 30s.
if data.get("stream") is True:
    verbose_proxy_logger.warning(
        "image_rate_limit_hook: forcing stream=False for image "
        "generation (was stream=True) - model=%r call_type=%r",
        data.get("model"),
        call_type,
    )
    data["stream"] = False
```

LiteLLM still wraps the response so the client receives it in the form it
asked for (chunked SSE if `stream=True` was set, JSON otherwise).

## Verification

| Request | Before | After |
|---------|--------|-------|
| `gpt-image-2 stream=true` | InternalServerError → cooldown 30s | 200 OK in 13.17s |
| `gpt-image-1.5 stream=true` | (assumed broken) | works |
| `gpt-image-2` baseline (no stream) | 200 OK ~12s | 200 OK ~12s (no regression) |
| `gpt-image-2 quality=high` | 400 (blocked, see other memory) | 400 (still blocked) |

Log evidence after fix:
```
[01:19:06] image_rate_limit_hook: ENTER call_type='image_generation' model='gpt-image-2' stream=True
[01:19:06] image_rate_limit_hook: forcing stream=False for image generation (was stream=True)
```

(debug ENTER print was removed after verification; only the `forcing stream` warning remains)

## Related deploy

- OpenAI upstream: `https://api.openai.com/v1` (decrypted from `LiteLLM_ProxyModelTable.litellm_params`)
- Decryption used `LITELLM_MASTER_KEY` + `LITELLM_SALT_KEY` env via
  `from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value_helper`
- Confirmed api_base is **direct OpenAI**, no custom proxy in between

## Why "The server had an error" was a red herring

OpenAI's 500 body says `The server had an error while processing your
request. Sorry about that!` — but that wording is also the default message
LiteLLM emits when it can't read from the upstream socket. Looking at the
actual stack:

```
File "/app/.venv/lib/python3.13/site-packages/litellm/llms/custom_httpx/aiohttp_transport.py", line 75
    raise mapped_exc(message) from exc
httpx.ReadTimeout: Timeout on reading data from socket
```

This is **our** socket timeout, not OpenAI's HTTP 500. The fix is upstream of
the timeout: disable streaming for image models so the call completes in one
HTTP response rather than holding the socket open waiting for SSE chunks
that will never come.

## Open questions

- Does Gemini image generation have the same problem? My tests with
  `gemini/gemini-3-pro-image` and `gemini/gemini-3.1-flash-image` with default
  params (no `stream=true` set by client) returned 200 OK in 7-15s, so they
  appear to handle non-streaming fine. The hook only fires for
  `call_type in IMAGE_CALL_TYPES` so it covers them too if they ever get
  `stream: true` from a future SDK.
- Could we instead disable streaming for image models at the opencode config
  level (so SDK never sends `stream: true`)? That would be cleaner but
  requires changing the opencode.json schema, which the user wants to keep
  minimal. Hook-side fix is more robust.

## Files touched (this round)

```
/opt/litellm/image_rate_limit_hook.py      # +stream=False override (~1040 bytes)
```

Backups on VM:
- `image_rate_limit_hook.py.bak.stream-fix`
- `image_rate_limit_hook.py.bak.debug-call-type`
- `image_rate_limit_hook.py.bak.remove-debug`
- (and earlier `.bak.gpt-image2-quality`, `.bak.debug-print`)
