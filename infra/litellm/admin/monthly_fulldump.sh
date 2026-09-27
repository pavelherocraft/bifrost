#!/bin/bash
# Monthly full DB dump (all tables incl. SpendLogs). Pulled to the local machine
# by pull-fulldump.ps1; on VM keep only the 2 newest dumps.
set -u
F=/opt/backups/litellm/full
LOG=/opt/backups/litellm/backup.log
mkdir -p "$F"
OUT="$F/full_dump_$(date +%Y%m%d).sql.gz"
docker exec litellm-pg pg_dump -U litellm -d litellm | gzip > "$OUT"
echo "$(date '+%F %T') full_dump -> $OUT ($(du -sh "$OUT" | cut -f1))" >> "$LOG"
ls -1t "$F"/full_dump_*.sql.gz 2>/dev/null | tail -n +3 | xargs -r rm -f
