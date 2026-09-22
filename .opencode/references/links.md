# Постоянные ссылки и опорные данные (канон)

> Читается агентами каждую сессию. Секреты (пароли, ключи) — ТОЛЬКО в serena
> memory `infra/hcbifrost-vm-litellm`, здесь их нет. Обновляй при находках.

## 1. Цены и тарифы

| Что | Ссылка / команда |
|---|---|
| OR: все модели (цены уже со скидками) | `https://openrouter.ai/api/v1/models` |
| OR: цены по провайдерам + СКИДКИ | `https://openrouter.ai/api/v1/models/{slug}/endpoints` → поле `pricing.discount` |
| OR: страница модели | `https://openrouter.ai/<slug>` |
| Машинно-читаемая страница модели (у некоторых вендоров) | `<url>/llms.txt` (работает у OR, atlascloud) |
| Atlas Coding Plan — таблица мультипликаторов поинтов | https://www.atlascloud.ai/coding-plan (секция Pay-per-Use Packs) |
| Atlas: как биллятся модели | https://atlascloud.ai/docs/en/billing/model-billing |
| Atlas: примеры расчёта (кэш-токены!) | https://atlascloud.ai/docs/en/billing/examples |
| Alibaba Model Studio pricing | https://www.alibabacloud.com/help/en/model-studio/model-pricing |

**Формулы:**
- Полная (безскидочная) цена OR = `price / (1 − discount)` из endpoints API
- Atlas поинты: `280M поинтов = $99`; поинты = токены × мультипликатор
  (категории: input / output / cache_write / cache_read — все абсолютные,
  = официальная цена $/1M × 1.98); доллары = поинты ÷ 280,000,000 × 99
- Эффективная цена Atlas points ≈ ×0.7 от официального прайса (30% скидка)
- Договор по биллингу: model_cost = OR-полные цены для ВСЕХ моделей,
  независимо от реального провайдера (референс-тариф)

## 2. LiteLLM (доки и внутренности)

| Что | Ссылка |
|---|---|
| Salt key / шифрование litellm_params | https://docs.litellm.ai/docs/proxy/prod#5-set-litellm-salt-key |
| Password: hash/verify | `litellm.proxy.utils` → `hash_password()` / `verify_password()` (НЕ proxy_common_utils) |
| Аудит | нативный `store_audit_logs` — enterprise-only, на community молча не пишет; у нас кастомный PG-аудит `audit_custom` |

**Наш LB/DB:**
- `LiteLLM_ProxyModelTable.litellm_params` — model/api_base/api_key хранятся
  ЗАШИФРОВАННЫМИ: SQL-сравнение с plaintext НЕ работает. Расшифровка в
  контейнере: `decrypt_value_helper(blob, 'dbg')` из
  `litellm.proxy.common_utils.encrypt_decrypt_utils`
- Кэш-токены только в `LiteLLM_Daily{User,Team,Tag,EndUser}Spend`
  (`cache_read_input_tokens`, `cache_creation_input_tokens`); в SpendLogs их
  нет; `date` — TEXT → кастовать `date::timestamp`
- UI Usage читает Daily-таблицы, не SpendLogs

## 3. Наша инфра

| Что | Где |
|---|---|
| VM (LiteLLM prod) | `dev01@162.55.137.149:1995` (пароль в serena memory); контейнеры: litellm (:4001), litellm-pg |
| **Передний край** | `89.19.213.124` — **Timeweb reverse-proxy** (managed-хостинг, доступа нет): TLS-терминатор `hcbifrost.herocraft.com`, proxy → наша VM. Лимиты: **read-timeout 60s** (долгие генерации → 504) и **client_max_body_size 1MB** — всё >1MiB (компакции с картинками) режется 413-HTML ещё до нас |
| Наш nginx (VM) | `/etc/nginx/sites-enabled/litellm-bifrost`; лимиты 100M server / 50m локации; слушает 80/8080, НЕ 443 |
| opencode-api | VM `:9000` (nginx стрипает `/setup-opencode/api/`); кэширует /model/info при старте → после изменений моделей НУЖЕН `systemctl restart opencode-api` |
| config LiteLLM | `/opt/litellm/config.yaml` (model_cost ~104 записи; root-owned → sudo; yaml round-trip python) |
| Хук запросов | `/opt/litellm/user_agent_hook.py` (UA-инъекция, reasoning_effort allowlist, kimi empty-msg sanitize, k2.8 temperature drop, HOOK_TRACE off) |
| opencode-setup api | `/opt/opencode-setup/api.py` (`_REASONING_CAPABLE`, `_REASONING_VARIANTS`, image-edit список) |
| Admin-скрипты | `/opt/litellm/admin/`: `or_full_prices.py [--apply]` (рефреш OR-полных цен), `cost_compare.py [key] [model] [day]` (Atlas points vs Alibaba vs OR), `atlas_spend.py [day]` (весь Atlas-трафик в поинтах), `points_calc.py`, `audit_retention.sh` (cron 3:15) |
| UI LiteLLM | https://hcbifrost.herocraft.com/litellm/ui/ |
| Cron (root на VM) | 6h: truncate hook-trace/json логов; 3:15: audit retention (>90d); **3:30: spendlogs_retention.sh** (SpendLogs >180д → DELETE + VACUUM; Daily-таблицы не трогаются — архив в месячных полных дампах); **2:45 nightly: backup_nightly.sh** (конфиги+4 таблицы → /opt/backups/litellm/ГГГГММДД, ротация 14д); **5 мин: healthcheck.sh** (liveness/edge/диск/контейнеры → admin/healthcheck.log + ALERT.last + TG-алерт); **Сб 4:00: weekly_tg_snapshot.sh** (бандл → ТГ-группа); **1-го 3:00: monthly_fulldump.sh** (полный pg_dump → /opt/backups/litellm/full/, хранить 2) |
| Telegram | `/opt/litellm/tg.env` (root-600): `TG_BOT_TOKEN`, `TG_CHAT_ID` — бот **@litellm_health_hc_bot** → группа «HC AI HUB» (`-5283012258`), подключён и проверен e2e 2026-09-17. Privacy mode: бот видит только упоминания — достаточно, отправлять может всегда |
| Локальный харнесс | `P:\Programming\bifrost\vm-ssh-helper.ps1` (корень репо; **только это имя** — файлы `vmssh.ps1` таинственно исчезают каждые 5–15 мин, хелпер выживает): **`vm-ssh-helper.ps1`** (`-File x.sh|x.py [-Python]` / `-Command "cmd"` / `-Get remote -Out local` — base64 через stdin, BOM/CRLF-устойчив). В репо `.opencode/bin/`: `sync-vm-snapshots.ps1`, `pull-fulldump.ps1` (зовут хелпер из корня), `setup-tasks.cmd` (разовый запуск под админом — создаёт Windows-таски) |
| Локальные снапшоты | `.opencode/vm-snapshots/` (gitignored): admin-скрипты, hook, api.py, nginx-conf, crontab, config.yaml.sanitized, backups/ (4 бандла), full-dumps/ (3 дампа) |
| Windows-таски | `hcbifrost-weekly-snapshot-sync` (Сб 09:00), `hcbifrost-monthly-fulldump-pull` (1-го 09:00) — создаются setup-tasks.cmd |

## 4. Team IDs (постоянные)

- Agents: `817d2234-ae12-4b46-8f29-0d47d0adbd7d`
- All Access: `02445a34-eb7c-4bff-9a55-c465ff531944`
- General: `80496d6a-1452-4258-aa93-27a12e6fd3a9`

## 5. Связанные памятки и скиллы

- serena memory: `infra/hcbifrost-vm-litellm` (секреты), `infra/litellm-model-updates` (история моделей)
- скиллы: `litellm-add-model`, `litellm-diagnose`, `litellm-cleanup`, `vm-ssh`
