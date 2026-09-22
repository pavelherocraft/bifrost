---
name: litellm-add-model
description: Add a new LLM model to the hcbifrost LiteLLM proxy (full checklist — upstream preflight, OpenRouter pricing, team grants, model_cost dual keys, reasoning flag, restarts, smoke test with spend verification, temp key cleanup, changelog). Use when the user asks to add/connect/enable a model or provider on the LiteLLM server. Triggers on "добавь модель", "подключи модель", "add model", "add provider", "включи модель в литллм", "дай командам модель". Transport — see skill vm-ssh.
---

# Добавление модели в LiteLLM (hcbifrost)

Полный чеклист на основе инцидентов. Пропуск шага = 403, spend=0 или
сломанный opencode. Транспорт — скилл `vm-ssh` (base64-паттерн, все
команды ниже выполняются через него).

## 0. Секреты и ссылки

Прочитай serena memory `infra/hcbifrost-vm-litellm`:
VM ssh-доступ, sudo-пароль, LiteLLM master key (`MK`), team IDs
(Agents, All Access и др.), известные api-ключи провайдеров.

Постоянные ссылки (цены, доки, формулы, инфра) — канон в
`.opencode/references/links.md` (см. также serena memory `infra/links`).

## 1. Собери входные данные

| Параметр | Откуда |
|---|---|
| `PUBLIC` — публичное имя (`tencent/Hy4`) | пользователь / конвент `vendor/Name` |
| `UPSTREAM` — имя у провайдера (`hy4-preview`) | пользователь; проверяется preflight |
| `BASE` — api_base | token plan: `https://tokenhub-intl.tencentcloudmaas.com/plan/v3`; pay-per-use: `.../v1`; другое — от пользователя |
| `KEY` — api key | пользователь / memory |
| teams — кому дать доступ | пользователь (типово «все кроме Agents») |
| цены in/out за 1M | OpenRouter (шаг 3) или пользователь |

**ЗАПРЕЩЕНЫ запятые в `PUBLIC`** — LiteLLM обрезает имя по запятой в
auth check → вечный 403 (инцидент `tencent/glm-5-2 (reserved, ...)`).
Вместо `, ` пиши ` - `.

## 2. Preflight: проверь upstream напрямую

`/models` у этих провайдеров часто 404 — проверяй минимальным POST:

```bash
curl -sk --max-time 30 -X POST "$BASE/chat/completions" \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"model":"UPSTREAM","messages":[{"role":"user","content":"pong"}],"max_tokens":8}' \
  -w "\nHTTP %{http_code}\n"
```

- 200 + есть `reasoning_content` → reasoning-модель (запомни для шага 7)
- 400 "model does not exist" → попробуй другие варианты имени (stable vs `-preview`)
- 403 "not supported by TokenPlan" → модель не в token plan, нужен pay-per-use ключ/база

## 3. Цены (OpenRouter — референс для ВСЕХ моделей)

**Договор: биллинг всегда по OR-ценам БЕЗ скидок (полным), независимо от
реального провайдера** — цены это внутренний референс-тариф, не факт. затраты.

- Текущие (со скидками): `https://openrouter.ai/api/v1/models` → `pricing.prompt/completion`
- Скидки и полный прайс: `https://openrouter.ai/api/v1/models/{slug}/endpoints` →
  каждый endpoint имеет `pricing.discount` (доля), цены там уже со скидкой:
  **полная = price / (1 − discount)**
- Консенсус: mode значений по endpoints (≥2 повторов), иначе медиана
- Рефреш всего model_cost (все модели, включая кастомные деплои):
  `/opt/litellm/admin/or_full_prices.py --apply` на VM (dry-run без флага,
  внутри алиас-таблица tencent/atlas/custom_openai/k2.8 → OR-слаги;
  бэкап `config.yaml.bak.orfull*` automatic). После — рестарт litellm.
- Модели без OR-листинга (doubao, kimi-k2.7/2.8, gpt-image, старые claude) —
  цены от пользователя/провайдера, отметить в changelog.
- Слаг может отличаться суффиксом: qwen3.8-max на OR = `qwen/qwen3.8-max-0902`

## 4. Бэкап команд + создание модели

```sql
CREATE TABLE IF NOT EXISTS "TeamTable_bak_<tag>_<yyyymmdd>" AS SELECT * FROM "LiteLLM_TeamTable";
```

```bash
curl -sk -X POST http://127.0.0.1:4001/model/new \
  -H "Authorization: Bearer $MK" -H "Content-Type: application/json" \
  -d '{"model_name":"PUBLIC","litellm_params":{"model":"UPSTREAM","custom_llm_provider":"custom_openai","api_base":"BASE","api_key":"KEY"},"model_info":{"max_input_tokens":CTX,"max_tokens":OUT}}'
```
`model_info` на строке персистится (ctx/out видны в `/v1/model/info`).

## 4b. Патч СУЩЕСТВУЮЩЕЙ модели (лимиты/цены/params)

Спеки брать из официальной документации вендора (у Alibaba у модели есть
своя страница help.aliyun.com/en/model-studio/<model> с секцией «Context
limits»: Context length / Max input / Max output — это ТРИ разных числа,
не путать: в UI отдаём Context length как «context», Max output как
«output»).

Правильный способ — PATCH endpoint (hot reload, рестарт litellm не нужен):

```bash
MK=$(grep -oP '(?<=LITELLM_MASTER_KEY=).*' /opt/litellm/.env | tr -d '"')
curl -sk --max-time 30 -X PATCH "http://127.0.0.1:4001/model/<MODEL_UUID>/update" \
  -H "Authorization: Bearer $MK" -H "Content-Type: application/json" \
  -d '{"model_info": {"max_input_tokens": 1000000, "max_tokens": 131072}}'
```

- `model_id` = UUID из `LiteLLM_ProxyModelTable.id` (НЕ model_name)
- PATCH меняет только переданные поля (можно и `litellm_params`, и
  `model_info`); api_key шифруется автоматически
- Рестарт litellm НЕ нужен (router hot-reload), но opencode-api — НУЖЕН
  (кэш `_MODEL_INFO`)
- Верификация: БД + `/model/info` + `/setup-opencode/api/models?user=<uuid>`
- Прямой SQL над `litellm_params`/`model_info` — только additive (`||`,
  `jsonb_set`); полная перезапись ломает зашифрованные поля

## 5. Гранты командам

Все, кроме Agents:
```sql
UPDATE "LiteLLM_TeamTable" t SET models = (SELECT array_agg(DISTINCT v)
  FROM unnest(array_cat(COALESCE(t.models,'{}'::text[]), ARRAY['PUBLIC'])) AS v)
  WHERE t.team_id != '<agents-team-id>' RETURNING team_alias, array_length(models,1);
```
Конкретные команды: `WHERE t.team_id IN ('<id1>','<id2>')`.

**Проверка гранта — только через `@>`, НЕ `= ANY`:**
```sql
SELECT team_alias FROM "LiteLLM_TeamTable" WHERE models @> ARRAY['PUBLIC'];
```
(`'{PUBLIC}' = ANY(models)` молча возвращает пусто — ложный «не выдано».)

## 6. model_cost — ДВА-ТРИ КЛЮЧА

У LiteLLM lookup в `cost_per_token` идёт по `custom_openai/<bare>` ПЕРВЫМ,
bare — третьим; одного bare-ключа бывает недостаточно (spend=0 у hy4,
при этом у glm-5-2 работал — race с register_model). Для tencent-плана
SpendLogs пишет **UPSTREAM**-имя (например `deepseek/deepseek-flash` при
публичном `tencent/DeepSeek-V4.1-Flash`) — третья запись под публичное имя
не помешает. Итого добавляй ОБА (а лучше все три):

**Провайдер openrouter — особенность**: SpendLogs логгирует модель **С
префиксом провайдера** — точный ключ = `openrouter/<or-slug>` (например
`openrouter/deepseek/deepseek-v4.1-flash`). После первого живого вызова
проверь фактическое имя в SpendLogs и добавь ключ ИМЕННО его.

```yaml
    'UPSTREAM':
      input_cost_per_token: <X>e-YY
      output_cost_per_token: <Z>e-YY
    'custom_openai/UPSTREAM':
      input_cost_per_token: <X>e-YY
      output_cost_per_token: <Z>e-YY
      litellm_provider: custom_openai
```

Вставка (файл root-owned, anchor — соседняя запись; см. vm-ssh §4 про
верификацию имён после записи):

```bash
echo '<sudo>' | sudo -S python3 -c "
with open('/opt/litellm/config.yaml') as f: t = f.read()
anchor = \"    'соседняя-запись':\n      input_cost_per_token: ...\n\"
add = \"    'UPSTREAM':\n      input_cost_per_token: <X>e-YY\n      output_cost_per_token: <Z>e-YY\n    'custom_openai/UPSTREAM':\n      input_cost_per_token: <X>e-YY\n      output_cost_per_token: <Z>e-YY\n      litellm_provider: custom_openai\n\"
if \"    'UPSTREAM':\" in t: print('already')
elif anchor not in t: print('anchor MISS')
else: t = t.replace(anchor, anchor + add, 1); open('/opt/litellm/config.yaml','w').write(t); print('OK')
"
python3 -c "import yaml; print(yaml.safe_load(open('/opt/litellm/config.yaml'))['litellm_settings']['model_cost'].get('UPSTREAM'))"
```
Осторожно: sudo + heredoc одновременно съедают пароль — комбинируй как
`echo pass | sudo -S python3 -c "..."` (одна строка, без `<< EOF`).

Warning `register_model: ... cache cost fields will default to 0` —
БЕЗВРЕДЕН (это про cache-поля, не про обычные цены).

## 7. reasoning (только для reasoning-моделей)

`/opt/opencode-setup/api.py` (sudo python replace по anchor):
- `_REASONING_CAPABLE` — добавить точное `PUBLIC` имя
- `_REASONING_VARIANTS` — добавить low/high/max варианты, если модель
  хонорирует `reasoning_effort` (проверь пробой: одинаковая задача с
  low/max — у reasoning-моделей max даёт 1.5–2× reasoning-токенов baseline;
  слабый градиент — тоже хонорирует, просто мягче)

## 7b. Опциональные пробы

**Boundary max_output** (бинарный поиск по max_tokens):
`max_tokens: 393216` → 200, `393217` → 400 (пример V4.1-Flash).
Вписывай найденное в `model_info.max_tokens`.

**Vision без PIL** (в контейнере нет Pillow) — крафт PNG вручную:
```python
import zlib, struct, base64
def png(w, h, rgb):
    def chunk(t, d):
        c = t + d
        return struct.pack('>I', len(d)) + c + struct.pack('>I', zlib.crc32(c))
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    raw = b''.join(b'\x00' + bytes(rgb) * w for _ in range(h))
    return base64.b64encode(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
        + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')).decode()
```
Красный 64×64 → спросить цвет → «Red» = vision OK. Битая 1×1 даёт
ложное «unsupported image» — не доверяй отрицательному результату на мусоре.

## 8. Рестарты

```bash
docker restart litellm        # + readiness loop (vm-ssh §5)
echo '<sudo>' | sudo -S systemctl restart opencode-api
```

## 9. Smoke: 200 + spend (по UPSTREAM-имени!)

Temp-ключ ОБЯЗАТЕЛЬНО с `team_id` — иначе 400 "Invalid model name":

```bash
K=$(curl -sk -X POST http://127.0.0.1:4001/key/generate \
  -H "Authorization: Bearer $MK" -H "Content-Type: application/json" \
  -d '{"key_alias":"tmp-<tag>","team_id":"<team-id>","user_id":"<user-id>","duration":"15m"}' \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['key'])")

curl -sk --max-time 30 -X POST http://127.0.0.1:4001/v1/chat/completions \
  -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"model":"PUBLIC","messages":[{"role":"user","content":"Reply pong"}],"max_tokens":50}' \
  -w "\nHTTP %{http_code}\n"

sleep 4   # строка spend коммитится асинхронно
docker exec -i litellm-pg psql -U litellm -d litellm -c \
  "SELECT model, prompt_tokens, completion_tokens, ROUND(spend::numeric,6) FROM \"LiteLLM_SpendLogs\" WHERE \"startTime\" > NOW() - interval '5 min' AND model='UPSTREAM';"
```

**SpendLogs пишет model = UPSTREAM-имя**, не публичный алиас.
Сверь математику: `p*ic + c*oc ≈ spend`. Spend=0 → скилл litellm-diagnose.

## 10. Чистка temp-ключа

```bash
curl -sk -X POST http://127.0.0.1:4001/key/delete \
  -H "Authorization: Bearer $MK" -H "Content-Type: application/json" \
  -d '{"key_aliases":["tmp-<tag>"]}'
```

## 11. Документация

- **CHANGELOG.md** (`P:\Programming\bifrost\CHANGELOG.md`), секция `[YYYY-MM-DDx]`:
  public/upstream имена, base URL, **ключи ТОЛЬКО плейсхолдерами**
  (`sk-...-placeholder`) — репо публичное! Команды, цены, verified spend.
- **serena memory** `infra/hcbifrost-vm-litellm` — реальные ключи и детали.

## Gotchas (выучено кровью)

1. Запятые в `PUBLIC` → 403 (обрезание имени в auth check)
2. model_cost одним bare-ключом → вероятный spend=0; добавляй оба ключа
3. Temp-ключ без `team_id` → 400 "Invalid model name ... Call /v1/models"
4. SpendLogs ищи по UPSTREAM имени
5. `register_model` cache-warning — безвреден
6. ssh рвёт длинные скрипты → дроби шаги; пустой вывод → повтори проще
7. После записи файлов на VM — верифицируй имена (`repr` listdir, vm-ssh §4)
8. Файл конфига root-owned → только sudo; sudo+heredoc съедает пароль
9. TokenPlan: имя в консоли ≠ API-id (DeepSeek-V4.1-Flash → `deepseek/deepseek-flash`);
   403002 «key not authorized» одинаков для несуществующих и невключённых —
   существование проверяй только live-пробой; активация в консоли ≠ мгновенный доступ
10. Двойной рестарт ОБЯЗАТЕЛЕН: `docker restart litellm` + `systemctl restart
    opencode-api` — api.py кэширует /model/info при старте, без второго
    рестарта opencode не увидит модель/лимиты
11. Upstream с фиксированным temperature (Kimi K2.8: «only 1 is allowed») →
    дроп параметра в `/opt/litellm/user_agent_hook.py` (образец — блок
    k2.8 в async_pre_call_hook), иначе клиенты с temperature≠1 ловят 400
12. Цены всегда ПОЛНЫЕ (без скидок): см. §3; после ручной правки model_cost
    не забудь рестарт litellm
13. Кэш-токены: cached_tokens в SpendLogs НЕТ — кэш-статистика только в
    `LiteLLM_Daily*` (cache_read_input_tokens / cache_creation_input_tokens,
    date — TEXT, кастуй `date::timestamp`)
