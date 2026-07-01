# LiteLLM — обновление моделей (ctx/out/vision/video)

## TL;DR

`POST /model/update` **не персистит** поля `max_input_tokens` / `max_tokens` /
`supports_vision` / `supports_video_input` в `model_info` для моделей, созданных
через UI/API. Визуально ответ — success, но в `/model/info` поле пустое, в DB
`LiteLLM_ProxyModelTable.model_info` — без изменений. Правильный путь — SQL
UPDATE в Postgres + перезапуск контейнера + перезапуск `opencode-api`.

## Проверенная проблема (2026-06-24)

Добавили через UI 3 модели: `GLM-5.2 (res)`, `GLM-5.1 (res)`, `GLM-4.7 (res)`.
Все три — `db_model: true`, но `model_info` пустой. Вызовы `/model/update` с
`{model_name, litellm_params, model_info: {max_input_tokens, max_tokens}}`
отвечали `model_id: <uuid>`, значения в DB не записывались.

Без `litellm_params` (только `model_name` + `model_info`) — ответ
`Authentication Error, model not found`.

С `/model/new` — создаётся **дубликат** с другим UUID, а старый остаётся.
Удаление старого через `/model/delete` по id работает, но это костыль.

## Правильный workflow

### 1. SQL UPDATE (единственный надёжный путь для `model_info`)

```sql
-- ctx/out
UPDATE "LiteLLM_ProxyModelTable"
SET model_info = model_info || jsonb_build_object(
  'max_input_tokens', 204800,
  'max_tokens', 131072
),
updated_at = NOW()
WHERE model_name = 'GLM-4.7 (res)';

-- vision
UPDATE "LiteLLM_ProxyModelTable"
SET model_info = model_info || jsonb_build_object(
  'supports_vision', true,
  'supports_video_input', true
),
updated_at = NOW()
WHERE model_name = 'QWEN3.7-plus';
```

`jsonb_build_object(...)` мержится с существующим `model_info` — остальные
поля (`id`, `access_via_team_ids`, `direct_access`, `blocked` и т.д.) не
затираются. **Не использовать `model_info = ...` целиком** — потеряешь id и
permissions.

Подключение к DB:
```bash
docker exec litellm-pg psql -U litellm -d litellm -c "SELECT ... FROM \"LiteLLM_ProxyModelTable\""
```

### 2. Перезапуск контейнера (in-memory кэш)

LiteLLM при старте читает модели в in-memory dict. SQL-апдейт **не**
триггерит reload — нужен рестарт:

```bash
docker restart litellm
# дождаться readiness
for i in 1 2 3 4 5 6 7 8 9 10; do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:4001/health/readiness)
  [ "$code" = "200" ] && { echo READY; break; }
  sleep 3
done
```

### 3. Перезапуск `opencode-api` (свой кэш)

`/opt/opencode-setup/api.py` кэширует `_MODEL_INFO` глобально на старте
(строка 36-37 в `api.py`). Без рестарта `/setup-opencode/api/models` отдаёт
**старые** значения, даже если LiteLLM уже обновился:

```bash
systemctl restart opencode-api
```

### 4. Верификация

```bash
curl -sk "https://hcbifrost.herocraft.com/litellm/model/info" \
  -H "Authorization: Bearer $MASTER_KEY" | \
  python -c "import json,sys; d=json.load(sys.stdin); \
  [print(m['model_name'], m.get('model_info',{}).get('max_input_tokens'), m.get('model_info',{}).get('max_tokens')) \
  for m in d['data'] if 'GLM' in m.get('model_name','')]"
```

Должны появиться `max_input_tokens` и `max_tokens`. После этого при следующей
генерации `/setup-opencode/?user=<uuid>` `index.html` подхватит новые значения
и проставит `limit: { context, output }` в `opencode.json`.

## Когда `model_info` пустой (частый случай)

Признаки:
- `GET /model/info` → `model_info: { id, db_model, blocked, access_via_team_ids, direct_access }` (без ctx/out)
- В БД `model_info` тоже без `max_input_tokens` / `max_tokens`

Причина: модель создана через UI или `/model/new` без указания `model_info`.
Поле `model_info` в `LiteLLM_ProxyModelTable` остаётся `{}` или с минимальным
набором. SQL UPDATE добавляет нужные поля.

## Видео / vision / audio

Те же правила. `api.py` читает:
- `model_info.supports_vision` → `vision: true` в API → `modalities.input` += `"image"`
- `model_info.supports_video_input` → `video: true` → `modalities.input` += `"video"`
- `model_info.supports_audio_input` → `audio: true` → `modalities.input` += `"audio"`

`index.html` (строки 83-86):
```js
if (m.vision) inputMods.push("image");
if (m.video)  inputMods.push("video");
if (m.audio)  inputMods.push("audio");
```

## Что **не** трогать в `litellm_params` через SQL

`api_base`, `api_key`, `litellm_credential_name` — зашифрованы в DB
(`EncryptedJson`). Прямое обновление этих полей через SQL сломает модель.
Для смены endpoint/credential — только через UI или `/model/update`/`/model/new`
API.

## GLM-5.2 (res) limits fix (2026-07-01)

**Симптом**: `/setup-opencode/?user=<uuid>` для `GLM-5.2 (res)` генерировал
`limit: { context: 0, output: 0 }`. Другие `(res)` модели (`GLM-4.7 (res)`,
`GLM-5.1 (res)`) имели нормальные лимиты.

**Причина**: модель `GLM-5.2 (res)` была добавлена через UI (или
`POST /model/new`) позже остальных, и `model_info.max_input_tokens` /
`max_tokens` не были заполнены — это известное поведение `/model/update`,
которое визуально success но не персистит поля.

**До состояния** в DB:
```sql
SELECT model_info FROM "LiteLLM_ProxyModelTable" WHERE model_name = 'GLM-5.2 (res)';
-- {"id": "ac45db04-...", "db_model": false}  -- без лимитов!
```

**Fix** — точно как в чеклисте ниже:

```sql
UPDATE "LiteLLM_ProxyModelTable"
SET model_info = model_info || jsonb_build_object(
  'max_input_tokens', 1048576,
  'max_tokens', 131072
),
updated_at = NOW()
WHERE model_name = 'GLM-5.2 (res)';
```

Затем:
1. `docker restart litellm` → ждать `/health/readiness` = 200 (~30s)
2. `pkill -9 -f "python3 /opt/opencode-setup/api.py"; nohup python3 ... &`
3. Верификация: `/model/info` показывает `ctx=1048576 out=131072`,
   `/setup-opencode/api/models?user=<uuid>` (Coders team) содержит
   `GLM-5.2 (res): { context: 1048576, output: 131072, reasoning: true }`

**Урок**: после добавления ЛЮБОЙ новой (res) модели через UI нужно
**сразу** проверить `/model/info` и при необходимости сделать SQL UPDATE.
Готовых "fix it" tooling нет — обнаруживается только когда пользователь
видит `context: 0` в сгенерированном opencode.json.

---

## Итого: чеклист для обновления ctx/out/vision/video/audio

1. `docker exec litellm-pg psql ... UPDATE ... jsonb_build_object(...)`
2. `docker restart litellm` + ждать `/health/readiness` = 200
3. `systemctl restart opencode-api`
4. Проверить `/model/info` через API
5. Перегенерить `/setup-opencode/?user=<uuid>` в браузере (Ctrl+Shift+R)
6. **Перезапустить opencode** у пользователя — opencode кэширует `/v1/models` на старте и продолжит показывать старые лимиты до рестарта

## MiniMax-M3 ctx bump (2026-06-27)

`MiniMax-M3` (`model_id 3039366c-ccc9-40cb-b62d-266aad75839e`) → `max_input_tokens` `512000 → 1000000` по запросу пользователя. Output (`max_tokens`) оставлен `131072`. После UPDATE / restart litellm / restart opencode-api значение подтверждено через `/model/info` (`MiniMax-M3 ctx= 1000000 out= 131072`).

## Reasoning thin-font fix (2026-06-30, ФИНАЛЬНАЯ ВЕРСИЯ)

После reinstall LiteLLM 4 модели (Kimi K2.6, Kimi K2.7, MiniMax-M2.7, MiniMax-M3)
отображали reasoning жирным шрифтом вместо тонкого. Остальные работали корректно.

**КРИТИЧЕСКОЕ открытие** (финальное): opencode AI SDK (в `app.asar` →
`out/main/chunks/node-D_aJyxXX.js:230386`) проверяет:

```js
if (delta.reasoning_content != null && delta.reasoning_content.length > 0) {
  // emit reasoning-start + reasoning-delta events
}
```

`.length > 0` — **непустой** `reasoning_content` обязателен в **первом**
assistant chunk для включения thinking mode. Пустая инъекция `""` **бесполезна**.

**Streaming форматы** (после LiteLLM handler'а) и что требуется от первого chunk'а:

| Модель | 1-й chunk | RC len > 0? | Работает? |
|---|---|---|---|
| GLM-5.2 | `{"reasoning_content": "1", "role": "assistant"}` | ✅ "1" | ✅ |
| mimo-v2.5 | `{"content": "", "role": "assistant"}` + RC chunks | (1-й нет RC) | ✅ |
| QWEN3.7-plus | `{"content": "", "reasoning_content": "", "role": "assistant"}` | ❌ "", но потом RC | ✅ |
| **Kimi K2.6/K2.7** | `{"role": "assistant"}` (БЕЗ RC) + RC chunks | ❌ | ❌ до фикса |
| **MiniMax-M3** | `{"reasoning_content": "The...", "role": "assistant"}` | ✅ | ✅ после `custom_openai` |
| **MiniMax-M2.7** | `{"role": "assistant"}` + пустые `{}` chunks | ❌ (LiteLLM bug) | ❌ |

Прямой апстрим MiniMax (`https://api.minimax.io/v1`):
- M2.7: `{"content": "<think>...", "role": "assistant"}` — reasoning **только** в `<think>` content
- M3: `{"content": "<think>...", "reasoning": "...", "role": "assistant"}` — отдельное `reasoning` поле

LiteLLM `minimax` handler парсит `<think>` теги и **теряет их** для streaming.
LiteLLM `custom_openai` handler — transparent pass-through, `reasoning` → `reasoning_content`.

**ФИНАЛЬНЫЕ решения**:

1. **MiniMax-M3**: переключить `custom_llm_provider` с `minimax` на `custom_openai`
   + `thinking.type = "enabled"` в litellm_params. Encrypted SQL UPDATE через
   `encrypt_value_helper`/`decrypt_value_helper` из `litellm.proxy.common_utils.encrypt_decrypt_utils`.

2. **Kimi K2.6/K2.7**: расширить `/opt/litellm/user_agent_hook.py` (v3) — для моделей в
   `_MERGE_FIRST_CHUNK_MODELS = ("Kimi K2.6", "Kimi K2.7", "MiniMax-M2.7")`
   **буферизировать** первый chunk если он только `{"role": "assistant"}` без непустого RC,
   и **слить** его со следующим chunk'ом (добавить `role` к чанку с RC).
   Не инъекция пустого RC (бесполезна), а полноценный merge.

3. **MiniMax-M2.7**: **known issue**, НЕ исправлено. Upstream отдаёт reasoning только через
   `<think>` в content (без separate `reasoning` field). LiteLLM streaming handlers
   парсят и теряют теги до hook'а. Workaround: использовать M3.

**Логика merge_first в hook'е**:

```python
async for item in response:
    if merge_first and chunk_idx == 0 and not first_chunk_buffered:
        if first_chunk is role-only-without-RC:
            first_chunk_buffered = True
            continue  # swallow first chunk, don't yield
    if first_chunk_buffered:
        delta.role = "assistant"
        first_chunk_buffered = False
    yield item
```

Результат: первый отданный чанк выглядит как `{"reasoning_content": "We", "role": "assistant"}` —
идентично GLM/mimo формату.

**Hook v3 (11279 bytes)**:
- `_MERGE_FIRST_CHUNK_MODELS` — список моделей требующих merge
- `_process_chunk()` — вынесенная <think> stripping логика
- `_trace()` — диагностическое логирование в `/tmp/hook-trace.log` (включается через `HOOK_TRACE=1`)
- Backup: `/opt/litellm/user_agent_hook.py.bak.before_rc_inject`

**Зашифрованные поля**: `litellm_params.custom_llm_provider`, `litellm_params.model`,
`litellm_params.litellm_credential_name` — зашифрованы через `LITELLM_SALT_KEY` +
`litellm.proxy.common_utils.encrypt_decrypt_utils.encrypt_value_helper`/`decrypt_value_helper`.
SQL UPDATE напрямую нельзя; нужен Python скрипт в контейнере (`docker exec litellm python3 ...`).

**Hook deploy**: `deploy3.py` (локальный файл `C:\Users\Admin\AppData\Local\Temp\opencode\deploy3.py`)
копирует локальный файл на VM с sudo. Backup: `/opt/litellm/user_agent_hook.py.bak.before_rc_inject`.
После деплоя — `docker restart litellm`. Проверка что hook загрузился: в логах
`docker logs litellm --tail 100 | grep user_agent_hook` должна быть строка
`merge_models=('Kimi K2.6', 'Kimi K2.7', 'MiniMax-M2.7')`.

**Файлы для reinstall** (сохранить при следующей переустановке LiteLLM):
1. `/opt/litellm/user_agent_hook.py` — текущий hook v3 (11279 bytes)
2. `/opt/litellm/image_rate_limit_hook.py`
3. `/opt/litellm/start.sh` — entrypoint script
4. `/opt/litellm/config.yaml` — model config + callback registrations
5. `/etc/nginx/sites-available/litellm-bifrost` — nginx config с `/onboarding` и `/invitation/*` location'ами
6. `/opt/opencode-setup/users-btn.js` (v15) — UI injection
7. `/opt/opencode-setup/api.py` — setup-opencode API

---

## [2026-06-30] — Invite link 404 fix (nginx routing)

**Проблема**: admin создаёт invite в LiteLLM UI → URL `https://hcbifrost.herocraft.com/ui/?invitation_id=<uuid>` ведёт на 404.

**Root cause**:
1. **LiteLLM UI генерирует invite URL относительно origin** (без `/litellm/` префикса):
   `${origin}/ui/?invitation_id=<id>` — это реальный формат, NOT `/onboarding?invite_link=...`
2. **SPA fetch вызовы тоже относительные**: `fetch('/invitation/info?invitation_id=...')`, `fetch('/invitation/update')`, `fetch('/onboarding/get_token?invite_link=...')`, `fetch('/onboarding/claim_token')`.
3. **Nginx primary server не имел роутов для `/ui/`, `/invitation/*`, `/onboarding/*`** (без префикса) → 404.

Дополнительный нюанс: LiteLLM FastAPI обслуживает UI под `SERVER_ROOT_PATH=/litellm`:
- `GET /ui/?invitation_id=...` → **404** (FastAPI без root_path)
- `GET /litellm/ui/?invitation_id=...` → **200** (с root_path)
- Существующий `location ^~ /litellm/` работал через fallback `@litellm_ui` (named location), который re-send full path

**Fix в `/etc/nginx/sites-available/litellm-bifrost`** (primary server, listen 80+8080):
```nginx
location ^~ /ui/ {
    # Главное правило — обрабатывает /ui/?invitation_id=<id>
    proxy_pass http://litellm_upstream/litellm/ui/;
    # ^-- /litellm/ prefix обязателен (FastAPI SERVER_ROOT_PATH)
    include /etc/nginx/snippets/proxy-common.conf;
    proxy_hide_header Content-Security-Policy;
    proxy_hide_header X-Frame-Options;
    sub_filter '</head>' '<script src="/setup-opencode/users-btn.js?v=15"></script></head>';
    sub_filter_once off;
}
location = /onboarding {
    # Legacy invite flow (safety net)
    proxy_pass http://litellm_upstream/litellm/ui/onboarding/$is_args$args;
    include /etc/nginx/snippets/proxy-common.conf;
    proxy_hide_header Content-Security-Policy;
    proxy_hide_header X-Frame-Options;
    sub_filter '</head>' '<script src="/setup-opencode/users-btn.js?v=15"></script></head>';
    sub_filter_once off;
}
location ~ ^/(invitation|onboarding)/ {
    # API: /invitation/{new,info,update} + /onboarding/{get_token,claim_token}
    proxy_pass http://litellm_upstream;
    include /etc/nginx/snippets/proxy-common.conf;
    proxy_hide_header Content-Security-Policy;
    proxy_hide_header X-Frame-Options;
}
```
Backup: `.bak.invite-fix`.

**E2E test (real invite)**: invite `0eb7b373-6ab5-40d6-9363-61f779270be3` (user_id=72f7c9e2-..., expires 2026-07-07):
- `GET /ui/?invitation_id=0eb7b373-...` → 200 + LiteLLM Dashboard HTML + users-btn injected
- `GET /invitation/info?invitation_id=0eb7b373-...` → 200 + `{id, user_id, is_accepted: False, expires_at}`

**Gotcha**: создание invite через **master_key** возвращает 400 "User id does not exist", потому что LiteLLM FK constraint на `LiteLLM_InvitationLink.created_by/updated_by` → `LiteLLM_UserTable(user_id)`, а `user_api_key_dict.user_id` для master_key = `litellm_proxy_admin_name` (default), не реальный user_id. Решение: использовать virtual `sk-...` key реального proxy_admin пользователя.

**UI URL format**: `/ui/?invitation_id=<id>` (NOT `/onboarding?invite_link=<id>`). API uses different param name `invite_link` for `/onboarding/get_token`.
5. DB: litellm_params для 4 reasoning-моделей (provider switch + thinking.enabled для M3)
