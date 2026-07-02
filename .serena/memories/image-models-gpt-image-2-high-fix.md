# Image generation models — gpt-image-2 high quality 504 fix

**Date:** 2026-07-02

## TL;DR

`gpt-image-2 + quality=high` через **Timeweb reverse proxy** (89.19.213.124)
получал HTTP 504 через 60.27s, потому что OpenAI upstream генерирует ~120s,
а Timeweb имеет 60s timeout. Починено через pre-call hook в
`/opt/litellm/image_rate_limit_hook.py`, который блокирует эту комбинацию
с понятным 400 + alternatives.

## Image models status (all working)

| Model | Baseline | quality=high | quality=medium | Notes |
|-------|----------|-------------|----------------|-------|
| `gpt-image-2` | OK 15s | **HTTP 400 (blocked)** | OK 45s | high — Timeweb timeout |
| `gpt-image-1.5` | OK 14s | OK 28s | OK 14s | works |
| `gemini/gemini-3-pro-image` | OK 15s | N/A (uses "auto") | N/A | works |
| `gemini/gemini-3.1-flash-image` | OK 7s | N/A (uses "auto") | N/A | works |

**Size constraints** (per OpenAI docs):
- `gpt-image-2`: `1024x1024`, `1024x1536`, `1536x1024`, `auto` (no 256x256)
- `gpt-image-1.5`: same as gpt-image-2
- `gemini-*-image`: `1024x1024` (and likely others, not exhaustively tested)

## Root cause analysis

`gpt-image-2 quality=high` → 504 за 60.27s при обращении через
`https://hcbifrost.herocraft.com/litellm/v1/images/generations`.

Проверили все слои:

1. **LiteLLM router_settings.timeout** (config.yaml:119) — был `60` сек.
   Увеличено до `300` (5 минут). Backed up to `.bak.timeout-60`.

2. **nginx default_server** (port 8080) — не имел `proxy_read_timeout`,
   использовал default 60s. Добавлен `proxy_read_timeout 600s; proxy_send_timeout 600s;`.
   Backed up to `.bak.default-server`.

3. **Direct tests** (bypassing Timeweb):
   - `127.0.0.1:4001` (LiteLLM direct) → HTTP 200 за 119s ✅
   - `127.0.0.1:8080` (nginx default_server 600s) → HTTP 200 за 122s ✅
   - `127.0.0.1:80` (nginx primary) → 404 (path /v1/ — нужен /litellm/ prefix)

4. **Timeweb reverse proxy** (89.19.213.124) — единственный оставшийся
   bottleneck с 60s timeout. **Не наш контроль** — managed service.

## Final fix: pre-call hook

`/opt/litellm/image_rate_limit_hook.py` дополнен блоком, который ловит
`gpt-image-2 + quality=high` в `async_pre_call_hook` и возвращает:

```json
{
  "error": "slow_quality_combination_blocked",
  "message": "gpt-image-2 with quality=high is currently blocked: "
             "OpenAI generation takes ~120s which exceeds the upstream "
             "Timeweb reverse-proxy timeout (60s), so the client would "
             "see an opaque 504 after a 60s wait. Use one of the alternatives.",
  "model": "gpt-image-2",
  "quality": "high",
  "alternatives": [
    {"model": "gpt-image-1.5", "quality": "high", "approx_latency": "30s", ...},
    {"model": "gemini/gemini-3-pro-image", "quality": "auto", "approx_latency": "15s", ...},
    {"model": "gpt-image-2", "quality": "medium", "approx_latency": "46s", ...}
  ]
}
```

Поведение: HTTP 400 за 0.29s (без обращения к OpenAI, без 60s wait).

## ImageRateLimitLogger registration fix

`image_rate_limit_hook.py` уже был на диске (`15687 bytes` после patch),
но **не был зарегистрирован** в `litellm.callbacks`. Причина:
`config.yaml` не имел `callbacks:` блока. В отличие от `user_agent_hook`,
который регистрируется через monkey-patch в `utils_patched.py:585+`,
`image_rate_limit_hook` не имел аналогичного patch'а.

EntryPoint.sh в контейнере пытался зарегистрировать (видно в логах:
`[entrypoint] ensuring ImageRateLimitLogger is registered...`), но видимо
не всегда срабатывал (двойной instance check, или import order issue).

**Решение**: расширил `utils_patched.py` добавив блок регистрации
`image_rate_limit_logger` после блока `user_agent_logger`. Теперь hook
**гарантированно зарегистрирован** при каждом старте LiteLLM.

Backed up: `utils_patched.py.bak.register-rate-limit` (1036 bytes delta).

## Files touched

```
/opt/litellm/config.yaml                                     # timeout 60 -> 300
/opt/litellm/utils_patched.py                                # +1036 bytes (hook registration)
/etc/nginx/sites-available/litellm-bifrost                   # +2 lines (default_server timeout)
/opt/litellm/image_rate_limit_hook.py                        # +new quality check block
```

Backups on VM:
- `/opt/litellm/config.yaml.bak.timeout-60`
- `/etc/nginx/sites-available/litellm-bifrost.bak.default-server`
- `/opt/litellm/utils_patched.py.bak.register-rate-limit`
- `/opt/litellm/image_rate_limit_hook.py.bak.gpt-image2-quality`
- `/opt/litellm/image_rate_limit_hook.py.bak.debug-print` (debug print уже удалён)

## Future improvements

- Если уберем Timeweb (свой reverse proxy / CDN с большим timeout) — можно
  снять блокировку gpt-image-2 quality=high.
- Если OpenAI оптимизирует generation (новый релиз) — возможно будет <60s.
- Streaming для image generation (если OpenAI его введёт) — позволит
  отдавать клиенту chunked response с первыми байтами раньше.

## Related

- Skills test memory: `litellm-skills-image-models.md`
- LiteLLM model updates memory: `infra/litellm-model-updates.md`