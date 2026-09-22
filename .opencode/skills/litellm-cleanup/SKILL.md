---
name: litellm-cleanup
description: Delete temporary virtual keys (tmp-*) from the LiteLLM DB and temp files on the hcbifrost VM and locally. Careful, conservative cleanup — never touches live configs, backups, or system files. Use when the user asks to clean up temp keys/files after LiteLLM work. Triggers on "почисти", "удали временные ключи", "удали temp файлы", "cleanup temp keys", "clean tmp files". Transport — see skill vm-ssh.
---

# Чистка временных ключей и файлов (hcbifrost)

Принцип: **если сомнения — оставь**. Удаляем только заведомо временное.

## 0. Секреты

VM-доступ — serena memory `infra/hcbifrost-vm-litellm`. Транспорт — скилл `vm-ssh`.

## 1. Temp-ключи LiteLLM (tmp-*)

Список → удаление → верификация:

```bash
docker exec -i litellm-pg psql -U litellm -d litellm -c \
  "SELECT key_alias, expires::timestamp(0) FROM \"LiteLLM_VerificationToken\" WHERE key_alias LIKE 'tmp-%' ORDER BY key_alias;"
```

Удаление (если список большой — собери JSON файлом, чтобы не воевать с кавычками):

```bash
docker exec -i litellm-pg psql -U litellm -d litellm -tA -c \
  "SELECT key_alias FROM \"LiteLLM_VerificationToken\" WHERE key_alias LIKE 'tmp-%';" > /tmp/tmpkeys.txt
python3 -c 'import json; lines=open("/tmp/tmpkeys.txt").read().splitlines(); print(json.dumps({"key_aliases":[l for l in lines if l.strip()]}))' > /tmp/del.json
curl -sk -X POST http://127.0.0.1:4001/key/delete \
  -H "Authorization: Bearer $MK" -H "Content-Type: application/json" -d @/tmp/del.json
docker exec -i litellm-pg psql -U litellm -d litellm -c \
  "SELECT COUNT(*) FROM \"LiteLLM_VerificationToken\" WHERE key_alias LIKE 'tmp-%';"   # должно быть 0
```

Формат API: `{"key_aliases": [...]}` или `{"keys": [...]}`. НЕ `{"tokens": ...}` (422).

## 2. Файлы на VM

### `/tmp` — только файлы владельца dev01
```bash
# сухой прогон:
find /tmp/ -maxdepth 1 -type f -user dev01 -print
# удаление:
find /tmp/ -maxdepth 1 -type f -user dev01 -delete
```

### НЕ ТРОГАТЬ на VM
- root-owned: `.font-unix`, `.ICE-unix`, `.X11-unix`, `.XIM-unix`, `systemd-private-*`
- `/tmp/payload_logs_retention.log` — ЖИВОЙ лог crontab-ретеншна (пересоздаётся в 03:30), не мусор
- `/opt/litellm/admin/*` — ЖИВЫЕ файлы (payload_logs_hook.py, config-патчи)
- `/opt/litellm/config.yaml` и все `*.bak*` (бэкапы: `.bak.tencent-*`, `.bak.model-cost-*`, ...)
- таблицы БД `TeamTable_bak_*`, `ProxyModelTable_bak_*` — история откатов
- `/tmp/opencode/` — пустой workspace, безвреден (можно оставить)

### Проверь «дубли» имён после чистки (урок инцидента `\r`)
Если в `/opt/litellm/admin/` видны два похожих файла — проверь имена:
```bash
python3 -c "import os; print([repr(f) for f in os.listdir('/opt/litellm/admin')])"
```
Файл с `\r`/пробелом в конце имени — дубль от неудачной записи; удаляй только
после `diff` с каноничным.

## 3. Локально (Windows)

- `C:\Users\Admin\AppData\Local\Temp\opencode\` — рабочие копии и `.b64` транспортные файлы; `askpass.cmd` НЕ удалять (нужен ssh)
- Спрашивай пользователя при малейших сомнениях по конкретным файлам

## 4. Отчёт

После чистки: что удалено (списком), что оставлено и почему, верификация
(COUNT=0 ключей, листинг /tmp).
