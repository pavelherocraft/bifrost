#!/bin/bash
# Nightly config backup: configs + key tables -> /opt/backups/litellm/YYYYMMDD/, keep 14d
set -u
BASE=/opt/backups/litellm
D=$BASE/$(date +%Y%m%d)
mkdir -p "$D"
cp -f /opt/litellm/config.yaml "$D/" 2>/dev/null
cp -f /opt/litellm/user_agent_hook.py "$D/" 2>/dev/null
cp -f /opt/opencode-setup/api.py "$D/" 2>/dev/null
docker exec litellm-pg pg_dump -U litellm -d litellm \
  -t "LiteLLM_ProxyModelTable" -t "LiteLLM_VerificationToken" \
  -t "LiteLLM_TeamTable" -t "LiteLLM_UserTable" 2>/dev/null | gzip > "$D/config_tables.sql.gz"
echo "$(date '+%F %T') backup -> $D ($(du -sh "$D" | cut -f1))"
find "$BASE" -maxdepth 1 -mindepth 1 -type d -name '20*' -mtime +14 -exec rm -rf {} +
