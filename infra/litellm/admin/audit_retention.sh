#!/bin/sh
docker exec litellm-pg psql -U litellm -d litellm -c "DELETE FROM audit_custom WHERE ts < NOW() - interval '90 days';" >/dev/null 2>&1
docker exec litellm-pg psql -U litellm -d litellm -c 'DELETE FROM "LiteLLM_AuditLog" WHERE updated_at < NOW() - interval '"'"'90 days'"'"';' >/dev/null 2>&1
