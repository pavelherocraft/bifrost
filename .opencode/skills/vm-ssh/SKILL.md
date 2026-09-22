---
name: vm-ssh
description: Ad-hoc SSH operations on the hcbifrost VM (LiteLLM/Bifrost prod) from Windows PowerShell — read nginx/docker logs, query Postgres, restart services, transfer files safely, run diagnostics. Use for any server/VM task NOT covered by litellm-add-model / litellm-cleanup / litellm-diagnose. Triggers on "на сервере", "проверь на сервере", "прочитай логи", "рестартни", "docker logs", "nginx логи", "запрос в базу", "check the vm", "ssh to server".
---

# VM SSH — транспорт и ad-hoc операции (hcbifrost VM)

Единый способ выполнения команд на прод-VM из Windows PowerShell.
Рабочие процессы (добавление моделей, чистка, диагностика) — в скиллах
`litellm-add-model`, `litellm-cleanup`, `litellm-diagnose`; они используют
этот же транспорт.

## 0. Секреты

В этом скилле секретов НЕТ. Перед работой прочитай serena memory
`infra/hcbifrost-vm-litellm` — там host/port/user, sudo-пароль, LiteLLM
master key, имена контейнеров, team IDs.

## 1. askpass (SSH password auth без интерактива)

SSH на Windows требует askpass-хелпер. Он лежит в Temp и может пропасть
после чистки диска — ВСЕГДА проверяй и при необходимости пересоздавай:

```powershell
$p = "C:\Users\Admin\AppData\Local\Temp\opencode\askpass.cmd"
if (-not (Test-Path $p)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $p) | Out-Null
    # <ssh-password> — из serena memory
    Set-Content -Path $p -Value "@echo <ssh-password>"
}
```

Формат файла — одна строка `@echo <password>`.

## 2. Транспорт: ПРЕЖДЕ ВСЕГО хелпер vm-ssh-helper.ps1

С 2026-09-17 есть готовый хелпер `P:\Programming\bifrost\vm-ssh-helper.ps1`
(host alias `hcbifrost` в `~/.ssh/config`, askpass ставит сам, base64 через
stdin, устойчив к BOM/CRLF — PowerShell prepend'ит UTF-8 BOM в пайпы!).
ВАЖНО: использовать ИМЕННО `vm-ssh-helper.ps1` — файлы с именем `vmssh.ps1`
(в любом каталоге, включая корень репо) таинственно исчезают каждые
5–15 минут; хелпер с другим именем выживает.

```powershell
& "P:\Programming\bifrost\vmssh.ps1" -Command "docker ps --format '{{.Names}}'"
& "P:\Programming\bifrost\vmssh.ps1" -File "C:\local\script.sh"      # или .py c -Python
& "P:\Programming\bifrost\vmssh.ps1" -Get /opt/litellm/config.yaml -Out "C:\local\config.yaml"
```

Всё сложное (многострочные скрипты, кавычки) — пиши в локальный .sh-файл и
`-File`; `-Command` годится только для простых однострочников без вложенных
кавычек.

### Ручной паттерн (fallback, если хелпер недоступен)

PowerShell ломает кавычки/`$()`/`%{}` в однострочных ssh-командах.
Единственный надёжный паттерн:

```powershell
$script = @'
# bash-скрипт целиком, любые кавычки
'@
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script))
$env:SSH_ASKPASS = "C:\Users\Admin\AppData\Local\Temp\opencode\askpass.cmd"
$env:SSH_ASKPASS_REQUIRE = "force"
$env:DISPLAY = "none"
$b64 | ssh -T -o PreferredAuthentications=password -o PubkeyAuthentication=no -o ConnectTimeout=20 hcbifrost 'tr -Cd "A-Za-z0-9+/=" | base64 -d | bash'
```

(`tr -Cd` обязательна: PS-пайп prepend'ит UTF-8 BOM + CRLF, которые валят
`base64 -d`. Хост/порт/юзер берутся из `~/.ssh/config` alias `hcbifrost`.)

Правила:
- env-переменные ставь В ТОМ ЖЕ вызове bash-инструмента, что и ssh (между вызовами они не живут)
- here-string только `@'...'@` (literal). Одинарные кавычки в обычной строке PS ломают парсер
- НЕ передавай bash-код прямо в аргумент ssh — `%{http_code}`, `$((i+1))`, `|` интерпретируются PowerShell
- `head`/`tail`/`grep` локально НЕ существуют — используй `Select-Object -First/-Last`, `Select-String`

## 3. Обрывы соединения

- ssh регулярно рвёт длинные output/mid-stream. **Дроби шаги**: один вызов = одна логическая операция
- Пустой вывод при ожидаемом тексте ≠ «пусто» — часто это обрезанный стрим, повтори команду проще/короче
- Если соединение вообще не проходит: `Test-NetConnection <host> -Port <port>` — VM иногда лежит несколько минут, подожди и повтори (цикл из 5 попыток по 15 сек)

## 4. Запись файлов на VM (КРИТИЧЕСКИ ВАЖНО)

НИКОГДА не пиши файлы через heredoc/echo из транспортного слоя: CRLF-контаминация
создаёт файлы с именами вида `file.py\r`, которые молча не импортируются
(реальный инцидент: хук payload_logs отвалился из-за `\r` в имени).

Правильный паттерн — base64 локального файла + `printf`:

```powershell
$b64file = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes("C:\local\file.py"))
$script1 = "printf '%s' '$b64file' | base64 -d > /opt/litellm/admin/file.py; wc -l /opt/litellm/admin/file.py"
# ... транспортировка $script1 тем же base64-паттерном
```

ПОСЛЕ каждой записи — ОБЯЗАТЕЛЬНАЯ верификация имён:

```bash
python3 -c "import os; print([repr(f) for f in os.listdir('/opt/litellm/admin')])"
```

(`repr` подсвечивает `\r` и пробелы в именах). Плюс проверь контент-маркеры:
`sed -n '<N>p' <file>` или `grep -c '<marker>' <file>`.

Правка существующих файлов на VM — через `sudo python3 -c "..."` с
anchor/insert-логикой (файлы root-owned; см. примеры в litellm-add-model).

## 5. Готовые рецепты

### Readiness litellm после рестарта
```bash
docker restart litellm
i=0
while [ $i -lt 30 ]; do
  i=$((i+1))
  code=$(curl -sk -o /dev/null -w "%{http_code}" http://127.0.0.1:4001/health/readiness)
  if [ "$code" = "200" ]; then echo "ready ($i)"; break; fi
  sleep 5
done
```

### Docker logs (НЕ вызывай `docker logs` — виснет на больших логах)
```bash
LP=$(docker inspect litellm --format "{{.LogPath}}")
echo '<sudo-pass>' | sudo -S grep -a "PATTERN" "$LP" | tail -5
# или по хвосту: echo '<sudo-pass>' | sudo -S tail -c 500000 "$LP" | grep -ao 'PATTERN.\{0,100\}'
```

### nginx logs (нужен sudo — файлы www-data:adm 640)
```bash
echo '<sudo-pass>' | sudo -S grep "PATTERN" /var/log/nginx/access.log | tail -5
```
Молча пустой grep = отказ в доступе, а не «нет записей».

### Postgres (LiteLLM)
```bash
docker exec -i litellm-pg psql -U litellm -d litellm -c 'SELECT ... FROM "LiteLLM_SpendLogs" WHERE "startTime" > NOW() - interval '"'"'1 hour'"'"';'
```
Имена таблиц PascalCase в кавычках; `"startTime"` тоже в кавычках. Внутри
base64-скрипта обычные двойные кавычки, экранирование не нужно.

### sudo на VM
```bash
echo '<sudo-pass>' | sudo -S <command>
# python-скрипты под sudo:
echo '<sudo-pass>' | sudo -S python3 -c "<код>"
```

### Рестарт сервисов
```bash
docker restart litellm                                          # LiteLLM proxy
echo '<sudo-pass>' | sudo -S systemctl restart opencode-api     # opencode API
systemctl is-active opencode-api                                # проверка
```

### Копирование файла с VM на локальный диск
scp на этой машине зависает. Надёжный путь — base64 через ssh-вывод:
```powershell
$script_b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("base64 -w0 /remote/file"))
$remote = "echo $script_b64 | base64 -d | bash"
$out = ssh -T <опции> user@host $remote
[System.IO.File]::WriteAllBytes("C:\local\copy.b64", [System.Text.Encoding]::ASCII.GetBytes($out))
[System.IO.File]::WriteAllBytes("C:\local\copy", [Convert]::FromBase64String((Get-Content C:\local\copy.b64 -Raw)))
```

## 6. Что НЕ делать

- НЕ используй `docker logs litellm` напрямую (11+ ГБ, виснет)
- НЕ пиши файлы на VM через heredoc `<< EOF` из PowerShell-транспорта (CR/LF)
- НЕ удаляй ничего в `/opt/litellm/` кроме явных tmp-файлов (там живые конфиги + бэкапы)
- НЕ оставляй temp-ключи `tmp-*` в LiteLLM (чистка — скилл litellm-cleanup)
