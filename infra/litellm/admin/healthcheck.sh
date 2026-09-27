#!/bin/bash
# Healthcheck every 5 min: litellm liveness, edge, disk, containers -> log + ALERT + optional TG
set -u
LOG=/opt/litellm/admin/healthcheck.log
ALERTF=/opt/litellm/admin/ALERT
st=OK; msg=""

lit=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:4001/health/liveliness 2>/dev/null || echo 000)
[ "$lit" != "200" ] && { st=FAIL; msg="$msg litellm_liveness=$lit"; }

edge=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://hcbifrost.herocraft.com/litellm/health/liveliness 2>/dev/null || echo 000)
[ "$edge" != "200" ] && { st=FAIL; msg="$msg edge_via_domain=$edge"; }

disk=$(df / | awk 'NR==2{gsub("%",""); print $5}')
[ "$disk" -ge 90 ] && { st=FAIL; msg="$msg disk=${disk}%"; }

pg=$(docker inspect -f '{{.State.Running}}' litellm-pg 2>/dev/null || echo down)
lc=$(docker inspect -f '{{.State.Running}}' litellm 2>/dev/null || echo down)
[ "$pg" != "true" ] && { st=FAIL; msg="$msg pg_container=$pg"; }
[ "$lc" != "true" ] && { st=FAIL; msg="$msg litellm_container=$lc"; }

ts=$(date '+%F %T')
echo "$ts $st$msg" >> "$LOG"
sz=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
[ "$sz" -gt 1000000 ] && { tail -n 2000 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"; }

if [ "$st" = "FAIL" ]; then
  echo "$ts$msg" > "${ALERTF}.last"
  touch "$ALERTF"
  if [ -f /opt/litellm/tg.env ]; then
    . /opt/litellm/tg.env
    if [ -n "${TG_BOT_TOKEN:-}" ] && [ -n "${TG_CHAT_ID:-}" ]; then
      curl -s -m 10 "https://api.telegram.org/bot$TG_BOT_TOKEN/sendMessage" \
        --data-urlencode "chat_id=$TG_CHAT_ID" \
        --data-urlencode "text=[hcbifrost ALERT] $ts$msg" >/dev/null || true
    fi
  fi
else
  rm -f "$ALERTF" 2>/dev/null
fi
