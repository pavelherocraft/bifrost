# sync.ps1 — VM hcbifrost -> infra/ (committed) + локальные бэкапы (gitignored).
# Запуск: руками или таском hcbifrost-weekly-snapshot-sync (суббота 09:00).
# Выход: чистый диф в infra/ -> "пушкоммит" -> git-commit агент.
param([switch]$Quiet)
$ErrorActionPreference = 'Stop'
$repo   = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))   # repo root (tools -> infra -> repo)
$infra  = Join-Path $repo 'infra'
$vmsnap = Join-Path $repo '.opencode\vm-snapshots'
$vmssh  = Join-Path $repo 'vm-ssh-helper.ps1'
if (-not (Test-Path $vmssh)) { $vmssh = Join-Path $repo '.opencode\bin\vmssh.ps1' }
foreach ($d in 'litellm\admin','litellm\nginx','opencode-setup','systemd','sql') {
  New-Item -ItemType Directory -Force -Path (Join-Path $infra $d) | Out-Null
}
New-Item -ItemType Directory -Force -Path "$vmsnap\backups","$vmsnap\full-dumps" | Out-Null

function VM([string]$cmd) { & $vmssh -Command $cmd }
function Pull([string]$remote, [string]$local) { & $vmssh -Get $remote -Out $local | Out-Null }
# sudo-пароль — только из локального askpass (никогда не в git)
$sudoPass = (Get-Content (Join-Path $env:TEMP 'opencode\askpass.cmd') | Where-Object { $_ -match '^@echo\s+(\S+)' } | ForEach-Object { $Matches[1] } | Select-Object -First 1)

# ---------- 1. committed: litellm core ----------
$core = 'user_agent_hook.py','image_rate_limit_hook.py','block_master_key_hook.py',
        'master_key.py','litellm_entrypoint.sh','swap_glm_credentials.py',
        'utils_patched.py','start.sh','stop.sh','start_api.sh','crontab.root'
# crontab вытягиваем командой (root-only)
VM "echo $sudoPass | sudo -S crontab -l 2>/dev/null" | Set-Content (Join-Path $infra 'litellm\crontab.root')
foreach ($f in $core) {
  if ($f -ne 'crontab.root') { Pull "/opt/litellm/$f" (Join-Path $infra "litellm\$f") }
}
Pull '/opt/litellm/README.md' (Join-Path $infra 'litellm\VM_README.md')
Pull '/etc/nginx/sites-available/litellm-bifrost' (Join-Path $infra 'litellm\nginx\litellm-bifrost.conf')

# ---------- 2. committed: admin/ (без мусора) ----------
$admin = @((VM "ls -1 /opt/litellm/admin") -split "`n" | Where-Object { $_.Trim() -match '\.(py|sh|json|html)$' -and $_ -notmatch 'healthcheck\.log|ALERT' })
foreach ($f in $admin) { Pull "/opt/litellm/admin/$($f.Trim())" (Join-Path $infra "litellm\admin\$($f.Trim())") }

# ---------- 3. committed: opencode-setup + systemd + audit sql ----------
foreach ($f in 'api.py','index.html','users-btn.js','vkeys-btn.js','glm-swap-btn.js','payload-logs-btn.js') {
  Pull "/opt/opencode-setup/$f" (Join-Path $infra "opencode-setup\$f")
}
VM "cat /etc/systemd/system/opencode-api.service" | Set-Content (Join-Path $infra 'systemd\opencode-api.service')
$sql = VM "docker exec -i litellm-pg psql -U litellm -d litellm -At -c `"SELECT '-- function audit_row_change()'; SELECT pg_get_functiondef('audit_row_change()'::regprocedure); SELECT '-- triggers'; SELECT pg_get_triggerdef(oid) || ';' FROM pg_trigger WHERE tgname LIKE 'trg_audit%';`" </dev/null"
$sql | Set-Content (Join-Path $infra 'sql\audit_schema.sql')

# ---------- 4. committed: config.sanitized.yaml ----------
Pull '/opt/litellm/config.yaml' "$env:TEMP\opencode\cfg.raw.yaml"
$cfg = Get-Content "$env:TEMP\opencode\cfg.raw.yaml" -Raw
$cfg = $cfg -replace '(?m)^(\s*api_key:\s*)(?!os\.environ)\S.*$','$1<MASKED>'
$cfg = $cfg -replace '(Bearer\s+)[A-Za-z0-9_\-\.]{20,}','$1<MASKED>'
$cfg = $cfg -replace '(?i)(password\s*[:=]\s*)\S+','$1<MASKED>'
[IO.File]::WriteAllText((Join-Path $infra 'litellm\config.sanitized.yaml'), $cfg)
Remove-Item "$env:TEMP\opencode\cfg.raw.yaml" -ErrorAction SilentlyContinue

# ---------- 5. ГЛОБАЛЬНЫЙ САНИТАЙЗ + секрет-гейт ----------
$MASKS = @($sudoPass,'sk-or-v1[A-Za-z0-9\-]+','sk-tp-[A-Za-z0-9\-]+','sk-cp-[A-Za-z0-9\-]+','sk-[A-Za-z0-9]{24,}')
$leaks = @()
$selfPath = $PSCommandPath
Get-ChildItem $infra -Recurse -File | ForEach-Object {
  if ($_.FullName -eq $selfPath) { return }  # не маскируем сам скрипт (его regex-литералы)
  $c = Get-Content $_.FullName -Raw
  $orig = $c
  $c = $c -replace ('(?i)' + '(UI_' + 'PASSWORD=|POSTGRES_' + 'PASSWORD=|pass' + 'word:\s*|pass' + 'word\s*=\s*)\S+'), '$1<MASKED>'
  $c = $c -replace '(Bearer\s+)[A-Za-z0-9_\-\.]{20,}','$1<MASKED>'
  foreach ($m in $MASKS) { $c = $c -replace $m,'<MASKED>' }
  if ($c -ne $orig) { [IO.File]::WriteAllText($_.FullName, $c); $leaks += $_.Name }
}
if ($leaks) { Write-Host "АВТО-МАСКИРОВАНО: $($leaks -join ', ')" -ForegroundColor Yellow }

# ---------- 6. local-only: ночные бэкапы + полные дампы (НЕ коммитить) ----------
$latest = (VM "ls -1dt /opt/backups/litellm/20* 2>/dev/null | head -1").Trim()
if ($latest -match '20\d{6}') {
  $day = Split-Path -Leaf $latest
  New-Item -ItemType Directory -Force -Path "$vmsnap\backups\$day" | Out-Null
  foreach ($f in 'config.yaml','user_agent_hook.py','api.py','config_tables.sql.gz') {
    Pull "$latest/$f" "$vmsnap\backups\$day\$f"
  }
  Get-ChildItem "$vmsnap\backups" -Directory | Sort-Object Name -Descending | Select-Object -Skip 4 | Remove-Item -Recurse -Force
}
$dump = @(((VM "ls -1t /opt/backups/litellm/full/full_dump_*.sql.gz 2>/dev/null | head -1") -split "`n") | Where-Object { "$_".Trim() })[0]
if ("$dump".Trim()) {
  $name = Split-Path -Leaf "$dump".Trim()
  if (-not (Test-Path "$vmsnap\full-dumps\$name")) {
    Pull "$dump".Trim() "$vmsnap\full-dumps\$name"
    Get-ChildItem "$vmsnap\full-dumps" -Filter '*.sql.gz' | Sort-Object Name -Descending | Select-Object -Skip 3 | Remove-Item -Force
  }
}
if (-not $Quiet) { Write-Host "sync done -> $infra (диф смотри git status)" }
