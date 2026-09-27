#!/bin/bash
# Weekly snapshot bundle -> Telegram group (bot). Requires /opt/litellm/tg.env
# Bundle = latest nightly backup dir (configs + config_tables dump), well under TG 50MB limit.
set -u
BASE=/opt/backups/litellm
ENVF=/opt/litellm/tg.env
LOG=/opt/backups/litellm/backup.log
[ -f "$ENVF" ] || { echo "$(date '+%F %T') weekly_tg: tg.env missing" >> "$LOG"; exit 0; }
. "$ENVF"
if [ -z "${TG_BOT_TOKEN:-}" ] || [ -z "${TG_CHAT_ID:-}" ]; then
  echo "$(date '+%F %T') weekly_tg: TG not configured yet" >> "$LOG"; exit 0
fi
D=$(ls -1dt "$BASE"/20* 2>/dev/null | head -1)
[ -z "$D" ] || [ ! -d "$D" ] && { echo "$(date '+%F %T') weekly_tg: no nightly backup found" >> "$LOG"; exit 1; }
B="$BASE/weekly_snapshot_$(date +%Y%m%d).tar.gz"
tar czf "$B" -C "$BASE" "$(basename "$D")"
SZ=$(stat -c%s "$B")
if [ "$SZ" -gt 49000000 ]; then
  echo "$(date '+%F %T') weekly_tg: bundle $SZ bytes > 49MB TG limit, skipped (left at $B)" >> "$LOG"; exit 1
fi
RESP=$(curl -s -m 180 "https://api.telegram.org/bot$TG_BOT_TOKEN/sendDocument" \
  -F "chat_id=$TG_CHAT_ID" -F "document=@$B" \
  -F "caption=hcbifrost weekly snapshot $(date +%F) from $D")
if echo "$RESP" | grep -q '"ok":true'; then
  rm -f "$B"
  echo "$(date '+%F %T') weekly_tg: sent OK ($D)" >> "$LOG"
else
  echo "$(date '+%F %T') weekly_tg: SEND FAILED: $RESP (bundle kept at $B)" >> "$LOG"
fi
find "$BASE" -maxdepth 1 -name 'weekly_snapshot_*.tar.gz' -mtime +14 -delete
