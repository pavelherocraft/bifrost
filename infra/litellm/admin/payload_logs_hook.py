"""
LiteLLM success hook: selective per-user request logging (payload logging).

Logs the USER-authored messages (role == "user" only — no system prompts,
no assistant/tool context, no responses) of successful requests, but ONLY
for user_ids listed (enabled=true) in the Postgres table
`"PayloadLogAllowlist"`. Everyone else: zero overhead, zero storage.

v2 (2026-08-30):
  - FIX pool leak: connection is always returned via putback() in a
    finally block. (v1 leaked one conn per logged row; after maxconn=6
    rows the pool was exhausted and the hook silently died —
    "connection pool exhausted" x10k in logs.)
  - NEW-message dedupe: opencode resends the whole conversation on every
    turn, so v1 re-stored the same user texts over and over. v2 fetches
    the user's previous stored message array; if the new array is a
    prefix-extension of it, only the NEW tail is stored. If nothing is
    new (agent tool-loop turns, retries) the row is skipped entirely.
    A non-prefix array (new session) is stored in full.

Design decisions (see .opencode/plans/plan_payload_logs.md):
  - Default-deny: nothing is logged until a user_id is added to the
    allowlist. The global litellm `store_prompts_in_spend_logs` setting
    stays OFF, so LiteLLM_SpendLogs keeps storing metadata only.
  - Allowlist lives in DB (editable from the /admin/payload-logs/ UI),
    cached in memory with a short TTL so the hot path is a dict lookup.
  - Only successful requests are logged (async_log_success_event).
  - Fail-safe: ANY exception inside the hook is swallowed (logged at
    WARNING) — logging must never break user traffic.

Registered in /opt/litellm/utils_patched.py via the
_PATCH_PAYLOAD_LOGS_HOOK_ block inside `_init_litellm_callbacks()`.
"""

import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.pool

from litellm._logging import verbose_proxy_logger
from litellm.integrations.custom_logger import CustomLogger


def _read_database_url() -> Optional[str]:
    """Read DATABASE_URL from /app/.env (bind-mounted from /opt/litellm/.env)."""
    env_path = os.environ.get("PAYLOAD_LOG_ENV", "/app/.env")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception as e:
        verbose_proxy_logger.warning(
            "payload_logs_hook: cannot read DATABASE_URL from %s: %s", env_path, e
        )
    return None


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


class _AllowlistCache:
    """In-memory cache of enabled user_ids, refreshed on TTL expiry."""

    def __init__(self, ttl_seconds: int = 15):
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._expires_at: float = 0.0
        self._allowed: frozenset = frozenset()

    def _refresh_locked(self, conn) -> None:
        cur = conn.cursor()
        try:
            cur.execute(
                'SELECT user_id FROM "PayloadLogAllowlist" WHERE enabled = true'
            )
            self._allowed = frozenset(str(r[0]) for r in cur.fetchall())
        finally:
            cur.close()
        self._expires_at = time.monotonic() + self._ttl

    def get(self, conn) -> frozenset:
        if time.monotonic() >= self._expires_at:
            with self._lock:
                if time.monotonic() >= self._expires_at:
                    try:
                        self._refresh_locked(conn)
                    except Exception as e:
                        verbose_proxy_logger.warning(
                            "payload_logs_hook: allowlist refresh failed: %s", e
                        )
                        # keep stale list; retry after a short backoff
                        self._expires_at = time.monotonic() + min(self._ttl, 5)
        return self._allowed


class PayloadLogsLogger(CustomLogger):
    """Logs user-authored messages of successful calls for allowlisted users."""

    def __init__(self):
        super().__init__()
        self._db_url: Optional[str] = _read_database_url()
        self._pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
        self._pool_lock = threading.Lock()
        self._allowlist = _AllowlistCache(
            ttl_seconds=_int_env("PAYLOAD_LOG_CACHE_TTL", 15)
        )
        self._max_msg_chars = _int_env("PAYLOAD_LOG_MAX_MSG_CHARS", 1_000_000)
        self._debug = os.path.exists("/tmp/payload_log_debug")
        if self._db_url:
            try:
                self._pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=0,
                    maxconn=8,
                    dsn=self._db_url,
                    connect_timeout=5,
                    application_name="payload_logs_hook",
                )
                verbose_proxy_logger.warning(
                    "payload_logs_hook: ACTIVE v2 (db pool min=0 max=8, "
                    "cache_ttl=%ss, max_msg_chars=%d, dedupe=on)",
                    self._allowlist._ttl,
                    self._max_msg_chars,
                )
            except Exception as e:
                verbose_proxy_logger.warning(
                    "payload_logs_hook: FAILED to create DB pool: %s\n%s",
                    e,
                    traceback.format_exc(),
                )
                self._pool = None
        else:
            verbose_proxy_logger.warning(
                "payload_logs_hook: DATABASE_URL not found — hook is a no-op"
            )

    def _conn(self):
        if self._pool is None:
            return None
        with self._pool_lock:
            return self._pool.getconn()

    def _putback(self, conn) -> None:
        if self._pool is None or conn is None:
            return
        try:
            self._pool.putconn(conn)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # message extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _content_to_text(content, max_chars: int) -> Optional[str]:
        """Flatten one message content (str | list-of-parts | other) to text."""
        if content is None:
            return None
        text_parts = []
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict):
                    # OpenAI multimodal: {"type": "text", "text": "..."}
                    if str(part.get("type", "")).lower() == "text":
                        t = part.get("text")
                        if isinstance(t, str):
                            text_parts.append(t)
        if not text_parts:
            return None  # image-only / empty / unsupported content
        joined = "\n".join(text_parts).strip()
        if not joined:
            return None
        if len(joined) > max_chars:
            joined = joined[:max_chars] + "\n\n…[truncated at " + str(max_chars) + " chars]"
        return joined

    @classmethod
    def _extract_user_texts(cls, messages, max_chars: int) -> list:
        """Return texts of role=='user' messages only (no system/assistant)."""
        out = []
        if not isinstance(messages, list):
            return out
        for m in messages:
            if not isinstance(m, dict):
                continue
            if str(m.get("role", "")).lower() != "user":
                continue
            text = cls._content_to_text(m.get("content"), max_chars)
            if text:
                out.append(text)
        return out

    @staticmethod
    def _get_messages(kwargs: dict):
        """Best-effort lookup of the original request messages."""
        lp = kwargs.get("litellm_params") or {}
        psr = lp.get("proxy_server_request") or {}
        body = psr.get("body") or {}
        if isinstance(body, dict) and isinstance(body.get("messages"), list):
            return body["messages"]
        slo = kwargs.get("standard_logging_object") or {}
        msgs = slo.get("messages")
        if isinstance(msgs, list):
            return msgs
        return None

    # ------------------------------------------------------------------
    # new-message dedupe
    # ------------------------------------------------------------------

    def _new_tail(self, conn, user_id: str, user_texts: list) -> list:
        """
        Compare against the user's most recent FULL request array
        (`full_texts` column, distinct from the displayed `user_messages`
        tail). If the new array extends it (same prefix), return only the
        new tail. Identical arrays (retries) return []. Anything else
        (new session, compaction, interleaved sessions) returns full.
        """
        prev = None
        cur = conn.cursor()
        try:
            cur.execute(
                'SELECT full_texts FROM "UserRequestLogs" '
                "WHERE user_id = %s AND full_texts IS NOT NULL "
                "ORDER BY id DESC LIMIT 1",
                (user_id,),
            )
            row = cur.fetchone()
            if row and isinstance(row[0], list):
                prev = row[0]
        finally:
            cur.close()

        if not prev:
            return user_texts
        if len(user_texts) >= len(prev) and user_texts[: len(prev)] == prev:
            return user_texts[len(prev):]
        return user_texts

    # ------------------------------------------------------------------
    # hook entrypoint
    # ------------------------------------------------------------------

    async def async_log_success_event(
        self, kwargs, response_obj, start_time, end_time
    ):
        conn = None
        try:
            if self._pool is None:
                return
            lp = kwargs.get("litellm_params") or {}
            metadata = lp.get("metadata") or {}

            user_id = metadata.get("user_api_key_user_id") or ""
            user_id = str(user_id).strip()
            if not user_id:
                # master key / internal call — no user context
                if self._debug:
                    verbose_proxy_logger.warning(
                        "payload_logs_hook DEBUG: skip, no user_id "
                        "(metadata keys: %s)",
                        sorted(metadata.keys()),
                    )
                return

            messages = self._get_messages(kwargs)
            user_texts = self._extract_user_texts(messages, self._max_msg_chars)
            if not user_texts:
                if self._debug:
                    verbose_proxy_logger.warning(
                        "payload_logs_hook DEBUG: skip, no user texts extracted "
                        "(messages=%s)",
                        (messages or [])[:2] if messages else messages,
                    )
                return  # embeddings / empty / non-chat — nothing user-authored

            conn = self._conn()
            if conn is None:
                return
            try:
                allowed = self._allowlist.get(conn)
            except Exception as e:
                verbose_proxy_logger.warning(
                    "payload_logs_hook: allowlist lookup failed: %s", e
                )
                return
            if user_id not in allowed:
                if self._debug:
                    verbose_proxy_logger.warning(
                        "payload_logs_hook DEBUG: skip, user_id=%r not in "
                        "allowlist (size=%d)",
                        user_id,
                        len(allowed),
                    )
                return

            new_texts = self._new_tail(conn, user_id, user_texts)
            if not new_texts:
                if self._debug:
                    verbose_proxy_logger.warning(
                        "payload_logs_hook DEBUG: skip, no NEW user messages "
                        "(agent turn or retry), user_id=%r",
                        user_id,
                    )
                return

            key_hash = metadata.get("user_api_key") or None
            key_alias = (
                metadata.get("user_api_key_alias")
                or metadata.get("key_alias")
                or None
            )
            model = kwargs.get("model") or None
            request_id = (
                kwargs.get("litellm_call_id")
                or (kwargs.get("standard_logging_object") or {}).get("id")
                or None
            )
            try:
                latency_ms = int((end_time - start_time).total_seconds() * 1000)
            except Exception:
                latency_ms = None
            if isinstance(start_time, datetime):
                st = start_time
                if st.tzinfo is None:
                    st = st.replace(tzinfo=timezone.utc)
            else:
                st = datetime.now(timezone.utc)

            cur = conn.cursor()
            try:
                cur.execute(
                    'INSERT INTO "UserRequestLogs" '
                    "(request_id, user_id, key_alias, key_hash, model, "
                    " user_messages, full_texts, start_time, latency_ms) "
                    "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)",
                    (
                        request_id,
                        user_id,
                        key_alias,
                        key_hash,
                        model,
                        json.dumps(new_texts, ensure_ascii=False),
                        json.dumps(user_texts, ensure_ascii=False),
                        st,
                        latency_ms,
                    ),
                )
                conn.commit()
                if self._debug:
                    verbose_proxy_logger.warning(
                        "payload_logs_hook DEBUG: inserted %d new msg(s) "
                        "for %r (of %d total in request)",
                        len(new_texts),
                        user_id,
                        len(user_texts),
                    )
            finally:
                cur.close()
        except Exception as e:
            # NEVER break user traffic because of logging
            try:
                verbose_proxy_logger.warning(
                    "payload_logs_hook: insert failed: %s\n%s",
                    e,
                    traceback.format_exc(),
                )
            except Exception:
                pass
        finally:
            # ALWAYS return the connection to the pool (v2 fix: v1 leaked
            # it on the insert path, exhausting the pool after maxconn rows)
            if conn is not None:
                self._putback(conn)


# Module-level instance for the utils_patched.py registration block.
payload_logs_logger = PayloadLogsLogger()
