#!/bin/bash
# Payload logs retention: delete rows older than 2 days. Runs from dev01 crontab daily 03:30.
docker exec litellm-pg psql -U litellm -d litellm -c "DELETE FROM \"UserRequestLogs\" WHERE created_at < NOW() - INTERVAL '2 days'"
