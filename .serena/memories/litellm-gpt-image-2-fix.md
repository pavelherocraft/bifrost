# gpt-image-2 model fix in LiteLLM proxy (2026-06-26)

## Problem
`gpt-image-2` model in LiteLLM proxy returned 404 "Received Model Group=gpt-image-2" when called via `/v1/images/generations`.

## Root causes (3 separate issues)

### Issue 1: Wrong `api_base` in model config
**Symptom:** 404 from OpenAI chat endpoint  
**Cause:** `litellm_params.api_base = "https://api.openai.com/v1/images/generations"` (full endpoint URL)  
**Fix:** OpenAI SDK auto-appends `/images/generations` to api_base, so it must be `https://api.openai.com/v1` (without `/images/generations`).  
**Applied via:** `PATCH /model/{model_id}/update` with `{"litellm_params":{"api_base":"https://api.openai.com/v1"}}`  
**File modified:** DB record for `model_id=ace47273-2a04-4709-b19c-437d9e948d61`

### Issue 2: `extra_headers` injected by user_agent_hook reaches OpenAI request body
**Symptom:** 400 "Unknown parameter: 'extra_headers'"  
**Cause:** `user_agent_hook.py` adds `data["extra_headers"] = {"User-Agent": ...}`. For image generation, LiteLLM forwards this dict key as a request body parameter to OpenAI's image endpoint, which rejects it.  
**Fix:** Skip `extra_headers` injection (and pop any pre-existing) for image_generation call_types in both `async_pre_call_hook` and `async_pre_call_deployment_hook`.  
**File modified:** `/opt/litellm/user_agent_hook.py` (mounted into container as `/app/user_agent_hook.py`)

### Issue 3: Call_type values differ between hooks
**Symptom:** Patch check `if call_type == "image_generation"` failed in deployment_hook  
**Cause:**  
- `pre_call_hook` receives `call_type` as **string** `"image_generation"` (from endpoints.py:124)  
- `pre_call_deployment_hook` receives `call_type` as **CallTypes enum** `CallTypes.aimage_generation` (from utils.py:1789 where `call_type = original_function.__name__`)  

**Fix:** Use combined check:
```python
if (call_type == "image_generation" or call_type == "aimage_generation" 
    or (hasattr(call_type, "value") and call_type.value in ("image_generation", "aimage_generation"))):
```

## Verification commands

```bash
# Test image generation (should return 200 with base64 PNG)
curl -X POST "http://127.0.0.1:4001/v1/images/generations" \
  -H "Authorization: Bearer sk-litellm-placeholder-replace-before-prod" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-image-2","prompt":"a cute baby sea otter","n":1,"size":"1024x1024"}'

# Test chat completions still work
curl -X POST "http://127.0.0.1:4001/v1/chat/completions" \
  -H "Authorization: Bearer sk-litellm-placeholder-replace-before-prod" \
  -H "Content-Type: application/json" \
  -d '{"model":"GLM-5.2","messages":[{"role":"user","content":"hi"}],"max_tokens":10}'
```

## OpenAI key for gpt-image-2
Stored as encrypted credential in LiteLLM DB (model_id=ace47273-...).
Organization: `org-vcXoFtfQpBcFYYB4ZajfbXp7`

## Files modified
- `/opt/litellm/user_agent_hook.py` (with `.bak` backup)
- LiteLLM DB: `LiteLLM_ProxyModelTable` row for gpt-image-2 (api_base field)

## New OpenAI image models after this fix
When adding new OpenAI image generation models (gpt-image-X) through UI/API, only need:
1. `api_base: "https://api.openai.com/v1"` (NOT `/v1/images/generations` — SDK auto-appends)
2. `custom_llm_provider: openai`
3. `model_info.mode: image_generation`
4. Add model to team via `POST /team/model/add` with `{team_id, models: ["model-name"]}`

The `user_agent_hook.py` patch automatically handles all image_generation calls (no per-model work needed).

`gpt-image-1.5` (model_id `28e2450c-f6bd-497c-bea4-484775fa5482`) was added on 2026-06-26 — already worked with correct `api_base`, just needed team assignment.

## Backup file
`/opt/litellm/user_agent_hook.py.bak` — original user_agent_hook.py before patches
