#!/bin/bash
# Retention: LiteLLM_SpendLogs > 180 days -> DELETE (archived in monthly full dumps)
# Daily tables (aggregates for UI Usage) are NOT touched - they are tiny.
set -u
LOG=/opt/backups/litellm/backup.log
DAYS=180
BEFORE=$(date '+%F %T')
CNT=$(docker exec litellm-pg psql -U litellm -d litellm -At -c "SELECT count(*) FROM \"LiteLLM_SpendLogs\" WHERE \"startTime\" < NOW() - INTERVAL '$DAYS days';" </dev/null 2>/dev/null)
if [ "${CNT:-0}" -gt 0 ]; then
  docker exec litellm-pg psql -U litellm -d litellm -At -c "DELETE FROM \"LiteLLM_SpendLogs\" WHERE \"startTime\" < NOW() - INTERVAL '$DAYS days';" </dev/null >/dev/null 2>&1
  docker exec litellm-pg psql -U litellm -d litellm -At -c "VACUUM ANALYZE \"LiteLLM_SpendLogs\";" </dev/null >/dev/null 2>&1
  echo "$BEFORE spendlogs_retention: deleted $CNT rows older than $DAYS days" >> "$LOG"
else
  echo "$BEFORE spendlogs_retention: nothing older than $DAYS days" >> "$LOG"
fi
