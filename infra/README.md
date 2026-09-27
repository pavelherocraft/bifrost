# infra/ — кастомные решения hcbifrost VM (source of truth: VM)

Версионированные копии всех кастомных артефактов, которые живут на VM
(`dev01@162.55.137.149:1995`). **Источник истины — VM**; этот каталог —
снапшот для git-истории и DR. Синк: `tools/sync.ps1` (по субботам 09:00
таск `hcbifrost-weekly-snapshot-sync` + руками при необходимости).

## Состав

| путь | что это |
|---|---|
| `litellm/user_agent_hook.py` | главный хук прокси: UA-инъекция, reasoning-конвертация (`reasoning`→`reasoning_effort`), k2.8 temperature-drop, kimi empty-msg sanitize, HOOK_TRACE |
| `litellm/image_rate_limit_hook.py` | image-лимиты (25/50 в день), стрим-фикс для image API, блок gpt-image-2+high |
| `litellm/block_master_key_hook.py` | блокировка master key для инференса |
| `litellm/master_key.py` | вычисление master key |
| `litellm/litellm_entrypoint.sh` | кастомный entrypoint контейнера |
| `litellm/utils_patched.py` | обезьянья заплатка litellm/proxy/utils.py (регистрация хуков) |
| `litellm/swap_glm_credentials.py` | свапер GLM-креденшелов (пароли замаскированы) |
| `litellm/start.sh` / `stop.sh` / `start_api.sh` | lifecycle контейнеров (секреты замаскированы) |
| `litellm/config.sanitized.yaml` | конфиг прокси: model_cost (OR-полные цены), pass_through_endpoints (MiniMax Media), disable_cooldowns и пр. — **секреты замаскированы** |
| `litellm/admin/` | 23 админ-скрипта: or_full_prices (рефреш OR-цен), cost_compare/atlas_spend/points_calc (поинты Atlas vs Alibaba vs OR), healthcheck, backup_nightly, weekly_tg_snapshot, monthly_fulldump, spendlogs_retention (180д), audit_retention (90д), payload_logs_*, glm_swap_*, build_model_cost и др. |
| `litellm/nginx/litellm-bifrost.conf` | сервер-блок: лимиты тел (50m/100M), sub_filter кнопок UI, /setup-opencode alias |
| `litellm/crontab.root` | все cron-задачи VM (root) |
| `litellm/VM_README.md` | собственная документация VM (написана на VM; часть деталей могла устареть — край: «Cloudflare edge», по факту Timeweb) |
| `opencode-setup/api.py` | сервис :9000 — резолюция моделей для Setup-страницы (_REASONING_VARIANTS, vk-info, /models) |
| `opencode-setup/index.html` | Setup-страница (генератор opencode.json) |
| `opencode-setup/*-btn.js` | кнопки UI: users, vkeys, glm-swap, payload-logs (инжектятся nginx sub_filter) |
| `systemd/opencode-api.service` | юнит сервиса opencode-api |
| `sql/audit_schema.sql` | кастомный PG-аудит: таблица audit_custom + audit_row_change() + 4 триггера (спас при member_delete-каскаде) |
| `tools/sync.ps1` | этот синк (VM → infra/ + локальные бэкапы) |

## Политика

- **Никогда не коммитить**: `.env`, `tg.env`, `.new_master_key`, DB-дампы
  (backups/, full-dumps/ — там хэши ключей и данные SpendLogs), `*.bak`,
  логи, одноразовый мусор (`check_*/fix_*/test_*/db_*/set_*/verify_*` на VM)
- **Санитайз обязателен**: `sync.ps1` маскирует `Bearer <tok>`, `api_key:`,
  `password`, известные префиксы ключей (`sk-`, `sk-or-v1`, `sk-tp-`,
  `sk-cp-`) и sudo-пароль; перед коммитом — секрет-скан
- Изменения делаются **на VM**, затем sync → коммит (диф в `infra/`)
- Восстановление DR: `start.sh` (пересоздать контейнеры) + config.yaml из
  снапшота (подставить секреты) + `audit_schema.sql` + admin/ + hooks
