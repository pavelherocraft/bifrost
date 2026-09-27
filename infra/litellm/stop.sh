#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
docker stop litellm litellm-pg 2>/dev/null || true
docker rm litellm litellm-pg 2>/dev/null || true
