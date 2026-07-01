# LiteLLM 401 Error Analysis — 2026-06-28 (last 72h)

37 ошибок 401 за 72ч из 9631 запросов. **84% (31 ошибка)** — баг: клиент
отправляет literal строку `${LITELLM_API_KEY}` в Authorization header.

## Метрики (72h)

| Status | Count |
|---|---:|
| 200 | 3340 |
| 500 | 370 (только MCP zai_*) |
| **401** | **37** |
| 404 | 115 (35× dashscope.svg) |
| 429 | 6 (image rate limit) |
| 307 | 7 (nginx double-prefix) |

**Container restarts: 0** (аптайм с 2026-06-27 13:40:25 UTC).

## Разбивка 401

### Group 1: `${LI****KEY}` literal env var — 31 ошибка (84%)

`Authorization: Bearer ${LITELLM_API_KEY}` отправляется буквально.
3 burst'а:
- 14:12:41 — ~6 ошибок за 8 сек
- 18:09:18–18:10:24 — ~20 ошибок за ~1 мин
- 21:12:22 — 1 ошибка

Паттерн: `GET /v1/models` + `GET /models` парами, иногда
`GET /key/info?key=${LITELLM_API_KEY}` (404). Это `opencode-api`
(`/opt/opencode-setup/api.py`) без `LITELLM_API_KEY` env var.

**Фикс**: в systemd-юните `Environment=LITELLM_API_KEY=sk-...`
или в `api.py` требовать ключ явно.

### Group 2: `02e2****a984` + `b525****9c1d` — 9 ошибок

13:41:49–13:41:51 (через 1.5 мин после старта контейнера). 7×
`POST /v1/images/generations` + 1× `POST /v1/chat/completions`.
Ключи отозваны, но opencode использует их из кэша.

**Фикс**: `/key/delete` или Admin UI → Keys → удалить мёртвые.

### Group 3: `****` пустой header — 1 ошибка

`GET /v2/guardrails/list` в 21:12:02 без Authorization.

### Group 4: Admin API без auth — 18 ошибок

`GET /v1/models` ×6, `GET /models` ×6, `GET /organization/list` ×3,
`GET /team/list` ×2, `/health/readiness/details` ×2, плюс по 1×
`/key/list`, `/tag/list`, `/project/list`, `/prompts/list`,
`/policies/list`, `/v2/guardrails/list`, `/v2/team/list?user_id=...`,
`/in_product_nudges`, `/sso/get/ui_settings`,
`/models?include_model_access_groups=True`.

Source IP всегда `172.18.0.1` (nginx bridge). Публичные IP — в
host nginx: `sudo cat /var/log/nginx/access.log* | grep litellm`.

## Дополнительные аномалии (не 401)

- **2× `GET /key/info?key=${LITELLM_API_KEY}` → 404** — тот же env var баг.
- **370× 500 на MCP** (`/zai_web_search/mcp`, `/zai_zread/mcp`,
  `/zai_web_reader/mcp`) — `MCP client list_prompts failed: Method not
  found: prompts/list`. Z.AI MCP не поддерживает `prompts/list`.
- **6× 429** на `/v1/images/generations` — `image_rate_limit_hook`.

## Как воспроизвести

```bash
ssh -p 1995 dev01@162.55.137.149 '
  docker logs litellm --since 72h 2>&1 > /tmp/log.txt
  grep -c "Virtual Key expected" /tmp/log.txt
  grep "Virtual Key expected" /tmp/log.txt | \
    grep -oE "Received=[^,]+" | sort -u
'
```