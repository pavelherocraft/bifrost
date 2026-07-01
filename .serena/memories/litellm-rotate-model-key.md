# LiteLLM: обновление API key модели без пересоздания

## Способ 1: PATCH endpoint (рекомендуемый)

LiteLLM поддерживает `PATCH /model/{model_id}/update` для частичного обновления модели из БД. Не нужно пересоздавать модель — меняется только указанное поле.

### Шаг 1: Узнать model_id

`model_id` — это UUID из `LiteLLM_ProxyModelTable.id` (НЕ `model_name`!).

```bash
# Вариант A: через /model/info
curl -s -H "Authorization: Bearer sk-..." \
  "https://hcbifrost.herocraft.com/litellm/model/info" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); m=[x for x in d['data'] if x['model_name']=='GLM-5.2'][0]; print(m['model_info']['id'])"

# Вариант B: прямой SQL
docker exec litellm-pg psql -U litellm -d litellm -c "SELECT model_id, model_name FROM \"LiteLLM_ProxyModelTable\" WHERE model_name IN ('GLM-5.1','GLM-4.7','GLM-5.2');"
```

### Шаг 2: PATCH

```bash
# Подготовить payload (api_key как plain text — LiteLLM сам зашифрует)
cat > /tmp/patch.json <<EOF
{"litellm_params": {"api_key": "новый-ключ-здесь"}}
EOF

curl -X PATCH "https://hcbifrost.herocraft.com/litellm/model/$MODEL_ID/update" \
  -H "Authorization: Bearer sk-litellm-placeholder-replace-before-prod" \
  -H "Content-Type: application/json" \
  --data @/tmp/patch.json
```

### Шаг 3: Проверить

Сделать тестовый запрос:
```bash
curl -X POST -H "Authorization: Bearer sk-..." \
  -H "Content-Type: application/json" \
  -d '{"model":"MODEL_NAME","messages":[{"role":"user","content":"hi"}],"max_tokens":10}' \
  "https://hcbifrost.herocraft.com/litellm/v1/chat/completions"
```
Если ответ пришёл (даже с reasoning_content) — ключ валидный.

## Что принимает PATCH

Schema `updateDeployment` в `/app/litellm/types/router.py:325`:
- `model_name` (опц.)
- `litellm_params` (опц.) — принимает `api_key`, `api_base`, `custom_llm_provider`, `model`, `timeout`, `api_version`, `organization`, `tpm`, `rpm` и др.
- `model_info` (опц.) — позволяет менять `supports_vision`, `max_input_tokens`, `max_tokens` и т.п. через PATCH вместо SQL
- `blocked` (опц.)

PATCH-семантика: обновляет **только** переданные поля, остальные сохраняются.

## Ограничения

1. **Только для моделей из БД** (`store_model_in_db=True`). Модели из `config.yaml` не редактируются — endpoint вернёт 400 "Cannot edit config-based model".
2. **model_id ≠ model_name** — `model_id` это UUID, `model_name` это отображаемое имя ("GLM-5.2"). PATCH работает по UUID.
3. **Hot reload** — изменения подхватываются сразу, без перезапуска proxy. Внутри `patch_model` после UPDATE БД вызывается `add_or_update_deployment` который пересоздаёт deployment в `llm_router`.
4. **Шифрование автоматическое** — передаёшь plain text api_key, LiteLLM сам шифрует при записи в БД.

## Способ 2: прямой SQL (НЕ рекомендуется)

В БД `litellm_params.api_key` хранится **зашифрованный** (AES с LITELLM_MASTER_KEY + salt). Чтобы заменить только api_key через SQL:

1. Расшифровать существующий JSON
2. Заменить поле
3. Зашифровать обратно
4. UPDATE

Сложно и хрупко — лучше использовать PATCH endpoint.

## Способ 3: UI

LiteLLM UI → Models → выбрать модель → Edit → Credentials/Provider секция. Работает, но медленно для частых операций.

## Скрипт для batch-обновления

`C:\Users\Admin\AppData\Local\Temp\opencode\litellm-files\patch_keys.sh` — пример:
```bash
#!/bin/bash
for mid in f393fafb-... 647ba2c7-... fcb40715-...; do
  echo "--- $mid ---"
  curl -s -X PATCH -H "Authorization: Bearer sk-..." \
    -H "Content-Type: application/json" \
    --data @/opt/litellm/patch_payload.json \
    "https://hcbifrost.herocraft.com/litellm/model/$mid/update" | head -c 400
  echo
done
```

## Связанные endpoints

- `POST /model/new` — создать новую модель
- `POST /model/delete` — удалить модель
- `GET /model/info` — список всех моделей с расшифрованными параметрами (но НЕ api_key)
- `POST /model/{model_id}/update` — полная замена (не PATCH!)
- `PATCH /model/{model_id}/update` — частичное обновление ← используем этот

## Источник

`/app/litellm/proxy/management_endpoints/model_management_endpoints.py:187-...` — функция `patch_model`. Schema в `/app/litellm/types/router.py:325` — `class updateDeployment(BaseModel)`.