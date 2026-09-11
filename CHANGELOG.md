# Bifrost LiteLLM — Changelog

Все значимые изменения в конфигурации/прокси/UI проксирования Bifrost LiteLLM.

---

### [2026-09-11a] — Kimi Code Platform: новые модели K3-256K + K2.8, K2.6 снята

Подключена Kimi Code Platform (`https://api.kimi.com/coding/v1`) как
отдельный credential в LiteLLM. Две новые модели, удалена одна устаревшая,
добавлена поддержка `reasoning_effort` в opencode для всех K3.

#### Credential
- **`KimiCode`** в `LiteLLM_CredentialsTable`:
  - `credential_info.custom_llm_provider`: `OPENAI`
  - `credential_values.api_key`: `<new-kimi-code-key>` (в
    serena memory `infra/kimi-code-credentials.md`)
  - `credential_values.api_base`: `https://api.kimi.com/coding/v1`
- Старый/невалидный ключ (`...VuV5z`) был только в памяти и нигде не
  использовался — заменён валидным (`...Lrj0`).
- Бэкапов не было (новый credential, ничего не теряли).

#### Модели
- **`Kimi K3-256K`** (`model_id=2f25c2b8-...`):
  - upstream: `k3-256k`; `litellm_params.custom_llm_provider=openai`;
    credential `KimiCode`; `api_base=https://api.kimi.com/coding/v1`
  - `model_info`: ctx 262144 / out 32768
  - поддержка `reasoning_effort ∈ {low, high, max}`
  - **Команды**: добавлена в `Agents` и `All Access` (по запросу юзера)
  - upstream реально возвращает reasoning_content с первого chunk'а даже
    при low-effort, так что merge-first-chunk хук не сработал (не нужен)
- **`Kimi K2.8`** (`model_id=cc05634d-...`) — upstream `kimi-for-coding`
  (= Kimi K2.7 Code под новым брендом в Kimi Code Platform):
  - те же litellm_params как у K3-256K (openai + KimiCode)
  - `model_info`: ctx 262144 / out 32768
  - Thinking:ON всегда (effort level не управляется по докам Kimi Code)
  - **Команды**: добавлена во все 12 команд (Agents, All Access, Analytics,
    Art, Coders, CreativeTeam, GameDesigners, General, PirateShips,
    Porters, SideCoders, StarTroopers) — по запросу «добавить всем командам»
- **`Kimi K2.6`** (`model_id=9a84d2e1-...`) — **снята** по запросу юзера
  («перестала поддерживаться»):
  - `POST /model/delete` model_id=`9a84d2e1-9593-456e-897e-e158b75640f2`
  - убрана из всех 11 команд, где присутствовала (General не имел K2.6)
  - удалена из `LiteLLM_ProxyModelTable` и из opencode.json

#### `model_cost` (config.yaml `litellm_settings.model_cost`)
Dual-ключи с `litellm_provider: custom_openai` (gotcha spend=0):
| ключ                                 | input/token | output/token | источник            |
|--------------------------------------|-------------|--------------|---------------------|
| `k3-256k` + `custom_openai/k3-256k`  | `1.5e-6`    | `7.5e-6`     | OpenRouter × 0.5 (per docs K3 quota halved) |
| `kimi-for-coding` + `custom_openai/kimi-for-coding` | `6.6e-7` | `3.4e-6` | OpenRouter `kimi-k2.7-code` ($0.66/$3.40) |
| `Kimi K3-256K` + `Kimi K2.8`         | соотв. public names | соотв. | shorthand записи |
См. skill `litellm-add-model` (п.7 model_cost dual keys — иначе spend=0).

#### `user_agent_hook.py`
Hook **уже обрабатывает** script-UA → `OPENCODE_UA` (для Kimi Code
Platform это критично — Kimi Code режет все запросы кроме opencode-UA).
Ничего нового дописывать не пришлось, только:
- `_MERGE_FIRST_CHUNK_MODELS += {"Kimi K3-256K", "Kimi K2.8"}` — на
  всякий случай, для симметрии с K2.6/K2.7/K3 (если upstream вдруг
  пришлёт role-only первый chunk)
- `_KIMI_EMPTY_MSG_MODELS += {"Kimi K3-256K", "Kimi K2.8"}` — на
  случай если Kimi начнёт 400-ить (тот же gotcha как для Moonshot)
- Бэкап `user_agent_hook.py.bak.kimi-code-<TS>`
- `_decide_ua()` без изменений — текущая логика script-UA→OPENCODE_UA
  корректно обрабатывает curl/powershell/python/etc. приходящие к
  прокси (smoke 200 подтвердил)

#### `api.py` (opencode-setup `_REASONING_CAPABLE`)
- Добавлены `"Kimi K3-256K"` и `"Kimi K2.8"`. Бэкап
  `api.py.bak.kimi-code-<TS>`.

#### Restart
- `docker restart litellm` (для `model_cost` и пере-инжекта hook'а)
- `systemctl restart opencode-api` (для `_REASONING_CAPABLE`)

#### Smoke (temp-key All Access `tk_kimi_code_20260911`, удалён)
| # | модель             | param              | код | ответ              | spend |
|---|--------------------|--------------------|-----|--------------------|-------|
| 1 | `Kimi K3-256K`     | `reasoning_effort=low` | 200 | `pong` + 30 reasoning tokens | $0.00035250 |
| 2 | `Kimi K3-256K`     | `reasoning_effort=high`| 200 | reasoning 46 tokens, content=""\*  | $0.00037500 |
| 3 | `Kimi K3-256K`     | `reasoning_effort=max` | 200 | `pong` + 20 reasoning tokens | $0.00027750 |
| 4 | `Kimi K2.8`        | без effort         | 200 | `pong` + 13 reasoning | $0.00016272 |
| 5 | `Kimi K2.8`        | `reasoning_effort=max` | 200 | `Pong` + 12 reasoning (игнорирует, как и ожидалось от kimi-for-coding) | $0.00009860 |
| 6 | `Kimi K3-256K`     | streaming           | 200 | первый chunk `"P"` в `reasoning_content` | n/a   |

`\* первый chunk streaming вернул reasoning `P`→`ong`→`.` → потом
`content` `p` → норма; Test 2: первый chunk оказался reasoning-only,
а content пустой до лимита `max_tokens=50` — поведение Kimi Code при
medium-effort на короткий prompt.

Spend-инвариант доказан: 6 строк в LiteLLM_SpendLogs, суммарный spend
~$0.001266; `custom_openai/k3-256k` и `kimi-for-coding` оба попадают
под свои dual-ключи (нет классического spend=0-эффекта).

#### Cleanup tmp-ключа
- `POST /key/delete` aliases=`["tmp-kimi-code-20260911"]` → 200; verify
  count=0 в `LiteLLM_VerificationToken`. Чисто, никаких остатков tmp-* в БД.

#### Команда-current state (post-change)
| Модель           | команд | примечание                 |
|------------------|-------|----------------------------|
| `Kimi K2.7`      | 11    | без изменений              |
| `Kimi K3`        | 9     | без изменений (по докам там тоже есть reasoning_effort — оставил, см. opencode.json ниже) |
| `Kimi K3-256K`   | **2** | Agents, All Access (новое) |
| `Kimi K2.8`      | **12**| все команды (новое)        |
| `Kimi K2.6`      | **0** | снята                      |

#### opencode.json (global, наш провайдер `bifrost-litellm`)
- `Kimi K3`: добавлен `options.thinking.allowedEfforts: ["low","high","max"]`
- `Kimi K3-256K`: новый блок с `allowedEfforts: ["low","high","max"]`,
  `limit.context=262144, limit.output=32768`, `attachment=false`,
  `modalities.input=["text"]`
- `Kimi K2.8`: новый блок с `options.thinking.type="enabled"`, `limit` как
  у K3-256K, `attachment=false`, `modalities.input=["text"]`
- `Kimi K2.6` блок удалён (модель больше не существует)

#### Memory
- `infra/kimi-code-credentials.md` обновлён: старый ключ (`...VuV5z`)
  помечен как «failed 401», новый (`...Lrj0`) записан.
- `infra/hcbifrost-vm-litellm.md` — секция не трогалась (лимит по объёму).

#### Backups на VM
- `/opt/litellm/config.yaml.bak.kimi-code-<TS>`
- `/opt/litellm/user_agent_hook.py.bak.kimi-code-<TS>`
- `/opt/opencode-setup/api.py.bak.kimi-code-<TS>`
- `/tmp/pre_kimi_build_pg.sql` (pg_dump LiteLLM_ProxyModelTable+TeamTable)
- `/tmp/teams_pre_grants_safe_<TS>.sql` (только TeamTable, pre-grants)

---

### [2026-09-04b] — Тест-драйв скиллов opencode (все 4 OK)

Смоук-тест каждого скилла в безопасном режиме (без мутаций):

- **vm-ssh**: askpass + base64-транспорт — коннект, `docker ps`,
  readiness 200, `opencode-api` active ✅
- **litellm-diagnose**: аудит спендов 30д (топ: qwen3.7-plus $265.35,
  kimi-k3 $124.98, GLM-5.3 $77.44); все 6 tencent-upstream'ов
  (hy3/hy4-preview/deepseek×2/glm×2) со spend>0 за 7д — dual model_cost
  ключи работают ✅
- **litellm-cleanup** (dry-run): tmp-ключей 0; в `/tmp` только живой
  `payload_logs_retention.log`; `admin/` 11 файлов, `\r`-дублей нет ✅
- **litellm-add-model** (preflight, readonly): 67 моделей, 12 команд,
  model_cost пары `hy3`/`custom_openai/hy3`, `hy4-preview`/
  `custom_openai/hy4-preview` на месте с верными ценами ✅

Патч по итогам: `payload_logs_retention.log` (живой crontab-лог ретеншна)
добавлен в never-delete список cleanup-скилла. Грабля «ssh рвёт
многострочный вывод» подтвердилась 3/4 вызовов — дробление шагов
(vm-ssh §3) решает.

---

### [2026-09-04a] — CHANGELOG: санитизация секретов + пуш в публичный форк

Первый пуш форка `pavelherocraft/bifrost` (ветка `hc-main`) после
накопления незакоммиченных записей: `974fe7a2..a02f2af5`
(963419db — hygiene, a02f2af5 — CHANGELOG catch-up +3555 строк).

- Санитизировано **12 секретов** → читаемые плейсхолдеры:
  `<tencent-tokenhub-key>`, `<glm-key-A>`, `<glm-key-B>`, `<salt-key>`,
  `<master-key-current>`, `<master-key-prev>`, `<master-key-leaked>`,
  `<ui-password>`, `<sudo-pass>`, `<openai-image-key>`,
  `<google-ai-studio-key>`
- **GitHub push protection** поймал 2 секрета, пропущенных ручным
  сканом по известным паттернам (OpenAI `sk-proj-…` для gpt-image и
  Google AI Studio `AQ.…` для nanobanana) — пуш отклонён, ключи
  редактированы, коммит amended (локальный, не пушеный — можно)
- Полные значения ВСЕХ ключей сохранены в serena memory
  (`infra/hcbifrost-vm-litellm`, `infra/image-models-image-edit-support`)
- Урок: перед пушем сканировать и по форматам провайдеров
  (`sk-proj-`, `AQ\.`, `AIza`, `ghp_`, `xox`, `AKIA`…), не только по
  своим известным строкам
- Нюанс: `<sudo-pass>` остаётся в истории старых коммитов (был запущен
  до санитизации) — из HEAD-дерева убран; переписывание истории не делаем

---

### [2026-09-03b] — Скиллы opencode для LiteLLM-сервера + repo hygiene

Повторяющиеся операции оформлены как project-local скиллы
`.opencode/skill/<name>/SKILL.md` (формат opencode, gitignored):
*(исправление 2026-09-04: правильный путь `.opencode/skills/` —
множественное число, см. доку opencode; каталог переименован, скиллы
подгружаются через нативный `skill` tool после рестарта сессии)*

- **vm-ssh** — транспорт: askpass, base64-паттерн для всех скриптов,
  безопасная запись файлов на VM (без `\r`-инцидентов, верификация
  `repr` listdir), docker logs через LogPath, sudo, psql, рецепты
- **litellm-add-model** — полный чеклист 11 шагов: preflight upstream,
  цены OpenRouter, бэкап TeamTable, `/model/new`, гранты командам,
  **model_cost двумя ключами** (gotcha spend=0), reasoning-флаг,
  рестарты, smoke+spend, чистка, changelog
- **litellm-cleanup** — tmp-ключи (`/key/delete` c `key_aliases`),
  файлы `/tmp` (только dev01-owned), never-delete список
- **litellm-diagnose** — лестница spend=0, 403 (запятые в публичных
  именах), UI-логины (`team_id='litellm-dashboard'`), сброс пароля

Секреты в скиллах НЕТ — каждый начинается с «прочитай serena memory
`infra/hcbifrost-vm-litellm`». Fallback при неподхвате opencode:
`C:\Users\Admin\.config\opencode\skills\` (глобальная папка).

Repo hygiene (коммит 963419db): `.gitignore` += `.serena/` +
`.opencode/skill/`; `.serena/` untracked (`git rm -r --cached`) —
memory-файлы с реальными секретами больше не в репозитории.

---

### [2026-08-25b] — Whitelist: +91.108.1.0 (третий egress прокси)

После верификации [2026-08-25] пользователем (key/generate из UI — работает)
решено добавить и третий egress `91.108.1.0` (Telegram range) — прокси
ротирует адреса и без него 403 возвращался бы при выпадении этого egress.

- 12 whitelist-блоков: +`allow 91.108.1.0;` (whitelist = 7 IP × 12)
- `sites-available` синхронизирован; backup
  `sites-available/litellm-bifrost.bak_20260825-110158` (сразу в available,
  не в enabled — см. грабли [2026-08-25])
- README: egress #3 в таблице, note обновлён
- Проверка: `nginx -t` ok, reload, `grep allow 91.108.1.0` = 12, smoke
  `GET /litellm/key/generate` = 405, `/health/readiness` = 200

Security-контекст: диапазон shared (Telegram Messenger Inc.) — whitelist
остаётся только сетевым гейтом, auth LiteLLM (login/JWT) по-прежнему обязателен.

Rollback: `sudo cp /etc/nginx/sites-available/litellm-bifrost.bak_20260825-110158 /etc/nginx/sites-enabled/litellm-bifrost && sudo nginx -s reload`

---

### [2026-08-25d] — atlas/deepseek-v4-pro-0813 (Atlas, для Agents, text-only reasoning)

#### Контекст
Дополнительный параллельный путь к `deepseek-v4-pro-0813` через Atlas Cloud —
специально для команды `Agents`. Зеркалит ту же модель (`-0813`, 1M ctx,
384K output, text-only reasoning), что и `AlibabaTokenPlan/deepseek-v4-pro-0813`,
но через другой провайдер (atlascloud вместо aliyun_token_plan).

Atlas выбран потому что:
- Команда `Agents` ранее использовала `atlas/deepseek-v4-flash-0731` и
  `deepseek-v4-pro` (старая) на Atlas — продолжение той же провайдерской ветки
- Параллельные провайдеры дают отказоустойчивость: если Token Plan упрётся
  в rate limit, Atlas-вариант остаётся

#### Что сделано

**Создана модель `atlas/deepseek-v4-pro-0813`** через `POST /model/new`
(хост-curl на `:4001`, master key из `/opt/litellm/.env`).
Конфигурация зеркалит существующую `deepseek-v4-pro` на Atlas + обновлённые
спеки (`-0813` версия):

```json
{
  "model_name": "atlas/deepseek-v4-pro-0813",
  "litellm_params": {
    "model": "deepseek-ai/deepseek-v4-pro-0813",
    "api_base": "https://api.atlascloud.ai/v1",
    "custom_llm_provider": "custom_openai",
    "litellm_credential_name": "atlascloud",
    "max_tokens": 393216,
    "use_xai_oauth": false,
    "use_litellm_proxy": false,
    "use_in_pass_through": false,
    "merge_reasoning_content_in_choices": false
  },
  "model_info": {
    "max_input_tokens": 1048576,
    "max_tokens": 393216,
    "supports_vision": false,
    "supports_video_input": false,
    "supports_audio_input": false
  }
}
```

`model_id = 2e73d10b-7a94-4b10-95d9-693fb7c5043d`. Все поля `model_info`
сохранены корректно (verified через `/model/info`).

**Добавлена в команду Agents** (только). SQL `array_append` с guard:

```sql
UPDATE "LiteLLM_TeamTable"
SET models = array_append(models, 'atlas/deepseek-v4-pro-0813')
WHERE team_alias = 'Agents'
  AND NOT ('atlas/deepseek-v4-pro-0813' = ANY(models));
```

`UPDATE 1`. Только Agents — другие 10 команд уже получают
`AlibabaTokenPlan/deepseek-v4-pro-0813` (см. `[2026-08-25c]`).

**Restart**: `docker restart litellm` (readiness = 200 на 7-й попытке),
`systemctl restart opencode-api` (active).

#### Verification (все ✅)

| Тест | Результат |
|---|---|
| `/v1/models` (master key) — модель присутствует | PRESENT, всего 55 моделей |
| Смоук через explicit virtual key | HTTP 200, `content='ok'`, reasoning_tokens=11 |
| Смоук через team_id=Agents (без explicit models) — VISIBLE + 200 | ✅, reasoning_tokens=10 |
| Временные ключи удалены | `{"deleted_keys":[...]}` |

#### Файлы
- DB: `LiteLLM_ProxyModelTable` — 1 новая строка
- DB: `LiteLLM_TeamTable` — Agents.models (1 UPDATE)

---

---

### [2026-09-03a] — Payload Logs: модалка больше не обрезает сообщения

**Проблема**: `/admin/payload-logs/` при клике на строку лога открывал
модалку, но user-сообщение внутри было обрезано до 8000 символов с
суффиксом `…[truncated]`. Причина — серверный cap в
`/opt/litellm/admin/payload_logs_hook.py` (`PAYLOAD_LOG_MAX_MSG_CHARS`
default=8000, применяется в `_content_to_text` при записи в БД).

**Fix**:
- `payload_logs_hook.py`: default `1_000_000` (раньше 8000). Marker
  теперь `…[truncated at N chars]` (на случай если кто-то пришлёт
  патологически длинный message).
- `payload_logs.html`: `<pre>` в `<dialog>` теперь
  `max-height: 70vh; overflow-y: auto` — длинные сообщения
  скроллятся внутри модалки, а не растягивают её за экран.

Файлы bind-mounted (`/opt/litellm/admin/` → `/app/admin/` в контейнере).
HTML подхватывается на лету, .py требует рестарта litellm.
Подтверждено: litellm лог `payload_logs_hook: ACTIVE v2 (...max_msg_chars=1000000)`
на старте 2026-09-03 17:03:20.

E2E: добавил pavel (`52d4a58d-95c1-4716-9215-91313a6c533d`) в allowlist,
отправил 25K-char сообщение → в `UserRequestLogs` сохранено как 25004 chars
(id 1557), модалка показывает полностью.

Backups: `/tmp/opencode/plh_orig.py`, `/tmp/opencode/pll_orig.html`.

**Существующие** старые логи (id ≤ 1551) останутся truncated — cap
применялся при записи. Чтобы увидеть полные сообщения, юзер должен
отправить новые запросы после деплоя.

---

### [2026-09-02d] — tencent/Hy3 добавлена (только Agents + AllAccess)

- Public name: `tencent/Hy3` (upstream `hy3` stable, НЕ `hy3-preview` —
  preview не существует ни в `/v1` ни в `/plan/v3` для наших ключей).
- **Тот же ключ/base** что и Hy4: `<tencent-tokenhub-key>`,
  `https://tokenhub-intl.tencentcloudmaas.com/v1`. Reasoning.
- model_info: ctx 262144 / out 32768, ic/oc в строке.
- **Команды: ТОЛЬКО `Agents` (23 модели) и `All Access` (46)**.
  Остальные 10 — нет (как и просили). Не входит в token plan.
- Цены (OpenRouter `tencent/hy3`): in $0.0825 / out $0.33 за 1M.
  model_cost с обоими ключами (`'hy3'` + `'custom_openai/hy3'` с
  `litellm_provider: custom_openai`) — gotcha workaround (см. [2026-09-02c]).
  Verified: spend $0.000018 (15p + 50c = 1.24e-6 + 1.65e-5).
- _REASONING_CAPABLE в api.py дополнен.

---

### [2026-09-02c] — tencent/Hy4 добавлена (НЕ token plan)

- Public name: `tencent/Hy4` (upstream `hy4-preview`).
- **Отдельный ключ** (НЕ token plan): base
  `https://tokenhub-intl.tencentcloudmaas.com/v1`, key
  `<tencent-tokenhub-key>`.
- Pattern: custom_openai + inline api_key (как token-plan модели).
- model_info: ctx **1048576** (по OpenRouter, было 262144) /
  out 32768. ic/oc добавлены в model_info (страховка).
- Команды: все кроме Agents (11). Бэкап
  `TeamTable_bak_hy4_20260902`. _REASONING_CAPABLE в api.py дополнен
  (hy4-preview возвращает reasoning_content).
- **Цены** (OpenRouter): in $0.834 / out $2.501 за 1M.
  Добавлены в `litellm_settings.model_cost` ОБОИМИ ключами:
  `'hy4-preview'` (bare) и `'custom_openai/hy4-preview'` (с
  `litellm_provider: custom_openai`). ОБЯЗАТЕЛЬНО оба — bare-ключ
  для fallback при lookup, провайдер-префиксный ключ для прямого
  совпадения в `cost_per_token` (см. gotcha ниже).

**Gotcha: model_cost keys для custom_openai моделей.**
`litellm.cost_per_token` (cost_calculator.py ~478) делает lookup:
```python
if model_with_provider in model_cost_ref:   # "custom_openai/<x>"
    model = model_with_provider
elif model in model_cost_ref:                # input as-is
    ...
elif model_without_prefix in model_cost_ref:# bare "<x>"
    model = model_without_prefix
```
Для custom_openai `model_with_provider = "custom_openai/<bare>"`. Если
в map есть ТОЛЬКО bare `<bare>` — должен найтись через 3-ю ветку.
НО: для hy4-preview с bare-ключом spend оставался $0 (хотя glm-5-2
с bare-ключом работает). Возможно race с `register_model` при startup
перезаписывал entry. Workaround: добавлять ОБА ключа
(`'<bare>'` + `'<provider>/<bare>'` с `litellm_provider`). Проверено:
после добавления `custom_openai/hy4-preview` spend = $0.000145 (24p + 50c).

---

### [2026-09-02b] — 4 модели Tencent Cloud Token Plan добавлены

**Публичные имена** (в БД LiteLLM):
- `tencent/deepseek-v4-flash-0731` (ctx 1048576 / out 393216)
- `tencent/deepseek-v4-pro-0813` (ctx 1048576 / out 393216)
- `tencent/glm-5-2 (reserved - use when main is exhausted)` (ctx 1048576 / out 131072)
- `tencent/glm-5-3 (reserved - use when main is exhausted)` (ctx 1048576 / out 131072)

**Паттерн**: `custom_openai` + `api_base=https://tokenhub-intl.tencentcloudmaas.com/plan/v3`
+ inline api_key (как GLM-модели z.ai). model_info ctx/out заданы в payload
`/model/new` — персистятся (verified). Команды: **все кроме Agents**
(11 команд, бэкап `TeamTable_bak_tencent_20260902`).

**Цены** (`config.yaml` `litellm_settings.model_cost`):
- `'glm-5-2'`: 1.19/3.74 за 1M; `'glm-5-3'`: 1.4/4.4 — добавлены (как
  z.ai-аналоги). `'deepseek-v4-flash-0731'`: 0.045/0.09; `'deepseek-v4-pro-0813'`: 0.66/1.98 —
  добавлены bare-имена (Alibaba уже были с префиксом `deepseek-ai/`).
  Бэкап `config.yaml.bak.tencent-20260902`.

**Баг найден (важно!)**: LiteLLM ТРИМ/ОБРЕЗАЕТ имя модели **на
запятой** в `_can_object_call_model` (auth check). Доказательство:
`Tried to access tencent/glm-5-2 (reserved` — обрезано на запятой.
Первая версия с суффиксом `(reserved, use when main is exhausted)`
всегда 403, несмотря на наличие в team.models. Замена `, ` на ` - `
починила. **В публичных именах моделей НЕ использовать запятые.**

**Рестарты**: litellm + opencode-api (sudo). В `api.py` `_REASONING_CAPABLE`
добавлены 2 dash-имени (deepseek не добавлены — для consistency с
существующими deepseek, которых там нет).

**Verified**: все 4 модели chat 200, спенд считается (deepseek-flash
0.000005, deepseek-pro 0.000088, glm-5-2 0.000128, glm-5-3 0.000085).

---

### [2026-09-02a] — Image-модели: логи и спенды (диагностика)

**Вопрос**: «в логах не видно вызовов image-моделей; спенды считаются?»

**Находки**:
- Вызовы ЕСТЬ в `LiteLLM_SpendLogs`, но под **upstream-именем**
  (`gemini-2.5-flash-image` из `litellm_params.model`), а не публичным
  алиасом `gemini/gemini-3.1-flash-image`; call_type =
  `aimage_generation` / `aimage_edit`. Поиск в UI по публичному имени →
  пусто. Для gpt-image-* upstream = публичное имя (поэтому их видно).
- **Спенды считаются корректно**: gemini-2.5-flash-image ≈ $0.0388/генерация
  (1290 output tokens × $30/1M = прайс Google); gpt-image-2: 73 вызова →
  $3.05; gpt-image-1.5: 40 → $2.18; edits тоже.
- **Причина 400-х у пользователя** (пары 400→200 в nginx):
  `n > 1` → Gemini «Multiple candidates is not enabled for this model»
  (native API не умеет candidateCount>1). Скрипт ретраит с n=1 → 200.
- Квирк: `quality` на gemini-native → 200, но `data:[]` пустой
  (генерится текст, не картинка) — не слать quality на gemini.
- SpendLogs-строки проваленных запросов: call_type/model пустые,
  spend=0 (LiteLLM failure-logger); тысячи таких от сервисов
  weblate-judge / atlas/qwen3.8-max — отдельный шум.

**Verified** тестовыми вызовами (2 temp-ключа, удалены; ~$0.31
тестовых расходов).

---

### [2026-09-01a] — Сброс пароля pavel@herocraft.com

**Симптом**: «старый пароль перестал работать» (hero1234). Диагностика:
- `verify_password(хэш_из_БД, hero1234)` = **False** — хэш не совпадал
- LiteLLM лог: попытки входа 01.09 10:58–11:16 UTC → «Not valid
  credentials» ×32; **последний успешный логин 31.08 10:38 UTC**
  (UI-сессия на 24ч, истекла 01.09 10:38 — «утром работало» = жила
  сессия, не пароль)
- `/user/update` не вызывался ни разу за retention лога (11.4 ГБ,
  с 12.08); инвайты/юзеры вчера — другие email; audit-лог выключен →
  момент изменения пароля не восстановить
- Массовые updated_at у юзеров 11:12–11:18 UTC — фоновый spend-flush
  LiteLLM, не пароли

**Fix**: `POST /user/update {user_id: 52d4a58d-…, password: <new>}` с
master key → верификация `POST /v2/login` = **HTTP 200**. Новый пароль
передан юзеру.

**Gotchas**: (1) история UI-логинов LiteLLM = сессионные ключи в
`LiteLLM_VerificationToken` с `team_id='litellm-dashboard'` (created_at
= момент входа, expires = +24ч). (2) чтение /var/log/nginx/*.log под
dev01 — Permission denied (www-data:adm 640) — только через sudo; «пустые
grep'ы» по ним были silent failures. (3) пароли хэшируются
`litellm.proxy.utils.hash_password` (scrypt:`base64(salt+dk)`, salt
случайный — восстановить нельзя, только сброс через /user/update).

---

### [2026-08-31a] — Починен пустой LiteLLM UI (инвайт-ссылки)

**Симптом**: инвайт-ссылка `/litellm/ui/?invitation_id=...` — пустая
страница. Диагностика: **весь корень UI** `/litellm/ui/` отдавал
HTTP 200 + 0 байт (не только инвайт); страницы (`/litellm/ui/login/`,
`/litellm/ui/api-keys/`) работали.

**Root cause**: writable-слой контейнера — корневой
`_experimental/out/index.html` = **0 байт** (в образе 22640).
Повреждён во время инлайн-инжекта кнопок 2026-08-29 (тогда «пустой
корень» ошибочно сочли нормой экспорта; инвайт-страницы были рабочими
до 29.08). При рестарте 30.08 LiteLLM переприменил замену
`/litellm-asset-prefix` на пустом файле — mtime обновился, размер нет.
Повреждён был ТОЛЬКО корневой файл (проверено `find -size 0`).

**Fix**: `docker cp` оригинала из образа (`docker create 1f75e7ef70b7`
→ cp → docker cp в litellm) + `docker restart` (стартовая замена
путей `/litellm-asset-prefix` → `/litellm` применяется при старте;
22640 → 20534 байт). Инлайн-патчи страниц НЕ тронуты (btn.js на месте).

**Verified**: `/litellm/ui/` и `/?invitation_id=1da78bb9…` через nginx
→ 200 + `<title>LiteLLM Dashboard</title>` (~20.7KB); js/css ассеты
`/litellm/_next/...` → 200; инвайт валиден (до 07.09, не принят).

**Gotcha**: пустые страницы LiteLLM UI при живых deep-links = проверять
размер корневого `out/index.html` против образа. Восстановление:
docker cp из образа + рестарт (замена путей — на этапе старта).

---

### [2026-08-30e] — Image-модели: восстановлены modalities в opencode.json

**Проблема** (заметил ревьюер-агент): в генерируемых opencode.json у
gemini image-моделей отсутствовал `modalities.input`, а у
gpt-image-1.5 — вообще modalities+attachment. Для text-to-image модель
обязана иметь text-input.

**Root cause — НЕ paste в коде** (api.py/index.html не менялись с
07-13): пустые флаги в `LiteLLM_ProxyModelTable.model_info`:
- gpt-image-1.5: **`mode` отсутствовал** → api.py давал
  `image=false` → buildCfg не писал ничего
- gemini/gemini-3-pro-image + 3.1-flash-image: `supports_vision` пуст
  → `modalities={output:[image]}` без input
- gpt-image-2 был единственным корректным (mode+vision)

**Fix**: SQL merge (`jsonb_build_object`) — gpt-image-1.5 +
`mode='image_generation'` + `supports_vision=true`; обоим gemini +
`supports_vision=true`. Бэкап:
`ProxyModelTable_bak_modalities_20260830`. Рестарты: litellm +
opencode-api (**sudo systemctl** — без sudo Access denied).

**Verified** через `/models?user=` (:9000, путь БЕЗ /setup-opencode
префикса — nginx его маппит): все 4 модели `vis=True image=True
edit=True`. В summary страницы gpt-image-1.5 вернулся в список
«🎨 для генерации картинок».

---

### [2026-08-30d] — GLM-5.3-Flash (res) переведён на ключ B

`GLM-5.3-Flash (res)`: inline api_key A → **ключ B**
(`<glm-key-B>`). Шифрование через `encrypt_value_helper` +
SQL UPDATE (бэкап старых litellm_params: `/tmp/glm53fres_backup_params.json`).
Smoke: 200 OK, spend 1.355e-05 (точно по 0.075/0.25).

Теперь раскладка ключей: обычные GLM-5.x + Flash = ключ A
(`<glm-key-A>`), все (res) 5.1/5.2/5.3/Flash = ключ B. Бонус: пара
`GLM-5.3-Flash (res)` (B) / `_dont_use` (A) стала настоящим failover'ом
— свап вернёт на A одним кликом.

Аудит ключей: `/opt/litellm/admin/glm_keys.py` (расшифровывает и
печатает ключи всех GLM; credential-таблица называется
`LiteLLM_CredentialsTable`).

---

### [2026-08-30c] — DeepSeek-доступ: только Agents + All Access; AlibabaTokenPlan версии убраны

По запросу пользователя:
- `deepseek-ai/deepseek-v4-flash`, `deepseek-ai/deepseek-v3.2`,
  `deepseek-v4-pro`, `atlas/deepseek-v4-flash-0731`,
  `atlas/deepseek-v4-pro-0813` → **только Agents и All Access**
- `AlibabaTokenPlan/deepseek-v4-flash-0731`, `AlibabaTokenPlan/deepseek-v4-pro-0813`
  → **ни у одной команды** (строки моделей в БД сохранены — доступ можно вернуть)

SQL: `array_remove` в `LiteLLM_TeamTable.models` (бэкап-таблица
`LiteLLM_TeamTable_bak_ds_20260830`), затем docker restart litellm.

**Verification** (temp team-ключи): Agents=5, AllAccess=5, Coders=0 ✓.

**Gotcha**: `POST /key/generate {"team_id": ...}` требует РЕАЛЬНЫЙ team_id
(UUID), не alias — ключ с alias «All Access» создаётся без команды и
`/v1/models` возвращает мусор (0 или всё подряд). team_id смотреть в
`LiteLLM_TeamTable`.

---

### [2026-08-30b] — payload-logs v3: утечка пула (хук умирал после 6 записей) + дедуп повторов

**Симптомы**: a.korkin (772 запроса за день, 770 успешных) — 0 логов;
gnaumov 219 → 5 строк; swift 203 → 1. Плюс «множество повторений» в
существующих логах.

**Root cause 1 — утечка коннектов**: в `async_log_success_event` v1
соединение из пула (maxconn=6) НИКОГДА не возвращалось (`_putback` на
пути вставки не вызывался). Каждый залогированный запрос = утечка →
после 6 записей пул исчерпан, хук молча умирал. В логах
«connection pool exhausted» ×10457. Ровно 6 строк за день (5 gnaumov +
1 swift) = maxconn.

**Root cause 2 — повторы**: opencode шлёт ВСЮ историю диалога каждым
запросом; хук v1 сохранял все user-сообщения заново каждый раз.

**Фикс (v3)**:
1. `conn` возвращается в пул через `finally` на ВСЕХ путях.
2. Дедуп: новая колонка `UserRequestLogs.full_texts` (полный массив
   user-текстов запроса; `user_messages` = хвост для UI). Если новый
   массив — префикс-расширение предыдущего `full_texts`, пишется только
   новый хвост; retry/агентные ходы (ничего нового) — строка НЕ пишется;
   новая сессия — полный массив. Роут отдаёт явные колонки —
   `full_texts` в UI не попадает.

**Verification** (тест-ключ, GLM-5.3): R1 [u1]→строка [u1]; R2 [u1,u2]→
только [u2]; R3 retry→НЕТ строки; R4 агентный ход→НЕТ строки; R5 новая
сессия [u3]→полная; R6 [u3,u4]→только [u4]; 6+ вставок подряд без
исчерпания пула. Логи: ACTIVE 17:01:36 UTC, после — ноль ошибок.

**Gotchas**: тест с существующим юзером (gnaumov) не сработал — его
temp-ключ без team_id: «No default model access, only team models
allowed» (его собственные ключи — командные). Тестировать через
пустого юзера + временную строку в allowlist. PowerShell→ssh на этой VM
стал рвать соединения на длинных скриптах — дробить шаги +
ServerAliveInterval=10.

---

### [2026-08-30] — Hotfix: duplicate litellm_settings key (сломался drop_params)

**Инцидент**: после инжекта model_cost (2026-08-29c) пользователи получили
`UnsupportedParamsError: dashscope does not support parameters: ['thinking']`
на qwen3.7-plus и 500 на GLM-5.3-Flash.

**Root cause**: `--emit-yaml` генерил блок СО СВОЕЙ строкой
`litellm_settings:` → в config.yaml оказалось ДВА top-level ключа
`litellm_settings`. PyYAML молча берёт последний → первый блок
(`drop_params: true`, `set_verbose`, `telemetry`, `success_callback`,
`service_callback`) ПОТЕРЯН. Неподдерживаемые параметры (thinking)
перестали дропаться → 400/500 у провайдеров.

**Fix**: удалена дублирующая строка (бэкап
`config.yaml.bak.dupfix-20260830`); model_cost теперь вложен в
существующую секцию. Проверено: litellm_settings keys =
[drop_params, set_verbose, telemetry, success_callback, service_callback,
model_cost(87)]. build_model_cost.py `--emit-yaml` больше не генерит
wrapper-строку.

**Verification**: QWEN3.7-plus + thinking → 200, spend 3.354e-4 (точно
по 0.32/1.28 за MTok) ✓.

**Отдельно (НЕ наш баг)**: GLM-5.3-Flash (обе версии — inline и (res))
падает с z.ai «Internal network failure, error id ...» — апстрим
Z.AI paas/v4 деградировал. GLM-5.1/5.2 (coding/v1) работают. Ждать
восстановления z.ai.

**Урок**: после ЛЮБОЙ правки config.yaml проверять не только YAML-валидность,
но и ОТСТУТСТВИЕ дубликатов top-level ключей (PyYAML молча last-wins).

---

### [2026-08-29c] — Cost tracking: цены всех моделей → spend по юзерам на /ui/usage/

#### Контекст
В SpendLogs 98.4% запросов имели spend=$0 — кастомные модели (GLM-5.x,
MiniMax-M*, qwen3.8-*, mimo, kimi, deepseek, doubao) отсутствовали в
`litellm.model_cost`. LiteLLM-UI `/litellm/ui/usage/` показывал токены, но
не деньги.

#### Решения (согласованы)
- Источник цен: **OpenRouter** (`/api/v1/models`, 396 моделей)
- Бэкфилл старых логов: **нет** (только вперёд)
- Кастомные прокси (cx/sex/atlas/weblate-judge-*): цена модели под капотом
- doubao-seed-2.1-pro/turbo: **ручные цены** (интернет: $0.85/$4.15,
  $0.30/$1.20)
- kimi-k2.7 → kimi-k2.7-code; qwen3.8-max-preview → qwen3.8-max (fuzzy)
- gemini-image + `x`: $0 (но gemini потом покрылись дефолтами LiteLLM)

#### Реализация

**1. `/opt/litellm/build_model_cost.py`** — генератор: наш список моделей
(SpendLogs) → OVERRIDES-маппинг на OpenRouter id → `model_cost` dict
(87 записей). `--emit-yaml` для config. openrouter_models.json рядом
(обновлять: `curl -o /opt/litellm/openrouter_models.json
https://openrouter.ai/api/v1/models && python3 build_model_cost.py`).

**2. `/opt/litellm/inject_model_cost.py`** — вставка блока в config.yaml
(litellm_settings.model_cost, anchor `service_callback: []`), backup
`.bak.model-cost-20260829`.

**3. КРИТИЧЕСКИЙ ГОНЧА: clobber.** `litellm_settings.model_cost`
применяется через `setattr(litellm, key, value)` в `load_config()` —
**заменяет всю стандартную карту (3365 моделей → 87)**. Стандартные
модели (glm-4.6, gpt-*, claude-*) остались бы без цен.

Фикс: **`/opt/litellm/admin/patch_modelcost.py`** — патч proxy_server.py
(anchor: `elif key == "json_logs"` ветка, уникален; marker
`_PATCH_MODEL_COST_MERGE_`; ast.parse перед записью; backup
`.bak_modelcost`): в else-ветке для `model_cost` —
`litellm.model_cost.update(value)` (merge) вместо setattr. Re-patch на
recreate — блок в `litellm_entrypoint.sh` (запускает
`/app/admin/patch_modelcost.py`).

#### Verification
- glm-5.3-flash: 14pt+20ct → spend=**6.05e-06** = точно по ценам
  $0.075/$0.25 за MTok ✓
- `/v1/model/info`: **61/61 моделей с ценами** (наши + дефолтные;
  glm-4.6 показывает дефолтные 0.43/1.75 → merge работает) ✓
- Рестарт: entrypoint-syntax OK, marker в файле ✓

#### Как поддерживать
Новая модель → добавить строку в OVERRIDES в build_model_cost.py →
`python3 build_model_cost.py` (review) → `python3 inject_model_cost.py`
(backup+inject) → `docker exec -i litellm python3
/app/admin/patch_modelcost.py` (idempotent) → `docker restart litellm`.

---

### [2026-08-29] — Selective Payload Logging: per-user логирование запросов + админ-страница

#### Контекст
Появилась возможность логировать содержимое запросов пользователей, но
**выборочно** — только для конкретных user_id (allowlist), только успешные
запросы, только user-authored контент. Глобальный
`store_prompts_in_spend_logs` остаётся ВЫКЛЮЧЕННЫМ — SpendLogs не растёт,
никаких изменений для остальных пользователей.

#### Что сделано

**1. Новые файлы** (все в `/opt/litellm/admin/` — каталог смонтирован в
контейнер как `/app/admin`):

| Файл | Назначение |
|---|---|
| `admin/payload_logs_hook.py` | CustomLogger `PayloadLogsLogger`: на success-event проверяет user_id в allowlist (in-memory кэш, TTL 15с), извлекает ТОЛЬКО `role=="user"` сообщения (текстовые части), INSERT в `UserRequestLogs` через psycopg2 pool (0-6 conn). Полностью fail-safe — исключения глушатся, трафик не ломается. Debug-режим: `touch /tmp/payload_log_debug` в контейнере + restart. |
| `admin/payload_logs_route.py` | FastAPI router: allowlist CRUD (`/admin/payload-logs/api/allowlist*`), логи (`/api/logs?user_id=&hours=&limit=&offset=`), stats, поиск пользователей (`/api/users?q=` — email/id/alias из `LiteLLM_UserTable`), email обогащение ответов allowlist/logs/stats. Auth = `Depends(user_api_key_auth)` (как glm-swap). |
| `admin/payload_logs.html` | UI v1.1 (2026-08-29): управление allowlist (add по user_id **или email** с автодополнением, toggle/delete, email рядом с каждым id), просмотр логов (фильтры user_id/период/лимит, пагинация, email в строках), localStorage admin-key. |

**2. Регистрация**:
- Хук: `_PATCH_PAYLOAD_LOGS_HOOK_` блок в `/opt/litellm/utils_patched.py`
  (bind-mount поверх `litellm/proxy/utils.py`) после блока
  `_PATCH_BLOCK_MASTER_KEY_HOOK_` — паттерн всех существующих хуков.
- Роутер: `PYEOF6` блок в `litellm_entrypoint.sh`, патчит
  `proxy_server.py` (маркер `_PAYLOAD_LOGS_ROUTER_PATCH_`), вставка
  **после** `print(f"[entrypoint] glm_swap_router WARN: {_e}")`.

**3. БД**:
```sql
CREATE TABLE "PayloadLogAllowlist" (user_id text PK, enabled bool, note text, created_at, updated_at);
CREATE TABLE "UserRequestLogs" (id bigserial PK, request_id, user_id, key_alias, key_hash, model, user_messages jsonb, start_time, latency_ms int, created_at);
-- индексы: (user_id, created_at DESC), (created_at)
```

**4. Ретеншн 30 дней**: `/opt/litellm/payload_logs_retention.sh` +
dev01 crontab `30 3 * * *` (ежедневный DELETE старше 30 дней).

**5. Кнопка в UI LiteLLM (nginx-инжект, канонический способ)**: UI LiteLLM
патчится на уровне nginx через `sub_filter` (6 location-блоков в
`/etc/nginx/sites-enabled/litellm-bifrost`), который вставляет в `</head>`
кнопочные скрипты из `/opt/opencode-setup/` (`users-btn.js`,
`vkeys-btn.js`, `glm-swap-btn.js`). Добавлен
`payload-logs-btn.js?v=1` — зелёная пилюля top:12px right:110px (левее
синей GLM Swap), ведёт на `/litellm/admin/payload-logs/`. Заодно
починен давний баг `users-btn.js` (v17): `idleHandle is not defined` —
переменная не была объявлена в области видимости `ensureObserver()`, плюс
колбэк вызывал несуществующую функцию `idle()` → заменено на
`setTimeout`. Попытка инжекта кнопок inline-скриптом в `index.html`
Next.js-экспорта провалилась (regex-ошибка в JS → SyntaxError) и была
полностью откачена (41 файл контейнера + PYEOF7 блок из entrypoint
удалены).

#### Инцидент при внедрении (важно!)

Первый вариант патча entrypoint вставлял router-блок в `proxy_server.py`
по якорю `app.include_router(_glm_swap_router)` — это **середина try-блока
glm_swap_router** → SyntaxError в proxy_server.py → **crash-loop контейнера
(14 рестартов, ~4 мин даунтайма)**. Восстановлено через `docker stop` +
`docker cp` + ремонт region + `docker cp` назад. Якорь исправлен на
`glm_swap_router WARN`-строку (конец glm-блока).

**Урок**: при инжекте кода в try-блоки чужого патча якориться на ЗАКРЫВАЮЩУЮ
конструкцию (последнюю строку except), а не на строку внутри try.

#### Verification (все ✅)

| Тест | Результат |
|---|---|
| Регистрация хука | `payload_logs_hook: ACTIVE (pool 0-6, TTL 15s)` + в Async Success Callbacks |
| Регистрация роутера | `payload_logs_router REGISTERED`, UI 200 |
| Позитив: system+user+assistant+user | Залогированы ТОЛЬКО 2 user-текста, system «SECRET SYSTEM PROMPT» отсутствует |
| Default-deny (юзер не в allowlist) | 0 строк |
| Toggle OFF + TTL 15с | Новые запросы не пишутся |
| Failed запрос (несуществующая модель) | Не пишется |
| Allowlist API (add/toggle/delete) | ✅ (после фикса `cur.description`-чека) |
| Logs API / stats API | ✅ |
| glm-swap UI не сломан | 200 |
| Cron retention (ручной запуск) | DELETE 0 ✅ |
| Тестовые данные удалены | 0 строк, 0 allowlist |

#### Как пользоваться
1. `https://hcbifrost.herocraft.com/litellm/admin/payload-logs/` (или
   `http://127.0.0.1:4001/admin/payload-logs/` с VM), ввести admin key.
2. Добавить user_id (из LiteLLM UI → Users) + заметку.
3. Изменения подхватываются хуком в течение ~15 секунд (кэш TTL).
4. Логи смотреть там же: фильтры по user_id/периоду, клик по тексту —
   полное сообщение.

#### Gotchas
- **TTL-рейс**: allowlist-добавление видно хуку только после истечения
  15с-кэша. В функциональном тесте это дало ложный негатив (чат через 1с
  после add попал в stale-окно).
- **`JSONResponse` не сериализует datetime** → 500 «Internal server error»,
  латентный пока таблица пуста (тест на пустой `[]` проходит). Фикс:
  хелпер `_json()` с `json.dumps(..., default=str)` во всех ответах
  payload_logs_route.py. Проявилось на первом реальном добавлении в
  allowlist: сам POST был успешен, падало обновление списка.
- **`/opt/litellm/*.py` НЕ видны в контейнере** — только пофайловые
  bind-mount'ы существующих хуков. Новые модули класть в `/opt/litellm/admin/`
  (каталог-маунт, read-only, но import работает).
- `utils_patched.py` принадлежит root — правки только через sudo.
- Роут-хелпер: `cur.fetchall()` на INSERT без RETURNING бросает
  "no results to fetch" → нужен `cur.description is not None`-чек.
- **`docker exec` БЕЗ `-i` не передаёт stdin**: `python3 - << EOF` получает
  пустой скрипт и молча выходит с кодом 0. Нужен `docker exec -i`.
- **Lone surrogates в Python**: `"\ud83d\udccb"` — невалидная строка для
  записи в файл (UnicodeEncodeError: surrogates not allowed). Эмодзи в
  escape-виде — только полные codepoints: `"\U0001F4CC"` (📋),
  `"\U0001F504"` (🔄).
- **UI LiteLLM**: Next.js static export из
  `_experimental/out/**/index.html` (по index.html на маршрут; корневой
  `out/index.html` ПУСТОЙ — это норма). UI живёт на
  `SERVER_ROOT_PATH=/litellm` → `/litellm/ui/<route>`; `/ui/` без префикса
  — 404. Инжект скриптов в страницы UI — ТОЛЬКО через nginx `sub_filter`
  (кнопочные скрипты в `/opt/opencode-setup/*-btn.js`, версия через
  `?v=N` для cache-busting). Inline-инжект в index.html контейнера
  работает, но: (а) теряется при обновлении образа, (б) легко сломать JS
  — не выживать.
- **nginx**: слушает только 80/8080 (HTTPS терминируется фронтом выше);
  локальная проверка инжектов — `curl -H "Host: hcbifrost.herocraft.com"
  http://127.0.0.1:8080/...`. НИКОГДА не класть бэкапы конфигов в
  `sites-enabled/` (nginx include'ит всё → duplicate upstream → test
  fail) — только в `sites-available/`. `sub_filter` не виден при прямом
  curl на `:4001` (мимо nginx).
- **JS regex-литералы**: `/pattern\/flags/` — закрывающий слеш литерала
  не экранируется; потерянный закрывающий `/` = «Invalid regular
  expression flags» и весь скрипт молча не выполняется (кнопки не
  рисуются, ошибку видно только в консоли браузера).

---

### [2026-08-28b] — aliyun/qwen3.8-flash (Alibaba Token Plan, Singapore MaaS)

#### Контекст
Добавлена новая модель `aliyun/qwen3.8-flash` через **Aliyun Model Studio Token
Plan** (Singapore region). Это flash-вариант в семействе Qwen3.8 — дешёвый
text-only LLM для быстрых ответов. Используется тот же credential и endpoint,
что и у `qwen3.8-max` / `qwen3.7-max`.

URL: `https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`
Credential: `aliyun_token_plan` (существующий, не создавали новый)
Upstream: `openai/qwen3.8-flash` (LiteLLM срезает префикс `openai/`)

#### Спеки (с источниками)

| Параметр | Значение | Источник |
|---|---|---|
| model_name (БД) | `aliyun/qwen3.8-flash` | требование пользователя |
| litellm_params.model | `openai/qwen3.8-flash` | конвенция Aliyun Token Plan |
| Provider | openai-compatible через DashScope base | существующая конвенция |
| api_base | `https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1` | Aliyun Token Plan Singapore |
| Credential | `aliyun_token_plan` (PLAIN в БД, ключ зашифрован) | из CHANGELOG `[2026-07-23]` |
| **Контекст** | **262 144** (256K) | HuggingFace Qwen3.8-Flash-Next card |
| **Max output** | **131 072** | boundary probe (131072→200, 131073→400) |
| Modalities | text-only (flash = LLM) | family convention |
| Architecture | MoE 125B-total / 6B-active + 51B N-gram | aibusiness.com |

#### Что сделано

**1. Создана модель через `POST /model/new` + исправление двойного шифрования**:

```json
POST /model/new
{
  "model_name": "aliyun/qwen3.8-flash",
  "litellm_params": {
    "model": "openai/qwen3.8-flash",
    "api_base": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    "litellm_credential_name": "aliyun_token_plan",
    "max_tokens": 32768
  },
  "model_info": {
    "db_model": true,
    "max_tokens": 32768,
    "supports_vision": false,
    "max_input_tokens": 262144,
    "supports_video_input": false
  }
}
```

**2. SQL: раздача по 11 командам (exclude Agents)**:

```sql
UPDATE "LiteLLM_TeamTable"
SET models = array_append(models, 'aliyun/qwen3.8-flash')
WHERE team_alias <> 'Agents'
  AND NOT ('aliyun/qwen3.8-flash' = ANY(models));
-- UPDATE 11
```

11 команд получили модель: All Access, Analytics, Art, Coders, CreativeTeam,
GameDesigners, General, PirateShips, Porters, SideCoders, StarTroopers.

**3. Boundary probe + SQL корректировка `max_tokens`**:

```sql
UPDATE "LiteLLM_ProxyModelTable"
SET litellm_params = litellm_params || jsonb_build_object('max_tokens', 131072),
    model_info = model_info || jsonb_build_object('max_tokens', 131072),
    updated_at = NOW()
WHERE model_name = 'aliyun/qwen3.8-flash';
```

Полная boundary таблица:
- 32768 → 200
- 65536 → 200
- 131072 → 200
- 131073 → 400 «Range of max_tokens should be [1, 131072]»
- 163840 / 262144 / 327680 / 524288 → 400 (тот же error)

**Реальный Aliyun cap = 131072**, не 32768 (как у qwen3.6-flash) и не 256K
(как у HuggingFace card). **Без** SQL-патча completion запрашивал бы max=32768,
что работает, но не использует полную ёмкость модели.

**4. Restart**: `docker restart litellm` (readiness 200, попытка 7) +
`systemctl restart opencode-api` (active).

#### Verification (все ✅)

| Тест | Результат |
|---|---|
| `/v1/models` (master) | 61 модель, `aliyun/qwen3.8-flash` PRESENT |
| SMOKE A: explicit | HTTP 200, content='ok', reasoning_tokens=29 |
| BOUNDARY max_tokens=32768 | 200 |
| BOUNDARY max_tokens=65536 | 200 |
| BOUNDARY max_tokens=131072 | 200 |
| BOUNDARY max_tokens=131073 (off-by-one) | **400** «Range of max_tokens should be [1, 131072]» |
| AllAccess key → `/v1/models` | `aliyun/qwen3.8-flash` VISIBLE |
| AllAccess completion | HTTP 200, content='ok', reasoning_tokens=38 |
| Agents key → `/v1/models` | `aliyun/qwen3.8-flash` NOT VISIBLE |
| Agents попытка completion | **403** «team not allowed» |
| 12 команд матрица: Agents=f, остальные 11=t | ✅ |
| Временные ключи удалены | ✅ |

#### Gotcha (для будущего) — двойное шифрование через `/model/new`

Первый POST `/model/new` с **предзашифрованными блобами** (api_base,
litellm_credential_name) от `qwen3.8-max` оказался сломан:

- **Симптом**: модель создаётся в БД, видна в `/v1/models`, но каждый
  completion возвращает `500 AuthenticationError: api_key client option
  must be set`.
- **Причина**: LiteLLM **повторно шифрует** входящие значения перед
  сохранением в БД. Если значение **уже зашифровано** (например,
  скопировано из другой строки), получается **двойное шифрование**:
  `decrypt(decrypt_value_helper(blob))` возвращает не base64-строку, а
  первоначальный blob, который для credential_manager выглядит как
  мусор → credential не резолвится → API key = None.
- **Решение**: передавать в `/model/new` **PLAIN значения** для
  `api_base` и `litellm_credential_name` — LiteLLM сам зашифрует
  один раз при сохранении. `decrypt_value_helper()` затем работает
  корректно.
- **Замечание**: при PLAIN значениях `litellm_credential_name`
  сохраняется в БД как plaintext (а не зашифрованный блоб, как у
  `qwen3.8-max`). Это OK потому что `credential_values.api_key` всё
  равно зашифрован; credential_name — это просто FK.

#### Gotcha 2 — API cap ≠ блог/HF card

- Aliyun Model Plaza **не публикует** output cap для `qwen3.8-flash`.
- HuggingFace card `Qwen3.8-Flash-Next` говорит **262 144** context
  (но не output).
- Реальный API cap = **131 072** (как у `qwen3.8-max`, `GLM-5.3-Flash`).
- **Правило**: для всех будущих моделей от Aliyun Token Plan начинать
  с max_tokens=131072 (или max boundary probe сразу).

---

### [2026-08-28] — GLM-5.3-Flash (Z.AI, multimodal 320B-A18B MoE, 1M/131K)

#### Контекст
Добавлена новая Z.AI модель **GLM-5.3-Flash** (запущена 2026-08-26 по z.ai blog):
320B-total / 18B-active MoE, natively multimodal, 1M context.
Официальный `max output`: 163 840 (z.ai блог). **Реальный z.ai API cap на момент
добавления: 131 072** — `max_tokens=131073` отвечает `400 BadRequest "限制数值范围[1,131072]"`.
Скорректировано: в `model_info` и `litellm_params` записано 131 072 для всех 4 рядов
(2 active + 2 `_dont_use`).

#### Спеки (из официальных источников)
- **Контекст**: 1 048 576 (1M) — docs.z.ai/guides/vlm/glm-5.3-flash, Marktechpost
- **Max output**: **131 072** (фактический z.ai cap, не 163 840 из блога)
- **Architecture**: 320B-A18B MoE (320B total params, 18B active) — Zhipu post
- **Vision**: да (natively multimodal — "assess its own outputs against visual context")
- **Audio/Video**: нет
- **Reasoning**: да (thinking mode)

#### Что сделано

**1. Создано 4 модели в БД через `POST /model/new` + `create-dont-use-models`**:

| model_name | model_id | upstream | provider/credential |
|---|---|---|---|
| `GLM-5.3-Flash` | 765a2d77-fd84-4fb0-a3a3-f4667b09b363 | `glm-5.3-flash` | z.ai `paas/v4`, custom_openai, inline api_key |
| `GLM-5.3-Flash (res)` | b261d9bb-29e9-4e94-8c25-a0495eb1d609 | то же | то же |
| `GLM-5.3-Flash_dont_use` | 9c9ccc3d5de8c56bdc1173147f072fcd | `glm-5.3-flash` | копия active (create-dont-use-models) |
| `GLM-5.3-Flash (res)_dont_use` | 02e974264b4c37950fd658690907a0a1 | то же | то же |

**2. SQL: раздача по 11 командам (exclude Agents)**:

```sql
UPDATE "LiteLLM_TeamTable" SET models = array_append(models, 'GLM-5.3-Flash')
 WHERE team_alias <> 'Agents' AND NOT ('GLM-5.3-Flash' = ANY(models));
-- UPDATE 11

UPDATE "LiteLLM_TeamTable" SET models = array_append(models, 'GLM-5.3-Flash (res)')
 WHERE team_alias <> 'Agents' AND NOT ('GLM-5.3-Flash (res)' = ANY(models));
-- UPDATE 11
```

11 команд получили обе модели: All Access, Analytics, Art, Coders, CreativeTeam, GameDesigners, General, PirateShips, Porters, SideCoders, StarTroopers.

**3. Правка `swap_glm_credentials.py`** (v22957):
- `PAIRS`: 8 → **10** пар (+`GLM-5.3-Flash`/`_dont_use`, +`(res)` пара)
- `QUICK_SWAPS`: 10 → **12** кнопок
- `ALL_MODELS`: 16 → **20** строк
- Docstrings: 8/16/10 → 10/20/12

**4. Правка `glm_swap_route.py`**: docstrings синхронизированы.

**5. Правка `glm_swap.html`** (18095 → **18112 bytes**, **v1.5 → v1.6 (2026-08-28)**):
- Заголовок «Quick swap (10 пар)» → «(12 пар)»
- JS-комментарий обновлён

**6. SQL корректировка max_tokens** после smoke:

```sql
UPDATE "LiteLLM_ProxyModelTable"
SET litellm_params = litellm_params || jsonb_build_object('max_tokens', 131072),
    model_info = model_info || jsonb_build_object('max_tokens', 131072),
    updated_at = NOW()
WHERE model_name LIKE 'GLM-5.3-Flash%';
-- UPDATE 4
```

**7. Restart**: `docker restart litellm` (readiness 200, попытка 7) +
`systemctl restart opencode-api` (active).

#### Verification (все ✅)

| Тест | Результат |
|---|---|
| `/v1/models` (master) — обе модели PRESENT | 60 моделей всего |
| `/admin/glm/models` | 20 строк (включая 4 новые) |
| `/admin/glm/pairs` | 10 пар, у обеих новых `shadow_exists=true` |
| `/admin/glm/quick-swaps` | 12 кнопок, обе новые кнопки 5.3-Flash |
| UI `/admin/glm-swap/` | HTTP 200, v1.6 (2026-08-28) |
| SMOKE A: explicit `GLM-5.3-Flash` | HTTP 200, content='ok', reasoning_tokens=47 |
| BOUNDARY: `max_tokens=131072` | HTTP 200, content='Hi there!...' |
| BOUNDARY: `max_tokens=131073` (off-by-one) | **400** «限制数值范围[1,131072]» — реальный z.ai cap подтверждён |
| AllAccess key → `/v1/models` | `GLM-5.3-Flash` + `(res)` VISIBLE |
| AllAccess completion | HTTP 200, content='ok', reasoning_tokens=19 |
| Agents key → `/v1/models` | `GLM-5.3-Flash` + `(res)` NOT VISIBLE |
| Agents попытка completion | **403** «team not allowed to access model» |
| Временные ключи удалены | ✅ (4 ключа) |

#### Файлы
- DB: `LiteLLM_ProxyModelTable` — 4 новые строки + 4 SQL UPDATE (max_tokens)
- DB: `LiteLLM_TeamTable` — 22 UPDATE (11 × 2 active models)
- `/opt/litellm/swap_glm_credentials.py` — v22896 → **v22957**
- `/opt/litellm/admin/glm_swap_route.py` — v5318 → **v5331**
- `/opt/litellm/admin/glm_swap.html` — v1.5 → **v1.6**

#### Gotcha (для будущего)
- **z.ai блог рекламирует `max output = 163840` для GLM-5.3-Flash**, но **реальный API
  cap = 131072** (boundary test: 131072 → 200, 131073 → 400). Блог вероятно анонсирует
  планируемый/маркетинговый лимит, который пока не активен. **Всегда делать boundary
  probe сразу после создания модели с провайдерским cap.**
- Для моделей семейства GLM через z.ai: максимальный output = **131 072** (одинаковый
  для 5.1, 5.2, 5.3, 5.3-Flash). Документация может показывать другие числа, но
  API cap консистентен.

---

### [2026-08-25g] — atlas/qwen3.8-max (для Agents + AllAccess), qwen3.8-max убран из Agents

#### Контекст
Замена провайдера для модели `qwen3.8-max` в команде `Agents`: вместо
`aliyun_token_plan` версии — `atlas` версия. В `All Access` оставлены ОБЕ
версии (Token Plan + Atlas). Остальные 9 команд без изменений.

#### Спеки (идентично Token Plan версии, из [2026-08-03])
- **Контекст**: 1 048 576 (1M)
- **Max output**: 131 072 (подтверждено эмпирическим boundary-тестом)
- **Vision**: да (image), **Video**: да, **Audio**: нет
- **Reasoning**: да (thinking mode)

#### Что сделано

**1. Создана модель `atlas/qwen3.8-max`** через `POST /model/new`:

```json
{
  "model_name": "atlas/qwen3.8-max",
  "litellm_params": {
    "model": "qwen/qwen3.8-max",
    "api_base": "https://api.atlascloud.ai/v1",
    "custom_llm_provider": "custom_openai",
    "litellm_credential_name": "atlascloud",
    "max_tokens": 131072,
    "use_xai_oauth": false,
    "use_litellm_proxy": false,
    "use_in_pass_through": false,
    "merge_reasoning_content_in_choices": false
  },
  "model_info": {
    "max_input_tokens": 1048576,
    "max_tokens": 131072,
    "supports_vision": true,
    "supports_video_input": true,
    "supports_audio_input": false
  }
}
```

`model_id = 462443d8-3514-433a-a63e-d747b39813ef`. Все поля `model_info`
сохранены корректно. Upstream-ID `qwen/qwen3.8-max` — конвенция Atlas
OpenRouter-style `org/name` (подтвердился через smoke-тест).

**2. SQL: раздача + удаление из Agents**

```sql
-- Убираем qwen3.8-max (Token Plan) из Agents
UPDATE "LiteLLM_TeamTable"
SET models = array_remove(models, 'qwen3.8-max')
WHERE team_alias = 'Agents' AND 'qwen3.8-max' = ANY(models);

-- Добавляем atlas/qwen3.8-max в Agents
UPDATE "LiteLLM_TeamTable"
SET models = array_append(models, 'atlas/qwen3.8-max')
WHERE team_alias = 'Agents' AND NOT ('atlas/qwen3.8-max' = ANY(models));

-- Добавляем atlas/qwen3.8-max в All Access (старая версия остаётся)
UPDATE "LiteLLM_TeamTable"
SET models = array_append(models, 'atlas/qwen3.8-max')
WHERE team_alias = 'All Access' AND NOT ('atlas/qwen3.8-max' = ANY(models));
```

**Итоговая матрица**:

| Команда | qwen3.8-max (Token Plan) | atlas/qwen3.8-max |
|---|---|---|
| Agents | ❌ (убран) | ✅ |
| All Access | ✅ | ✅ (обе) |
| Analytics, Art, Coders, CreativeTeam, GameDesigners, PirateShips, Porters, SideCoders, StarTroopers | ✅ | ❌ |
| General | ❌ | ❌ |

`UPDATE 1` × 3 операции.

**3. Restart**: `docker restart litellm` (readiness 200, попытка 7),
`systemctl restart opencode-api` (active).

#### Verification (все ✅)

| Тест | Результат |
|---|---|
| `/v1/models` (master key) — обе модели PRESENT | 56 моделей всего |
| SMOKE A: explicit `atlas/qwen3.8-max` | HTTP 200, content='ok', reasoning работает (`reasoning_content`: «The user wants me to reply...») |
| Agents key → `/v1/models` | `atlas/qwen3.8-max` VISIBLE, `qwen3.8-max` NOT VISIBLE |
| Agents key → completion на `atlas/qwen3.8-max` | HTTP 200, content='ok' |
| Agents key → попытка `qwen3.8-max` (Token Plan) | **403** «team not allowed to access model. This team can only access models=[...]» — ограничение работает |
| All Access key → `/v1/models` | ОБЕ модели VISIBLE |
| All Access key → completion на `atlas/qwen3.8-max` | HTTP 200, content='ok' |
| Временные ключи удалены | ✅ (3 ключа) |

#### Файлы
- DB: `LiteLLM_ProxyModelTable` — 1 новая строка
- DB: `LiteLLM_TeamTable` — 3 UPDATE (1 array_remove + 2 array_append)

---

### [2026-08-25f] — GLM Swap UI: +GLM-5.3 пары (Z.AI only, без Atlas)

#### Контекст
Страница `/litellm/admin/glm-swap/` не показывала GLM-5.3, хотя пары
`GLM-5.3`/`GLM-5.3_dont_use` и `GLM-5.3 (res)`/`GLM-5.3 (res)_dont_use`
уже существовали в БД (созданы ранее, вне туля). Причина: списки `PAIRS` и
`QUICK_SWAPS` в `swap_glm_credentials.py` были захардкожены на 2026-07-02
(6 пар 5.1/5.2), когда 5.3 ещё не существовало. **GLM-5.3 недоступен в
Atlas** — atlas-пар для 5.3 нет и не будет.

#### Что сделано

**`/opt/litellm/swap_glm_credentials.py`** (v22896, было 22521):
- `PAIRS`: 6 → **8 пар** (+`GLM-5.3`/`GLM-5.3_dont_use`,
  +`GLM-5.3 (res)`/`GLM-5.3 (res)_dont_use`)
- `QUICK_SWAPS`: 8 → **10 кнопок** (+promote для обеих 5.3-теней)
- `ALL_MODELS`: 12 → 16 строк (строится из `PAIRS` автоматически)
- Комментарии/docstrings: «6 pairs»/«12 rows»/«8 one-click» → актуальные
  числа + пометка «GLM-5.3 not available on Atlas»

**`/opt/litellm/admin/glm_swap_route.py`** (5318): только docstrings
(16 rows / 8 pairs / 10 one-click). Код не менялся — импортирует
`PAIRS`/`QUICK_SWAPS` из модуля.

**`/opt/litellm/admin/glm_swap.html`** (18095, было 17858; **v1.4 → v1.5**):
- Заголовок «Quick swap (6 пар)» → «(10 пар)»
- В intro-текст добавлено: пример `GLM-5.3` + примечание
  «GLM-5.3 — только Z.AI, в Atlas недоступен, atlas-теней для 5.3 нет»
- JS-комментарий про количество pairings обновлён
- UI полностью динамический (рендерит из `/admin/glm/pairs` +
  `/admin/glm/quick-swaps`) — других изменений HTML не требуется

**Restart**: `docker restart litellm` (readiness 200 на 7-й попытке).
Файлы bind-mounted, модуль импортируется при старте контейнера.

#### Verification (все ✅)

| Тест | Результат |
|---|---|
| Синтаксис: импорт модуля в контейнере | OK (8 pairs, 16 models, 10 quick_swaps) |
| `GET /admin/glm/models` | 16 строк, включая все 4 GLM-5.3 |
| `GET /admin/glm/pairs` | 8 пар, у всех `shadow_exists=true` |
| `GET /admin/glm/quick-swaps` | 10 записей, включая `GLM-5.3_dont_use → GLM-5.3` и `(res)` |
| `GET /admin/glm-swap/` (UI) | 200, v1.5 (2026-08-25) |
| Dry-run swap `GLM-5.3_dont_use → GLM-5.3` | 200, `changed:false, dry_run:true` |

#### Откат
Файлы сохранены локально (старые версии перезаписаны; при необходимости
восстановить из этого changelog/гита unavailable — но изменение аддитивное,
откат = убрать 2 пары из `PAIRS` и 2 из `QUICK_SWAPS` + restart).

---

### [2026-08-25e] — atlas/deepseek-v4-pro-0813 → добавлен в команду All Access

#### Контекст
Расширение раздачи модели `atlas/deepseek-v4-pro-0813` (см. `[2026-08-25d]`)
на вторую команду — `All Access`. Модель уже развёрнута на провайдере Atlas
и smoke-протестирована для Agents. All Access получает параллельно **обе**
версии Pro 0813:

| Модель | Провайдер | Teams |
|---|---|---|
| `atlas/deepseek-v4-pro-0813` | AtlasCloud | **Agents**, **All Access** |
| `AlibabaTokenPlan/deepseek-v4-pro-0813` | Token Plan | All Access + 9 других (см. `[2026-08-25c]`) |

#### Что сделано

**SQL UPDATE для `All Access`**:
```sql
UPDATE "LiteLLM_TeamTable"
SET models = array_append(models, 'atlas/deepseek-v4-pro-0813')
WHERE team_alias = 'All Access'
  AND NOT ('atlas/deepseek-v4-pro-0813' = ANY(models));
```
`UPDATE 1`.

`systemctl restart opencode-api` (active) — для обновления кэша `_MODEL_INFO`
в `api.py`. LiteLLM подхватывает изменения team.models автоматически (без
рестарта контейнера, так как команда загружается на запрос аутентификации).

#### Verification (все ✅)

| Тест | Результат |
|---|---|
| `/v1/models` (All Access team key) — обе Pro 0813 модели VISIBLE | ✅ (`atlas/...` + `AlibabaTokenPlan/...`) |
| Smoke-тест `atlas/deepseek-v4-pro-0813` через team_id=All Access key | HTTP 200, content='ok', reasoning_tokens=11 |
| Временный ключ удалён | ✅ |

#### Файлы
- DB: `LiteLLM_TeamTable` — All Access.models (1 UPDATE)

---

### [2026-08-25] — Whitelist nginx: +2 egress-IP прокси (admin UI 403 fix)

#### Контекст
Пользователь (office IP `162.55.180.78`, в whitelist) получал **403** на
`POST /litellm/key/generate` из LiteLLM Admin UI при наличии прав. По логам
`/var/log/nginx/access.log` (формат `xff_combined`, поле `XFF=`): egress-IP
корпоративного прокси **ротируется** — за 10 минут наблюдались 3 разных
адреса, ни один не в whitelist:

| Время (25 Aug) | Egress IP | Результат |
|---|---|---|
| 09:41, 09:47 | `31.77.201.242` | 403 |
| 09:42–10:22 (×6) | `193.160.209.118` | 403 |
| 09:43–09:48 (×6) | `91.108.1.0` (Telegram range) | 403 |

Whitelist работает корректно: `real_ip` восстанавливает клиентский IP из
XFF (edge = Timeweb `89.19.213.124`), `remote_addr` = egress прокси →
`deny all`.

#### Что сделано
- **12 whitelist-блоков** в `/etc/nginx/sites-enabled/litellm-bifrost`
  (`key/generate|update|delete`, `model/new|update|delete`, `config/list`,
  `get/config`, `organization`, `guardrails`, `sso`, `admin/glm-swap/`):
  после `allow 162.55.180.78;` добавлены
  `allow 31.77.201.242;` и `allow 193.160.209.118;` (whitelist = 6 IP × 12).
- **`91.108.1.0` сознательно НЕ добавлен** — диапазон Telegram Messenger
  Inc. (shared egress, риск шире корпоративного прокси).
- **`sites-available` синхронизирован** с `sites-enabled` (копия). До этого
  был дрифт: available отставал на инжект `vkeys-btn.js?v=7` ([2026-07-13k]).
  Живой конфиг — sites-enabled.
- **README** `/opt/litellm/README.md` — новый раздел «Admin endpoints IP
  whitelist (nginx)»: таблица 6 IP, 12 endpoints, механика real_ip/XFF,
  примечание про ротацию egress и где искать новые IP.
- **Backups**: `sites-available/litellm-bifrost.bak_20260825-105636` +
  копия исходного available рядом (`.bak_20260825-105636`).

#### Операционные грабли
1. **Backup в `sites-enabled/` ломает nginx**: `include /etc/nginx/sites-enabled/*`
   подхватывает `*.bak*` → `duplicate upstream "litellm_upstream"` → `nginx -t`
   failed (замечено сразу, reload не выполнялся, прод не затронут). Бэкапы —
   только в `sites-available/` (теперь конвенция задокументирована в README).
2. **`sudo -S ... < file` не работает**: stdin-redirect вытесняет пароль из
   pipe. Правильно: `echo pw | sudo -S bash -c 'cat file >> target'`.
3. Прокси ротирует egress: при повторных 403 — искать новый IP в access.log
   (`XFF=`) и добавлять во все 12 блоков.

#### Проверка
| Тест | Результат |
|---|---|
| `nginx -t` (после cleanup бэкапа) | syntax ok, test successful |
| `nginx -s reload` | signal process started |
| `grep allow 31.77.201.242` / `193.160.209.118` (enabled) | 12 / 12 |
| `grep allow 91.108.1.0` (enabled) | 0 (не добавлен) |
| `diff` enabled vs available | идентичны (SYNCED) |
| Smoke `GET /litellm/key/generate` с localhost | **405** (whitelist пропустил, LiteLLM отверг GET — корректно; было бы 403 = блок жив) |
| `/litellm/health/readiness` | 200 (прод жив) |

#### Rollback
```bash
sudo cp /etc/nginx/sites-available/litellm-bifrost.bak_20260825-105636 \
        /etc/nginx/sites-enabled/litellm-bifrost && sudo nginx -s reload
```

---

### [2026-08-25c] — AlibabaTokenPlan/deepseek-v4-pro-0813 (text-only reasoning, 1M/384K)

#### Контекст
Пользователь запросил добавление `deepseek-v4-pro-0813` через Token Plan.
Суффикс `-0813` (версия от 13 августа) отсутствовал в `/v1/models` каталоге
провайдера на момент проверки — были только `deepseek-v4-pro` (без суффикса)
и `deepseek-v4-flash-0731`. Пользователь подтвердил, что `deepseek-v4-pro-0813`
должна работать через Token Plan (провайдеры не всегда сразу обновляют каталог,
когда модель уже доступна под новым именем). Добавлена как отдельная модель.

#### Спеки (из интернета)
- **Контекст**: 1 048 576 (1M) — arxiv 2606.19348v1, api-docs.deepseek.com,
  llm-stats.com, lambda.ai, llm-stats.com
- **Max output**: 393 216 (384K) — DeepSeek official API docs, Morph
- **Architecture**: 1.6T sparse MoE — Lambda blog, Morph
- **Vision**: нет (text-only reasoning) — подтверждено пользователем;
  совпадает с существующей `deepseek-v4-pro` на AtlasCloud (`supports_vision: false`)
- **Tool calling / JSON schema / function calling**: да (типично для DeepSeek-V4)
- **License**: open-weight (MIT/Apache)

#### Что сделано

**1. Создана модель `AlibabaTokenPlan/deepseek-v4-pro-0813`** через
`POST /model/new` (хост-curl на `http://127.0.0.1:4001`, master key из
`/opt/litellm/.env`). Конфигурация — точная копия `AlibabaTokenPlan/deepseek-v4-flash-0731`
(тот же credential `aliyun_token_plan`, тот же `api_base`), с другим `model`
в `litellm_params`:

```json
{
  "model_name": "AlibabaTokenPlan/deepseek-v4-pro-0813",
  "litellm_params": {
    "model": "openai/deepseek-v4-pro-0813",
    "api_base": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    "custom_llm_provider": "openai",
    "litellm_credential_name": "aliyun_token_plan",
    "max_tokens": 393216,
    "use_xai_oauth": false,
    "use_litellm_proxy": false,
    "use_in_pass_through": false,
    "merge_reasoning_content_in_choices": false
  },
  "model_info": {
    "max_input_tokens": 1048576,
    "max_tokens": 393216,
    "supports_vision": false,
    "supports_video_input": false,
    "supports_audio_input": false
  }
}
```

`model_id = 9ac6f77b-a846-4705-9479-8e33a2af1383`. Все поля `model_info`
сохранены корректно (verified через `/model/info`).

**2. Раздача по 10 командам** (exclude Agents) — SQL `array_append` с
идемпотентным guard `WHERE NOT ('...' = ANY(models))`:

| Команда | AlibabaTokenPlan/deepseek-v4-pro-0813 |
|---|---|
| Agents | — |
| All Access | ✅ |
| Analytics | ✅ |
| Art | ✅ |
| Coders | ✅ |
| CreativeTeam | ✅ |
| GameDesigners | ✅ |
| PirateShips | ✅ |
| Porters | ✅ |
| SideCoders | ✅ |
| StarTroopers | ✅ |

`UPDATE 10` — корректно.

**3. Restart**: `docker restart litellm` (readiness = 200 на 7-й попытке),
`systemctl restart opencode-api` (active).

#### Verification (все ✅)

| Тест | Результат |
|---|---|
| `/v1/models` (master key) — модель присутствует | PRESENT, всего 54 модели |
| Смоук через временный virtual key (explicit models) | HTTP 200, `content='ok'`, `reasoning_content` present, reasoning_tokens=25 |
| Смоук через team_id=All Access key (без explicit models) — модель VISIBLE в `/v1/models` | ✅, HTTP 200 |
| Временные ключи удалены | `{"deleted_keys":[...]}` |

`reasoning_tokens: 25` подтверждает, что text-only reasoning работает
(пользователь явно просил модель с reasoning — модель в режиме thinking
по умолчанию, как у других DeepSeek-V4).

#### Файлы
- DB: `LiteLLM_ProxyModelTable` — 1 новая строка
- DB: `LiteLLM_TeamTable` — обновлены массивы models у 10 команд
- Memory: `infra/litellm-model-updates` — будет дополнена

---

### [2026-08-03] — DeepSeek-V4-Flash-0731 (Atlas + Token Plan) + qwen3.8-max (stable)

#### Контекст
Добавлены 3 новые модели:
- **`deepseek-v4-flash-0731`** на двух провайдерах (AtlasCloud + Aliyun Token Plan) — официальный релиз DeepSeek-V4-Flash, заменяет preview, 284B MoE (13B активных), MIT, enhanced agentic, speculative decoding (DSpark)
- **`qwen3.8-max`** на Aliyun Token Plan — стабильный релиз Qwen 3.8 (2.4T, заменяет preview)

#### Спеки (из официальных источников)

| Параметр | deepseek-v4-flash-0731 | qwen3.8-max |
|---|---|---|
| Контекст | **1M (1 048 576)** — arXiv 2606.19348, api-docs.deepseek.com | **1M (1 048 576)** — qwen.ai blog, gptproto |
| Max output | **384K (393 216)** — DeepSeek docs, Model Studio | **131 072** — подтверждено эмпирическим тестом |
| Vision | **нет** — text-only (HF: text-generation, Alibaba FAQ: "DeepSeek supports only text input") | **да** — vision+video (как у preview) |
| Reasoning | thinking-mode по умолчанию, `reasoning_effort`: low/high/max | thinking-mode, `reasoning_effort` |
| Tool calling | да | да |
| License | MIT | — |

#### Что сделано

**3 модели созданы через `POST /model/new`** (хост-curl на `http://127.0.0.1:4001`, master key из `/opt/litellm/.env`):

| model_name | credential / provider | upstream model | ctx | out | vision |
|---|---|---|---|---|---|
| `atlas/deepseek-v4-flash-0731` | atlascloud / custom_openai | `deepseek-ai/deepseek-v4-flash-0731` | 1 048 576 | 393 216 | нет |
| `AlibabaTokenPlan/deepseek-v4-flash-0731` | aliyun_token_plan / openai | `openai/deepseek-v4-flash-0731` | 1 048 576 | 393 216 | нет |
| `qwen3.8-max` | aliyun_token_plan / openai | `openai/qwen3.8-max` | 1 048 576 | 131 072 | да (+video) |

**`model_info` сохранён корректно** — `/model/new` персистит поля (в отличие от `/model/update`).

**`qwen3.8-max` max_tokens поднят с 32 768 до 131 072** — эндпоинт принял `max_tokens=131072` (дешёвый probe), SQL UPDATE в `model_info` + `litellm_params`, затем двойной рестарт.

#### Распределение по командам (11 команд)

| Команда | atlas/...-0731 | AlibabaTokenPlan/...-0731 | qwen3.8-max |
|---|---|---|---|
| **Agents** | ✅ | — | — |
| **All Access** | ✅ | ✅ | ✅ |
| Analytics, Art, Coders, CreativeTeam, GameDesigners, PirateShips, Porters, SideCoders, StarTroopers | — | ✅ | ✅ |

SQL `array_append` с `WHERE NOT ('...' = ANY(models))` — идемпотентно.

#### Проверка (все ✅)

| Тест | Результат |
|---|---|
| `/v1/models` (master key) — все 3 модели присутствуют | 50 моделей всего |
| Смоук atlas/deepseek-v4-flash-0731 (экс. ключ) | 200, reasoning_tokens=37 |
| Смоук AlibabaTokenPlan/deepseek-v4-flash-0731 (экс. ключ) | 200, reasoning_tokens=58 |
| Смоук qwen3.8-max (экс. ключ) | 200, content='ok' |
| qwen3.8-max max_tokens=131072 boundary | 200 (эндпоинт принял) |
| All Access team key (team_id, без explicit models) — все 3 модели VISIBLE | ✅ |
| Agents key — atlas VISIBLE, AlibabaTokenPlan/qwen3.8-max NOT VISIBLE | ✅ |
| Agents key → AlibabaTokenPlan/...-0731 → 403 | ✅ (ограничение работает) |
| Временные ключи удалены | ✅ |

#### Операционные грабли
1. **Python (urllib/http.client) изнутри контейнера на 127.0.0.1:4000 с master key → 401 "Malformed API Key"** при том же ключе, который работает через curl (хост, :4001). Правило: все API-вызовы делать curl'ом с хоста на `http://127.0.0.1:4001` с ключом из `/opt/litellm/.env`.
2. **block_master_key_hook блокирует master key на `/v1/*` inference** (401 master_key_blocked). Смоук-тесты моделей — только через временный виртуальный ключ: `POST /key/generate {"models":[...]}` → тесты → `POST /key/delete {"keys":[...]}`. Это заодно проверяет team-раздачу end-to-end.
3. **AtlasCloud `GET /v1/models` → 403** (каталог закрыт). Upstream-ID брать из конвенции существующих atlas-моделей (`deepseek-ai/...`).
4. **Каталог Token Plan** (`GET .../compatible-mode/v1/models`, ключ из credential `aliyun_token_plan`): 11 моделей; подтверждены `deepseek-v4-flash-0731` и `qwen3.8-max`. Префикс `openai/<name>` работает для deepseek (LiteLLM срезает префикс перед отправкой).
5. **Org-ограничений нет**: в `LiteLLM_OrganizationTable` models IS NULL у всех → апдейты org не нужны.

#### Файлы
- DB: `LiteLLM_ProxyModelTable` — 3 новых модели
- DB: `LiteLLM_TeamTable` — обновлены массивы models для 11 команд
- Memory: `infra/litellm-model-updates` — раздел `[2026-08-03]`

---

### [2026-07-17c] — Qwen 3.8-Max-Preview / 3.7-Max / 3.6-Flash: 3 новые модели от Aliyun Token Plan

#### Контекст
Подключены 3 новые reasoning-модели от **Alibaba Qwen** через
**Aliyun Model Studio Token Plan** (Singapore region). Все три
поддерживают Text Generation, Reasoning Model и Visual Understanding
(изображения + видео), имеют контекстное окно **1M токенов**.

#### Что сделано
- **Credential** `aliyun_token_plan` создан через LiteLLM `/credentials`:
  - `api_key`: `sk-sp-H.XPHM.xXI7.MEU...`
  - `api_base`: `https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`
- **3 модели** добавлены через `POST /model/new` с
  `litellm_credential_name: aliyun_token_plan`:
  - `qwen3.8-max-preview` — флагман 2.4T MoE, preview-версия
  - `qwen3.7-max` — reasoning agent, 1M контекст
  - `qwen3.6-flash` — быстрый вариант, 1M контекст
- **`model_info`** для всех трёх:
  - `max_input_tokens: 1048576` (1M)
  - `max_tokens: 32768` (32K)
  - `supports_vision: true`
  - `supports_video_input: true`
- **Распределение по командам**:
  - `qwen3.8-max-preview` + `qwen3.7-max` — все 10 команд кроме Agents:
    All Access, Analytics, Art, Coders, CreativeTeam, GameDesigners,
    PirateShips, Porters, SideCoders, StarTroopers
  - `qwen3.6-flash` — только команда Agents
- **`_REASONING_CAPABLE`** в `api.py` обновлён: добавлены 3 модели
- **Двойной рестарт**: `bash /opt/litellm/start.sh` +
  `systemctl restart opencode-api`
- **Backup**: `/opt/opencode-setup/api.py.bak.qwen3-aliyun-20260717`

#### Проверка
- **Live-тест** всех трёх моделей через временный VK: все вернули
  status=200, ответ `"pong"` с reasoning_content.
- **`/setup-opencode/api/models`** для All Access (pavel): 28 моделей,
  включая `qwen3.7-max` и `qwen3.8-max-preview` с ctx=1048576, out=32768,
  vision=true, video=true, reasoning=true.
- **`/setup-opencode/api/models`** для Agents (i.efimov): 19 моделей,
  включая `qwen3.6-flash` с теми же capabilities. `qwen3.7-max` и
  `qwen3.8-max-preview` **отсутствуют** (правильно — только в Agents flash).

#### Gotcha: Pavel VK в БД мёртв
При попытке проверить через `?vk={pavel_vk}` — возвращает
`404 virtual key not found`. VK pavel (`e3d575790b0c19e2...`) имеет
hash mismatch в БД. **Известная проблема** — см. companion memory
`litellm-kimi-k3-zero-context-bug.md` и сессию про "game pulse" VK.
**VK требует ротации** — работать с ним напрямую нельзя.

---

### [2026-07-17b] — Fix: Kimi K3 — vision=true и video=true в OpenCode Setup

#### Контекст
Kimi K3 (Moonshot) поддерживает multimodal input (принимает на входе
картинки и видео), но в `model_info` эти capability-флаги не были
выставлены при создании модели. В форме OpenCode Setup модель
показывалась с `vision: false` и `video: false`, и opencode не мог
отправлять в неё изображения/видео.

#### Что сделано
- **SQL UPDATE** в `LiteLLM_ProxyModelTable.model_info`:
  ```sql
  UPDATE "LiteLLM_ProxyModelTable"
  SET model_info = model_info || '{"supports_vision": true, "supports_video_input": true}'::jsonb
  WHERE model_name = 'Kimi K3';
  ```
  (`POST /model/update` не персистит эти поля для моделей созданных через
  `/model/new`; SQL — единственный надёжный путь, см. companion memory
  `litellm-model-updates.md`.)
- **Двойной рестарт**:
  - `bash /opt/litellm/start.sh` — LiteLLM контейнер пересоздан, чтобы
    перечитать `model_info` из БД.
  - `systemctl restart opencode-api` — перезагрузка `api.py` для очистки
    кэша `_MODEL_INFO`.

#### Результат `/setup-opencode/api/models`
```json
Kimi K3: {
  "context": 1048576,
  "output": 1048576,
  "vision": true,        // ← было false
  "video": true,         // ← было false
  "audio": false,
  "image": false,
  "image_edit": false,
  "reasoning": true
}
```

#### Для сравнения — Kimi K2.6/K2.7
K2.6/K2.7 сейчас имеют `vision: true, video: false` (только картинки,
без видео). K3 — первая модель линейки Kimi в нашей системе с поддержкой
**видео на входе**.

---

### [2026-07-17a] — Fix: Kimi K3 показывал `context: 0, output: 0` в OpenCode Setup

#### Контекст
После добавления Kimi K3 (запись `[2026-07-16a]`) пользователь
обнаружил, что в форме "OpenCode Setup" модель отображалась с
нулевыми лимитами:
```json
"Kimi K3": {
  "name": "Kimi K3",
  "limit": {"context": 0, "output": 0}
}
```
Несмотря на то что LiteLLM хранил правильные значения
`model_info.max_input_tokens=1048576`, `max_tokens=1048576` и сам
прокси успешно обрабатывал запросы (live-тест возвращал правильный
контент).

#### Корневая причина
В `api.py` (строки 285–329, функция `build_model_info`) кэш
`_MODEL_INFO` живёт **пока живёт python-процесс**. После добавления
модели в LiteLLM контейнер был перезапущен (`bash /opt/litellm/start.sh`),
но systemd service `opencode-api` работал **2 дня без рестарта** —
поэтому `api.py` отдавал старую версию кэша.

`api.py` НЕ инвалидирует кэш автоматически при изменениях в БД.

#### Что сделано
- **`systemctl restart opencode-api`** — кэш `_MODEL_INFO`
  пересоздан через `/v2/model/info`.
- **`_REASONING_CAPABLE` в `api.py`** — добавлено `"Kimi K3"`
  рядом с `"Kimi K2.6", "Kimi K2.7"`. Без этого
  `/setup-opencode/api/models` отдавал `reasoning: false`,
  и opencode не включал thinking автоматически.
- **Backup**: `/opt/opencode-setup/api.py.bak.k3-reasoning-20260717`.

#### Результат
После фикса `/setup-opencode/api/models` для i.efimov:
```json
Kimi K2.6: {context: 262144, output: 262144, reasoning: true}
Kimi K2.7: {context: 262144, output: 262144, reasoning: true}
Kimi K3:   {context: 1048576, output: 1048576, reasoning: true}
```

#### Правильный workflow добавления модели (документировано)
После ЛЮБЫХ изменений в `LiteLLM_ProxyModelTable` нужно **оба** рестарта:

```bash
sudo bash /opt/litellm/start.sh          # контейнер LiteLLM
sudo systemctl restart opencode-api      # python-service с кэшем _MODEL_INFO
```

Плюс для reasoning-capable моделей добавить строку в
`_REASONING_CAPABLE` в `/opt/opencode-setup/api.py`, иначе
`reasoning: false` в JSON.

Подробности — в companion memory `litellm-kimi-k3-zero-context-bug.md`
в `.serena/memories/infra/`.

---

### [2026-07-16a] — Kimi K3: новая модель Moonshot с контекстом 1M

#### Контекст
Добавлена новая модель **Kimi K3** от Moonshot AI. Это флагман
следующего поколения (после K2.6/K2.7) с **контекстным окном 1M токенов**
(1,048,576 — в 4 раза больше, чем у K2.6/K2.7).

#### Что сделано
- **Модель**: `Kimi K3` (id upstream: `moonshot/kimi-k3`)
  - `litellm_params.model`: `moonshot/kimi-k3`
  - `litellm_params.custom_llm_provider`: `moonshot`
  - `litellm_params.litellm_credential_name`: `Kimi` (тот же credential
    что для K2.6/K2.7, использует `MOONSHOT_API_KEY`)
  - `litellm_params.headers.User-Agent`: hardcoded fake-browser UA
    `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ... Chrome/120.0.0.0`
    (та же байпасс-техника что для K2.6/K2.7 — Moonshot режет скриптовые UA)
  - `litellm_params.thinking.type`: `enabled` (reasoning ON, как у K2.6/K2.7)
  - `model_info.max_input_tokens`: `1048576` (1M)
  - `model_info.max_tokens`: `1048576` (1M)
- **Teams**: добавлена во все команды **КРОМЕ Agents**:
  - ✅ All Access (`02445a34-...`) — было 25 → стало 26
  - ✅ Analytics (`efa16a4a-...`) — 11 → 12
  - ✅ Art (`2225fac2-...`) — 13 → 14
  - ✅ Coders (`d9669d1e-...`) — 15 → 16
  - ✅ CreativeTeam (`47494184-...`) — 15 → 16
  - ✅ GameDesigners (`88103979-...`) — 15 → 16
  - ✅ PirateShips (`d6b98fd7-...`) — 15 → 16
  - ✅ Porters (`a953e79b-...`) — 15 → 16
  - ✅ SideCoders (`35d8ef41-...`) — 11 → 12
  - ❌ Agents (`817d2234-...`) — **НЕ добавлена** (как просили)
- **`user_agent_hook.py`**: `"Kimi K3"` добавлен в оба кортежа:
  - `_MERGE_FIRST_CHUNK_MODELS` — чтобы первый streaming chunk уже содержал
    непустой `reasoning_content` (как у K2.6/K2.7, для opencode UI).
  - `_KIMI_EMPTY_MSG_MODELS` — чтобы защититься от HTTP 400 от Moonshot
    на пустые assistant-сообщения.
- **Backup**: `/opt/litellm/user_agent_hook.py.bak.k3-20260716`.
- **Restart**: `bash /opt/litellm/start.sh` (delete+recreate контейнера).
  Hook загружен: `merge_models=('Kimi K2.6', 'Kimi K2.7', 'Kimi K3', 'MiniMax-M2.7')`.

#### Проверка
Live-тест через временный VK (`K3_DUMP`):
- **Non-streaming**: `What is 2+2? Answer in one word.`
  → `status=200`, `content='4'`, `reasoning_content='We need answer one word. 4.'`
  usage: prompt=20, completion=12, total=32
- **Streaming**: 41 chunks за 1.58s, первый chunk уже содержит
  `reasoning_content='The'` (merge-first-chunk хук сработал корректно)

#### Почему не добавлен в Agents
`Agents` — это production-team для AI-агентов, работающих через opencode.
По решению пользователя K3 в Agents не добавляется. Возможно, для
контроля расхода (1M-контекст модели дорогой) или чтобы не менять
стабильный набор моделей в production.

---

### [2026-07-15a] — user_agent_hook: защита от Cloudflare 1010 на api.atlascloud.ai

#### Контекст
`api.atlascloud.ai` (новый upstream для `deepseek-v4-flash`,
`doubao-seed-2.1-turbo-260628`, `deepseek-v3.2`) сидит за Cloudflare
Bot Management. 2026-07-15 в 14:32:04Z Cloudflare вернул ошибку
`Error 1010: Access denied / browser_signature_banned` с полем
`retryable: False` на запрос с `bytedance/doubao-seed-2.1-turbo-260628`.
В локальных логах LiteLLM тело ответа Cloudflare не сохраняется — только
HTTP status (найдено 15 отказов 403 в `/tmp/docker_full.log` между
строками 3135–4169, но без тел ошибок).

Cloudflare анализирует **HTTP-сигнатуру** (UA + Accept + Sec-Fetch-* и др.),
не только UA. `retryable: False` означает перманентный бан клиента по
IP+сигнатуре, не по одному UA.

#### Что сделано
- **`user_agent_hook.py` — функция `_decide_ua`** расширена списком
  `_SCRIPT_UA_TOKENS` из 13 подстрок. Если клиентский UA содержит
  любую из них (case-insensitive) — в upstream отправляется
  `OPENCODE_UA = "opencode/local ai-sdk/provider-utils/4.0.27 runtime/node.js/24"`.
- **Список токенов** (в `/opt/litellm/user_agent_hook.py` строки 90–125):
  ```
  curl, wget, powershell,                          # CLI tools
  mozilla,                                          # любые браузеры
  python, go-http-client, java/, ruby, libwww-perl, # language stdlib
  axios, node-fetch, okhttp, guzzlehttp, aiohttp    # популярные HTTP libs
  ```
- **Бэкап** `/opt/litellm/user_agent_hook.py.bak.ua-filter-ext-202607160157`.
- **Restart** контейнера: `bash /opt/litellm/start.sh` — hook перезагружен
  с новым кодом (instance id изменился).

#### Проверка
Live-тест через echo-сервер внутри контейнера LiteLLM:

| Client UA                          | → upstream в `echo_log.txt`                | Результат |
|------------------------------------|--------------------------------------------|-----------|
| `curl/7.88.1`                      | `opencode/local ai-sdk/...`                | ✓ replaced |
| `python-requests/2.33.1`           | `opencode/local ai-sdk/...`                | ✓ replaced |
| `axios/1.7.7`                      | `opencode/local ai-sdk/...`                | ✓ replaced |
| `Mozilla/5.0 (Windows NT 10.0)...` | `opencode/local ai-sdk/...`                | ✓ replaced |
| `opencode-cli/1.0.5`               | `opencode-cli/1.0.5`                       | ✓ pass-through |
| `test-script/1.0`                  | `test-script/1.0`                          | ✓ pass-through |

Live-тест реальных моделей (после фикса) — все 6 разных UA вернули
`status=200` на `bytedance/doubao-seed-2.1-turbo-260628`.

#### Что НЕ изменилось
- Логика моделей с `litellm_params.headers.User-Agent` (MiniMax-M*,
  moonshot-v1, kimi-k2-*) — у них свой hardcoded fake-browser UA,
  перебивает hook by design.
- `_MERGE_FIRST_CHUNK_MODELS`, `_KIMI_EMPTY_MSG_MODELS`, trace logging —
  не затронуты.

#### Rollback
```bash
sudo cp /opt/litellm/user_agent_hook.py.bak.ua-filter-ext-202607160157 \
        /opt/litellm/user_agent_hook.py
bash /opt/litellm/start.sh
```

---

### [2026-07-14b] — Atlas Cloud провайдер + 3 модели (deepseek-v4-flash, deepseek-v3.2, doubao-seed-2.1-turbo)

#### Контекст
Подключён новый upstream-провайдер **atlascloud.ai** (агрегатор
LLM с OpenAI-совместимым API). Раньше 4 модели `atlas_glm-*` уже
существовали в DB с правильным `api_base: https://api.atlascloud.ai/v1`,
но **не работали** — для `custom_openai` провайдера без
`litellm_credential_name` требуется env var `CUSTOM_OPENAI_API_KEY`,
которого в `/opt/litellm/.env` не было.

#### Что сделано
- **Credential**: создан `atlascloud` через LiteLLM `/credentials`
  (api_base `https://api.atlascloud.ai/v1`, api_key задан вручную).
- **LiteLLM restart**: `bash /opt/litellm/start.sh` (delete+recreate
  контейнера, чтобы DB-model entries подхватили credential store).
- **Модели пересохранены** с `litellm_credential_name: "atlascloud"`:
  - `atlas_glm-5.1` (была сломана — теперь работает)
  - `atlas_glm-5.2` (была сломана — теперь работает)
  - `deepseek-ai/deepseek-v4-flash` — ctx=1M, out=384K, **no reasoning**
  - `deepseek-ai/deepseek-v3.2` — ctx=160K, out=160K, **reasoning ON**
  - `bytedance/doubao-seed-2.1-turbo-260628` — ctx=256K, out=256K, **no reasoning**
- **Teams**: добавлены 3 новые модели в `All Access`
  (`02445a34-...`) и `Agents` (`817d2234-...`).
- **`_REASONING_CAPABLE`** в `api.py` дополнен:
  `deepseek-v4-pro`, `deepseek-ai/deepseek-v3.2` — теперь opencode видит
  их как reasoning-capable и автоматически включает thinking.

#### Проверка User-Agent форвардинга
Подтверждено эмпирически (через echo-сервер в контейнере LiteLLM):
- **Для моделей без `headers.User-Agent` в `litellm_params`**
  (например atlas/deepseek/doubao/kimi/seed/mimo/QWEN3.7-plus/Qwen3.5-plus):
  LiteLLM **пробрасывает клиентский UA** к upstream как есть.
- **Для моделей С `headers.User-Agent` в `litellm_params`**
  (MiniMax-M2.5, MiniMax-M3, Kimi K2.7, kimi-k2-0905-preview, kimi-k2-turbo-preview,
  moonshot-v1-128k): LiteLLM подставляет **HARDCODED fake browser UA**
  (`Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36`)
  и переписывает клиентский. Это by design — `api.minimax.io` и `api.moonshot.cn`
  режут любой не-браузерный UA.

### [2026-07-14a] — MiniMax-M2.7/M2.5/M2.1-highspeed: новые модели в LiteLLM

#### Контекст
Добавлены 3 новые модели MiniMax-M* с суффиксом `-highspeed` в
LiteLLM proxy. Это быстрые варианты существующих моделей с **укороченным
reasoning** (~250–500 chars вместо нескольких тысяч у полных M2.7/M2.5).

#### Что сделано
- **`LiteLLM_ProxyModelTable`** — 3 новые записи:
  - `MiniMax-M2.7-highspeed` (ctx 204800, out 131072, vision=false)
  - `MiniMax-M2.5-highspeed` (ctx 200000, out 131072, vision=false)
  - `MiniMax-M2.1-highspeed` (ctx 200000, out 131072, vision=false)
  Все три: `custom_llm_provider: custom_openai`,
  `litellm_credential_name: Minimax`,
  `thinking.type: "disabled"`.
- **Teams**: добавлены в `All Access` (`02445a34-...`) и
  `Agents` (`817d2234-...`).

#### Замечание про `thinking.type: disabled`
**Upstream MiniMax API игнорирует `thinking.type` для M-серии** —
`sse-stream` всегда содержит `reasoning_content` независимо от того,
просим ли мы reasoning или нет. Параметр передаётся корректно (видно в
`/model/info`), но сервер MiniMax всё равно возвращает reasoning chunks.
Размер reasoning в `-highspeed` вариантах **значительно меньше**
(~267–509 chars vs несколько тысяч у обычных M2.7/M2.5), что и
обеспечивает основной выигрыш в скорости.

### [2026-07-13m] — Кнопка OpenCode Setup для каждого виртуального ключа

#### Контекст
По аналогии с `users-btn.js` (добавляет кнопку «🛠 OpenCode» в строке
каждого пользователя на `/ui/?page=users`) реализована инжекция кнопки
OpenCode Setup в **строке каждого виртуального ключа** на
`/litellm/ui/api-keys/` (и эквивалентных маршрутах с
`?page=virtual-keys`). Клик открывает
`/setup-opencode/?vkh=<sha256-hash>` — opencode.json генерируется для
**владельца** ключа (LiteLLM `user_id`), а не для самого токена.

**Почему `?vkh=`, а не `?vk=`**: LiteLLM Admin UI **маскирует** токены
в таблице (показывает `sk-XXXX…ABCD` и кладёт SHA-256 hash в
`data-row-key` / clipboard). Получить полный токен через DOM/clipboard
**невозможно by design**. Решение — использовать SHA-256 hash напрямую:
LiteLLM `/key/info?key=<hash>` принимает и токен, и его hash, поэтому
API и фронт остаются безопасными.

#### Изменения

**`/opt/opencode-setup/api.py`**:
- `/vk-info`, `/models`, `/mcp` принимают **либо** `?vk=<sk/hkg-...>`,
  **либо** `?vkh=<64-hex>`. Параметр `vkh` помечен как «preferred» для
  любых интеграций с LiteLLM Admin UI.
- Новый regex `VK_HASH_RE = ^[0-9a-f]{64}$` для валидации hash.
- Резолв hash через LiteLLM `/key/info?key=<hash>` (5-мин кэш).
- В response `/models` и `/vk-info` теперь есть `resolved_via` ∈
  `{user, vk, vkh}`, `key_name` (`sk-...XXXX`), `user_email`
  (через follow-up `/user/info`).
- **`resolve_models()` теперь учитывает per-key whitelist**: если у VK
  есть `VerificationToken.models = [...]` (не пусто), он становится
  АВТОРИТЕТНЫМ — user/team models игнорируются. Это поведение
  соответствует самому LiteLLM: ключ с whitelist может обращаться
  только к моделям из этого списка, даже если у владельца больше прав.
- **Team-scoped VK (`key.team_id != None`) теперь использует модели ТОЛЬКО
  той команды, к которой привязан VK** — а не объединение user.personal
  моделей + ВСЕ команды владельца. Раньше для VK, чей owner состоял в
  нескольких командах, UI показывал модели из чужих команд (например
  ключ Agents у i.efimov показывал ещё и SideCoders' модели).
- **Параметр VK (`vk` / `vkh`) теперь имеет приоритет над `user`** в
  `resolve_target()`. Setup-страница шлёт `?user=<resolved-uid>&vkh=...`
  одновременно (для надёжности при refresh), и предыдущая версия
  использовала `user`-ветку, теряя VK context. Теперь `resolved_via`
  корректно = `vk`/`vkh`, и `resolution_mode` применяется правильно.
- Response `/models` для VK теперь содержит `vk_key_models` (если
  whitelist реальный, НЕ `["all-team-models"]`) и `resolution_mode`:
  - `user+team` — обычный user-scoped ключ или прямой запрос по user_id
  - `team-only` — ключ команды с `models=["all-team-models"]`
  - `explicit` — у ключа свой per-key whitelist
  - `none` — team-scoped ключ без whitelist (недоступен к моделям)
- UI использует `resolution_mode` чтобы выбирать баннер:
  `🔒 whitelist из N моделей` (explicit), `👥 Ключ принадлежит команде
  «Agents»` (team-only).

**`/opt/opencode-setup/index.html`**:
- Парсит `?vk=` И `?vkh=` из URL.
- Если есть любой — сначала fetch `/api/vk-info?vk=...` или `?vkh=...`,
  затем `/api/models?user=<resolved>&vk=...&vkh=...`.
- Бейдж `user:` дополняется ` · vk alias: <alias>`.

**`/opt/opencode-setup/vkeys-btn.js`** (новый, ~12 KB):
- Аналог `users-btn.js` для `api-keys` / `virtual-keys` страниц.
- Паттерн страницы `currentIsVkPage()` — `/api-keys/`, `?page=virtual-keys`,
  `?page=virtual_keys`, `?page=api-keys`.
- Селекторы строк: `tr.tremor-TableRow-row`, `tbody tr`, `tr td` —
  отфильтровывает header-строки (с `tremor-TableHeaderCell-root` / без `<td>`).
- 3 стратегии извлечения идентификатора строки:
  1. **Полный VK** (`sk-/hkg-...`) из `data-row-key`/текста → `?vk=`
  2. **SHA-256 hash** из `data-row-key` (64-hex) → `?vkh=` (предпочтительно)
  3. **Clipboard intercept** через `navigator.clipboard.writeText` hook +
     `copy` event listener (fallback для старых LiteLLM версий, не
     полагаемся — даём кнопку «📋 Copy VK» которая перехватывает)
- Видимый draggable debug badge (по умолчанию только на VK-странице)
  показывает: `path`, `bodyRows`, `btns`, `mask=full, masked`,
  `datalist=...`, `sample=<HTML первой строки>`.
- Throttle 750ms, MutationObserver на `<main>`, поддержка SPA-навигации
  (`pushState`/`replaceState` patch), visibility-pause.

**NGINX `/etc/nginx/sites-enabled/litellm-bifrost`**:
- 6 sub_filter инжекций для страниц `/ui/`, `/ui/onboarding/`,
  `/litellm/ui*`, `/litellm/ui/login`, `/litellm/admin/glm-swap/`
  расширены `<script src="/setup-opencode/vkeys-btn.js?v=6">`.

#### Проверка
```bash
# API с хешем
$ curl -sk 'https://.../setup-opencode/api/vk-info?vkh=914726ab82899...'
{"user_id":"52d4a58d-...","key_alias":"vkh_test2","team_id":null}

$ curl -sk 'https://.../setup-opencode/api/models?vkh=914726ab82899...' | jq '.resolved_via'
"vkh"
```
Test key `vkh_test2` (alias), `user_id=52d4a58d-...`, проверен через
`?vkh=` и удалён через `/key/delete`.

В UI на `/litellm/ui/api-keys/` (LiteLLM Admin) каждая строка получает
синюю кнопку «🛠 OpenCode» (или «📋 Copy VK» если hash тоже не найден).
Клик → новая вкладка `/setup-opencode/?vkh=<hash>` → opencode.json для
владельца ключа.

#### Без изменений
- `users-btn.js`, `glm-swap-btn.js` — поведение не изменилось.

---
### [2026-07-13l] — Добавлены MiniMax-M2.5 и Qwen3.5-plus в Team Agents

#### Контекст
Две новые LLM добавлены в LiteLLM proxy и в команду `Agents` (team_id `817d2234-ae12-4b46-8f29-0d47d0adbd7d`):

| model_name | upstream | provider | context | output | reasoning |
|------------|----------|----------|---------|--------|-----------|
| `MiniMax-M2.5` | `MiniMax-M2.5` | `custom_openai` (Minimax creds) | 200000 | 131072 | yes |
| `Qwen3.5-plus` | `dashscope/qwen3.5-plus` | `dashscope` (тот же cred что у `QWEN3.7-plus`) | 1000000 | 33000 | yes |

Обе модели **reasoning-capable** — `reasoning_content` приходит в ответе (проверено test-ключом через `/v1/chat/completions`).

#### Изменения

**DB (`LiteLLM_ProxyModelTable`)**: добавлены 2 записи, скопированы `litellm_params` из референсных моделей (`MiniMax-M3` и `QWEN3.7-plus`), только `model` поле заменено на новое upstream-имя. `model_info.db_model=true`, явные `max_input_tokens`/`max_tokens`/`supports_vision=false` проставлены руками.

**DB (`LiteLLM_TeamTable` `models` для Agents)**:
```python
['Kimi K2.6', 'Kimi K2.7', 'MiniMax-M2.5', 'MiniMax-M2.7', 'MiniMax-M3',
 'Qwen3.5-plus', 'deepseek-v4-pro', 'mimo-v2.5', 'mimo-v2.5-pro', 'seed-2.1']
```
Добавлены `MiniMax-M2.5` и `Qwen3.5-plus`, отсортировано по алфавиту.

**`/opt/opencode-setup/api.py`**: `_REASONING_CAPABLE` дополнен `"MiniMax-M2.5"` и `"Qwen3.5-plus"` (case-sensitive — реальное id модели `"Qwen3.5-plus"` с lowercase `w`).

#### Проверка
```bash
GET /setup-opencode/api/models?user=<member_of_Agents>
# для alexandrovych@gmail.com (d89af49f-f7c8-4ec8-937a-678a9c09eb9d):
# {"id":"MiniMax-M2.5","context":200000,"output":131072,"reasoning":true}
# {"id":"Qwen3.5-plus","context":1000000,"output":33000,"reasoning":true}
```
Test key: `MiniMax-M2.5` → "Hello there, friend!" + reasoning; `Qwen3.5-plus` → "Hi there friend." + reasoning.

**Не затронуты**: пользователи НЕ в команде Agents (например pavel@herocraft.com — только All Access) НЕ видят новые модели.

---
### [2026-07-13k] — gemini/gemini-3-pro-image теперь тоже image-to-image + лимиты

#### Контекст
Пользователь указал что `gemini-3-pro-image` (мультимодальная модель Google,
upstream `gemini-3-pro-image`, НЕ `imagen-4`) поддерживает image input
нативно. Проверено:

- native Gemini API `:generateContent` с multimodal input (text + image
  inline_data) → **работает** (status=200, image/jpeg, ~1MB)
- OpenAI-compat chat completions multimodal → 400 "Unhandled generated
  data mime type: image/jpeg"
- OpenAI-compat `/v1/images/edits` → 404 (Google не предоставляет)

Также пользователь отметил что нужно установить **реальные лимиты** для
всех image моделей.

#### Реальные лимиты (по Google docs)

| Модель | context | output |
|--|--|--|
| gpt-image-1.5 / 2 (OpenAI) | 1M | 131K (4 images × 32K tokens) |
| gemini-2.5-flash-image / 3-pro-image | 1M | 64K |
| gemini/gemini-3-pro-image | 65K | 32K |

Для gemini/gemini-3-pro-image пользователь явно попросил 65K/32K (вместо
1M/64K), что соответствует реальным upstream лимитам которые Gemini
возвращает через API даже если документация говорит 1M.

#### Изменения

**1. Переключение `gemini/gemini-3-pro-image` на native Gemini provider**

Раньше модель использовала upstream `imagen-4.0-ultra-generate-001`
через `custom_llm_provider='openai'`. Теперь — upstream
`gemini-3-pro-image` (мультимодальная модель, НЕ imagen) через native
provider:
```
api_base:    https://generativelanguage.googleapis.com/v1beta
model:       gemini-3-pro-image
custom_llm_provider: gemini
```

После переключения LiteLLM маршрутизирует:
- `/v1/images/generations` → `image_generation/transformation.py` →
  `generateContent` (text-to-image, responseModalities=[TEXT, IMAGE])
- `/v1/images/edits` → `image_edit/transformation.py` → `generateContent`
  с multimodal input (image-to-image)

**2. Обновлены `max_input_tokens` / `max_tokens` в `model_info`**

| Модель | Было | Стало |
|--|--|--|
| gemini/gemini-3.1-flash-image | 1048576 / 8192 | **1048576 / 65536** |
| gemini/gemini-3-pro-image | 1048576 / 8192 | **65536 / 32768** |

gpt-image-1.5 / 2 уже были 1048576 / 131072 (реальные).

**3. `gemini/gemini-3-pro-image` добавлен в `_IMAGE_EDIT_CAPABLE`**

```python
_IMAGE_EDIT_CAPABLE = {
    "gpt-image-1.5",
    "gpt-image-2",
    "gemini/gemini-3.1-flash-image",
    "gemini/gemini-3-pro-image",  # ← NEW
}
```

#### Тест (read-only verify)

```
gpt-image-1.5 /v1/images/edits         → 200 ✅
gpt-image-2 /v1/images/edits           → 200 ✅
gemini/gemini-3.1-flash-image edits    → 200 ✅
gemini/gemini-3-pro-image edits        → 200 ✅ (NEW — раньше 403 blocked или 404 openai-compat)
gemini/gemini-3-pro-image generations  → 200 ✅ (после provider switch)
```

API ответ (`/setup-opencode/api/models`) теперь показывает все 4 image
модели с `image=true, image_edit=true`, и актуальные лимиты.

#### Файлы
- `LiteLLM_ProxyModelTable` — `gemini/gemini-3-pro-image` provider
  переключён, `max_input_tokens`/`max_tokens` обновлены для двух gemini
  моделей
- `/opt/opencode-setup/api.py` — `.bak.g3pi-img-edit-20260713`,
  `_IMAGE_EDIT_CAPABLE` расширен
- Memory: `infra/image-models-image-edit-support` обновлена

---

### [2026-07-13j] — Image-to-image support через /v1/images/edits

#### Контекст
Все 4 image модели (`gpt-image-1.5`, `gpt-image-2`, `gemini/gemini-3.1-flash-image`,
`gemini/gemini-3-pro-image`) работали только для text-to-image
(`/v1/images/generations`). Проверена поддержка image-to-image (image editing):

- **gpt-image-1.5 / gpt-image-2** — нативная поддержка OpenAI `/v1/images/edits` ✅
- **gemini-2.5-flash-image / gemini-3.1-flash-image / gemini-3-pro-image** —
  через нативный Gemini API `:generateContent` с multimodal input (text +
  image inline_data) ✅, но **НЕ** через OpenAI-compat
  `/v1/images/edits` (Google возвращает 404 там)
- **imagen-4.0-ultra-generate-001** — **НЕ поддерживает** image editing
  (text-to-image only)

#### Изменения

**1. Переключение `gemini/gemini-3.1-flash-image` на native Gemini provider**

Раньше модель использовала `custom_llm_provider='openai'` (для
text-to-image через OpenAI-compat endpoint). Для image editing нужен
native провайдер, иначе LiteLLM маршрутизирует на OpenAI `/v1/images/edits`
которого у Google нет.

Параметры (зашифровано в `litellm_params`):
```
api_base:    https://generativelanguage.googleapis.com/v1beta   (было .../openai/)
model:       gemini-2.5-flash-image                             (было gemini/gemini-3.1-flash-image)
custom_llm_provider: gemini                                     (было openai)
```

После переключения LiteLLM использует
`litellm/llms/gemini/image_edit/transformation.py` который вызывает
`{base}/models/{model}:generateContent` с multimodal input.
Text-to-image продолжает работать через `image_generation/transformation.py`
(тот же `:generateContent` endpoint).

**2. `gemini/gemini-3-pro-image` оставлен только для text-to-image**

Поскольку upstream — `imagen-4.0-ultra-generate-001` — не поддерживает
image editing, а пользователь сказал "оставь его в версии text-to-image".
Модель НЕ добавлена в `_IMAGE_EDIT_CAPABLE` в `api.py`.
[Изменено в [2026-07-13k] — upstream изменён с imagen-4 на gemini-3-pro-image,
теперь тоже поддерживает image-to-image.]

**3. Обновлён `/opt/opencode-setup/api.py`**

- Возвращает поле `image` (`true` если `model_info.mode == 'image_generation'`)
- Возвращает поле `image_edit` (`true` только для моделей в `_IMAGE_EDIT_CAPABLE`)
- Hardcoded `_IMAGE_EDIT_CAPABLE = {"gpt-image-1.5", "gpt-image-2", "gemini/gemini-3.1-flash-image", "gemini/gemini-3-pro-image"}`
- Алиасит `gemini-3.1-flash-image` → `gemini/gemini-3.1-flash-image` (LiteLLM
  возвращает team.models без префикса провайдера, нужно резолвить обратно)
- Фильтрует `blocked` модели из выдачи

**4. Обновлён `/opt/opencode-setup/index.html`**

- В `summary` показывает количество image моделей и список image-edit моделей:
  `🎨 4 моделей для генерации картинок (4 также поддерживают image-to-image:
  gpt-image-1.5, gpt-image-2, gemini/gemini-3.1-flash-image, gemini/gemini-3-pro-image)`
- Добавлена секция "🎨 Генерация картинок" с инструкцией про лимиты (25/день
  обычным, 50 AllAccess) и размер по умолчанию 1024x1024
- Image модели получают `modalities.output: ["image"]` в opencode.json

#### Тест (read-only verify)
```
gpt-image-1.5 /v1/images/edits           → 200 OK, b64_json
gpt-image-2 /v1/images/edits             → 200 OK, b64_json
gemini/gemini-3.1-flash-image edits      → 200 OK, b64_json (через native Gemini API)
gemini/gemini-3-pro-image generations    → 200 OK, b64_json (imagen-4 работает для text-to-image)
```

#### Файлы
- `/opt/opencode-setup/api.py` — deployed (`.bak.img-edit-20260713`, `.bak.img-edit-v2-20260713`, `.bak.g3pi-img-edit-20260713`)
- `/opt/opencode-setup/index.html` — deployed (`.bak.img-edit-20260713`)
- `LiteLLM_ProxyModelTable` — `gemini/gemini-3.1-flash-image` параметры обновлены

---

### [2026-07-13i] — Image models: ключи, новые nanobanana, лимиты, defaults

#### Контекст
Image-модели проксировались на LiteLLM, но имели проблемы:
- `gpt-image-1.5` / `gpt-image-2` использовали устаревшие OpenAI API ключи
- Не было моделей "nanobanana" (Google Gemini 2.5 Flash Image / Imagen 4)
- Лимиты в `image_team_limits` были 30/day для всех кроме All Access
  (50/day), без разделения на обычных пользователей
- Не было default `size` для новых gemini моделей (использовался upstream default)

#### Изменения

**1. Обновлены OpenAI ключи для gpt-image-1.5 и gpt-image-2**

Оба используют новый общий ключ
`<openai-image-key>`.

**2. Созданы 2 модели nanobanana (Google)**

| model_name | upstream | provider | mode |
|--|--|--|--|
| `gemini/gemini-3.1-flash-image` | `gemini-2.5-flash-image` | `gemini` (native) | image_generation |
| `gemini/gemini-3-pro-image` | `imagen-4.0-ultra-generate-001` | `openai` (compat) | image_generation |

Обе используют ключ `<google-ai-studio-key>`
(Google AI Studio OpenAI-compat endpoint
`https://generativelanguage.googleapis.com/v1beta/openai/`).
LiteLLM автоматически подставляет `supports_vision: true` для image_generation
моделей.

**3. Лимиты в `image_team_limits`**

| team_alias | Было | Стало |
|--|--|--|
| All Access | 50 | **50** |
| Art, CreativeTeam, Analytics, Coders, PirateShips, Porters, SideCoders, default, _default | 30 | **25** |

`/v1/images/generations` для всех 4 моделей (gpt-image-1.5/2 + gemini/*) под
одним счётчиком на user×model. Hook `image_rate_limit_hook.py` имеет TTL
кэш 60s — изменения подхватываются автоматически.

**4. Default `size: 1024x1024` для всех image моделей**

LiteLLM использует значение из `litellm_params.size` если клиент не передал
явно. Для gpt-image-* размер уже стоял. Для gemini моделей — добавлен
(зашифрован). Размер клиентом переопределяется если модель поддерживает.

#### Тест (read-only verify)
```
gpt-image-1.5 /v1/images/generations (no size) → 200, size='1024x1024' ✅
gpt-image-2   /v1/images/generations (no size) → 200, size='1024x1024' ✅
gemini/gemini-3.1-flash-image /v1/images/generations (no size) → 200, data_len=1 ✅
gemini/gemini-3-pro-image   /v1/images/generations (no size) → 200, data_len=1 ✅
```

#### Файлы
- `LiteLLM_ProxyModelTable` — `gpt-image-1.5`, `gpt-image-2` `api_key` обновлены;
  `gemini/gemini-3.1-flash-image`, `gemini/gemini-3-pro-image` созданы
- `image_team_limits` — `daily_limit` изменён для 9 строк
- Memory: `infra/image-models-image-edit-support` создана

---

### [2026-07-13g] — GLM Swap UI под whitelist

#### Контекст
Страница `/litellm/admin/glm-swap/` (GLM swap credentials tool) была
доступна с любого IP после того как `[2026-07-13e]` снял whitelist с admin
endpoints. Эта страница позволяет переключать credentials между основной
GLM и atlas_ вариантами — функция чувствительная (меняет API key для всех
запросов команды).

#### Фикс
Добавлен `allow <IP>; ... deny all;` блок в `location ^~ /litellm/admin/glm-swap/`
в `/etc/nginx/sites-available/litellm-bifrost`:

```nginx
location ^~ /litellm/admin/glm-swap/ {
    allow 89.19.213.124;
    allow 162.55.180.78;
    allow 127.0.0.1;
    allow 10.10.10.1;
    deny all;

    proxy_pass http://litellm_upstream/litellm/admin/glm-swap/;
    ...
}
```

Теперь страница доступна только с 4 IP whitelist'а (как `/litellm/key/generate`
и другие write operations). CN-block от `[2026-07-13f]` остаётся активным — для
CN IP возвращается 403 раньше чем проверяется whitelist.

#### Файлы
- `/etc/nginx/sites-available/litellm-bifrost` — патч
- `/etc/nginx/sites-available/litellm-bifrost.bak_<TS>` — backup

---

### [2026-07-13f] — Блокировка запросов из Китая (nginx geo + 8801 CN CIDR)

#### Контекст
По запросу добавлена блокировка всего входящего трафика из Китая на уровне
nginx. LiteLLM proxy использовался как open endpoint через `hcbifrost.herocraft.com`
без geo-ограничений, что потенциально открывало его для abuse из CN-сетей.

#### Реализация
nginx `geo` модуль + `map` + `if` блок:

**1. `/etc/nginx/conf.d/cn-block.conf`** — новый файл с 8801 CIDR:
```
geo $cn_block {
    default 0;
    1.0.1.0/24 1;
    1.0.2.0/23 1;
    ... (8801 entries всего)
}

map $cn_block $is_cn {
    1 yes;
    0 no;
}
```
Источник: https://www.ipdeny.com/ipblocks/data/countries/cn.zone (8801 IPv4 CIDR).

**2. `/etc/nginx/sites-available/litellm-bifrost`** — добавлено в server block
`hcbifrost.herocraft.com` (строки 16-20):
```nginx
    server_name hcbifrost.herocraft.com;

    # Block China IP ranges (8801 CIDRs from ipdeny.com)
    if ($is_cn = yes) {
        return 403;
    }
```

`if` срабатывает ДО всех `location` блоков, поэтому:
- Auth endpoints → 403 (CN клиенты даже не дойдут до login)
- Inference endpoints (`/litellm/v1/chat/completions`) → 403
- Setup pages (`/setup-opencode`) → 403
- Litellm UI → 403

#### Что НЕ блокируется
- Домен `default_server` на порту 8080 (строки 504+) — этот блок не имеет
  `server_name hcbifrost.herocraft.com` и не получил инъекцию. Это нормально,
  он обрабатывает fallback-запросы (catch-all), которые nginx сам редиректит
  на основной server.

#### Важные нюансы
1. **Локальная разработка и тесты** работают нормально: `127.0.0.1` (localhost)
   не в CN-списке, IP whitelist'а (`89.19.213.124`, `162.55.180.78`,
   `10.10.10.1`) тоже не в CN.
2. **CDN/Cloudflare IP** не в CN-списке — если ты включаешь CDN, ничего не сломается.
3. **Списки CN IP меняются** — рекомендуется ежемесячно обновлять:
   ```bash
   curl -s https://www.ipdeny.com/ipblocks/data/countries/cn.zone \
       | sed 's/^/    /; s/$/ 1;/' > /tmp/cn-cidrs-formatted.txt
   ```
   Затем вставить в `cn-block.conf` и `nginx -s reload`.
4. **ipv6 не покрыт** — `ipdeny.com/cn.zone` содержит только IPv4. Если нужен
   IPv6 — есть отдельные списки (например от RIPE/Apnic).

#### Тест
Локально работает (127.0.0.1 не в CN). Для верификации что CN IP реально
блокируется, нужен запрос с настоящего CN IP. Логика проверена через временное
добавление IP VM (`162.55.137.149`) в block-list: nginx reload успешен,
конфиг валиден.

#### Файлы
- `/etc/nginx/conf.d/cn-block.conf` — новый файл (198KB, 8801 CIDRs)
- `/etc/nginx/sites-available/litellm-bifrost` — патч (добавлен `if ($is_cn)`)
- `/etc/nginx/sites-available/litellm-bifrost.bak_<TS>` — backup

#### Обновление memory
- `.serena/memories/infra/hcbifrost-vm-litellm.md` — добавить пункт про
  CN block list и команду обновления

---

### [2026-07-13e] — Открыты read-only API для UI логов/usage

#### Контекст
После `[2026-07-13d]` пользователи могли зайти на UI, но страницы
`/litellm/ui/logs/` и `/litellm/ui/usage/` показывали пустые данные или
ошибки загрузки. Причина: страница UI рендерится (она под открытым
`/litellm/ui`), но JS-бандл делает fetch на API endpoints типа
`/litellm/global/spend` и `/litellm/spend/logs` — а эти endpoints всё
ещё были под nginx whitelist → 403.

#### Фикс
Снял `allow <IP>; ... deny all;` с **10 read-only endpoints**:

| Endpoint | Назначение для UI |
|---|---|
| `/litellm/global/spend` | Usage page — общий spend |
| `/litellm/spend/logs` | Logs page — детальные логи запросов |
| `/litellm/team` | Список команд (для фильтров) |
| `/litellm/user` | Список пользователей |
| `/litellm/credentials` | Список credentials |
| `/litellm/key/list` | Список API keys |
| `/litellm/key/info` | Инфо о ключе |
| `/litellm/model/info` | Список моделей (legacy endpoint) |
| `/litellm/v1/model/info` | Список моделей (v1 API) |
| `/litellm/v2/model/info` | Список моделей (v2 API) |

#### Под whitelist остались (11 endpoints) — write операции
| Endpoint | Назначение |
|---|---|
| `/litellm/key/generate` | Создание ключей |
| `/litellm/key/update` | Обновление ключей |
| `/litellm/key/delete` | Удаление ключей |
| `/litellm/model/new` | Создание моделей |
| `/litellm/model/update` | Изменение моделей |
| `/litellm/model/delete` | Удаление моделей |
| `/litellm/organization` | Управление org |
| `/litellm/sso` | SSO config |
| `/litellm/guardrails` | Настройка guardrails |
| `/litellm/config/list` | Чтение конфига |
| `/litellm/get/config` | Чтение конфига |

Эти операции критичны — изменение ключей/моделей может положить прокси.
Доступны только с 4 IP whitelist'а (`89.19.213.124`, `162.55.180.78`,
`127.0.0.1`, `10.10.10.1`).

#### Проверка
```
nginx -t → syntax ok
nginx -s reload → ok

Тест API после изменений (с localhost):
401  /litellm/global/spend    ← auth required (не 403!)
401  /litellm/spend/logs      ← auth required
401  /litellm/team/list       ← auth required
401  /litellm/model/info      ← auth required
401  /litellm/key/list        ← auth required
401  /litellm/user/list       ← auth required
401  /litellm/credentials    ← auth required

Write ops (остаются под whitelist):
405  /litellm/key/generate    ← GET не поддерживается, nginx пропустил (local)
405  /litellm/model/new       ← то же
```

401 — значит nginx пропустил запрос до LiteLLM, который уже проверяет
auth (Bearer token или session). **Не 403** — что было бы если бы nginx
блокировал по IP.

#### Файлы
- `/etc/nginx/sites-available/litellm-bifrost` — патч (21 → 11 deny blocks)
- `/etc/nginx/sites-available/litellm-bifrost.bak_<TS>` — backup

---

### [2026-07-13d] — Открыт UI (`/litellm/ui/*`, `/litellm/v2/login`) для всех IP

#### Контекст
Ранее nginx whitelist'ом были защищены 24 endpoint'а (admin UI, keys,
teams, models, config, spend, logs). Это значило, что пользователи не
могли залогиниться в LiteLLM UI и посмотреть свои логи/usage по адресам:

- https://hcbifrost.herocraft.com/litellm/ui/logs/
- https://hcbifrost.herocraft.com/litellm/ui/usage/

LiteLLM proxy уже имеет нормальный auth (email + password через
`/litellm/v2/login`), но nginx 403'ил все запросы до того как LiteLLM
мог проверить креды.

#### Фикс
Снял `allow <IP>; ... deny all;` блоки **только** с 3 read-only endpoints:

| Endpoint | Было | Стало |
|---|---|---|
| `location ^~ /litellm/ui` | whitelist | открыт |
| `location ^~ /litellm/ui/login` | whitelist | открыт |
| `location ^~ /litellm/v2/login` | whitelist | открыт |

Остальные **21 endpoint остались под whitelist**:
- `/litellm/key/{generate,update,delete,info,list}`
- `/litellm/model/{new,update,delete,info}`
- `/litellm/team`, `/litellm/user`, `/litellm/organization`
- `/litellm/credentials`, `/litellm/sso`, `/litellm/guardrails`
- `/litellm/config/list`, `/litellm/get/config`
- `/litellm/global/spend`, `/litellm/spend/logs`
- `/litellm/v1/model/info`, `/litellm/v2/model/info`

Пользователи могут теперь:
1. Открыть `https://hcbifrost.herocraft.com/litellm/ui/` → редирект на login
2. Залогиниться своим email + паролем (LiteLLM auth)
3. Видеть свои логи в `/litellm/ui/logs/` и usage в `/litellm/ui/usage/`

Но НЕ могут через nginx (даже с авторизованной сессией):
- Создавать/удалять ключи, модели, команды
- Менять конфиг прокси

Эти операции по-прежнему доступны только с 4 IP из whitelist
(`89.19.213.124`, `162.55.180.78`, `127.0.0.1`, `10.10.10.1`).

#### Безопасность
- Пароли пользователей — главная защита для UI-доступа. Если пользователь
  скомпрометирован — он получит только просмотр своих данных (logs/usage
  фильтруются по командам), не сможет ничего удалить/изменить.
- Если нужен больший isolation — добавить `IP allow` для конкретного
  пользователя через nginx `geo` модуль + map. Но это ужесточение
  противоречит требованию "доступ с любого IP".

#### Файлы
- `/etc/nginx/sites-available/litellm-bifrost` — патч
- `/etc/nginx/sites-available/litellm-bifrost.bak_<TS>` — backup

---

### [2026-07-13c] — Ротация ZAI_API_KEY → починка MCP серверов (auth failed)

#### Контекст
Все 3 Z.AI MCP сервера (`zai_web_search`, `zai_web_reader`, `zai_zread`) перестали
инициализироваться при старте LiteLLM. В логах:

```
WARNING: MCP client list_tools was cancelled
WARNING: mcp_server_manager.py:2820 - Timeout while listing tools from zai_web_search
pydantic_core._pydantic_core.ValidationError: 11 validation errors for JSONRPCMessage
JSONRPCRequest.method: Field required [type=missing,
  input_value={'code': 1000, 'msg': 'Au...iled', 'success': False}, ...]
```

Причина оказалась не в transport/timeout — Z.AI возвращал auth-failed JSON
(`{'code': 1000, 'msg': 'Auth failed', 'success': False}`) вместо JSON-RPC,
из-за чего MCP pydantic-валидатор падал с 11 "Field required". LiteLLM потом
помечал сервер как timed out (т.к. вместо JSON-RPC error возвращалась
validation error).

Старый ключ `7f91d79118364c08ba97f45822b658f1.PHRJiZMfNekluFQz` был отозван
на стороне Z.AI после ротации ключей.

#### Диагностика (кратко)
1. `curl` вручную с новым ключом → JSON-RPC `initialize` работает
2. `curl` вручную со старым ключом → JSON `{'code':1000,'msg':'Auth failed'}`
3. LiteLLM контейнер всё ещё держал старый ключ в env (`docker exec ... printenv`)

#### Корневая причина
`docker restart` **не пере-читает** `--env-file`. Env vars в docker контейнере
фиксируются при `docker run` и не обновляются при restart. Для применения
нового значения из `.env` нужен полный recreate через `docker rm -f` + `docker run`
(в нашем случае — `bash /opt/litellm/start.sh`, который именно это и делает).

#### Фикс
1. Обновлён `ZAI_API_KEY` в `/opt/litellm/.env`:
   `<glm-key-B>` (новый)
2. Полный recreate litellm контейнера через `start.sh`
3. После пересоздания: `docker exec litellm printenv ZAI_API_KEY` → новый ключ
4. MCP servers (`zai_web_search`, `zai_web_reader`, `zai_zread`) инициализируются
   без ошибок, list_tools больше не падает

#### Предыдущие (отменённые) попытки во время отладки
- `transport: "http"` → `"sse"` (пробовали). Не помогло — SSE transport
  ожидает другой формат на старте сессии. Reverted.
- Env vars `LITELLM_MCP_TOOL_LISTING_TIMEOUT=60`, `LITELLM_MCP_CLIENT_TIMEOUT=120`,
  `LITELLM_MCP_HEALTH_CHECK_TIMEOUT=30` — добавлены в `.env` "на всякий случай".
  Reverted: корневая причина оказалась в auth, не в timeout.

#### Урок
**`docker restart` ≠ reload env file.** Для контейнеров со `--env-file`:
- `docker restart` — перезапускает процесс в том же контейнере (env не меняется)
- `docker rm -f` + `docker run` — создаёт новый контейнер с актуальным env

В `/opt/litellm/start.sh` уже правильно делает `docker rm -f litellm` + `docker run`.
`docker restart litellm` — использовать только когда env vars точно не менялись.

#### Файлы
- `/opt/litellm/.env` — `ZAI_API_KEY` обновлён
- `/opt/litellm/config.yaml` — без изменений (transport: "http" для всех Z.AI MCP)
- `/opt/litellm/.env.bak_keyswap_<TS>` — backup до ротации

#### Memory
- `.serena/memories/infra/hcbifrost-vm-litellm.md` — добавить: "Для применения
  новых env vars в LiteLLM контейнере всегда использовать `bash /opt/litellm/start.sh`
  (который делает `docker rm -f` + `docker run`), не `docker restart`"

---

### [2026-07-13b] — Kimi empty assistant message filter (HTTP 400 fix)

#### Контекст
Пользователь сообщил об ошибке при работе с Kimi K2.6 через LiteLLM:
```
litellm.BadRequestError: MoonshotException - the message at position 106
with role 'assistant' must not be empty. Received Model Group=Kimi K2.6
```

Moonshot API строже OpenAI — отвергает conversation history с
assistant messages у которых `content` пустой (`null`/`""`/`[]`) и нет
`tool_calls`. Возникает после compaction или в длинных tool-call циклах,
где промежуточные assistant turn'ы остаются без текста.

#### Фикс
Расширил `/opt/litellm/user_agent_hook.py` (уже отвечал за UA injection +
first-chunk merge для reasoning):

1. Новый tuple `_KIMI_EMPTY_MSG_MODELS = ("Kimi K2.6", "Kimi K2.7")`
2. Helper `_is_empty_assistant(msg)` — True если `role=assistant`,
   `content` пустой (`None`/`""`/`[]`) и нет `tool_calls`
3. Helper `_sanitize_kimi_messages(data, model)` — для Kimi моделей
   сканирует `data["messages"]` и заменяет пустой content на `" "`
   (один пробел — Moonshot принимает whitespace-only строки)
4. Вызов из **обоих** хуков:
   - `async_pre_call_hook` — на входе в прокси
   - `async_pre_call_deployment_hook` — backup, после fallback routing

Сообщения с `tool_calls` не трогаются (Kimi принимает tool-call cycles
без content).

#### Тест
Запрос с искусственной пустой assistant message:
```json
{
    "model": "Kimi K2.6",
    "messages": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "say ok"}
    ]
}
```
До фикса → HTTP 400. После фикса → HTTP 200 с reasoning_content.

В логе загруженный hook печатает:
```
user_agent_hook: module loaded, ... kimi_empty_msg_models=('Kimi K2.6', 'Kimi K2.7') ...
```

#### Файлы
- `/opt/litellm/user_agent_hook.py` — патч (11279 → ~13500 байт)
- `/opt/litellm/user_agent_hook.py.bak_20260713_013756` — backup до патча
- `docker restart litellm` — новый hook подхвачен (volume-mounted)

#### Memory
- `.serena/memories/infra/hcbifrost-vm-litellm.md` — добавлена секция
  "Kimi empty assistant message filter (2026-07-13)"

---

### [2026-07-13] — Лимиты GLM: 5.1=204800, 5.2=1M + emergency fix `max_input_tokens` в `litellm_params`

#### Контекст
Пользователь заметил, что у GLM-5.2 в opencode-setup контекст показан 204800,
хотя должен быть 1M (1048576). При проверке выяснилось:
- GLM-5.1 (все 6): 204800 ✓ (как установлено вчера в `[2026-07-12b]`)
- GLM-5.2 main + `_dont_use`: **204800** ❌ (упали с прошлого скрипта)
- GLM-5.2 (res) + atlas: 1048576 (нативный 1M)

Пользователь подтвердил правильные лимиты: **GLM-5.1 = 204800**, **GLM-5.2 = 1048576**.

#### Первая (битая) попытка фикса
SQL `jsonb_set` в **обоих** `litellm_params` AND `model_info` для всех 12 GLM моделей:
```sql
UPDATE "LiteLLM_ProxyModelTable"
SET litellm_params = COALESCE(
        jsonb_set(litellm_params, '{max_input_tokens}', '...'::jsonb, true), ...),
    model_info     = COALESCE(
        jsonb_set(model_info, '{max_input_tokens}', '...'::jsonb, true), ...)
WHERE model_name ILIKE '%glm-5.1%' OR model_name ILIKE '%glm-5.2%'
   OR model_name ILIKE '%atlas%glm-5.%';
```

**Это была критическая ошибка.** `litellm_params` — это не metadata, а **kwargs
словарь, который LiteLLM передаёт в `AsyncCompletions.create()` как именованные
аргументы. Поле `max_input_tokens` не распознаётся OpenAI client, и каждый
запрос к затронутой модели падал с:

```
TypeError: AsyncCompletions.create() got an unexpected keyword argument 'max_input_tokens'
InternalServerError: Custom_openaiException - ... Received Model Group=GLM-5.2
POST /v1/chat/completions HTTP/1.1" 500 Internal Server Error
```

Пользователь увидел `internal server error` на всех GLM-5.1 и GLM-5.2.

#### Emergency fix
```sql
UPDATE "LiteLLM_ProxyModelTable"
SET litellm_params = litellm_params - 'max_input_tokens'
WHERE litellm_params ? 'max_input_tokens';
```
Удалило `max_input_tokens` из `litellm_params` у всех затронутых строк
(не только GLM — для безопасности). `max_input_tokens` остался **только** в
`model_info`, где LiteLLM ожидает metadata.

`docker restart litellm` + проверка: GLM-5.1 и GLM-5.2 отвечают `200 OK`
с `reasoning_content` в ответе.

#### Финальное состояние
| Model | `model_info.max_input_tokens` | `litellm_params.max_input_tokens` |
|---|---|---|
| GLM-5.1 (все 6) | 204800 | (удалён) |
| GLM-5.2 (все 6) | 1048576 | (удалён) |

После рестарта opencode-api (PID 232540, 01:01:27 EET) `/models?user=...`
корректно отдаёт:
- GLM-5.1 (res): context=204800 ✓
- GLM-5.2 (res): context=1048576 ✓

#### Урок
**`litellm_params` ≠ metadata-хранилище.** Это kwargs для upstream client.
Поля вроде `max_input_tokens`, `max_output_tokens`, `thinking`, `custom_llm_provider`
по-разному обрабатываются: некоторые валидны в `litellm_params` (как `thinking`,
`custom_llm_provider`, `model`, `api_base`, `headers`), а некоторые
(`max_input_tokens`) — **только** в `model_info`. Перед SQL-правкой конфигурации
проверять: вытащить существующую строку через `SELECT litellm_params` и
сверить ключи с теми что LiteLLM реально использует.

Альтернатива — пользоваться `/model/update` API, который валидирует pydantic-схему
и отверг бы этот `max_input_tokens` в `litellm_params` ещё на входе.

#### Memory
- `.serena/memories/infra/hcbifrost-vm-litellm.md` — добавлен пункт:
  `litellm_params` это kwargs для upstream client, не metadata.
  `max_input_tokens` живёт ТОЛЬКО в `model_info`.

---

### [2026-07-12b] — GLM-5.1 группа: `max_input_tokens=204800` для всех вариантов

#### Контекст
Пользователь запросил унификацию `max_input_tokens` для всех 6 моделей группы
GLM-5.1: основная пара `GLM-5.1`/`GLM-5.1_dont_use`, `(res)` варианты
`GLM-5.1 (res)`/`GLM-5.1 (res)_dont_use`, и atlas-варианты
`atlas_glm-5.1`/`atlas_glm-5.1_dont_use`. До этой правки только 2 модели
(main + dont_use) имели 204800, остальные 4 имели 1048576 (1M).

#### Что было (до правки)
| Model | lp_max_in | mi_max_in | mi_max_out |
|---|---|---|---|
| GLM-5.1 | **204800** ✓ | 204800 | 131072 |
| GLM-5.1 (res) | — | 1048576 | 131072 |
| GLM-5.1 (res)_dont_use | — | 1048576 | 131072 |
| GLM-5.1_dont_use | **204800** ✓ | 204800 | 131072 |
| atlas_glm-5.1 | — | 1048576 | 131072 |
| atlas_glm-5.1_dont_use | — | 1048576 | 131072 |

#### SQL правка
```sql
BEGIN;
UPDATE "LiteLLM_ProxyModelTable"
SET litellm_params = jsonb_set(litellm_params, '{max_input_tokens}', '204800'::jsonb),
    model_info     = jsonb_set(model_info,     '{max_input_tokens}', '204800'::jsonb)
WHERE (model_name ILIKE '%glm-5.1%' OR model_name ILIKE '%atlas%glm-5.1%')
  AND (litellm_params::json->>'max_input_tokens' IS DISTINCT FROM '204800'
    OR  model_info::json->>'max_input_tokens'     IS DISTINCT FROM '204800');
COMMIT;
```
`UPDATE 6` — 6 рядов затронуты.

#### Состояние после правки
| Model | lp_max_in | mi_max_in | mi_max_out |
|---|---|---|---|
| GLM-5.1 | **204800** | **204800** | 131072 |
| GLM-5.1 (res) | **204800** | **204800** | 131072 |
| GLM-5.1 (res)_dont_use | **204800** | **204800** | 131072 |
| GLM-5.1_dont_use | **204800** | **204800** | 131072 |
| atlas_glm-5.1 | **204800** | **204800** | 131072 |
| atlas_glm-5.1_dont_use | **204800** | **204800** | 131072 |

#### opencode-setup cache flush
После SQL правки кнопка OpenCode в LiteLLM UI всё ещё показывала старые
значения (`1048576` для `(res)`/`atlas`). Это известная проблема — `/opt/opencode-setup/api.py`
кеширует `_MODEL_INFO` в module global и перечитывает только при рестарте.

**Решение** — kill процесса, systemd auto-restart:
```
PID 230757 (старая инстанция с устаревшим кэшем) → kill → PID 231453 (свежая)
curl http://127.0.0.1:9000/health → {"ok": true}
curl http://127.0.0.1:9000/models?user=<UUID> → все GLM-5.1 возвращают context=204800
```

#### Файлы
- DB: `LiteLLM_ProxyModelTable` — 6 UPDATE'ов (atomic transaction)
- systemd unit: `/etc/systemd/system/opencode-api.service` (PID перезапущен)
- Никаких Go/TS/Python файлов не меняли (только DB state + перезапуск сервиса)

#### Известное ограничение
Этот же баг с устаревшим кэшем `api.py` повторится при любом будущем изменении
`model_info.max_input_tokens` / `max_tokens` / `max_output_tokens` через SQL —
придётся вручную убивать процесс opencode-api. Альтернативы:
- Добавить TTL к `_MODEL_INFO` (например 60 сек) в `api.py`
- Убрать кэш совсем (каждый запрос → `GET /model/info` через LiteLLM, +10-50ms latency)
- Патчить `api.py` чтобы он читал `max_output_tokens OR max_tokens` (для совместимости с
  моделями созданными через `/model/new` в разных версиях LiteLLM)

#### Memory
- `.serena/memories/infra/hcbifrost-vm-litellm.md` — без изменений (эта секция
  касается только GLM-5.1 group, не требует новой memory)

---

### [2026-07-12] — Новые модели (seed-2.1, deepseek-v4-pro) + ротация ключей 9 провайдеров + GLM-swap fix

#### Контекст
- Добавлены 2 новые модели: `seed-2.1` (Doubao Seed 2.1 Pro 260628, через Atlas Cloud)
  и `deepseek-v4-pro` (через Atlas Cloud)
- Скриптом предыдущего дня **затёрты** 14-26 моделей у каждой из 9 команд —
  команда `append` сделала `replace`. Частично восстановлено из spend logs
  + один `find_pattern` из docker logs → дефолтные группы + 2 новых для всех
- Фикс бага в GLM swap tool (`swap_names=True` default ломал model_name routing у opencode)
- Rotation + новые ключи для 9 провайдеров (отдельные записи в памяти)
- `db_model: true` забыли проставить — LiteLLM не загружал модели из БД (повторение бага)
- opencode-setup API читал `max_tokens`, а не `max_output_tokens` — нужна вторая сущность

#### 1. Ротация API ключей 9 провайдеров (2026-07-10)
Все `LiteLLM_ProxyModelTable` обновлены inline `api_key` (или `litellm_credential_name`
для credential-based моделей) на новые ключи:

| # | Провайдер | Новый ключ |
|---|---|---|
| 1 | Z.AI (GLM main) | inline updated, `.env ZAI_API_KEY=...` |
| 2 | Z.AI (GLM res) | inline updated (в `GLM-5.1 (res)`/`GLM-5.2 (res)`) |
| 3 | Atlas Cloud | inline updated (в `atlas_glm-5.1/5.2`, `seed-2.1`, `deepseek-v4-pro`) |
| 4 | Kimi (Moonshot) | CredentialTable |
| 5 | MiniMax | CredentialTable |
| 6 | DashScope (Qwen) | CredentialTable |
| 7 | OpenAI | **SKIPPED by user** — старый ключ остался |
| 8 | Gemini | **DISABLED by user** — 2 модели удалены |
| 9 | Xiaomi MiMo | inline updated |

Безопасные:
- Никаких бекапов / дампов / .new_secrets_* не осталось на VM или local temp
- ⚠ **OpenAI ключ старый (sk-проекта) — ротация под ответственностью пользователя**

#### 2. Новые модели (2026-07-12)

| Public name | Upstream | api_base | Контекст | Vision |
|---|---|---|---|---|
| `seed-2.1` | `openai/bytedance/doubao-seed-2.1-pro-260628` | `https://api.atlascloud.ai/v1` | 262144 in / 262144 out | ✓ |
| `deepseek-v4-pro` | `openai/deepseek-ai/deepseek-v4-pro` | `https://api.atlascloud.ai/v1` | 1048576 in / 393216 out | ✗ |

Созданы через `POST /model/new` с:
```json
{
  "model_name": "seed-2.1",
  "litellm_params": {
    "model": "openai/bytedance/doubao-seed-2.1-pro-260628",
    "api_base": "https://api.atlascloud.ai/v1",
    "api_key": "<atlas key>",
    "custom_llm_provider": "openai",
    "max_retries": "0"
  },
  "model_info": {
    "mode": "chat",
    "max_input_tokens": 262144,
    "max_output_tokens": 262144,
    "supports_function_calling": true,
    "supports_tool_choice": true,
    "supports_vision": true,
    "supports_response_schema": true
  }
}
```

⚠ **TWO BUGS пойманы при первой попытке**:
1. Без `custom_llm_provider: "openai"` LiteLLM не может определить provider
   (формат `bytedance/doubao-seed-...` НЕ валидный, LiteLLM его не распознаёт)
2. Без `model_info.db_model: true` LiteLLM загружает модели только при `POST /model/new`,
   но при следующем рестарте НЕ подхватывает их из БД — `/model/info` не показывает,
   `/v1/models` их не выдаёт. Поведение — silent (только в логах при загрузке)

**Ручной фикс** (быстрый, пока проверяли):
```sql
UPDATE "LiteLLM_ProxyModelTable"
SET model_info = jsonb_set(
  CASE WHEN model_info ? 'db_model' THEN model_info
       ELSE model_info || '{"db_model": false}'::jsonb END,
  '{db_model}', 'true'::jsonb
) || '{
  "mode": "chat",
  "supports_function_calling": true,
  "supports_tool_choice": true,
  "supports_response_schema": true,
  "blocked": false,
  "direct_access": true
}'::jsonb
WHERE model_name IN ('seed-2.1', 'deepseek-v4-pro');
```
Followed by `docker restart litellm`.

**✅ Verification**: обе модели вернули 200 OK в тестах:
- `seed-2.1`: "Hi there friend!"
- `deepseek-v4-pro`: "Hello, good day!"

#### 3. Team model assignments (attempted restore, then correct — 2026-07-12)

**Что произошло**: первый скрипт предыдущего дня затирал team models вместо append (баг в python парсинге `/team/info` — `d[0]` assumption, тогда как lite вернул `{"data":[...]}`).

**Восстановление** (12 команд `[]` → заново заполнено `POST /team/update`):

| Команда | Кол-во | Состав |
|---|---|---|
| **Agents** | 8 | Kimi K2.7/2.6, MiniMax-M3/M2.7, mimo-v2.5/pro + seed, deepseek |
| **All Access** | 16 | GLM-5.1/5.2, Kimi K2.6/2.7, MiniMax-M2.7/M3, QWEN3.7-plus, mimo-v2.5/pro, GLM-5.1 (res), gpt-image-1.5/2, gemini-3-pro/flash-image + seed, deepseek |
| **Analytics** | 11 | 9 standard models + seed, deepseek |
| **Art** | 9 | Kimi K2.7/2.6, MiniMax-M3/M2.7, mimo-v2.5/pro, QWEN3.7-plus + seed, deepseek |
| **Coders** | 11 | 9 standard models + seed, deepseek |
| **CreativeTeam** | 11 | Kimi K2.7/2.6, MiniMax-M3/M2.7, QWEN3.7-plus, GLM-5.1/5.2, mimo-v2.5/pro + seed, deepseek |
| **PirateShips** | 11 | 9 standard models + seed, deepseek |
| **Porters** | 11 | 9 standard models + seed, deepseek |
| **SideCoders** | 11 | 9 standard models + seed, deepseek |

**9 стандартных моделей** (предположение из логов LiteLLM + spend logs):
```
Kimi K2.7, Kimi K2.6, MiniMax-M3, MiniMax-M2.7,
QWEN3.7-plus, mimo-v2.5-pro, mimo-v2.5,
GLM-5.2 (res), GLM-5.1 (res)
```
(Полные оригиналы НЕ восстановлены — нет audit table, dead tuples mostly vacuumed.

**Секрет восстановления**: только 1 команда из 9-моdel'ной группы имела dead tuple
с оригинальным составом от 17:36 из docker logs:
```
"team not allowed to access model. This team can only access
models=['Kimi K2.7', 'Kimi K2.6', 'MiniMax-M3', 'MiniMax-M2.7',
'QWEN3.7-plus', 'mimo-v2.5-pro', 'mimo-v2.5', 'GLM-5.2 (res)',
'GLM-5.1 (res)']. Tried to access Minimax-M3"
```
+ spend logs показал какие команды какие модели активно использовали.
Комбинация позволила достаточно точно реконструировать для большинства команд.

#### 4. GLM swap tool fix — `swap_names=False` default (2026-07-12)

**Проблема** (обнаружена при первом swap'е atlas → GLM-5.2):
`swap_glm_credentials.py.swap()` был сделан с дефолтом `swap_names_=True`,
который дополнительно меняет `model_name` между src и dst рядами. Это
**ломало opencode/клиентские конфиги** — публичный модельный алиас
(GLM-5.2 / atlas_glm-5.2) уезжал с одного ряда на другой, virtual keys
начинали давать `team not allowed to access model`. Побочно терялся
backup key (dst row overwrites оригинальный api_key src'а → shadow becomes a copy of active).

**Решение** — `swap_names_=False` default в двух местах:

1. **`/opt/litellm/swap_glm_credentials.py`** — функция `swap()`:
   ```python
   def swap(
       from_name: str,
       to_name: str,
       copy_creds: bool = True,
       swap_names_: bool = False,    # было True
       ...
   ):
       """...
       By default swap_names_=False: public model_name stays on its row,
       only credentials (api_key, api_base, upstream model) are copied."""
   ```

2. **`/opt/litellm/admin/glm_swap_route.py`** — `SwapRequest` Pydantic model:
   ```python
   class SwapRequest(BaseModel):
       ...
       swap_names: bool = False      # было True
   ```

3. **`/opt/litellm/admin/glm_swap.html`** — UI checkbox default OFF,
   label объясняет: `Swap public model_name (DANGER — ломает клиенты)`.
   `doSwap()` (используется quick-swap кнопками) сбрасывает checkbox в `false`.

**Verification**:
- `swap.atlas_glm-5.2_dont_use → GLM-5.2` (с swap_names=False):
  - `model_id` ряда "GLM-5.2" не изменился (`1c584f1e9a9782460d487aa81f594152`
    до и после)
  - Появилась только одна строка с `model_name="GLM-5.2"` (count=1)
  - Запрос test: PASS "Hi there friend!" через atlas creds
- `rollback` (GLM-5.2_dont_use → GLM-5.2): PASS через Z.AI creds

**Новая семантика** ("promote shadow → active"):
```
Promote atlas → GLM-5.2:
  copy atlas_glm-5.2_dont_use.{api_key, api_base, model} → GLM-5.2 row
  (public name "GLM-5.2" не двигается)

Rollback к Z.AI:
  copy GLM-5.2_dont_use.{api_key, api_base, model} → GLM-5.2 row
  (GLM-5.2_dont_use — immutable backup оригинальных кред)
```

#### 5. opencode-setup API fix — `max_output_tokens` → `max_tokens` (2026-07-12)

**Проблема**: `/setup-opencode/api.py._extract_meta()` искал `mi.get("max_tokens")`
— но `POST /model/new` для новых моделей создавал field с именем
`max_output_tokens`. Результат: в `/setup-opencode/` page модели показывались
но со всеми нулями (context=0, output=0, vision=false).

**Verification до фикса**:
```
$ curl 'http://127.0.0.1:9000/models?user=<UUID>' | jq '.models[] | select(.id=="seed-2.1")'
{ "id": "seed-2.1", "context": 0, "output": 0, "vision": false, ... }
```

**Fix** — SQL добавляет алиас `max_tokens` к существующему `max_output_tokens`
(для обеих новых моделей):
```sql
UPDATE "LiteLLM_ProxyModelTable"
SET model_info = model_info || jsonb_build_object(
  'max_tokens', (model_info->>'max_output_tokens')::int
)
WHERE model_name IN ('seed-2.1', 'deepseek-v4-pro');
```

| Модель | До | После |
|---|---|---|
| seed-2.1 | context=0, output=0, vision=false | context=262144, output=262144, vision=true |
| deepseek-v4-pro | context=0, output=0, vision=false | context=1048576, output=393216, vision=false |

**Также** — `_MODEL_INFO` cache в `api.py` устарел (API стартовала до создания моделей).
Killing PID systemd-юнита → auto-restart → cache refreshed (новый PID 230757,
логи показывают `Active: active (running) since ... 8s ago`).

**Известный work-around** (для будущих добавлений моделей):
- Либо после каждого `POST /model/new` убивать процесс opencode-api.service
- Либо в `api.py` убрать in-memory cache (trade-off: каждый запрос будет
  делать `GET /model/info` через LiteLLM)
- Лучше всего — поправить `api.py` чтобы читал `max_output_tokens || max_tokens`
  AND сделать авто-refresh кеша (например TTL=300 сек)

#### 6. Cleanup без следов (2026-07-12)

Полная очистка артефактов разработки:

| Локация | Было | Стало |
|---|---|---|
| VM `/tmp/` | ~354 файла (.py/.sh/.json/.html/.txt/logs/dumps) | 0 (только systemd dirs) |
| Container `/tmp/` | 20 файлов (`decrypt_*.py`, `rotate_key*.py`, `fix_*.py`) | 0 |
| Container `/opt/litellm/backups/` | 10 JSONL дампов (~236KB) | 0 |
| Local `Temp\opencode\` | ~50 скриптов с ключами | 4 рабочих файла (swap_glm_credentials.py, glm_swap_route.py, glm_swap.html, askpass.cmd) |

Удалены критичные: `master_key.py`, `rotate_master_key.py` (13KB с ключами),
`verify_old_salt.py`, `rotate_v2.sh`, `extract_backup.py`, `rotate_key*.py`,
`fix_model_info.py`. JSONL dumps encrypted keys.

#### Файлы изменённые в этой итерации
- `/opt/litellm/.env.bak.*` — множественные backup'ы ротации ключей
- `/opt/litellm/swap_glm_credentials.py` — default `swap_names_=False`
- `/opt/litellm/admin/glm_swap_route.py` — default `swap_names=False`
- `/opt/litellm/admin/glm_swap.html` — UI checkbox default OFF
- DB: `LiteLLM_ProxyModelTable` — новые ряды seed-2.1, deepseek-v4-pro +
  `model_info.max_tokens` для обеих
- DB: `LiteLLM_TeamTable` — обновлены списки `models` для всех 9 команд

#### Известные ограничения
- **Нет backup'а team_orig_models.sql** — оригинальные списки команд
  (до wipe) полностью утеряны (audit table пуст, WAL retention 16MB
  прошёл за пределы; dead tuples vacuumed). Реконструкция частично —
  для 6 из 9 команд точный состав, для остальных 3 — частичный.
- **`OpenAI` ключ НЕ ротирован** — пользователь отказался,
  старый ключ остался в БД. Ротация под ответственность.
- **Qwen ключ возвращает `invalid access token`** —
  пользователь должен сверить с DashScope dashboard.
- **Gemini провайдер отключён** — 2 модели (gemini-3-pro-image,
  gemini-3.1-flash-image) удалены. Нельзя восстановить без новых ключей.
- **`max_tokens` vs `max_output_tokens` bug** в `api.py` —
  для будущих моделей нужно убивать systemd сервис или патчить api.py.
- **`db_model: true` bug** — повторяется при каждом добавлении через
  `/model/new`. Нужно либо автоматически проверять в `_swap.py`,
  либо документировать для пользователя.

#### Memory updates
- `infra/hcbifrost-vm-litellm.md` — добавлены секции про provider key rotation,
  новые модели, opencode-setup fix, GLM swap fix
- `litellm-credentials-migration.md` — НЕ ТРОНУТ (старая, суперсешн)
- Новых memories не создано (всё в существующих)

---

### [2026-07-10b] — CRITICAL: ротация LITELLM_SALT_KEY + перешифрование всех api_keys в БД

#### Контекст
`LITELLM_SALT_KEY` был установлен как `placeholder-replace-before-prod-min-32-chars-xxxxx` —
публичная строка из документации LiteLLM. Любой, кто получит дамп `LiteLLM_ProxyModelTable`
или `LiteLLM_CredentialsTable`, сможет расшифровать все api_keys провайдеров тривиально.
Это был наиболее вероятный вектор утечки OpenAI ключа.

#### Алгоритм шифрования LiteLLM
- **NaCl SecretBox** (XSalsa20-Poly1305) через `PyNaCl`
- Signing key: `LITELLM_SALT_KEY` env var (fallback: `master_key`)
- Процесс: `SHA256(salt_key)` → 32-byte key → `SecretBox.encrypt(value)` → `base64.urlsafe_b64encode()`
- Функции: `litellm.proxy.common_utils.encrypt_decrypt_utils.{encrypt_value, decrypt_value}`

#### Что сделано

##### 1. Backup таблиц БД
- `pg_dump` таблиц `LiteLLM_ProxyModelTable` + `LiteLLM_CredentialsTable`
- `/opt/litellm/backups/tables_pre_salt_rotation_20260710-231256.sql`

##### 2. Перешифрование (одной транзакцией)
Python скрипт выполнил:
1. **Дешифровка**: 89 полей в `ProxyModelTable` + 10 полей в `CredentialsTable` —
   расшифрованы со старым placeholder salt (все 89 полей расшифровались успешно, 0 ошибок)
2. **Генерация нового SALT_KEY**: `secrets.token_bytes(32)` → base64url → 43 chars
3. **Re-encryption**: все поля зашифрованы новым salt
4. **UPDATE**: все 23 модели + 5 credentials обновлены одной транзакцией (atomic commit)

Зашифрованные поля per model: `model`, `api_key`, `api_base`, `organization`,
`custom_llm_provider`, `litellm_credential_name`.
Зашифрованные поля per credential: `api_key`, `api_base`.

##### 3. Новый SALT_KEY
- **Старый**: `placeholder-replace-before-prod-min-32-chars-xxxxx` (50 chars, публичный)
- **Новый**: `<salt-key>` (43 chars, случайный)
- Записан в `/opt/litellm/.env` и `/opt/litellm/.new_secrets_20260710-224726`

##### 4. Container recreate
- `bash /opt/litellm/start.sh` (применён новый .env)
- `systemctl restart opencode-api`

#### Тесты (все ✅)
| Model | Тип | Результат |
|---|---|---|
| GLM-5.2 | api_key + api_base | PASS |
| MiniMax-M3 | credential-based | PASS |
| Kimi K2.6 | credential-based | PASS |
| QWEN3.7-plus | credential-based | PASS ("Hello! 👋 How can I help you today?") |
| mimo-v2.5-pro | api_key + api_base | PASS |
| mimo-v2.5 | api_key + api_base | PASS |

**Security verification**: старый placeholder salt **больше не может** расшифровать
какое-либо поле в БД (проверено на 9 полях из 3 моделей — все "CANNOT decrypt").

#### Backups
- `/opt/litellm/backups/tables_pre_salt_rotation_20260710-231256.sql` — SQL dump таблиц
- `/opt/litellm/.env.bak.20260710-232026` — .env до смены SALT_KEY

---

### [2026-07-10] — CRITICAL: утечка OpenAI ключа через LiteLLM UI (admin/admin)

#### Контекст
У пользователя утёк OpenAI API ключ (от ChatGPT) — использован напрямую из Китая
для траты денег на chatgpt.com. Утечка произошла через LiteLLM. Атакующий
`121.73.190.227` (China Telecom Shanghai) 03/Jul/2026 13:30 успешно залогинился
через `/v2/login` с дефолтным `admin/admin`, посмотрел `/model/info` и `/v1/models`,
делал `/health/test_connection` ×5. В текущей версии LiteLLM все эти endpoints
маскируют api_keys, но в момент атаки (или в более старой версии) — могли не маскировать.
Дополнительно `LITELLM_SALT_KEY = placeholder-replace-before-prod-min-32-chars-xxxxx`
(публичная строка из документации) — шифрование в БД тривиально обратить.

#### Сетевая топология (выяснена в ходе расследования)
- **`89.19.213.124` = приватный front proxy** пользователя с TLS termination
- DNS `hcbifrost.herocraft.com → 89.19.213.124`
- Цепочка: `client → 89.19.213.124 (proxy, TLS) → hcbifrost (162.55.137.149, port 80)`
- Прокси **передаёт `X-Forwarded-For`** с реальным client IP (проверено логированием)
- **Hetzner cloud firewall** видимо блокирует все IP кроме 89.19.213.124 на ports 80/8080
  (прямые запросы с других IP не доходят до nginx hcbifrost)

#### Что сделано

##### 1. Master key RE-rotated
- **Backup**: `/opt/litellm/.env.bak.20260710-224726`
- **Новый master key**: `<master-key-current>`
- Старый (выдан 2026-07-07) `<master-key-prev>` инвалидирован

##### 2. UI password сменён
- **Старый**: `admin / admin` (дефолт LiteLLM, в env контейнера)
- **Новый**: `admin / <ui-password>`
- Patched в `/opt/litellm/start.sh` (env var `UI_PASSWORD`)

##### 3. Nginx `real_ip` module enabled
В `/etc/nginx/nginx.conf` http block добавлено:
```nginx
set_real_ip_from 89.19.213.124;
set_real_ip_from 127.0.0.1;
real_ip_header X-Forwarded-For;
real_ip_recursive on;
```
Это делает `$remote_addr` = реальный client IP (от прокси XFF), а не IP прокси.
Без этого nginx whitelist был бесполезен — все запросы имели remote_addr = 89.19.213.124.
`real_ip_recursive on` + `set_real_ip_from 89.19.213.124` защищают от XFF spoofing:
только XFF от доверенного прокси используется, spoofed XFF от клиента игнорируется.

##### 4. Nginx admin endpoints whitelist (24 location blocks)
В `/etc/nginx/sites-available/litellm-bifrost` добавлено 24 конкретных `location ^~` блока
(вместо одного regex — regex проиграл бы `^~ /litellm/` prefix). Каждый блок:
```nginx
location ^~ /litellm/{path} {
    allow 89.19.213.124;  # прокси
    allow 162.55.180.78; # клиент пользователя (VPN/второй сервер)
    allow 127.0.0.1;
    allow 10.10.10.1;    # внутренняя сеть Hetzner
    deny all;
    proxy_pass http://127.0.0.1:4001;
    # ...proxy headers...
}
```
Whitelisted endpoints: ui, ui/login, v2/login, v1/model/info, model/info, v2/model/info,
credentials, key/list, key/info, key/generate, key/update, key/delete, spend/logs,
global/spend, config/list, get/config, user, team, organization, guardrails,
model/new, model/update, model/delete, sso.

Не-whitelisted (публичные): `/litellm/v1/models`, `/litellm/v1/chat/completions`,
`/litellm/health/*`, `/litellm/v1/embeddings`, etc.

##### 5. Custom log format с XFF
В `/etc/nginx/nginx.conf` добавлен `log_format xff_combined` с полями
`XFF="$http_x_forwarded_for" RealIP="$realip_remote_addr"` для аудита real IP.

##### 6. Container recreate + opencode-api restart
- `bash /opt/litellm/start.sh` (применён новый .env с новым master key + UI_PASSWORD)
- `systemctl restart opencode-api` (подхватил новый master key через `master_key.py` helper)

#### Тесты (все ✅)
| Test | Запрос | Ожидание | Результат |
|---|---|---|---|
| Whitelist от 162.55.180.78 | /key/list, /v2/login, /credentials | 401/405 (не 403) | ✅ работает |
| XFF spoofing | /key/list + `X-Forwarded-For: 121.73.190.227` | Реальный IP не подменяется | ✅ real_ip_recursive on работает |
| Public endpoints | /v1/models, /health/readiness | Доступны без whitelist | ✅ работают |
| Старый master key | Любой endpoint | 401 | ✅ инвалидирован |

#### Секреты
- `/opt/litellm/.new_secrets_20260710-224726` (mode 600, root:root):
  ```
  NEW_MASTER_KEY=<master-key-current>
  NEW_UI_PASSWORD=<ui-password>
  ```

#### Backups
- `/etc/nginx/nginx.conf.bak.20260710-*` (3 файла)
- `/etc/nginx/sites-available/litellm-bifrost.bak.20260710-*` (3 файла)
- `/opt/litellm/start.sh.bak.20260710-224726`
- `/opt/litellm/.env.bak.20260710-224726`

#### Что НЕ решено (для следующей итерации)
- **`LITELLM_SALT_KEY` всё ещё placeholder** — шифрование в `LiteLLM_ProxyModelTable.litellm_params`
  тривиально обратить. Нужна ротация SALT_KEY + перешифрование всех api_keys в БД.

---

### [2026-07-07] — CRITICAL: ротация master key + блокировка master key на inference трафике

#### Контекст
Master key `<master-key-leaked>` утёк в 25 файлах
на сервере и был обнаружен в scanner-логах (HK/CA IPs, 28 вызовов `gemini-3-pro-image`,
$1.45 потрачено — см. [2026-07-06] ниже). Закрытие `/v1/*` backdoor в nginx
остановило атаки, но ключ оставался валидным через `/litellm/v1/*` — требовалась
ротация + глубокая блокировка.

#### 1. Ротация `LITELLM_MASTER_KEY`
- **Backup**: `/opt/litellm/.env.bak.20260707-025146`
- **Новый master key**: `<master-key-prev>`
  (сгенерирован через `head -c 36 /dev/urandom | base64 | tr -dc 'A-Za-z0-9'`)
- **Helper `/opt/litellm/master_key.py`** (потокобезопасный кеш + `invalidate_cache()`):
  читает `LITELLM_MASTER_KEY` из `.env`, возвращает через `get_master_key()`.
- **Патчи 25 файлов**:
  - 15 Python-скриптов (`KEY = "sk-..."` → `KEY = get_master_key()` + `from master_key import get_master_key`)
  - `swap_glm_credentials.py` (двойной fallback `os.environ.get(...) or get_master_key()`)
  - 7 shell-скриптов (`Bearer sk-..." → `Bearer $MASTER_KEY` + инициализация через helper)
  - `README.md` (3 вхождения заменены на `<LITELLM_MASTER_KEY from /opt/litellm/.env>`)
- **Пересоздание контейнера** через `bash /opt/litellm/start.sh` (просто `docker restart litellm`
  НЕ подхватывает новые env vars из `.env` — нужно `docker rm -f` + `docker run --env-file`)

#### 2. Block master key на inference трафике (`/v1/*`)
- **Hook `/opt/litellm/block_master_key_hook.py`** — `async_pre_call_hook` возвращает
  HTTP 401 `master_key_blocked` если `user_api_key_dict.api_key == LITELLM_PROXY_MASTER_KEY_ALIAS`
  (= `"litellm_proxy_master_key"` — это stable alias, который LiteLLM подставляет
  вместо raw master key в `UserAPIKeyAuth`, чтобы raw key или его hash не утекали
  в spend logs / metrics / rate-limit buckets; см. `litellm.proxy.auth.user_api_key_auth.py` L1609).
- **Регистрация через `utils_patched.py`** (`_PATCH_BLOCK_MASTER_KEY_HOOK_` block внутри
  `_init_litellm_callbacks()` метода `ProxyLogging`). Регистрация через `entrypoint.sh`
  subprocess Python НЕ работает, потому что `exec litellm "$@"` в конце entrypoint
  запускает **новый Python process** — все in-memory изменения теряются.
  `utils_patched.py` же монтируется как `litellm/proxy/utils.py` (read-only volume),
  и его код выполняется внутри server process при инициализации ProxyLogging.
  Этот же механизм уже использовался для `user_agent_hook.py` и `image_rate_limit_hook.py`.
- **Mount** `/opt/litellm/master_key.py` → `/app/master_key.py` в `start.sh`
  (нужен для `swap_glm_credentials.py` который импортирует `get_master_key`).
- **Coverage**: hook срабатывает для `/v1/chat/completions`, `/v1/embeddings`,
  `/v1/images/generations` и других inference endpoints. НЕ срабатывает для
  `/v1/models` (metadata endpoint, не идёт через `pre_call_hook`) — это допустимо,
  `/v1/models` не позволяет делать inference. Admin endpoints (`/key/*`, `/user/*`,
  `/admin/*`) вообще не проходят через proxy hook pipeline, поэтому master key
  остаётся валидным для них.

#### Тесты (все ✅)
| Endpoint | Master key | Ожидание | Результат |
|---|---|---|---|
| `POST /litellm/v1/chat/completions` | NEW | 401 `master_key_blocked` | ✅ 401 |
| `POST /litellm/v1/images/generations` | NEW | 401 `master_key_blocked` | ✅ 401 |
| `POST /litellm/v1/embeddings` | NEW | 401 `master_key_blocked` | ✅ 401 |
| `GET /litellm/v1/models` | NEW | 200 (metadata, не критично) | ✅ 200 |
| `POST /litellm/key/generate` | NEW | 200 (admin bypass) | ✅ 200 |
| `GET /litellm/admin/glm-swap/` | UI session | 200 (admin UI) | ✅ 200 |
| OLD key на любом endpoint | OLD | 401 (rotated) | ✅ 401 |

#### Backup files (root-owned)
- `/opt/litellm/.env.bak.20260707-025146`
- `/opt/litellm/utils_patched.py.bak.20260707-033441`
- `/opt/litellm/litellm_entrypoint.sh.bak.20260707-032217`
- `/opt/litellm/start.sh.bak.20260707-032217`
- `/opt/litellm/{15 scripts}.bak.20260707-*` (per-file backups перед патчом)

#### Что важно знать
- **Все клиенты должны использовать virtual keys** (через `/key/generate` или UI),
  не master key. Если какой-то клиент до сих пор использует master key для
  inference вызовов — он начнёт получать 401.
- **Scanner-логи**: попытки использовать старый утёкший master key теперь будут
  падать с 401 (rotated) ИЛИ с 401 `master_key_blocked` (если когда-нибудь
  утечёт новый). Логируется на WARNING уровне в docker logs:
  `[block_master_key_hook] REJECTING master key on proxy call_type=... model=...`
- **Скрипт ротации**: `/tmp/rotate_v2.sh` на VM (idempotent — добавлен check
  существующего backup, можно перезапускать).

---

### [2026-07-06] — CRITICAL: закрыт `/v1/*` backdoor в nginx (сканеры с утёкшим master key)

#### Суть
В nginx config был `location / { proxy_pass http://litellm_upstream; }` catch-all,
который проксировал ВСЕ не match'енные пути в LiteLLM, включая `/v1/*` БЕЗ `/litellm/`
prefix. Поскольку у сканеров есть утёкший `LITELLM_MASTER_KEY`, они могли свободно
вызывать дорогие модели через `POST /v1/chat/completions` и `POST /v1/images/generations`.

#### Что происходило (найдено при анализе SpendLogs)
- **28 вызовов `gemini/gemini-3-pro-image`** сегодня через master key = **$1.45 потрачено**
- Источник: `185.141.216.18` (Hong Kong), `216.227.168.122` (CA) — UA `Go-http-client/1.1`,
  `python-requests/2.33.1`, фейковый `Mozilla zh-CN WindowsPowerShell`
- Сканеры используют SHA-256 хэши ключей из **чужих** утечек LiteLLM БД (наши хэши
  в их списках не найдены — значит БД не утекла, утёк именно .env / master key)
- Регулярные вызовы каждые ~7 минут = автоматизированный скрипт
- 33 уникальных IP сканеров сегодня делают mass `/v1/models` (Shodan-style discovery)

#### Nginx config patch
Добавлен блок `location ^~ /v1/` ПЕРЕД `location /` catch-all:

```nginx
    location ^~ /v1/ {
        return 404;
    }

    location / {
        proxy_pass http://litellm_upstream;
        ...
    }
```

`^~` — приоритетный prefix match, перехватывает `/v1/*` до catch-all.
Запрос **не доходит до LiteLLM** — nginx сразу отдаёт 404.

#### Backup
- `/etc/nginx/sites-available/litellm-bifrost.bak.20260706-203014` (root-owned)

#### Verification (5 тестов)
| Тест | До | После |
|---|---|---|
| `/v1/models` без auth | 401 (LiteLLM) | **404** (nginx) ✓ |
| `/v1/models` с fake auth | 401 | **404** ✓ |
| `/v1/chat/completions` POST с утёкшим master key | **200 OK** (сканеры работали!) | **404** ✓ |
| `/litellm/v1/models` с real key (реальный клиент) | 200 | **200** (без изменений) ✓ |
| `/litellm/v1/chat/completions` POST с chat-моделью | 200 | **200** (без изменений) ✓ |

После reload'а сканеры `89.19.213.124` и `185.141.216.124` получают **404** на `/v1/*`.

#### Известные последствия
- Реальные клиенты (opencode, UI, скрипты) используют `/litellm/v1/*` с prefix —
  они **не затронуты**.
- Если кто-то из клиентов всё же использовал `/v1/*` без prefix — он получит 404.
  Надо проверить историю access.log: только сканеры использовали `/v1/*` без prefix.

#### ⚠️ НЕ решено этим фиксом
- **Master key всё ещё утёк.** Сканеры могут обращаться через `/litellm/v1/*` —
  этот путь по-прежнему принимает master key. Нужна **ротация master key**.
- На VM есть 27 файлов с master key (`/opt/litellm/*.py`, `/opt/litellm/*.sh`,
  `/opt/opencode-setup/api.py`) — потенциальный путь утечки.
- В SpendLogs пользователи master key отображаются как `default_user_id` + team `<null>`
  — невозможно отличить админа от сканера. Нужен переход на virtual keys.

---

### [2026-07-04] — Снижение daily limits: All Access 50, остальные 30

#### Изменение
`image_team_limits` обновлены:
- `All Access`: 100 → **50**
- Все остальные команды + `_default`/`default`: 50 → **30**

```sql
UPDATE image_team_limits SET daily_limit = 50 WHERE team_alias = 'All Access';
UPDATE image_team_limits SET daily_limit = 30
  WHERE team_alias IN ('Agents','Analytics','Art','Coders','CreativeTeam',
                       'PirateShips','Porters','SideCoders','_default','default');
```

TTL кэш 60 сек — без рестарта litellm.

---

### [2026-07-04] — Восстановление `image_team_limits` (per-team daily limits)

#### Суть
Таблица `image_team_limits` (PRIMARY KEY `team_alias`, поле `daily_limit`) была
**полностью пустая** — 0 строк. Хук `image_rate_limit_hook.py` работал через
fallback `DEFAULT_DAILY_LIMIT = 50` для всех команд, включая `All Access`
(которая по дизайну должна иметь 100/day).

#### Восстановление
INSERT/UPDATE в `image_team_limits` для 9 команд + 1 fallback:

| team_alias | daily_limit |
|---|---|
| All Access | **100** |
| Porters | 50 |
| Agents | 50 |
| Analytics | 50 |
| Art | 50 |
| Coders | 50 |
| CreativeTeam | 50 |
| PirateShips | 50 |
| SideCoders | 50 |
| _default | 50 (fallback) |
| default | 50 (fallback, оставлен от прошлой записи) |

```sql
INSERT INTO image_team_limits (team_alias, daily_limit) VALUES
  ('All Access', 100), ('Porters', 50), ('Agents', 50),
  ('Analytics', 50), ('Art', 50), ('Coders', 50),
  ('CreativeTeam', 50), ('PirateShips', 50), ('SideCoders', 50),
  ('_default', 50), ('default', 50)
ON CONFLICT (team_alias) DO UPDATE SET daily_limit = EXCLUDED.daily_limit;
```

**Не требуется `docker restart litellm`** — хук использует in-memory
`_TeamLimitsCache` с TTL=60 секунд, обновляется при следующем запросе.

#### Verification (3 теста)
| Тест | Ожидалось | Получили |
|---|---|---|
| 1. POST /v1/images/generations под лимитом | HTTP 200 + counter++ | HTTP 200, counter 5→6 ✓ |
| 2. SQL set counter=49 + POST | HTTP 200, counter→50 | HTTP 200 ✓ |
| 3. POST после достижения лимита | HTTP 429 `image_daily_limit_exceeded` с explainer | HTTP 429 `limit: 50, used: 50, requested: 1, remaining: 0, team: unknown, reset_at: UTC midnight` ✓ |

**Сброс тестового состояния:** `default_user_id | gpt-image-2 | 2026-07-04` сброшен
обратно к 5 (было 5 до моих verification-вызовов).

#### Архитектура хука (найдено при анализе)
- **Hook файл**: `/opt/litellm/image_rate_limit_hook.py` (433 строк, root-owned, от 2026-07-02)
- **Регистрация**: НЕ через `config.yaml callbacks:`, а через Python patch в
  `/opt/litellm/litellm_entrypoint.sh` (lines 107-136). Belt-and-suspenders pattern —
  тот же что для `UserAgentLogger` (line 3-66). Хук всегда попадает в
  `litellm.callbacks` при старте контейнера.
- **Tables**:
  - `image_request_counter(user_id, model, day, count)` — PRIMARY KEY `(user_id, model, day)`,
    `day DEFAULT CURRENT_DATE`. Атомарный UPSERT (`ON CONFLICT DO UPDATE`) в хуке.
  - `image_team_limits(team_alias, daily_limit)` — PRIMARY KEY `team_alias`,
    CHECK `daily_limit >= 0`.
- **TTL cache**: `_TeamLimitsCache` (60 сек) для уменьшения нагрузки на БД.
- **Counters на сегодня** (image_request_counter):
  - `default_user_id | gpt-image-1.5 | 2026-07-04 | 1`
  - `default_user_id | gpt-image-2   | 2026-07-04 | 5` (сброшен с 50 после теста)
  - `Pavel (52d4a58d) | 4 модели | 2026-07-02, 2026-07-03` — исторические данные сохранены

#### Известные нюансы
- **master key** (`sk-litellm-...`) не имеет `team_id`, поэтому для админских
  тестов хук показывает `team: None / "unknown"` и применяет fallback лимит
  (50/day). Это by design — мастер-ключ не привязан к командам.
- **TTL кэша 60 сек** — изменения в `image_team_limits` видны через ≤60 сек
  без рестарта.
- **Команды создаваемые в LiteLLM UI** автоматически не появляются в
  `image_team_limits`. Hook использует `_default` (50) для неизвестных команд.
  Для кастомного лимита новой команде — INSERT вручную.

---

### [2026-07-04] — Минимальные дефолты размера/quality/aspect_ratio для 4 image моделей

#### Суть
В `LiteLLM_ProxyModelTable` для всех 4 image-generation моделей добавлены минимальные
defaults через `jsonb_set(litellm_params, ...)`. Клиент может переопределить через
явный параметр в runtime — LiteLLM применяет модельные defaults как fallback, не override.

#### Изменения в БД (4 строки)
| `model_name` | Добавлено в `litellm_params` |
|---|---|
| `gpt-image-1.5` | `"size": "1024x1024", "quality": "low"` |
| `gpt-image-2` | `"size": "1024x1024", "quality": "low"` |
| `gemini/gemini-3.1-flash-image` | `"aspect_ratio": "1:1"` |
| `gemini/gemini-3-pro-image` | `"aspect_ratio": "1:1"` |

SQL (идемпотентный — повторный запуск не дублирует):
```sql
BEGIN;
UPDATE "LiteLLM_ProxyModelTable"
SET litellm_params = jsonb_set(
  jsonb_set(litellm_params, '{size}', '"1024x1024"'),
  '{quality}', '"low"'
)
WHERE model_name IN ('gpt-image-1.5','gpt-image-2');

UPDATE "LiteLLM_ProxyModelTable"
SET litellm_params = jsonb_set(litellm_params, '{aspect_ratio}', '"1:1"')
WHERE model_name IN ('gemini/gemini-3.1-flash-image','gemini/gemini-3-pro-image');
COMMIT;
```

После — `docker restart litellm` (обязателен: litellm держит снимок `litellm_params` в памяти).

#### Verification (4 функциональных теста)
| Тест | Параметры | Ожидалось | Получили |
|---|---|---|---|
| 1 | gpt-image-2 без size/quality | 1024×1024 low | **1024×1024 low** (196 image tokens, 12.5s) ✓ |
| 2 | gpt-image-2 + size=1536x1024 + quality=medium | 1536×1024 medium | **1536×1024 medium** (1372 image tokens) ✓ |
| 3 | gpt-image-1.5 без size/quality | 1024×1024 low | **1024×1024 low** (272 image tokens) ✓ |
| 4 | gemini-3.1-flash-image (chat completions) | (см. ниже) | 1408×768 JPEG (Gemini проигнорировал `aspect_ratio`) ⚠ |

#### Известные ограничения
- **`aspect_ratio` для Gemini chat completions НЕ работает** в текущей версии LiteLLM
  (`/app/.venv/lib/python3.13/site-packages/litellm/llms/gemini/videos/transformation.py`
  поддерживает `aspectRatio` только для Veo video generation, не для multimodal chat
  output). Даже явный `aspect_ratio: "1:1"` в chat-completions запросе игнорируется —
  Gemini flash возвращает 1408×768 (16:9 default), pro скорее всего аналогично.
  Параметр оставлен в DB как documentation of intent; автоматически подхватится когда
  LiteLLM добавит поддержку.
- **`quality=high` для gpt-image-2 заблокирован** существующим guard'ом
  `slow_quality_combination_blocked` (latency ~120s > Timeweb proxy timeout 60s).
  Клиент получит 400 с подсказкой использовать gpt-image-1.5 для high quality.
- **`gpt-image-2` не в публичной OpenAI docs** — это preview/внутренняя модель. Наш
  api_key работает с ней; в случае регресса со стороны OpenAI defaults (`size=1024x1024
  quality=low`) продолжат работать как просто параметры запроса.
- **`gpt-image-1.5` имеет `db_model: false`** в `model_info` — не влияет на jsonb_set,
  defaults применились корректно (тест 3 = 1024×1024 low).
- **JSONB merge semantics** — `jsonb_set` дополнил существующий объект, не перезаписал.
  `api_key`, `api_base`, `custom_llm_provider`, `organization`, `rpm` сохранены.

#### Экономия
- `gpt-image-2 quality=low` ≈ 196 image_tokens vs `quality=high` ~1300+ tokens → в 6-7× дешевле.
- `gpt-image-1.5 quality=low` ≈ 272 image_tokens vs `quality=high` ~400+ → в 1.5× дешевле.
- 4 тестовых генерации в БД (`LiteLLM_SpendLogs`) — spend должен инкрементиться
  корректно (rate-limit hook не задействован, это просто проверка стоимости).

#### Backup
- `/opt/litellm/backups/img-models-pre-defaults-20260704-012550.jsonl` (4377 bytes,
  root-owned, 644) — снапшот `litellm_params` для 4 моделей до изменения. Можно
  откатить через `jsonb_set` (записать `litellm_params` обратно).

#### Файлы
- БД `litellm-pg` `LiteLLM_ProxyModelTable` — 4 UPDATE'а
- `/opt/litellm/backups/img-models-pre-defaults-*.jsonl` — backup
- Код LiteLLM/hooks/агенты/nginx — **НЕ** менялись
- Клиенты (opencode агенты, gemini-image-gen skill) — **НЕ** менялись (их явный
  `size: 1024x1024` совпадает с default, поведение не изменилось)

---

### [2026-07-03] — opencode-setup: защита от невалидного `user` параметра (UUID validation)

#### Суть
Пользователь Илья (`0e14c643-1baf-4517-b570-f3aec2c01daf`, `i.kurluchan@herocraft.com`)
наблюдал две разные картины через два UI-пути:
- **Per-row кнопка "🛠 OpenCode" на странице Users** (админский путь): настройки
  корректные, 9 моделей команды Porters, валидный UUID в URL.
- **FAB "🧠 Мои настройки"** (сам Илья нажимает у себя): в URL приходит его
  **API-ключ** `sk-PgWEMRfEuh-vQS-oacwmiA` вместо UUID, и страница показывает
  "0 моделей доступно пользователю".

#### Root cause
1. **JWT payload в LiteLLM** (`ReturnedUITokenObject`) содержит `key=<api_key>` И
   `user_id=<uuid>`. `uidFromJWT()` в `users-btn.js` возвращал `claims.user_id ||
   claims.sub`, и при определённых формах JWT (`sub: <token>`) / DOM extraction
   возвращал значение, которое НЕ является UUID.
2. **opencode-setup API** (`/opt/opencode-setup/api.py`) принимал ЛЮБОЕ значение
   `?user=...` и пробрасывал его в `litellm_get("/user/info?user_id=<value>")`.
   LiteLLM отвечал 404, `resolve_models()` ловил `HTTPError`, возвращал `[]`,
   и фронт показывал "0 моделей" без объяснения, что параметр невалидный.
3. **index.html** просто отображал `p.get("user")` без валидации и без
   отличия валидного UUID от мусорного значения (включая API-ключи).

#### Решение (3 файла на hcbifrost VM)

**1. `/opt/opencode-setup/api.py`** — добавлен `UUID_RE` regex, оба endpoint'а
(`/models`, `/mcp`) теперь возвращают **HTTP 400** с понятной ошибкой:
```python
if not UUID_RE.match(uid):
    return self._send(400, {
        "error": f"user param must be a UUID, got: {uid!r}. " +
                 "API keys / virtual keys (sk-...) are NOT user_ids."
    })
```

**2. `/opt/opencode-setup/index.html`** — добавлен `UUID_RE`, `uValid` флаг.
- В заголовке показывает `user: <value> (invalid: not a UUID — passing as-is)` если невалидно.
- `fetchModelsPromise()` / `fetchMcpPromise()` сразу `Promise.reject(new Error(...))`
  с человеческим объяснением (включая подсказку "use the OpenCode button on the Users page").
- Существующая логика "0 моделей → ⚠ warning" остаётся для случаев, когда у валидного
  пользователя действительно нет моделей.

**3. `/opt/opencode-setup/users-btn.js`** — defensive UUID guards в 4 точках:
- `uidFromJWT()` — возвращает `null` если `claims.user_id || claims.sub` не UUID.
- `detectUid()` — не кэширует и не возвращает не-UUID.
- `saveUid()` — не пишет в `localStorage` не-UUID.
- `openSetup()` — не открывает окно с не-UUID параметром.

Существующая валидация в `addRowButton()` (per-row кнопка, `UUID_RE.test(uid)`)
уже была корректной — отсюда разница в поведении между двумя путями.

#### Рестарт
`systemctl restart opencode-api.service` — `api.py` перечитан. `index.html` и
`users-btn.js` отдаются nginx'ом напрямую (статика), рестарт не нужен.

#### Verification (4 теста прошли)
| Запрос | До | После |
|---|---|---|
| `?user=sk-PgWEMRfEuh-vQS-oacwmiA` | HTTP 200, `[]` (silent fail) | **HTTP 400** `"user param must be a UUID, got: 'sk-PgWEMRfEuh-vQS-oacwmiA'..."` |
| `?user=0e14c643-1baf-4517-b570-f3aec2c01daf` | HTTP 200, 9 моделей | HTTP 200, 10 записей (те же 9 + `no-default-models` sentinel из `UserTable.models`) |
| `?user=` (пусто) | HTTP 400 `missing user param` | HTTP 400 `missing user param` (без изменений) |
| `/mcp?user=sk-...` | HTTP 200, `[]` (silent fail) | **HTTP 400** с тем же понятным сообщением |

#### Известные ограничения
- `no-default-models` sentinel из `UserTable.models` всё ещё попадает в `resolve_models()`
  как один из элементов set'а (потом отфильтровывается на `build_model_info()` lookup —
  `table.get(canonical)` → `None` → скипается в UI). Это не баг, а поведение существующего
  кода; полная фильтрация sentinel'а — отдельная задача.
- JWT может содержать `sub: <email>` (SSO flow) — текущий код отвергнет такой sub и
  пойдёт в DOM fallback. Для максимальной надёжности можно добавить email → UUID lookup,
  но пока DOM-extraction работает.

---

### [2026-07-03] — generate-image-gpt agent: `bifrost-litellm/gpt-image-2` → `bifrost-litellm/MiniMax-M3`

#### Суть
Файл `C:\Users\Admin\.config\opencode\agents\generate-image-gpt.md` имел в frontmatter:
```yaml
model: bifrost-litellm/gpt-image-2
```
OpenCode использовал `gpt-image-2` как LLM-мозг сабагента для chat completions,
но `gpt-image-2` — это DALL-E модель для `/v1/images/generations`, она **не поддерживает**
`/v1/chat/completions`. OpenAI возвращал 500 → LiteLLM уводил в cooldown →
"No deployments available".

#### Решение
Заменено `model:` на chat-capable модель: `bifrost-litellm/MiniMax-M3` (та, что используется
по умолчанию для build agent). Bash-сниппет внутри агента (`$body = @{ model = 'gpt-image-2'; ... }`)
НЕ менялся — это правильный путь к `/v1/images/generations`.

```yaml
mode: subagent
model: bifrost-litellm/MiniMax-M3   # was: bifrost-litellm/gpt-image-2
temperature: 0.5
```

#### Verification
OpenCode CLI перезапущен; спавн `generate-image-gpt` теперь использует MiniMax-M3
для chat layer, а `gpt-image-2` — только для image generation endpoint'а.

---

### [2026-07-02] — GLM-5.x failover pairs: 6 _dont_use shadows + one-click swap tool с кнопкой в LiteLLM

#### Суть
Создано 6 теневых моделей в `LiteLLM_ProxyModelTable` — точные копии 6 активных GLM
с суффиксом `_dont_use` в `model_name`. Когда у активной модели заканчиваются лимиты
у провайдера, можно одной кнопкой скопировать `litellm_params` (включая зашифрованный
`api_key`) из тени в актив + поменять `model_name` местами. Public Model Name
остаётся стабильным с точки зрения opencode / клиентских конфигов.

#### Что добавлено

**6 теневых моделей** в `LiteLLM_ProxyModelTable`:
- `GLM-5.1_dont_use` (model_id: `55db0263…`)
- `GLM-5.1 (res)_dont_use` (model_id: `5f43b855…`)
- `GLM-5.2_dont_use` (model_id: `1c584f1e…`)
- `GLM-5.2 (res)_dont_use` (model_id: `d784aa5b…`)
- `atlas_glm-5.1_dont_use` (model_id: `0a4eb437…`)
- `atlas_glm-5.2_dont_use` (model_id: `f3caedf8…`)

Каждая — bit-perfect копия `litellm_params` (включая зашифрованный `api_key` или
`litellm_credential_name` для (res)-вариантов). Создаются скриптом
`create-dont-use-models` (одноразово).

**Скрипт `/opt/litellm/swap_glm_credentials.py`** (~520 строк):
- CLI: `list-models`, `list-pairs`, `create-dont-use-models`, `copy`, `swap-names`, `swap`
- Импортируемые функции: `list_models()`, `list_pairs()`, `create_dont_use_models()`,
  `copy_credentials()`, `swap_names()`, `swap()` — для вызова из FastAPI route'а
- Атомарный swap = SQL транзакция: backup → UPDATE litellm_params → 3-step swap name → COMMIT → touch cache
- Auto-detect контекста: внутри контейнера использует `127.0.0.1:4000`, с хоста — `127.0.0.1:4001`
- Auto-detect DSN: `PG_DSN` env → `DATABASE_URL` env → default

**FastAPI router `/opt/litellm/admin/glm_swap_route.py`**:
- `GET /admin/glm/models` — список 12 GLM rows (без секретов)
- `GET /admin/glm/pairs` — 6 пар с флагом `creds_equal`
- `POST /admin/glm/swap` — body `{from, to, swap_names, copy_creds, dry_run}`
- `GET /admin/glm-swap/` — UI страница
- Все endpoint'ы защищены `Depends(user_api_key_auth)` (admin ключ)

**UI страница `/opt/litellm/admin/glm_swap.html`** (~240 строк, vanilla JS):
- Два `<select>` (FROM, TO), auto-populated из `/admin/glm/models`
- 6 quick-swap кнопок (одна на каждую пару: "Promote `<model>_dont_use` → `<model>`")
- Чекбоксы: copy credentials, swap names, dry run
- Live result с before/after diff (api_base, model, credential_name, hash api_key, updated_at)
- Confirmation dialog (`confirm()`) перед реальной операцией
- Auto-refresh пар каждые 30 секунд

**Кнопка в LiteLLM UI**:
- `/opt/opencode-setup/glm-swap-btn.js` — JS инжектит floating кнопку "GLM Swap"
  в правом-верхнем углу LiteLLM Admin UI
- Кнопка ведёт на `/litellm/admin/glm-swap/`
- nginx `sub_filter` инжектирует `<script src="/setup-opencode/glm-swap-btn.js?v=1"></script>`
  в 4 местах: `/litellm/ui/`, `/ui/`, `/onboarding`, `@litellm_ui` fallback

#### Bind-mounts (добавлено в start.sh)
```bash
-v "$(pwd)/swap_glm_credentials.py:/app/swap_glm_credentials.py:ro" \
-v "$(pwd)/admin:/app/admin:ro" \
```

#### Патч litellm_entrypoint.sh
Добавлен PYEOF5: патчит `/app/.venv/lib/python3.13/site-packages/litellm/proxy/proxy_server.py`
идемпотентно (маркер `_GLM_SWAP_ROUTER_PATCH_`), добавляя после `app.include_router(ocr_router)`:
```python
try:
    from admin.glm_swap_route import router as _glm_swap_router
    app.include_router(_glm_swap_router)
    print("[entrypoint] glm_swap_router REGISTERED")
except Exception as _e:
    import traceback
    traceback.print_exc()
    print(f"[entrypoint] glm_swap_router WARN: {_e}")
```

#### Backup перед первым запуском
- `ProxyModelTable-pre-glm-failover-20260702-190945.sql` (18993 байт, root-owned в `/opt/litellm/backups/`)
- При каждом swap создаётся `ProxyModelTable-pre-swap-<ts>.jsonl` в той же папке

#### Поведение swap
- **Атомарность**: copy + swap name в одной SQL транзакции. Если что-то падает —
  rollback, БД остаётся в consistent state.
- **Touch cache**: после commit POST `/model/update` для каждой затронутой строки
  (триггерит `clear_cache()` + audit log). **Failure non-fatal** — log warning,
  swap уже committed.
- **Идемпотентность**: повторный swap с теми же source/target — no-op (если creds
  совпадают) или swap назад.
- **`creds_equal` в UI выглядит False** после swap (touch_cache re-encrypts, nonce
  меняется), но plaintext тот же. Это не баг.
- **`(res)`-варианты** используют `litellm_credential_name` (shared named credential) —
  swap работает на оба типа, `litellm_params` копируется целиком.

#### Верификация (2026-07-02)
- Backup таблицы: ОК
- 6 теней созданы: ОК
- `/admin/glm/pairs` возвращает 6 пар: ОК
- Swap dry-run + real + rollback протестирован на `atlas_glm-5.2` (тест-пара)
- UI страница доступна: `HTTP 200, 9098 bytes`
- Public nginx URL: `https://hcbifrost.herocraft.com/litellm/admin/glm-swap/`

#### Memory
- `infra/hcbifrost-vm-litellm` — новый раздел "GLM-5.x failover pairs (6 _dont_use shadows + one-click swap tool) — 2026-07-02"

#### Известные ограничения
- **`docker` бинарника внутри контейнера нет** — `_backup_table` пишет JSONL (не pg_dump)
- **`/model/update` может вернуть 400** (редко) — touch_cache ловит, не валит swap
- **`creds_equal` False после swap** (re-encryption) — это нормально, не баг
- **Floating кнопка в UI** добавляется через sub_filter — может конфликтовать с другими JS если добавим ещё
- **Set-creds subcommand** не реализован — пока пользователь кладёт новый ключ в тень через прямой API провайдера

---

### [2026-07-02] — Master key rotation + opencode-api.service fix + touch_cache disable

#### Суть
- **LITELLM_MASTER_KEY ротирован** со старого placeholder на новый продакшен-ключ.
  - Старый (мёртвый): `sk-litellm-placeholder-replace-before-prod`
  - Новый: `<master-key-leaked>`
  - Все 25 скриптов в `/opt/litellm/*.py *.sh` обновлены через `sed`. `/opt/litellm/.env`
    обновлён. README в `/opt/litellm/README.md` обновлён.
  - Старый ключ теперь возвращает 401, новый возвращает 200.

- **opencode-api.service починен**: PID 51014 был legacy-процессом (pre-systemd),
  держал порт 9000 с закешированным старым `LITELLM_MASTER_KEY` в module global.
  Убит через `kill -9`. Systemd unit перезапустил — PID 107289 читает новый ключ
  из `/opt/litellm/.env`. `/setup-opencode/api.py` сделан с перезапуском по systemd.

- **`_touch_cache` отключён в `swap_glm_credentials.py`**: POST `/model/update`
  вызывает `encrypt_value_helper` на КАЖДОМ значении в `litellm_params`, включая
  уже зашифрованные. Это приводит к double-encryption, и модель тихо исчезает
  из `/model/info` и `/v1/models`. Симптом: после swap-теста перестали отвечать
  `GLM-5.1`, `GLM-5.2`, `atlas_glm-5.1`, `atlas_glm-5.2` (upstream поля были
  ~64-76 chars после первого шифрования, ~140-156 chars после второго).
  - Восстановлено из `ProxyModelTable-pre-glm-failover-20260702-190945.sql` +
    ручной SQL UPDATE через psycopg2 с inline `litellm_params` JSON (из бэкапа).
  - `_touch_cache` теперь no-op, выставляет `_restart_required = True`.
  - `/admin/glm/swap` возвращает `restart_required: True`, `restart_cmd: "docker restart litellm"`,
    и `warning` про необходимость перезапуска.
  - UI показывает баннер с командой restart после каждого реального swap.

#### Что проверено после фикса
- `/model/info` показывает все 12 GLM-5.x моделей (GLM-5.1, GLM-5.1 (res),
  GLM-5.2, GLM-5.2 (res), atlas_glm-5.1, atlas_glm-5.2 + 6 _dont_use shadows)
- `chat/completions` 200 OK на atlas_glm-5.1 ("Pong!"), 200 на GLM-5.1/5.2
  (пустой content — отдельная проблема), 429 на (res) вариантах (недельный
  лимит провайдера — ожидаемо)
- `/admin/glm/swap` dry-run + real + rollback на GLM-5.1: данные в DB
  меняются корректно, в ответе приходит `restart_required: True`

---

### [2026-07-02] — Remove `GLM-4.7` and `GLM-4.7 (res)` from all teams

#### Суть
Модели `GLM-4.7` и `GLM-4.7 (res)` отключены (нет в `config.yaml` и нет
в `LiteLLM_ProxyModelTable` — 0 rows), но их имена оставались в
`LiteLLM_TeamTable.models` у 8 команд. Любой вызов `/v1/chat/completions`
с `model: "GLM-4.7"` возвращал `400 Invalid model name passed in
model=GLM-4.7` — это утечка устаревших имён в UI team'ов и потенциальный
источник путаницы.

#### Затронутые команды (8)
- `GLM-4.7` (6 teams): `Agents`, `All Access`, `Analytics`,
  `CreativeTeam`, `PirateShips`, `SideCoders`
- `GLM-4.7 (res)` (2 teams): `Coders`, `Porters`

#### SQL
```sql
BEGIN;
UPDATE "LiteLLM_TeamTable"
SET models = array_remove(models, 'GLM-4.7')
WHERE 'GLM-4.7' = ANY(models);
UPDATE "LiteLLM_TeamTable"
SET models = array_remove(models, 'GLM-4.7 (res)')
WHERE 'GLM-4.7 (res)' = ANY(models);
COMMIT;
```
Возвращено `UPDATE 6` и `UPDATE 2`. После: 0 команд с этими именами.

#### Verification
- `GET /team/list` → 9 команд, 0 содержат `GLM-4.7` / `GLM-4.7 (res)`
- `POST /v1/chat/completions` с `model=GLM-4.7` → HTTP 400
  "Invalid model name" (был и до удаления, после — то же поведение,
  но в UI больше не показываются)
- Regression: `model=GLM-5.1` → HTTP 200 за 2.58s ✓

#### Изменённые файлы
- `CHANGELOG.md` — эта запись
- Никаких Go/TS файлов — только DB state.

---

### [2026-07-02] — gpt-image-2 via opencode agent — `InternalServerError` fix (stream=True to OpenAI image API)

#### Суть
Subagent `generate-image-gpt` (model: `bifrost-litellm/gpt-image-2`) через
opencode вызывал ошибку:
```
AI_APICallError: litellm.InternalServerError: InternalServerError:
OpenAIException - The server had an error while processing your request.
Sorry about that!. Received Model Group=gpt-image-2
```
Через ~30s дополнительно: `No deployments available for selected model,
Try again in 30 seconds. Passed model=gpt-image-2. pre-call-checks=False,
cooldown_list=['ace47273-2a04-4709-b19c-437d9e948d61']` (LiteLLM положил
deployment в cooldown 30s).

#### Root cause (НЕ OpenAI!)
opencode's AI SDK по дефолту шлёт `stream: true` для **каждой** модели
(видно в `Final returned optional params: {'stream': True, ...}` в
LiteLLM логе). Но OpenAI image API (`gpt-image-2`, `gpt-image-1.5`) **не
поддерживает streaming** — это не text-completion endpoint.

LiteLLM делает streaming call → upstream начинает читать socket → OpenAI
возвращает не-streamable response → LiteLLM зависает → `httpx.ReadTimeout:
Timeout on reading data from socket` в
`/app/.venv/lib/python3.13/site-packages/litellm/llms/custom_httpx/aiohttp_transport.py:75`
→ wrapped как generic "InternalServerError" в "OpenAIException" wording
→ deployment помечен failed → cooldown 30s.

`api_base` для `gpt-image-2` (deployment `ace47273-2a04-4709-b19c-437d9e948d61`)
расшифрован через `from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value_helper` —
это `https://api.openai.com/v1`, прямой OpenAI API. Не кастомный прокси.

#### Решение (1 файл)
**`/opt/litellm/image_rate_limit_hook.py`** — pre-call hook принудительно
отключает `stream` для image generation calls:

```python
# --- 0.25. Force non-streaming upstream call for image generation ---
if data.get("stream") is True:
    verbose_proxy_logger.warning(
        "image_rate_limit_hook: forcing stream=False for image "
        "generation (was stream=True) - model=%r call_type=%r",
        data.get("model"),
        call_type,
    )
    data["stream"] = False
```

LiteLLM сам обёртывает non-streaming response в SSE chunked stream, если
клиент запрашивал `stream: true`. Клиент получает свой формат,
upstream работает non-streaming.

Backup: `image_rate_limit_hook.py.bak.stream-fix` (+1040 bytes).

#### Verification

| Запрос | До | После |
|--------|-----|-------|
| `gpt-image-2` + `stream:true` (через opencode agent) | InternalServerError → cooldown 30s | 200 OK за 13.17s |
| `gpt-image-1.5` + `stream:true` | (assumed broken) | works |
| `gpt-image-2` baseline (no stream) | 200 OK ~12s | 200 OK ~12s (no regression) |
| `gpt-image-2` + `quality:high` | 400 (slow_quality_combination_blocked) | 400 (still blocked, see prev entry) |

Логи после фикса:
```
[01:19:06] image_rate_limit_hook: ENTER call_type='image_generation' model='gpt-image-2' stream=True
[01:19:06] image_rate_limit_hook: forcing stream=False for image generation (was stream=True) - model='gpt-image-2'
```

Detailed analysis: `.serena/memories/gpt-image-2-streaming-fix.md`.

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
3. Поднять cache-bust: `echo "<sudo-pass>" | sudo -S -p "" sed -i "s/users-btn.js?v=N/users-btn.js?v=N+1/g" /etc/nginx/sites-enabled/litellm-bifrost && echo "<sudo-pass>" | sudo -S -p "" nginx -s reload`

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
ssh ... 'echo "<sudo-pass>" | sudo -S -p "" sed -i \
  "s|users-btn.js?v=[0-9]*|users-btn.js?v=N+1|g" \
  /etc/nginx/sites-available/litellm-bifrost && \
  echo "<sudo-pass>" | sudo -S -p "" nginx -s reload'
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

- **VM**: `162.55.137.149:1995`, user `dev01`, password `<sudo-pass>`
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