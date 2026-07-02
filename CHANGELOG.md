# Bifrost LiteLLM — Changelog

Все значимые изменения в конфигурации/прокси/UI проксирования Bifrost LiteLLM.

---

### [2026-07-02] — gpt-image-2 quality=high 504 fix (Timeweb 60s timeout)

#### Суть
`gpt-image-2 + quality=high` через `https://hcbifrost.herocraft.com/litellm/v1/images/generations`
возвращал HTTP 504 через 60.27s. Причина: OpenAI upstream генерирует ~120s,
а Timeweb reverse proxy (89.19.213.124) имеет hardcoded 60s timeout.

Все остальные image models работают нормально:
`gpt-image-1.5 quality=high` (~28s), `gpt-image-2 quality=medium` (~45s),
`gemini/gemini-3-pro-image` (~15s), `gemini/gemini-3.1-flash-image` (~7s).

#### Решение (3 файла + 1 hook registration)

1. **`/opt/litellm/config.yaml:119`** — `router_settings.timeout 60 → 300` (5 минут).
   Backup: `config.yaml.bak.timeout-60`.

2. **`/etc/nginx/sites-available/litellm-bifrost`** — default_server `/` location
   получил `proxy_read_timeout 600s; proxy_send_timeout 600s;` (раньше
   использовал default 60s). Backup: `litellm-bifrost.bak.default-server`.
   Primary server (`/litellm/`) уже имел 600s через `proxy-common.conf`.

3. **`/opt/litellm/image_rate_limit_hook.py`** — добавлен pre-call check
   `if model == "gpt-image-2" and quality == "high": raise HTTPException(400, ...)`
   с понятным alternatives list (`gpt-image-1.5 high`, `gemini-3-pro`, `gpt-image-2 medium`).
   Backup: `image_rate_limit_hook.py.bak.gpt-image2-quality`.

4. **`/opt/litellm/utils_patched.py`** — расширен monkey-patch для регистрации
   `image_rate_limit_logger` в `litellm.callbacks` (аналогично user_agent_hook).
   Раньше hook файл был на диске, но не регистрировался при старте (config.yaml
   не имел `callbacks:` блока, в отличие от user_agent_hook). Backup:
   `utils_patched.py.bak.register-rate-limit`. **+1036 bytes**.

#### Verification

| Запрос | До | После |
|--------|-----|-------|
| `gpt-image-2 quality=high` (external) | 504 за 60.27s | 400 за 0.29s + alternatives |
| `gpt-image-2 quality=high` (direct 127.0.0.1:4001) | timeout 180s (curl) | 200 за 119s |
| `gpt-image-2 quality=high` (direct 127.0.0.1:8080) | 504 за 60.03s | 200 за 122s |
| `gpt-image-2 quality=medium` | 200 за 45s | 200 за 45s (без изменений) |
| `gpt-image-1.5 quality=high` | 200 за 28s | 200 за 28s (без изменений) |
| `gemini/gemini-3-pro-image` baseline | 200 за 15s | 200 за 15s (без изменений) |
| `gemini/gemini-3.1-flash-image` baseline | 200 за 7s | 200 за 7s (без изменений) |
| chat `MiniMax-M3` (sanity) | 200 OK | 200 OK (без изменений) |

Подтверждено через docker logs: `image_rate_limit_hook: async_pre_call_hook
CALLED call_type='image_generation' model='gpt-image-2' quality='high'`.

#### Поведение hook

```json
{
  "error": "slow_quality_combination_blocked",
  "message": "gpt-image-2 with quality=high is currently blocked: ...",
  "model": "gpt-image-2",
  "quality": "high",
  "alternatives": [
    {"model": "gpt-image-1.5", "quality": "high", "approx_latency": "30s", ...},
    {"model": "gemini/gemini-3-pro-image", "quality": "auto", "approx_latency": "15s", ...},
    {"model": "gpt-image-2", "quality": "medium", "approx_latency": "46s", ...}
  ]
}
```

Когда можно снять блокировку: либо перестать использовать Timeweb
(свой reverse proxy / CDN с большим timeout), либо когда OpenAI
оптимизирует generation для `gpt-image-2 quality=high` до <60s.

Подробный analysis: `.serena/memories/image-models-gpt-image-2-high-fix.md`.

---

### [2026-07-01] — GLM-5.2 (res) missing limits fix

#### Суть
У `GLM-5.2 (res)` в `LiteLLM_ProxyModelTable.model_info` отсутствовали поля
`max_input_tokens` и `max_tokens` — в результате `/setup-opencode/?user=<uuid>`
генерировал `limit: {context: 0, output: 0}` для этой модели.

Это известное ограничение `POST /model/update` — поле визуально "успешно",
но **не персистится** в DB. Другие `(res)` модели (`GLM-5.1 (res)`,
`GLM-4.7 (res)`) были заполнены ранее через тот же SQL workflow — а вот
`GLM-5.2 (res)` была пропущена, т.к. добавлена позже через UI.

#### SQL UPDATE

```sql
UPDATE "LiteLLM_ProxyModelTable"
SET model_info = model_info || jsonb_build_object(
  'max_input_tokens', 1048576,
  'max_tokens', 131072
),
updated_at = NOW()
WHERE model_name = 'GLM-5.2 (res)';
```

`jsonb_build_object(...)` мержится с существующими полями — `id`,
`access_via_team_ids`, `direct_access`, `blocked` остаются нетронутыми.

#### Workflow

1. `docker exec litellm-pg psql ... UPDATE ...` (см. выше)
2. `docker restart litellm` + ждать `/health/readiness` = 200
3. `pkill -9 -f "python3 /opt/opencode-setup/api.py"; nohup python3 ... &`
   (сбросить `_MODEL_INFO` кэш в api.py)
4. Верификация: `/model/info` + `/setup-opencode/api/models?user=<uuid>`

#### Verification

| Модель | До | После |
|--------|-----|-------|
| `GLM-5.2 (res)` в `/model/info` | `ctx=None, out=None` | `ctx=1048576, out=131072` |
| `GLM-5.2 (res)` в `/setup-opencode/api/models?user=44224bef` (Coders team) | отсутствовала | `context: 1048576, output: 131072, reasoning: true` |
| `GLM-5.2` (базовая) | `ctx=1048576, out=131072` | (без изменений) |

Hook `user_agent_hook.UserAgentLogger` загружен в callbacks после рестарта LiteLLM
— merged reasoning chunks для Kimi K2.6/K2.7/MiniMax-M2.7 продолжают работать.

---

### [2026-07-01] — opencode.json generator: reasoning auto-injection

#### Суть
`/setup-opencode/?user=<uuid>` теперь автоматически добавляет `options.thinking.type=enabled`
в opencode.json для всех reasoning-capable моделей. Раньше каждый пользователь получал
"голый" конфиг без thinking-опций, и нужно было руками дописывать или настраивать reasoning
через LiteLLM upstream. Теперь всё работает "из коробки" при перегенерации конфига.

#### Что изменено

**`/opt/opencode-setup/api.py`** (315 → 332 строк)

Добавлен hardcoded set `_REASONING_CAPABLE` (20 моделей) + поле `reasoning: bool` в
`resolve_models()` output. Set определяет reasoning-capable модели независимо от
LiteLLM upstream — то есть работает даже если у модели нет `litellm_params.thinking`.

```python
_REASONING_CAPABLE = {
    "glm-4.5", "glm-4.5-air", "glm-4.6",
    "GLM-4.7", "GLM-4.7 (res)",
    "GLM-5.1", "GLM-5.1 (res)",
    "GLM-5.2", "GLM-5.2 (res)",
    "kimi-k2-0905-preview", "kimi-k2-turbo-preview",
    "Kimi K2.6", "Kimi K2.7",
    "MiniMax-M3",
    "QWEN3.7-plus", "qwen3-coder-plus", "qwen3-coder-flash", "qwen3-max",
    "mimo-v2.5", "mimo-v2.5-pro",
}
```

**`/opt/opencode-setup/index.html`** (253 → 255 строк)

В `buildCfg()` добавлен блок:

```js
if (m.reasoning) {
  entry.options = { thinking: { type: "enabled" } };
}
```

#### Что НЕ нужно трогать

- **LiteLLM DB** — transparent passthrough через `custom_openai` уже работает для
  Kimi K2.7, MiniMax-M3, GLM-5.x (LiteLLM проксирует `thinking: {type: enabled}` в upstream).
- **LiteLLM config.yaml** — не нужно.
- **Локальный `C:\Users\Admin\.config\opencode\opencode.json`** — это для моего
  личного opencode, не пользователей.

#### Почему без `budgetTokens`

Первоначально я предположил по аналогии с Anthropic, что zhipu GLM требует
`budget_tokens`. **Неверно** — official zhipu docs
(https://docs.bigmodel.cn/cn/guide/models/text/glm-5) показывают просто
`{"thinking": {"type": "enabled"}}` без бюджета. Модель сама решает сколько думать.

GLM-5 имеет свой `reasoning_effort: "max"|"high"` параметр (отдельно от thinking).
Не добавлено в этот патч — можно точечно через upstream, если нужно.

#### Явно исключены (reasoning=false)

- `MiniMax-M2.7` — LiteLLM handler теряет `<think>` теги, парсинг не работает
- `MiniMax-M2`, `MiniMax-M2.5` — старые версии без thinking
- `moonshot-v1-128k` — старая v1 серия без reasoning
- `gpt-image-1.5`, `gpt-image-2` — только image generation
- `gemini/gemini-3-pro-image`, `gemini/gemini-3.1-flash-image` — только image generation

#### Verification

1. `curl http://127.0.0.1:9000/health` → `{"ok": true}` ✓
2. `curl /models?user=54f4915d-...` (admin) → JSON содержит `reasoning: true` для
   GLM-4.7, GLM-5.1, GLM-5.2, Kimi K2.6, Kimi K2.7, MiniMax-M3, QWEN3.7-plus,
   mimo-v2.5, mimo-v2.5-pro. `reasoning: false` для MiniMax-M2.7 и `no-default-models` ✓
3. Replicated `buildCfg()` logic в Python — reasoning=true → `options.thinking`,
   reasoning=false → нет. ✓

#### Deploy

- Deploy script: `C:\Users\Admin\AppData\Local\Temp\opencode\deploy_gen.py <local> <remote>`
  (generic, не завязан на users-btn.js как старый deploy3.py)
- Restart: `pkill -9 -f "python3 /opt/opencode-setup/api.py"; nohup python3 /opt/opencode-setup/api.py &`
- Backups: `/opt/opencode-setup/api.py.bak.reasoning-gen`, `index.html.bak.reasoning-gen`

#### Файлы

| Локальная копия | Remote |
|---|---|
| `C:\Users\Admin\AppData\Local\Temp\opencode\opencode-setup\api.py` | `/opt/opencode-setup/api.py` |
| `C:\Users\Admin\AppData\Local\Temp\opencode\opencode-setup\index.html` | `/opt/opencode-setup/index.html` |

#### Memory

`P:\Programming\bifrost\.serena\memories\opencode-reasoning-generator.md` — полное
описание + reasoning-capable список + excluded модели.

---

### [2026-06-27] — Per-user per-team per-day image generation rate limit

#### Суть
Добавлен custom pre-call hook `image_rate_limit_hook.py` в LiteLLM, который ограничивает
количество запросов на генерацию изображений в день для каждого пользователя, с лимитом,
определяемым по его команде:

| Team alias | daily_limit |
|---|---|
| All Access | 100 |
| остальные 7 команд (Art, Analytics, CreativeTeam, Coders, PirateShips, Porters, SideCoders) | 50 |
| `_default` (fallback для неизвестной команды или юзера без команды) | 50 |

Лимит действует только на 4 image-модели:
- `gpt-image-1.5`
- `gpt-image-2`
- `gemini/gemini-3.1-flash-image`
- `gemini/gemini-3-pro-image`

Chat completions, embeddings, transcriptions, etc. — НЕ задевает.

#### Семантика счётчика
- 1 HTTP-запрос с `n=1` → +1 в счётчик
- 1 HTTP-запрос с `n=10` → +10 в счётчик (1 картинка = 1 запрос)
- Если `current + n > limit` → отклоняет ВЕСЬ запрос (даже если часть картинок вписалась бы)
- Сутки считаются по UTC, сброс в 00:00 UTC
- Race-safe: атомарный UPSERT `INSERT ... ON CONFLICT DO UPDATE ... RETURNING count`

#### Архитектура

```
Client → nginx (/litellm/v1/images/generations)
       → LiteLLM proxy (port 4001)
          → async_pre_call_hook chain:
             1. ImageRateLimitLogger  ← проверяет счётчик, решает пускать или 429
             2. UserAgentLogger       ← уже существующий
          → route_request → upstream (gpt-image-*, gemini-image-*)
```

#### Новые таблицы в `litellm-pg`

```sql
CREATE TABLE image_team_limits (
  team_alias text PRIMARY KEY,
  daily_limit int NOT NULL CHECK (daily_limit >= 0)
);

CREATE TABLE image_request_counter (
  user_id text NOT NULL,
  model text NOT NULL,
  day date NOT NULL DEFAULT CURRENT_DATE,
  count int NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, model, day)
);
CREATE INDEX image_request_counter_day_idx ON image_request_counter (day);
```

#### Файлы изменены/добавлены

| Файл | Действие |
|---|---|
| `/opt/litellm/image_rate_limit_hook.py` | **создан** (~250 строк, CustomLogger с async_pre_call_hook) |
| `/opt/litellm/config.yaml` | добавлен callback `image_rate_limit_hook.image_rate_limit_logger` (первым в списке) |
| `/opt/litellm/litellm_entrypoint.sh` | добавлен PYEOF3 (установка `psycopg2-binary` если нет) + PYEOF4 (страховочная регистрация хука в `litellm.callbacks`, паттерн как для UserAgentLogger) |
| `/opt/litellm/start.sh` | добавлен bind-mount `-v $(pwd)/image_rate_limit_hook.py:/app/image_rate_limit_hook.py:ro` |
| DB `litellm-pg` | созданы таблицы + seed для 9 строк в `image_team_limits` |

#### psycopg2-binary — особенность установки
В образе `ghcr.io/berriai/litellm:main-stable` venv `/app/.venv/` **не содержит** psycopg2/psycopg3.
`litellm_entrypoint.sh` теперь устанавливает `psycopg2-binary` через `pip install --quiet`
на каждом старте контейнера (идемпотентно — pip пропускает если уже стоит).

DATABASE_URL хук читает из bind-mounted `/app/.env` —
`postgresql://litellm:litellm@litellm-pg:5432/litellm` уже там.

#### Ответ при 429
```json
{
  "error": {
    "message": "Daily image-generation limit reached for model 'gpt-image-1.5'. Requested 1, remaining 0 (limit 50/day, team=CreativeTeam).",
    "type": "image_daily_limit_exceeded",
    "code": 429,
    "limit": 50,
    "used": 50,
    "requested": 1,
    "remaining": 0,
    "team": "CreativeTeam",
    "reset_at": "UTC midnight"
  }
}
```

#### Управление лимитами

```bash
# Посмотреть текущие лимиты
docker exec litellm-pg psql -U litellm -d litellm -c "SELECT * FROM image_team_limits;"

# Изменить лимит для команды (нужен рестарт контейнера, т.к. хук кеширует мап на 60с)
docker exec litellm-pg psql -U litellm -d litellm -c "UPDATE image_team_limits SET daily_limit=200 WHERE team_alias='All Access';"
ssh -p 1995 dev01@162.55.137.149 'docker restart litellm'

# Добавить новую команду
docker exec litellm-pg psql -U litellm -d litellm -c "INSERT INTO image_team_limits(team_alias,daily_limit) VALUES ('NewTeam',75);"
ssh -p 1995 dev01@162.55.137.149 'docker restart litellm'

# Сбросить счётчик пользователя (admin override)
docker exec litellm-pg psql -U litellm -d litellm -c "DELETE FROM image_request_counter WHERE user_id='...';"

# Посмотреть текущие счётчики
docker exec litellm-pg psql -U litellm -d litellm -c "SELECT user_id,model,day,count FROM image_request_counter ORDER BY day DESC,user_id,model;"
```

#### Верификация (7/7 тестов прошли, 2026-06-27)
Созданы 2 тестовых ключа (CreativeTeam user, All Access user), счётчики выставлены вручную
через SQL для симуляции "почти у лимита":

| # | Сценарий | Ожидалось | Факт |
|---|---|---|---|
| 1 | +1 вызов → counter=1 | 200, count=1 | ✅ 200, 1 |
| 2 | counter=49 → +1 → 50, нет 429 | 200, count=50 | ✅ 200, 50 |
| 3 | counter=50 → +1 → 429, counter не растёт | 429, count=50 | ✅ 429, 50 |
| 4 | n=4 → counter=+4 | 200, count=4 | ✅ 200, 4 |
| 5 | counter=47, n=4 (47+4>50) → 429, no increment | 429, count=47 | ✅ 429, 47 |
| 6 | chat completions не трогают счётчик | 200, counter пуст | ✅ 200, пусто |
| 7 | All Access: 100-й ОК, 101-й 429 | 200, 429 | ✅ 200, 429 |

После тестов: счётчики очищены (`DELETE FROM image_request_counter`),
тестовые ключи удалены через `POST /key/delete` с `key_aliases=[...]`.

#### Известное ограничение — `Retry-After` header теряется
LiteLLM image endpoint (`/app/.venv/lib/python3.13/site-packages/litellm/proxy/image_endpoints/endpoints.py` ~line 187)
ловит **любое** исключение и переоборачивает его в `ProxyException` с полями
`message/type/param/code` — **теряя `headers`**. Поэтому `Retry-After`
в минутах до полуночи, который хук кладёт в `HTTPException(headers=...)`,
не доходит до клиента.

Body содержит `reset_at: "UTC midnight"` и `remaining: N` — этого достаточно
для клиентов, чтобы вычислить время retry самостоятельно. Если header
станет критичен — патчить блок `except` в `image_endpoints/endpoints.py`
(выйти за рамки этой задачи).

Для сравнения: chat-completions endpoint в streaming-пути делает
`if isinstance(e, HTTPException): raise e` — там headers сохраняются.

#### Memory
- `infra/hcbifrost-vm-litellm` — добавлен раздел "Image generation daily rate limit (per user / per team) — 2026-06-27"

---

## Архитектура

- **LiteLLM Proxy** в Docker (`ghcr.io/berriai/litellm:main-stable`) на порту `4000` → проброшен на хост как `4001:4000`
- **PostgreSQL** в Docker (`postgres:16-alpine`, контейнер `litellm-pg`) — хранит модели и пользователей
- **opencode-setup** — standalone HTML+JS генератор на `/opt/opencode-setup/` (порт `9000` для API), проксируется nginx
- **Nginx** на хосте: `hcbifrost.herocraft.com` — маршрутизация по Host header + path
- **Public URL**: `https://hcbifrost.herocraft.com/litellm/ui/` для админки, `https://hcbifrost.herocraft.com/setup-opencode/` для генератора

---

## Изменения

### [2026-06-26] — Добавление модели gpt-image-1.5

Модель добавлена через UI с правильными настройками с первого раза:
- `api_base`: `https://api.openai.com/v1` (без `/images/generations`)
- `mode`: `image_generation`
- `custom_llm_provider`: `openai`
- `model_id`: `28e2450c-f6bd-497c-bea4-484775fa5482`

Добавлена в команду "All Access" через `POST /team/model/add`.

Патч `user_agent_hook.py` (от исправления `gpt-image-2`) автоматически обработал все
image generation запросы для новой модели — никаких дополнительных правок не потребовалось.

#### Тест
`POST /v1/images/generations` с `model=gpt-image-1.5` → 200, возвращает base64 PNG.

---

### [2026-06-26] — Исправление модели gpt-image-2 (image generation)

#### Проблема
Модель `gpt-image-2` (OpenAI image generation) не работала через LiteLLM proxy:
- `POST /v1/images/generations` → 404 "Received Model Group=gpt-image-2" (OpenAI SDK отправлял запрос на chat completions endpoint вместо image endpoint)
- После исправления api_base → 400 "Unknown parameter: 'extra_headers'" (LiteLLM инжектил extra_headers в request body)

#### Корневые причины (3 проблемы)

**1. Неправильный `api_base` в модели**
`litellm_params.api_base = "https://api.openai.com/v1/images/generations"` — полный URL endpoint'а.
OpenAI SDK автоматически добавляет `/images/generations` к `base_url`, получался двойной путь:
`https://api.openai.com/v1/images/generations/images/generations` → 404.

**2. `extra_headers` от user_agent_hook попадает в request body**
`user_agent_hook.py` добавляет `data["extra_headers"] = {"User-Agent": ...}`.
LiteLLM для image generation forwarding ключ `extra_headers` как параметр request body (а не HTTP header),
что OpenAI image endpoint отвергает с 400.

**3. `call_type` различается между hooks**
- `pre_call_hook` получает строку `"image_generation"` (из endpoints.py)
- `pre_call_deployment_hook` получает `CallTypes.aimage_generation` (enum из utils.py)
Патч `if call_type == "image_generation"` не срабатывал для deployment hook.

#### Исправления

**DB patch** (api_base):
```bash
curl -X PATCH "http://127.0.0.1:4001/model/ace47273-2a04-4709-b19c-437d9e948d61/update" \
  -H "Authorization: Bearer sk-litellm-placeholder-replace-before-prod" \
  -H "Content-Type: application/json" \
  -d '{"litellm_params":{"api_base":"https://api.openai.com/v1"}}'
```

**user_agent_hook.py patch** (extra_headers + call_type):
```python
# async_pre_call_hook:
if (call_type == "image_generation" or call_type == "aimage_generation"
    or (hasattr(call_type, "value") and call_type.value in ("image_generation", "aimage_generation"))):
    try:
        data.pop("extra_headers", None)
    except Exception:
        pass
    return data

# async_pre_call_deployment_hook — аналогично для kwargs
```

Файл: `/opt/litellm/user_agent_hook.py` (монтируется в контейнер как `/app/user_agent_hook.py`)
Бэкап: `/opt/litellm/user_agent_hook.py.bak`

#### Результат
- `POST /v1/images/generations` (gpt-image-2) → 200, возвращает base64 PNG
- `POST /v1/chat/completions` (GLM-5.2, и др.) → 200, работает без изменений

#### Модель в БД
- `model_id`: `ace47273-2a04-4709-b19c-437d9e948d61`
- `model_name`: `gpt-image-2`
- `mode`: `image_generation`
- `litellm_provider`: `openai`
- `organization`: `org-vcXoFtfQpBcFYYB4ZajfbXp7`
- Команда: All Access (`02445a34-eb7c-4bff-9a55-c465ff531944`)

---

### [2026-06-26] — Добавление моделей Google для генерации изображений

Добавлены две модели Google для генерации изображений через Gemini API:

| Модель | Тип | Провайдер | Статус |
|---|---|---|---|
| `gemini-3.1-flash-image-preview` | Image generation | gemini | ✅ |
| `gemini-3-pro-image-preview` | Image generation | gemini | ✅ |

Обе модели используют Google API key из переменной окружения `GEMINI_API_KEY`.

#### Конфигурация
- `custom_llm_provider`: `gemini`
- `mode`: `image_generation`
- `litellm_params.model`: `gemini/<model-name>`

#### Тест
`POST /v1/images/generations` с `model=gemini-3.1-flash-image-preview` → 200, возвращает base64 PNG.

---

### [2026-06-25] — Смена API ключей GLM моделей через PATCH

#### Операция
Обновлён `api_key` для 3 моделей GLM без пересоздания:

| model_name | model_id | статус |
|---|---|---|
| GLM-5.2 | f393fafb-e751-412f-9a2b-09599e00b5fc | ✅ |
| GLM-4.7 | 647ba2c7-d8d6-4d78-a843-d504a30fe688 | ✅ |
| GLM-5.1 | fcb40715-a4f9-4cdc-85bc-d8b0cf5d4bcb | ✅ |

Новый ключ: `fdcc9952f19944c2bd2e02f9f3126c58.cMmdWA40NDkTtPoH`

#### Способ: PATCH endpoint
Использован `PATCH /model/{model_id}/update` вместо пересоздания модели.

```bash
cat > /opt/litellm/patch_payload.json <<EOF
{"litellm_params": {"api_key": "fdcc9952f19944c2bd2e02f9f3126c58.cMmdWA40NDkTtPoH"}}
EOF

for mid in f393fafb-... 647ba2c7-... fcb40715-...; do
  curl -X PATCH "https://hcbifrost.herocraft.com/litellm/model/$mid/update" \
    -H "Authorization: Bearer sk-litellm-placeholder-replace-before-prod" \
    -H "Content-Type: application/json" \
    --data @/opt/litellm/patch_payload.json
done
```

#### Тест
GLM-5.2 после обновления вернул chat response с `reasoning_content` — ключ валиден.

#### Документация
- Memory `litellm-rotate-model-key` — полный workflow: как узнать `model_id`, формат PATCH, ограничения (только для DB-моделей, hot-reload, auto-encryption)
- Файлы скриптов: `C:\Users\Admin\AppData\Local\Temp\opencode\litellm-files\patch_keys.sh`, `patch_payload.json`

### [2026-06-23] — Models, UI injection, MiniMax-M3 limit

#### Модели и лимиты

**`config.yaml`** — `litellm_settings: { drop_params: true, callbacks: ["user_agent_hook.user_agent_logger"] }`

| Модель | Где | context | output | vision |
|---|---|---|---|---|
| MiniMax-M3 | DB | 512000 | 131072 | да |
| MiniMax-M2.7 | DB | 204800 | 131072 | нет |
| MiniMax-M2 | YAML | 204800 | 131072 | нет |
| GLM-5.2 | DB | 1048576 | 131072 | нет |
| GLM-5.1 | DB | 204800 | 131072 | нет |
| GLM-4.7 | DB | 204800 | 131072 | нет |
| GLM-4.6 | YAML | 204800 | 131072 | нет |
| GLM-4.5 | YAML | 131072 | 98304 | нет |
| GLM-4.5-air | YAML | 131072 | 98304 | нет |
| Kimi K2.6 | DB | 262144 | 262144 | да |
| Kimi K2.7 | DB | 262144 | 262144 | да |
| QWEN3.7-plus | DB | 1000000 | 80000 | да |
| qwen3-coder-plus | YAML | 1000000 | 65536 | нет |
| qwen3-coder-flash | YAML | 1000000 | 65536 | нет |
| qwen3-max | YAML | 262144 | 65536 | нет |
| kimi-k2-0905-preview | YAML | 262144 | 262144 | нет |
| kimi-k2-turbo-preview | YAML | 262144 | 262144 | нет |
| moonshot-v1-128k | YAML | 131072 | 131072 | нет |

---

## Операции: обновление моделей (ctx/out/vision/video)

> Подробно: `.serena/memories/infra/litellm-model-updates.md`

**Проблема:** `POST /model/update` **не персистит** `max_input_tokens`,
`max_tokens`, `supports_vision`, `supports_video_input` в `model_info` для
моделей, созданных через UI/API. Ответ — success, но в DB и `/model/info`
поля пустые. Без `litellm_params` (только `model_name` + `model_info`)
ответ — `model not found`.

**Решение:**

1. **SQL UPDATE** в `LiteLLM_ProxyModelTable.model_info` (мерж через `||`):
   ```sql
   UPDATE "LiteLLM_ProxyModelTable"
   SET model_info = model_info || jsonb_build_object(
     'max_input_tokens', 204800,
     'max_tokens', 131072,
     'supports_vision', true,
     'supports_video_input', true
   ),
   updated_at = NOW()
   WHERE model_name = 'GLM-4.7 (res)';
   ```

2. **Перезапуск контейнера** (in-memory кэш LiteLLM):
   ```bash
   docker restart litellm
   # ждать /health/readiness = 200
   ```

3. **Перезапуск `opencode-api`** (свой `_MODEL_INFO` кэш в `api.py`):
   ```bash
   systemctl restart opencode-api
   ```

4. **Верификация** через `/model/info` API, затем перегенерация
   `/setup-opencode/?user=<uuid>` в браузере (Ctrl+Shift+R).

**Не трогать** через SQL: `api_base`, `api_key`, `litellm_credential_name` —
зашифрованы. Для смены endpoint/credential — только через UI или API.

**Важно:** `max_input_tokens` хранится ТОЛЬКО в `model_info` (НЕ в `litellm_params` — иначе OpenAI client падает).

**Vision:** определяется в `model_info.supports_vision: true` в БД. Читается через `/model/info`. Только 4 модели: MiniMax-M3, Kimi K2.6, Kimi K2.7, QWEN3.7-plus.

#### Reasoning

- `merge_reasoning_content_in_choices: false` глобально (для всех 8 DB моделей через `jsonb_set`) — LiteLLM не склеивает reasoning с content
- Custom `user_agent_hook.py` — `UserAgentLogger` (module-level instance) + `async_post_call_streaming_iterator_hook`
- Streaming hook — state machine с буфером: детектит <think> в content, буферизует до </think>, отбрасывает reasoning. Модели без <think> тегов (GLM, Kimi) проходят прозрачно.
- MiniMax reasoning работает корректно: `reasoning_content` → reasoning (тонкий шрифт), `content` → только ответ после </think> (жирный шрифт)
- MiniMax native format: `content` содержит <think>reasoning</think>answer + `reasoning_content` с чистым reasoning

#### User-Agent инжекция

- Подмена UA на `opencode/local ai-sdk/provider-utils/4.0.27 runtime/node.js/24` если в client_ua есть "Mozilla"
- Иначе — проброс клиентского UA
- `UserAgentLogger` — module-level singleton (LiteLLM ожидает instance, не class)

#### GLM Internal Server Error fix

- `max_input_tokens` был в `litellm_params` → OpenAI client падал
- Решение: убрать из `litellm_params`, оставить только в `model_info`

#### OpenCode UI injection

**`/opt/opencode-setup/users-btn.js`** инжектится через nginx `sub_filter '</head>' '${oc_inject}</head>'`.

- Кнопка "🛠 Мои настройки" — floating bottom-right, на ЛЮБОЙ странице LiteLLM UI
  - Авто-определение user_id: JWT из `sessionStorage["token"]` / cookie `token` → DOM scan ([data-testid*="user"], [class*="profile"], header, aside) → localStorage → prompt
  - URL: `/setup-opencode/?user=<uid>`
  - Сохраняет в `localStorage.bifrost_opencode_user_id`
  - Рядом маленькая `×` для сброса кэша
- Кнопка "🛠 OpenCode" — в каждой строке таблицы Users (только на `?page=users`)
  - Ищет строки по `data-testid*="user-status-<UUID>"`
  - Вставляет в action cell (`div[class*="flex"]`)

**Важно:** MutationObserver + DOM scan = зависает браузер. Throttle 750ms + кэш UUID обязательны.

**`/opt/opencode-setup/index.html`** — генератор конфигурации opencode для пользователя.

- Получает `?user=<UUID>`, fetchит `/setup-opencode/api/models?user=<UUID>`
- AGENT_PROMPT prefix перед JSON (инструкция агенту):
  > добавь или обнови настройки для провайдера агента, который я прямо сейчас использую (opencode, hermes или другой), если в текущих настройках имеется ключ, используй его. если ключа нет, запроси его у пользователя
- Кнопка "📋 Copy" копирует AGENT_PROMPT + JSON
- Кнопка "⬇ Download" скачивает `.txt`
- Инструкция для пользователя под textarea:
  > Скопируй текст с настройками выше, добавь его в своего агента (opencode, hermes или другой) и выполни. ключ или подтянется имеющийся или будет запрошен

**КРИТИЧЕСКИ:** `modalities: { input: ["text","image"] }` + `attachment: true` для vision моделей. НЕ `capabilities.input.image` — opencode игнорирует это поле!

**`/opt/opencode-setup/api.py`** — порт 9000, проксируется nginx → `/setup-opencode/api/`.

- `resolve_models(user_id)` → читает `/model/info` (single source of truth)
- Возвращает `[{id, context, output, vision}, ...]`
- Master key только здесь, не экспонируется в браузер

#### MiniMax-M3 limit change

- Было: 1048576 / 131072
- Стало: 512000 / 131072
- Через SQL: `UPDATE LiteLLM_ProxyModelTable SET model_info = jsonb_set(model_info::jsonb, '{max_input_tokens}', '512000'::jsonb, true) WHERE model_name = 'MiniMax-M3';`

#### MCP auth в opencode.json — фикс

- **Было:** `"headers": { "x-litellm-api-key": "{env:LITELLM_API_KEY}" }` — невалидный заголовок для opencode MCP клиента
- **Стало:** `"headers": { "Authorization": "Bearer {env:LITELLM_API_KEY}" }` — стандартная Bearer-авторизация, opencode проксирует на LiteLLM
- Файл: `/opt/opencode-setup/index.html` (`buildCfg()` для `cfg.mcp` секции)
- Local copy: `C:\Users\Admin\AppData\Local\Temp\opencode\opencode-setup\index.html`
- Deploy: `python C:\Users\Admin\AppData\Local\Temp\opencode\deploy3.py "<local>" /opt/opencode-setup/<remote>`

#### GLM (res) модели — добавление + ctx/out

- Добавлены через UI: `GLM-5.2 (res)` (ctx=1048576), `GLM-5.1 (res)` (ctx=204800), `GLM-4.7 (res)` (ctx=204800), output=131072 для всех
- `model_info` для них изначально был пустой (UI не записывает ctx/out)
- `/model/update` API **не персистит** `max_input_tokens`/`max_tokens` в `model_info` — пустой ответ success, но в DB значения не записываются
- **Решение:** прямой SQL UPDATE в `LiteLLM_ProxyModelTable` через `jsonb_build_object(...) || model_info` (мерж, не перезапись) + `docker restart litellm` + `systemctl restart opencode-api` (его собственный `_MODEL_INFO` кэш)
- Подробно: `.serena/memories/infra/litellm-model-updates.md`

#### QWEN3.7-plus — video support

- Включён `supports_video_input: true` в `model_info`
- SQL: `UPDATE "LiteLLM_ProxyModelTable" SET model_info = model_info || '{"supports_video_input": true}'::jsonb, updated_at = NOW() WHERE model_name = 'QWEN3.7-plus';`
- `api.py` уже читает `supports_video_input` → `index.html` добавляет `"video"` в `modalities.input` если `m.video === true`

#### Nginx изменения

- `proxy_hide_header Content-Security-Policy;` — иначе CSP блокирует injected script
- `proxy_hide_header X-Frame-Options;`
- `sub_filter '/litellm-asset-prefix/' '/';` — strip sentinel
- Cache-bust: `${oc_inject}` с `?v=N` (текущий `?v=11`)

#### Deploy workflow

1. Редактировать локально: `C:\Users\Admin\AppData\Local\Temp\opencode\opencode-setup\{users-btn.js,index.html,api.py}`
2. Deploy: `python C:\Users\Admin\AppData\Local\Temp\opencode\deploy3.py "<local>" /opt/opencode-setup/<remote>`
3. Поднять cache-bust: `echo "7Cr4iW9l8P" | sudo -S -p "" sed -i "s/users-btn.js?v=N/users-btn.js?v=N+1/g" /etc/nginx/sites-enabled/litellm-bifrost && echo "7Cr4iW9l8P" | sudo -S -p "" nginx -s reload`

---

## Известные ограничения / Gotchas

1. **PowerShell + sudo:** `&&` не работает после `sudo -S`. Использовать `runsudo.py` helper для каждой команды отдельно.
2. **Deploy tool:** иногда файлы на VM перезаписываются содержимым другого файла (race condition). Всегда проверять через `head/grep` после деплоя.
3. **Memory хранится через Serena:** см. memory `opencode-inject-buttons`, `litellm-ui-user-id-source`, `litellm-mcp-permissions`, `litellm-rotate-model-key`
4. **`max_input_tokens` в litellm_params → TypeError** в OpenAI client. Только в model_info.
5. **GLM (Z.AI coding endpoint)** жёстко отвергает `image_url` — vision не поддерживается upstream.
6. **QWEN3.7-plus vision**: минимум 10×10 px.
7. **opencode `unsupportedParts()`** в `transform.ts:217` стрипает image content если `model.capabilities.input.image === false` (из models.dev catalog). Нужен override через `modalities.input`.

### [2026-06-24] — MiMo models + multimodal modalities

#### Добавлены модели MiMo (Xiaomi)
- `mimo-v2.5-pro` — context=1000000, output=131072, text only
- `mimo-v2.5`     — context=1000000, output=131072, **multimodal input** (image + video + audio)

#### Расширение opencode-setup для multimodal
- `_extract_meta()` теперь возвращает `(ctx, out, vision, video, audio)` — читает `supports_vision`, `supports_video_input`, `supports_audio_input` из `model_info`
- `resolve_models()` добавляет `video` и `audio` поля в ответ API
- `buildCfg()` в `index.html` строит `modalities.input` как массив: `["text"]` + `"image"`/`"video"`/`"audio"` по флагам
- `attachment: true` ставится когда хотя бы одна не-text модальность есть

#### Флаги в БД (`LiteLLM_ProxyModelTable.model_info`)
```
mimo-v2.5:     supports_vision=true, supports_video_input=true, supports_audio_input=true
mimo-v2.5-pro: (без флагов — text only)
```

#### Тест (lordtyred@herocraft.com)
```
mimo-v2.5     ctx=1000000 out=131072 img=True vid=True aud=True
mimo-v2.5-pro ctx=1000000 out=131072 img=False vid=False aud=False
```

Сгенерированный opencode.json:
```json
"mimo-v2.5": {
  "name": "mimo-v2.5",
  "limit": {"context": 1000000, "output": 131072},
  "modalities": {"input": ["text", "image", "video", "audio"]},
  "attachment": true
},
"mimo-v2.5-pro": {
  "name": "mimo-v2.5-pro",
  "limit": {"context": 1000000, "output": 131072}
}
```

#### Файлы изменены
- `/opt/opencode-setup/api.py` — `_extract_meta`, `build_model_info`, `resolve_models`
- `/opt/opencode-setup/index.html` — `buildCfg` (inputMods array)

---

### [2026-06-23] — MCP серверы: Teams как источник правды

#### Изменение источника правды для MCP
- **Было:** `api.py` возвращал все MCP с `allow_all_keys=true` юзеру с любым активным ключом
- **Стало:** источник правды — `object_permission.mcp_servers` всех команд пользователя

#### Алгоритм `resolve_user_mcp_servers()`
1. `/user/info?user_id=<uid>` → список team_id юзера
2. Для каждой команды `/team/info?team_id=<tid>` → `object_permission.mcp_servers` (server_name/alias, **не UUID**) + `mcp_access_groups`
3. Объединить уникальные server_name/alias из всех команд
4. `/v1/mcp/server` → зарегистрированные серверы (LiteLLM internal manager, **не в Postgres**)
5. Фильтр: server_name/alias ∈ allowed set ИЛИ `mcp_access_groups` пересекается с allowed

#### Тест (lordtyred@herocraft.com, team=All Access)
- mcp_servers=[] в команде → `{"servers":[]}` ✓
- Добавил `zai_web_search` через `/team/update` → `{"servers":[{"name":"zai_web_search",...}]}` ✓
- Убрал → снова `{"servers":[]}` ✓
- Восстановил для текущего использования ✓

#### Привязка MCP к команде через API
```bash
curl -X POST -H "Authorization: Bearer sk-litellm-placeholder-replace-before-prod" \
  -H "Content-Type: application/json" \
  --data '{"team_id":"<UUID>","object_permission":{"mcp_servers":["zai_web_search","zai_zread"]}}' \
  "https://hcbifrost.herocraft.com/litellm/team/update"
```

#### Файлы изменены
- `/opt/opencode-setup/api.py` — переписаны `resolve_user_mcp_servers`, `resolve_user_teams`, добавлены `_collect_team_mcp_ids`, `_list_mcp_servers`
- API рестартован: `pkill -9 -f "python3 /opt/opencode-setup/api.py"; nohup python3 /opt/opencode-setup/api.py &`

#### Memory
- `litellm-mcp-permissions` — где хранится mcp_servers, как привязывать, сравнение моделей доступа (per-team / per-key / allow_all_keys)

---

### [2026-06-26] — Исправление vision (image) для Kimi K2.7 и MiniMax-M3

#### Проблема
Модели Kimi K2.7 и MiniMax-M3 не распознавали картинки через LiteLLM, хотя
работали напрямую из opencode (без прокси). QWEN3.7-plus, Kimi K2.6 и mimo-v2.5
работали корректно.

#### Корневая причина

LiteLLM **не стриппит** image content — это подтверждено хуком на `transform_request`.
Проблема была в отсутствии обязательного HTTP-заголовка `User-Agent` в `litellm_params`.

- **Kimi K2.7** (как и K2.6 в `config.yaml`) требует `User-Agent` через заголовок
  `KIMI_USER_AGENT` для корректной работы vision-эндпоинтов moonshot API.
  В БД запись модели не содержала `headers`.
- **MiniMax-M3** — аналогичная проблема. minimax.io API блокирует запросы без
  браузерного `User-Agent`.

#### Исправление

SQL UPDATE для обеих моделей — добавление `headers.User-Agent` в `litellm_params`:

```sql
-- Kimi K2.7 (model_id: 0ec138c9-a78d-477a-9cc2-1cc9477be77b)
UPDATE "LiteLLM_ProxyModelTable"
SET litellm_params = litellm_params
    || jsonb_build_object('headers', jsonb_build_object(
        'User-Agent', '<KIMI_USER_AGENT из /opt/litellm/.env>'
       ))
WHERE model_id = '0ec138c9-a78d-477a-9cc2-1cc9477be77b';

-- MiniMax-M3 (model_id: 3039366c-ccc9-40cb-b62d-266aad75839e)
UPDATE "LiteLLM_ProxyModelTable"
SET litellm_params = litellm_params
    || jsonb_build_object('headers', jsonb_build_object(
        'User-Agent', '<KIMI_USER_AGENT из /opt/litellm/.env>'
       ))
WHERE model_id = '3039366c-ccc9-40cb-b62d-266aad75839e';
```

После UPDATE: `docker restart litellm && systemctl restart opencode-api`.

**Важно**: НЕ добавлять `api_base` в `litellm_params` — credential `Kimi` уже
содержит правильный endpoint. Переопределение `api_base` ломает auth (401).

#### Ключевые выводы
- `supports_vision: true` в `model_info` — это только метаданные; LiteLLM НЕ
  использует его для фильтрации контента.
- `custom_llm_provider: moonshot` + `litellm_credential_name: Kimi` — достаточно
  для базовой работы, но moonshot требует `User-Agent`.
- Оба зашифрованных поля в `litellm_params` (`custom_llm_provider`, `model`,
  `litellm_credential_name`) хранятся в Fernet-формате через `LITELLM_SALT_KEY`.
  Их нельзя читать/писать через SQL напрямую; только `headers`, `api_base`,
  `max_tokens` и не- чувствительные поля доступны для обновления.

#### Тест
`POST /v1/chat/completions` с image_url (base64 data URI) через LiteLLM → 200 OK
для всех vision-моделей (K2.6, K2.7, M3, QWEN3.7, mimo-v2.5).

---

### [2026-06-30] — Reinstall: восстановление nginx + entrypoint paths + users-btn.js v11→v15

#### Контекст
LiteLLM был переустановлен (видимо новый image `ghcr.io/berriai/litellm:main-stable`).
После переустановки сломалось:
1. Контейнер litellm крашился — PYEOF1/2 в `litellm_entrypoint.sh` искали
   `litellm/proxy/utils.py` по пути `/app/litellm/proxy/`, но в новом image пакет
   переехал в venv: `/app/.venv/lib/python3.13/site-packages/litellm/proxy/`.
2. `start.sh` падал на `chmod +x litellm_entrypoint.sh` — файл принадлежит root,
   dev01 не может chmod; `set -e` убивал скрипт ПОСЛЕ `docker rm -f litellm`,
   оставляя систему без контейнера (502 Bad Gateway).
3. nginx конфиг потерял все `location ^~ /litellm/<endpoint>/` блоки (их было 50+)
   и `SERVER_ROOT_PATH` сбросился с `/litellm` на `/` — UI LiteLLM Dashboard
   рендерил 404.

#### Восстановление entrypoint paths
- `sed` заменил 4 пути в `/opt/litellm/litellm_entrypoint.sh`:
  `/app/litellm/proxy/...` → `/app/.venv/lib/python3.13/site-packages/litellm/proxy/...`
  (строки 11, 39, 53, 83).
- `start.sh`: bind mount `utils_patched.py` путь обновлён + `chmod ... 2>/dev/null
  || true` чтобы не валить скрипт при попытке chmod root-owned файла.
- `SERVER_ROOT_PATH` в `start.sh`: `/` → `/litellm`.

#### Новый nginx конфиг (вместо 50+ потерянных location блоков)
Один catch-all + named location для SPA fallback:
```nginx
location ^~ /litellm/ {
    proxy_pass http://litellm_upstream/;
    include /etc/nginx/snippets/proxy-common.conf;
    proxy_intercept_errors on;
    error_page 404 = @litellm_ui;
    sub_filter '/litellm-asset-prefix/' '/';
    sub_filter '</head>' '<script src="/setup-opencode/users-btn.js?v=13"></script></head>';
    sub_filter_once off;
    proxy_hide_header Content-Security-Policy;
    proxy_hide_header X-Frame-Options;
}
location @litellm_ui { proxy_pass http://litellm_upstream; ... }
```

Trailing slash в `proxy_pass http://litellm_upstream/` автоматически стриппит
`/litellm/` префикс → один блок вместо 50+ перечислений покрывает все 497
endpoint'ов LiteLLM.

#### users-btn.js — v11 → v15 (итерации)

**v11** (до реинсталла): работал, но `[data-testid*="user-status-"]` хардкод
и FAB исчезал при Next.js SPA навигации.

**v13**: универсальный парсер `collectRowCandidates()` — fallback на любую
`<table>` или `[role='table']`, поиск UUID в textContent row'ов. Работает даже
если data-testid переименуют.

**v14**: перформанс:
- `root.querySelectorAll('*')` заменён на узкий `tr, [role='row'], [data-user-id],
  [data-row-key]`.
- `uuidInNode()` → `uuidInSubtree(row, 150)` с cap-bounded walk.
- Scope сужен до `<main>` если есть.
- MutationObserver callback через `requestIdleCallback` (coalesce в один кадр).
- `visibilitychange` → early-return если вкладка скрыта; scan при возврате.
- `history.pushState/replaceState` monkey-patch + `popstate/hashchange` для
  форсированного scan при SPA-навигации.

**v15**: `MutationObserver` отключается на non-Users страницах через
`ensureObserver()`. На `/logs` (виртуализированная таблица с тысячами row'ов
бесконечный скролл) observer вызывал 100% CPU. Теперь:
- `/logs`, `/models-and-endpoints/` и т.п. — `observer.disconnect()`,
  только history hooks тикают.
- `/users` — observer активен, observeит `<main>`.

**Результат**: кнопки появляются быстро, /logs больше не вешает UI. На /users
появился лаг в несколько секунд для first-render (FAB мгновенно, per-row через
~2-3 сек) — приемлемо, фиксить не нужно.

#### КРИТИЧЕСКИЕ gotchas (reinstall checklist)

1. **LiteLLM image** — после `docker pull ghcr.io/berriai/litellm:main-stable`
   внутренний путь `litellm/proxy/` может переехать в venv
   (`/app/.venv/lib/python3.13/site-packages/litellm/proxy/`). Все in-container
   patches в entrypoint + bind mounts в start.sh требуют обновления.

2. **chmod в start.sh** — если entrypoint.sh root-owned, `chmod +x` от dev01
   fail'ит с `Operation not permitted`. Под `set -e` скрипт exit'ит ПОСЛЕ
   `docker rm -f` → контейнер потерян. Всегда добавлять `|| true` к chmod.

3. **nginx location блоки** — после reinstall LiteLLM может потерять все
   специфичные `location ^~ /litellm/<endpoint>/` блоки. Один catch-all с
   `proxy_pass ... /` (trailing slash) делает то же самое проще.

4. **SERVER_ROOT_PATH** — сброс с `/litellm` на `/` ломает LiteLLM UI (Next.js
   SPA-fallback рендерит 404). Должен быть `/litellm` в `start.sh` + `-e
   SERVER_ROOT_PATH=/litellm` в docker run.

5. **sub_filter_once duplicate** — `sub_filter_once off;` в одной location можно
   использовать только ОДИН раз. Несколько filter'ов делят один setting.

#### Деплой workflow (финальный)
```bash
# 1. Локально редактируем users-btn.js
# 2. deploy3.py на /opt/opencode-setup/users-btn.js
python C:\Users\Admin\AppData\Local\Temp\opencode\deploy3.py \
  "C:\Users\Admin\AppData\Local\Temp\opencode\opencode-setup\users-btn.js" \
  /opt/opencode-setup/users-btn.js
# 3. Bump cache-bust в nginx
ssh ... 'echo "7Cr4iW9l8P" | sudo -S -p "" sed -i \
  "s|users-btn.js?v=[0-9]*|users-btn.js?v=N+1|g" \
  /etc/nginx/sites-available/litellm-bifrost && \
  echo "7Cr4iW9l8P" | sudo -S -p "" nginx -s reload'
# 4. Hard reload в браузере (Ctrl+Shift+R)
```

#### Файлы сейчас (state of the world)
- `/opt/litellm/litellm_entrypoint.sh` — venv paths, PYEOF1-4
- `/opt/litellm/start.sh` — venv bind mount, `chmod || true`, `SERVER_ROOT_PATH=/litellm`
- `/opt/litellm/image_rate_limit_hook.py` — 13769 bytes, зарегистрирован через PYEOF4
- `/opt/litellm/user_agent_hook.py` — **11279 bytes (v3)**, зарегистрирован через PYEOF1
  - Включает merge_first chunk logic для Kimi K2.6/K2.7/M2.7
  - Trace logging в `/tmp/hook-trace.log` (env: HOOK_TRACE=1)
  - Backup предыдущих версий: `.bak`, `.bak.before_rc_inject`
- `/opt/opencode-setup/users-btn.js` — 25375 bytes (v16, pagination fix)
- `/etc/nginx/sites-available/litellm-bifrost` — catch-all + SPA fallback + sub_filter
- nginx `?v=16` для cache-bust users-btn.js

---

### [2026-06-30] — Users button pagination UUID mismatch fix

#### Суть
Кнопка 🛠 OpenCode на странице Internal Users корректно отображалась на первой странице,
но при переключении на вторую страницу кнопки указывали на **старые UUID пользователей**
с первой страницы. То есть кнопка расположена рядом с UserB, но ведёт на настройки UserA.

#### Root cause
Next.js при server-side pagination таблицы пользователей **переиспользует `<tr>` элементы**:
DOM остаётся на месте, меняется только содержимое (включая `data-testid="user-status-<UUID>"`).

В `users-btn.js` (v15) был early return в `addRowButton`:
```js
function addRowButton(tr, uid){
  if(tr.querySelector("[data-oc-row-btn]")) return;  // <-- BUG
  ...
}
```

Когда scan() находил тот же row (который теперь принадлежит новому пользователю), он
видел существующую кнопку и early-return'ил — **uid в кнопке оставался от старого пользователя**.

Дополнительно, в `(A)` block `collectRowCandidates` UUID извлекался через `replace("user-status-","")`
без валидации UUID regex — если data-testid имел другой формат (например,
`user-status-filter`), UID был бы мусором.

LiteLLM source code (chunk `0lstohw6r.qs..js`) подтверждает формат data-testid:
```js
"data-testid":`user-status-${e.original.user_id}`
```
Только один `<Tag>` (antd) в Status column имеет этот атрибут. UUID = `e.original.user_id`.

#### Fix (`/opt/opencode-setup/users-btn.js` v16, 25375 bytes)

1. **Validate UUID в `(A)` block**:
```js
var tid = byTestid[i].getAttribute("data-testid") || "";
var uidMatch = tid.match(/^user-status-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i);
if(uidMatch) rows.push({uid: uidMatch[1], row: r});
```

2. **`addRowButton` всегда удаляет старую кнопку перед добавлением новой**:
```js
function addRowButton(tr, uid){
  if(!uid || !UUID_RE.test(uid)) return;
  // CRITICAL: ALWAYS remove existing button first (don't early-return).
  // Next.js reuses <tr> elements when paginating users list: row DOM stays
  // put while data-testid and inner content are swapped to a new user.
  var existing = tr.querySelectorAll("[data-oc-row-btn]");
  for(var e = 0; e < existing.length; e++){
    if(existing[e].parentNode) existing[e].parentNode.removeChild(existing[e]);
  }
  ...
}
```

Backup: `/opt/opencode-setup/users-btn.js.bak.pagination-fix`

#### Deploy
- Размер: 23601 → 25375 bytes (+1774 bytes комментарии)
- nginx cache-bust bumped: `?v=15` → `?v=16` (4 location'а в `/etc/nginx/sites-available/litellm-bifrost`)
- Deploy через Python subprocess (см. `deploy4.py` в `/tmp/opencode/`) — обходит проблемы с CR/LF на Windows

#### Gotcha
- **Никогда** не используй `early return` в `addRowButton` (или подобных функциях
  добавляющих контент в переиспользуемый DOM). Если DOM node уже существует, его
  нужно **обновить** (replace text/attrs), а не оставлять as-is.
- Next.js pagination (`?page=2`) обычно сохраняет `<tr>` элементы и меняет контент,
  включая React `key` prop. Поэтому `data-testid` обновляется, но наш button — нет.
- 17 моделей в БД, image_team_limits = 9 строк seed, image_request_counter = 0
- psycopg2 2.9.12 в container venv

#### MiniMax-M3 reasoning fix (2026-06-30)

**Симптом**: MiniMax-M3 возвращал ответ с markdown-разметкой `**жирным**` в `content`,
но `reasoning_content` отсутствовал. До reinstall работало корректно — reasoning
отображался тонким шрифтом.

**Корневая причина — 2 бага**:

1. `model_info.merge_reasoning_content_in_choices = true` — LiteLLM **склеивал**
   `reasoning_content` в `content`. У других 15 моделей этот флаг стоял в
   `litellm_params.merge_reasoning_content_in_choices = false`, а у MiniMax-M3
   кто-то (возможно при reinstall или ранее) поставил его в `model_info`.
   **Gotcha**: один и тот же параметр существует в двух местах, и LiteLLM
   читает именно из `model_info`.

2. `litellm_params.thinking.type = "disabled"` — MiniMax провайдер вообще
   не возвращал `reasoning_content` независимо от LiteLLM. У MiniMax-M2.7 этого
   флага нет, и она возвращает reasoning нормально.

**Фикс**:
```sql
UPDATE "LiteLLM_ProxyModelTable"
SET
  litellm_params = litellm_params
    || jsonb_build_object('thinking', jsonb_build_object('type', 'enabled')::jsonb),
  model_info = model_info
    || jsonb_build_object('merge_reasoning_content_in_choices', false::boolean)
WHERE model_name = 'MiniMax-M3';
-- + docker restart litellm
```

**Проверка**:
```
message keys: ['content', 'provider_specific_fields', 'reasoning_content', 'role']
reasoning_content present: True, length: 430
usage.completion_tokens_details.reasoning_tokens: 129
```

**Lesson learned для reinstall checklist**: при обновлении LiteLLM image
проверять `model_info.merge_reasoning_content_in_choices` для reasoning-моделей
(MiniMax-M3, MiniMax-M2.7) — он может быть перезаписан дефолтом `true`.

#### Reasoning thin-font fix для 3 моделей (2026-06-30, позднее)

**Симптом**: 4 модели (Kimi K2.6, Kimi K2.7, MiniMax-M2.7, MiniMax-M3)
отображали reasoning жирным шрифтом вместо тонкого. Остальные (GLM-5.x,
QWEN3.7, mimo) работали корректно. До reinstall все 4 работали тонким.

**Анализ streaming форматов** (ключевая находка):

opencode (с провайдером `@ai-sdk/openai-compatible`) рендерит reasoning
**только если** в **первом** assistant chunk присутствует
`reasoning_content` с **непустым значением** (`length > 0`).

Из `@ai-sdk/openai-compatible` (`node-D_aJyxXX.js:230386`):
```js
if (delta.reasoning_content != null && delta.reasoning_content.length > 0) {
  // emit reasoning-start + reasoning-delta events
}
```

Пустой `reasoning_content: ""` ИГНОРИРУЕТСЯ — thinking mode не включается.

Сравнение 1-х chunks моделей после прохождения через LiteLLM:

| Модель | 1-й chunk | Что показывает opencode |
|---|---|---|
| GLM-5.2 | `{"reasoning_content": "1", "role": "assistant"}` | ✅ reasoning тонким |
| mimo-v2.5 | `{"content": "", "role": "assistant"}` + RC chunks | ✅ reasoning тонким |
| QWEN3.7-plus | `{"content": "", "reasoning_content": "", "role": "assistant"}` + RC | ✅ тонким (затем RC > 0) |
| **Kimi K2.6** | `{"role": "assistant"}` (только role, RC потом) | ❌ жирным |
| **Kimi K2.7** | `{"role": "assistant"}` (только role, RC потом) | ❌ жирным |
| **MiniMax-M2.7** | `{"role": "assistant"}` + пустые `{}` chunks (reasoning съеден LiteLLM) | ❌ жирным |
| **MiniMax-M3** | `{"reasoning_content": "The...", "role": "assistant"}` (после custom_openai fix) | ✅ тонким |

Прямой апстрим MiniMax (`https://api.minimax.io/v1`):
- M2.7: `{"content": "<think>...", "role": "assistant"}` — reasoning **только** в `` тегах content
- M3: `{"content": "<think>...", "reasoning": "...", "role": "assistant"}` — отдельное `reasoning` поле

LiteLLM `minimax` handler парсит `<think>` теги и **теряет их** для streaming.
LiteLLM `custom_openai` handler — transparent pass-through.

**Решения (по моделям)**:

1. **MiniMax-M3** — переключить `custom_llm_provider` с `minimax` на
   `custom_openai` (transparent pass-through). Тогда upstream `reasoning` field
   корректно маппится в `reasoning_content` и попадает в первый chunk.

2. **Kimi K2.6 / K2.7 / MiniMax-M2.7** — расширить `user_agent_hook.py`:
   для моделей в `_MERGE_FIRST_CHUNK_MODELS` **буферизировать** первый chunk
   если он содержит только `role: "assistant"` без непустого `reasoning_content`,
   и слить его со следующим chunk'ом (добавить `role` к чанку с RC). Это
   эмулирует формат GLM/mimo, где `role` + RC приходят вместе в первом chunk'е.

3. **MiniMax-M2.7** — **НЕ ИСПРАВЛЕНО** (known issue). Upstream отдаёт
   reasoning только через `<think>...</think>` в content (без separate
   reasoning field). LiteLLM streaming handlers (как `minimax`, так и
   `custom_openai`) парсят и **теряют** эти теги до того, как hook
   получает chunks. Workaround: использовать M3 вместо M2.7.

**Изменения**:

- `litellm_params.custom_llm_provider` для MiniMax-M3: `minimax` → `custom_openai`
  (через `encrypt_value_helper`, прямое SQL UPDATE невозможно — поля зашифрованы)
- `litellm_params.thinking.type` для MiniMax-M3: `"enabled"` (включает separate
  reasoning field в upstream ответе)
- `user_agent_hook.py` v3: 
  - `_MERGE_FIRST_CHUNK_MODELS = ("Kimi K2.6", "Kimi K2.7", "MiniMax-M2.7")` tuple
  - Логика buffer+merge первого role-only chunk в `async_post_call_streaming_iterator_hook`
  - Trace logging в `/tmp/hook-trace.log` для диагностики (включается через `HOOK_TRACE=1`)
  - Размер: 7428 → 11279 bytes
- Backup старого hook'а: `/opt/litellm/user_agent_hook.py.bak.before_rc_inject`

**Python скрипт для смены провайдера** (`/opt/litellm/encrypt_value_helper`
через `docker exec litellm python3 ...`):
```python
import os, json, psycopg2
from litellm.proxy.common_utils.encrypt_decrypt_utils import encrypt_value_helper, decrypt_value_helper

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute('SELECT litellm_params FROM "LiteLLM_ProxyModelTable" WHERE model_name = %s', ("MiniMax-M3",))
lp = dict(cur.fetchone()[0])
lp["custom_llm_provider"] = encrypt_value_helper("custom_openai")
lp["thinking"] = {"type": "enabled"}
cur.execute('UPDATE "LiteLLM_ProxyModelTable" SET litellm_params = %s WHERE model_name = %s', (json.dumps(lp), "MiniMax-M3"))
conn.commit()
```

**Проверка streaming** после всех фиксов:

```
Kimi K2.7 1-й chunk:  {"reasoning_content": "We", "role": "assistant"}  ← merged
Kimi K2.6 1-й chunk:  {"reasoning_content": "...", "role": "assistant"} ← merged
MiniMax-M3 1-й chunk: {"reasoning_content": "The user...", "role": "assistant"}  ← native
MiniMax-M2.7 1-й chunk: пустые chunks до ответа (LiteLLM bug, known issue)
```

Trace log пример (Kimi K2.7):
```json
{"event": "stream-start", "model": "Kimi K2.7", "merge_first": true}
{"idx": 0, "kind": "buffered_first", "role": "assistant"}
{"idx": 1, "kind": "merged", "reasoning_content": "We"}
{"idx": 0, "kind": "yielded", "reasoning_content": "We"}
{"idx": 1, "kind": "yielded", "reasoning_content": " need"}
...
```

**Lesson learned**:
1. opencode детектит thinking mode по **непустому** `reasoning_content` в
   первом chunk (`length > 0`). Инъекция пустого RC бесполезна.
2. LiteLLM `minimax` handler ломает streaming reasoning для моделей без
   separate `reasoning` field (M2.7). Решение — `custom_openai` pass-through
   + `thinking.type=enabled`.
3. При обновлении LiteLLM image нужно проверять `litellm_params.thinking`
   для MiniMax reasoning-моделей (дефолт может быть `disabled`).

**Source code reference**:
- opencode AI SDK парсер: `C:\Users\Admin\AppData\Roaming\npm\node_modules\opencode-ai\bin\opencode`
  → распакованный `app.asar` → `out/main/chunks/node-D_aJyxXX.js:230386`
- LiteLLM encryption: `litellm.proxy.common_utils.encrypt_decrypt_utils`
  (`encrypt_value_helper`/`decrypt_value_helper`, signing key = LITELLM_SALT_KEY env)

---

## VM / ssh / доступы

- **VM**: `162.55.137.149:1995`, user `dev01`, password `7Cr4iW9l8P`
- **Master key**: `sk-litellm-placeholder-replace-before-prod`
- **Docker port**: `4001:4000` (host:container)
- **PostgreSQL**: `docker exec litellm-pg psql -U litellm`, table `LiteLLM_ProxyModelTable`
- **LiteLLM UI**: admin/admin (UI_USERNAME/UI_PASSWORD env vars)
- **runsudo.py**: `python C:\Users\Admin\AppData\Local\Temp\opencode\runsudo.py <base64-encoded-shell>`
- **deploy3.py**: `python C:\Users\Admin\AppData\Local\Temp\opencode\deploy3.py <local> <remote>`

---

### [2026-06-30] — Invite link 404 fix

#### Суть
Admin создаёт нового пользователя в LiteLLM UI (Users → Invite), получает invite ссылку
вида `https://hcbifrost.herocraft.com/ui/?invitation_id=<uuid>`. После клика пользователь
видел nginx 404 page.

#### Root cause
LiteLLM 1.90.1 Next.js UI генерирует invite URL **относительно origin** (без префикса `/litellm/`):
- URL для пользователя: `${origin}/ui/?invitation_id=<id>` (НЕ `${origin}/litellm/ui/?invitation_id=<id>`)
- API вызовы из SPA: `fetch('/invitation/new', ...)`, `fetch('/invitation/info?invitation_id=...')`,
  `fetch('/invitation/update', ...)`, `fetch('/onboarding/get_token?invite_link=...')`,
  `fetch('/onboarding/claim_token', ...)`

Nginx primary server имел только:
- `location = /` → 302 → `/litellm/ui/`
- `location ^~ /litellm/` → proxy_pass с strip-prefix
- `location ^~ /setup-opencode` → static

Никаких правил для `/ui/`, `/invitation/*`, `/onboarding/*` (без префикса) → nginx возвращал 404.

Дополнительный нюанс: LiteLLM FastAPI обслуживает UI под `SERVER_ROOT_PATH=/litellm`.
То есть:
- `GET /ui/?invitation_id=...` → **404** (FastAPI StaticFiles не находит без root_path)
- `GET /litellm/ui/?invitation_id=...` → **200** (правильный путь через FastAPI root_path)

В существующем `location ^~ /litellm/` работал fallback `@litellm_ui` (named location),
который делает `proxy_pass http://litellm_upstream;` без strip-prefix — то есть upstream
получает полный путь `/litellm/ui/...` → 200. Это объясняет почему
`https://hcbifrost.herocraft.com/litellm/ui/...` всегда работал.

#### Fix (в `/etc/nginx/sites-available/litellm-bifrost`, primary server)

```nginx
# ---- LiteLLM UI SPA at /ui/ (without /litellm/ prefix) ----
# UI generates invite URLs as ${origin}/ui/?invitation_id=<id>
# /litellm/ prefix in proxy_pass is REQUIRED: LiteLLM serves UI under
# SERVER_ROOT_PATH=/litellm, so /ui/ returns 404 but /litellm/ui/ returns 200.
location ^~ /ui/ {
    proxy_pass http://litellm_upstream/litellm/ui/;
    include /etc/nginx/snippets/proxy-common.conf;
    proxy_hide_header Content-Security-Policy;
    proxy_hide_header X-Frame-Options;
    sub_filter '</head>' '<script src="/setup-opencode/users-btn.js?v=15"></script></head>';
    sub_filter_once off;
}

# ---- LiteLLM invite/onboarding routes (no /litellm/ prefix) ----
# UI calls /invitation/* + /onboarding/* APIs relatively.
location = /onboarding {
    # Legacy invite flow (now superseded by /ui/?invitation_id=... but kept as safety net)
    proxy_pass http://litellm_upstream/litellm/ui/onboarding/$is_args$args;
    include /etc/nginx/snippets/proxy-common.conf;
    proxy_hide_header Content-Security-Policy;
    proxy_hide_header X-Frame-Options;
    sub_filter '</head>' '<script src="/setup-opencode/users-btn.js?v=15"></script></head>';
    sub_filter_once off;
}
location ~ ^/(invitation|onboarding)/ {
    # API endpoints: /invitation/{new,info,update} + /onboarding/{get_token,claim_token}
    proxy_pass http://litellm_upstream;
    include /etc/nginx/snippets/proxy-common.conf;
    proxy_hide_header Content-Security-Policy;
    proxy_hide_header X-Frame-Options;
}
```

Backup: `/etc/nginx/sites-available/litellm-bifrost.bak.invite-fix`

#### End-to-end test (2026-06-30)
- Real invite `0eb7b373-6ab5-40d6-9363-61f779270be3` (user_id=72f7c9e2-...)
- `GET /ui/?invitation_id=0eb7b373-...` → **HTTP 200**, size=20591, `<title>LiteLLM Dashboard</title>`, users-btn injected ✓
- `GET /invitation/info?invitation_id=0eb7b373-...` → **HTTP 200**,
  `{id, user_id, is_accepted: False, expires_at: 2026-07-07}` ✓

#### Gotchas (для reinstall checklist)
- LiteLLM FK constraint на `LiteLLM_InvitationLink`:
  `created_by` и `updated_by` тоже FK на `LiteLLM_UserTable(user_id)`. Создание invite через
  **master_key** возвращает 400 "User id does not exist" потому что
  `created_by = litellm_proxy_admin_name` (default value), а это не реальный user_id в БД.
  Решение: использовать **virtual sk-... key** реального proxy_admin пользователя.
- `proxy_intercept_errors on; error_page 404 = @litellm_ui;` НЕ должен использоваться
  для `/onboarding` location — иначе invite_link validation errors (401 от upstream)
  ловятся и возвращаются как Next.js 404 HTML через fallback.
- UI генерирует `/ui/?invitation_id=<id>` (НЕ `/onboarding?invite_link=<id>` как можно было бы
  предположить из proxy_server.py `/onboarding/get_token` кода).

#### Files
- `/etc/nginx/sites-available/litellm-bifrost` — добавлены locations `^~ /ui/`, `= /onboarding`, regex для `invitation|onboarding`
- `.bak.invite-fix` — backup до изменений