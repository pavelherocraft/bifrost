#!/usr/bin/env python3
"""Master key helper — single source of truth.

Все скрипты в /opt/litellm/ и /opt/opencode-setup/ должны использовать:
    from master_key import get_master_key
    KEY = get_master_key()

Никаких захардкоженных sk-litellm-... в скриптах.
Источник: /opt/litellm/.env (LITELLM_MASTER_KEY=...)

При вызове из docker container litellm, .env примонтирован read-only.
"""
import os
import re
import threading

_ENV_FILE = os.environ.get("LITELLM_ENV_FILE", "/opt/litellm/.env")
_lock = threading.Lock()
_cached = None


def get_master_key():
    """Возвращает LITELLM_MASTER_KEY из .env.

    Кеширует результат в module-global (thread-safe).
    Для refresh вызвать invalidate_cache() (например, после ротации).
    """
    global _cached
    if _cached:
        return _cached
    with _lock:
        if _cached:
            return _cached
        if not os.path.exists(_ENV_FILE):
            raise RuntimeError(f"missing {_ENV_FILE}")
        with open(_ENV_FILE) as f:
            for line in f:
                m = re.match(r'^LITELLM_MASTER_KEY\s*=\s*["\']?([^"\'#\s]+)', line)
                if m:
                    _cached = m.group(1)
                    return _cached
        # Fallback: process env (for systemd services without .env)
        env_val = os.environ.get("LITELLM_MASTER_KEY")
        if env_val:
            _cached = env_val
            return env_val
        raise RuntimeError(f"LITELLM_MASTER_KEY not found in {_ENV_FILE}")


def invalidate_cache():
    """Сбросить кеш — использовать после ротации master key."""
    global _cached
    with _lock:
        _cached = None


if __name__ == "__main__":
    print(get_master_key())
