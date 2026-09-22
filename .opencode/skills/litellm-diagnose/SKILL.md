---
name: litellm-diagnose
description: Debug LiteLLM on the hcbifrost VM — spend=0 cost tracking ladder, 403 model access (comma truncation), user login/session diagnostics, password reset, spend audits. Use when something on the LiteLLM server misbehaves. Triggers on "spend не считается", "спенды нулевые", "403 на модель", "не пускает в личный кабинет", "не работает пароль", "сбрось пароль", "проверь спенды", "spend is zero", "model access denied", "reset password". Transport — see skill vm-ssh.
---

# Диагностика LiteLLM (hcbifrost)

Секреты — serena memory `infra/hcbifrost-vm-litellm`. Транспорт — скилл `vm-ssh`.

## 1. Spend = $0 (лестница)

1. **Ищешь по правильному имени?** SpendLogs пишет `model` = UPSTREAM имя
   (`litellm_params.model`), не публичный алиас:
   ```sql
   SELECT model, prompt_tokens, completion_tokens, ROUND(spend::numeric,6)
   FROM "LiteLLM_SpendLogs" WHERE "startTime" > NOW() - interval '15 min' ORDER BY "startTime" DESC;
   ```
2. **Строка вообще есть?** Нет строки → вызов не дошёл/ошибка (пустой call_type = failed).
3. **model_cost в config.yaml?** `/opt/litellm/config.yaml` →
   `litellm_settings.model_cost`. Нужны ОБА ключа — `'<upstream>'` И
   `'custom_openai/<upstream>'` (с `litellm_provider: custom_openai`) —
   см. скилл litellm-add-model §6. Проверь yaml:
   `python3 -c "import yaml; print(yaml.safe_load(open('/opt/litellm/config.yaml'))['litellm_settings']['model_cost'].get('<upstream>'))"`
4. **Рестарт делал?** Конфиг подхватывается только на старте (`docker restart litellm` + readiness).
5. **ic/oc на строке модели** (страховка, display в `/v1/model/info`):
   ```sql
   UPDATE "LiteLLM_ProxyModelTable" SET model_info = model_info ||
     '{"input_cost_per_token": <ic>, "output_cost_per_token": <oc>}'::jsonb
   WHERE model_name = '<PUBLIC>' RETURNING model_name;
   ```
   (после — рестарт litellm)
6. Глубже: lookup идёт `_select_model_name_for_cost_calc` → `custom_openai/<bare>` →
   `cost_per_token` (combined → model → without_prefix). `register_model` cache-warning — безвреден.

Сверка математики: `expected = prompt_tokens*ic + completion_tokens*oc`.

## 2. 403 на модель (доступ)

- **Текст ошибки обрезан по запятой** (`Tried to access tencent/x (reserved`)?
  → в публичном имени ЕСТЬ запятая, LiteLLM режет имя в auth check.
  Лечение: пересоздать модель с ` - ` вместо `, ` (см. litellm-add-model §1).
- Проверь членство в команде:
  ```sql
  SELECT team_alias, '<PUBLIC>' = ANY(models) FROM "LiteLLM_TeamTable";
  ```
- Ключ вызывающего принадлежит команде? `SELECT key_alias, team_id FROM "LiteLLM_VerificationToken" WHERE token = '<hash>';`

## 3. Логины в LiteLLM UI

UI-сессии — строки в `LiteLLM_VerificationToken` c `team_id='litellm-dashboard'`:
`created_at` = время входа, `expires` = +24ч.

```sql
SELECT created_at::timestamp(0), expires::timestamp(0)
FROM "LiteLLM_VerificationToken"
WHERE team_id='litellm-dashboard' AND user_id='<uid>'
ORDER BY created_at DESC LIMIT 5;
```
«Пароль перестал работать» часто = истекла 24ч-сессия. Audit log выключен
(enterprise-гейт) — причину «слома» старого пароля не восстановить.

## 4. Сброс пароля пользователя

```bash
# верификация старого hash (scrypt):
docker exec -i litellm python3 -c "
from litellm.proxy.utils import verify_password
print(verify_password('<plain>', '<hash из LiteLLM_UserTable.password_hash>'))
"
# сброс:
curl -sk -X POST http://127.0.0.1:4001/user/update \
  -H "Authorization: Bearer $MK" -H "Content-Type: application/json" \
  -d '{"user_id":"<uid>","password":"<новый>"}'
```
Новый пароль — сгенерируй и сообщи пользователю; факт сброса — в CHANGELOG
(без пароля!) и memory.

## 5. Аудит спендов

```sql
SELECT model, call_type, ROUND(SUM(spend)::numeric,4) spend, COUNT(*)
FROM "LiteLLM_SpendLogs"
WHERE "startTime" > NOW() - interval '30 days'
GROUP BY 1,2 ORDER BY SUM(spend) DESC LIMIT 30;
```
Цены для отчёта: `/v1/model/info` (поля внутри `model_info`, не `info`).
$0 у модели = нет в model_cost (или одного ключа мало — см. §1.3).

## 6. Общие проверки

- readiness: `curl -sk -o /dev/null -w "%{http_code}" http://127.0.0.1:4001/health/readiness`
- контейнеры: `docker ps --format '{{.Names}}\t{{.Status}}'`
- логи старта litellm: docker LogPath + `grep -a "ACTIVE\|FAILED"` (vm-ssh §5)
- master key НЕ работает для inference (block_master_key_hook) — тестируй
  только virtual keys с `team_id` (генерация: litellm-add-model §9)
