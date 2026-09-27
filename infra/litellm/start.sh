#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

NET=litellm-net
DB_NAME=litellm-pg
DB_VOL=litellm-pg-data

# 1. user-defined network
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET"

# 2. postgres container
if ! docker ps -a --format '{{.Names}}' | grep -q "^${DB_NAME}$"; then
  docker volume create "$DB_VOL" >/dev/null
  docker run -d \
    --name "$DB_NAME" \
    --restart unless-stopped \
    --network "$NET" \
    -v "${DB_VOL}:/var/lib/postgresql/data" \
    -e POSTGRES_USER=litellm \
    -e POSTGRES_PASSWORD=<MASKED> \
    -e POSTGRES_DB=litellm \
    postgres:16-alpine \
    -c 'max_connections=20' \
    -c 'shared_buffers=128MB'
fi

# 3. wait for postgres
echo "waiting for postgres..."
for i in $(seq 1 60); do
  if docker exec "$DB_NAME" pg_isready -U litellm >/dev/null 2>&1; then
    echo "postgres ready after ${i}s"
    break
  fi
  sleep 1
done
docker exec "$DB_NAME" pg_isready -U litellm >/dev/null 2>&1 || {
  echo "postgres failed to become ready"
  docker logs --tail 30 "$DB_NAME" || true
  exit 1
}

# 4. litellm container (recreate if exists)
docker rm -f litellm 2>/dev/null || true
chmod +x "$(pwd)/litellm_entrypoint.sh" 2>/dev/null || true
docker volume create litellm-data >/dev/null
docker run -d \
  --name litellm \
  --restart unless-stopped \
  --network "$NET" \
  -p 4001:4000 \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  -v "$(pwd)/.env:/app/.env:ro" \
  -v "$(pwd)/user_agent_hook.py:/app/user_agent_hook.py:ro" \
  -v "$(pwd)/image_rate_limit_hook.py:/app/image_rate_limit_hook.py:ro" \
  -v "$(pwd)/block_master_key_hook.py:/app/block_master_key_hook.py:ro" \
  -v "$(pwd)/master_key.py:/app/master_key.py:ro" \
  -v "$(pwd)/litellm_entrypoint.sh:/app/litellm_entrypoint.sh:ro" \
  -v "$(pwd)/swap_glm_credentials.py:/app/swap_glm_credentials.py:ro" \
  -v "$(pwd)/admin:/app/admin:ro" \
  -v "$(pwd)/utils_patched.py:/app/.venv/lib/python3.13/site-packages/litellm/proxy/utils.py:ro" \
  -v litellm-data:/app/data \
  --env-file "$(pwd)/.env" \
  -e SERVER_ROOT_PATH=/litellm \
  -e UI_USERNAME=admin \
  -e UI_PASSWORD=<MASKED> \
  -e STORE_MODEL_IN_DB=True \
  --entrypoint /app/litellm_entrypoint.sh \
  ghcr.io/berriai/litellm:main-stable \
  --config /app/config.yaml --port 4000 --num_workers 1

# 5. wait for litellm
echo "waiting for litellm /health/readiness..."
for i in $(seq 1 90); do
  if curl -sf http://127.0.0.1:4001/health/readiness >/dev/null 2>&1; then
    echo "litellm READY after ${i}s -> http://127.0.0.1:4001"
    exit 0
  fi
  sleep 1
done

echo "TIMEOUT. last 40 log lines:"
docker logs --tail 40 litellm || true
exit 1
