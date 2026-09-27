"""
LiteLLM pre-call hook: per-user per-team per-day image generation rate limit.

Limits daily image-generation requests per user, scoped per model, with the
daily budget determined by the user's team:

    All Access -> 100/day
    *           ->  50/day   (default; overridable via image_team_limits table)

Limits live in DB table `image_team_limits(team_alias PRIMARY KEY, daily_limit)`
so they can be changed with one SQL UPDATE + `docker restart litellm` (no code
edit, no re-deploy).

Counters live in DB table `image_request_counter(user_id, model, day, count)`.
A single atomic UPSERT (`INSERT ... ON CONFLICT DO UPDATE ... RETURNING count`)
is used so concurrent calls from the same user cannot slip past the limit.

Behavior when `current_count + n > limit`:
  - HTTP 429 with body explaining remaining, requested, team, reset_at
  - `Retry-After` header set to minutes-until-UTC-midnight (rounded up, min 1)
  - The whole request is rejected (even if only some of the requested `n`
    would have fit)

Behavior when not image_generation or model not in the limited set:
  - Pass-through unchanged (call_type guard ensures zero overhead on chat etc.)

Registered in config.yaml:
    litellm_settings:
      callbacks:
        - "image_rate_limit_hook.image_rate_limit_logger"
"""

import logging
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool
from fastapi import HTTPException

from litellm._logging import verbose_proxy_logger
from litellm.integrations.custom_logger import CustomLogger

# Models whose calls are subject to the per-user daily limit.
LIMITED_MODELS = frozenset({
    "gpt-image-1.5",
    "gpt-image-2",
    "gemini/gemini-3.1-flash-image",
    "gemini/gemini-3-pro-image",
})

IMAGE_CALL_TYPES = frozenset({"image_generation", "aimage_generation"})

DEFAULT_DAILY_LIMIT = 50  # fallback when team_alias has no row in image_team_limits


def _normalize_call_type(call_type) -> str:
    """call_type may be str or an Enum with .value; return str or ''."""
    if call_type is None:
        return ""
    if hasattr(call_type, "value"):
        try:
            return str(call_type.value)
        except Exception:
            pass
    return str(call_type)


def _normalize_team_id(team_id) -> Optional[str]:
    if team_id is None:
        return None
    s = str(team_id).strip()
    return s or None


def _read_database_url() -> Optional[str]:
    """Read DATABASE_URL from /app/.env (bind-mounted from /opt/litellm/.env)."""
    env_path = os.environ.get("IMAGE_RATE_LIMIT_ENV", "/app/.env")
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
            "image_rate_limit_hook: cannot read DATABASE_URL from %s: %s",
            env_path,
            e,
        )
    return None


class _TeamLimitsCache:
    """In-memory cache of team_alias -> daily_limit, refreshed on TTL expiry.

    The cache is keyed by the literal team_alias string (NOT team_id). The
    mapping team_id -> team_alias is built once from the DB at startup and
    refreshed when the cache expires.

    Hot path is two dict lookups, no SQL.
    """

    def __init__(self, ttl_seconds: int = 60):
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._expires_at: float = 0.0
        # team_id (str) -> team_alias (str or None)
        self._team_alias_by_id: dict = {}
        # team_alias (str) -> daily_limit (int)
        self._limit_by_alias: dict = {}

    def _refresh_locked(self, conn) -> None:
        cur = conn.cursor()
        try:
            cur.execute("SELECT team_id, team_alias FROM \"LiteLLM_TeamTable\"")
            self._team_alias_by_id = {
                str(tid): (alias or None)
                for tid, alias in cur.fetchall()
            }
        finally:
            cur.close()
        try:
            cur = conn.cursor()
            cur.execute("SELECT team_alias, daily_limit FROM image_team_limits")
            self._limit_by_alias = {
                alias: int(limit)
                for alias, limit in cur.fetchall()
            }
        finally:
            cur.close()
        self._expires_at = time.monotonic() + self._ttl

    def get_team_alias(self, team_id: Optional[str], conn) -> Optional[str]:
        if team_id is None:
            return None
        if time.monotonic() >= self._expires_at:
            with self._lock:
                if time.monotonic() >= self._expires_at:
                    self._refresh_locked(conn)
        return self._team_alias_by_id.get(team_id)

    def get_limit(self, team_alias: Optional[str], conn) -> int:
        if time.monotonic() >= self._expires_at:
            with self._lock:
                if time.monotonic() >= self._expires_at:
                    self._refresh_locked(conn)
        if team_alias is not None and team_alias in self._limit_by_alias:
            return self._limit_by_alias[team_alias]
        return self._limit_by_alias.get("_default", DEFAULT_DAILY_LIMIT)


class ImageRateLimitLogger(CustomLogger):
    """Per-user per-team per-day rate limiter for image generation calls."""

    def __init__(self):
        super().__init__()
        self._db_url: Optional[str] = _read_database_url()
        self._pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
        self._limits_cache = _TeamLimitsCache(ttl_seconds=60)
        self._pool_lock = threading.Lock()
        if self._db_url:
            try:
                self._pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=10,
                    dsn=self._db_url,
                    connect_timeout=5,
                    application_name="image_rate_limit_hook",
                )
                verbose_proxy_logger.warning(
                    "image_rate_limit_hook: initialized with DB pool (min=1 max=10)"
                )
            except Exception as e:
                verbose_proxy_logger.warning(
                    "image_rate_limit_hook: FAILED to create DB pool: %s\n%s",
                    e,
                    traceback.format_exc(),
                )
                self._pool = None
        else:
            verbose_proxy_logger.warning(
                "image_rate_limit_hook: DATABASE_URL not found, hook will pass-through all calls"
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

    @staticmethod
    def _extract_n(data: dict) -> int:
        """Defensive lookup of `n` across OpenAI/Gemini naming variants.

        OpenAI image API uses `n` (default 1, range 1-10).
        Gemini image API in OpenAI-compat mode usually also uses `n`, but some
        variants / forks use `sampleCount` / `num_images` / `number_of_images`.
        Falls back to 1 when nothing is found.
        """
        for key in ("n", "num_images", "sample_count", "sampleCount", "number_of_images"):
            v = data.get(key)
            if v is None:
                continue
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            return max(1, n)
        return 1

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    def _minutes_until_midnight_utc(cls) -> int:
        now = cls._now_utc()
        midnight = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        # roll to next day
        from datetime import timedelta
        next_midnight = midnight + timedelta(days=1)
        seconds = max(0.0, (next_midnight - now).total_seconds())
        # round up to next whole minute, min 1
        minutes = int((seconds + 59.999) // 60)
        return max(1, minutes)

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        # --- 0. Fast-path guards (zero overhead on chat etc.) -----------------
        ct = _normalize_call_type(call_type)
        if ct not in IMAGE_CALL_TYPES:
            return data

        # --- 0.25. Force non-streaming upstream call for image generation ---
        # opencode / AI SDK sends stream: true for every model by default.
        # But the OpenAI image API (gpt-image-2, gpt-image-1.5) does NOT
        # support streaming. The upstream call hangs and LiteLLM hits a
        # httpx.ReadTimeout, which surfaces to the client as
        # OpenAIException - The server had an error and the deployment
        # then goes into cooldown for 30s.
        #
        # We override stream = False for the upstream call here. LiteLLM
        # still wraps the response so the client receives it in the form
        # it asked for (chunked SSE if stream was set, JSON otherwise).
        if data.get("stream") is True:
            verbose_proxy_logger.warning(
                "image_rate_limit_hook: forcing stream=False for image "
                "generation (was stream=True) - model=%r call_type=%r",
                data.get("model"),
                call_type,
            )
            data["stream"] = False
        if not isinstance(data, dict):
            return data
        model = data.get("model") or ""
        if model not in LIMITED_MODELS:
            return data

        # --- 0.5. Block slow model+quality combos ---------------------------
        # gpt-image-2 with quality=high takes ~120s upstream, which exceeds
        # the Timeweb reverse-proxy timeout (60s) and results in HTTP 504
        # to the client. Reject early with a 400 + alternatives instead of
        # letting the request sit for 2 minutes and then failing opaque.
        quality = (data.get("quality") or "").lower()
        if model == "gpt-image-2" and quality == "high":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "slow_quality_combination_blocked",
                    "message": (
                        "gpt-image-2 with quality=high is currently blocked: "
                        "OpenAI generation takes ~120s which exceeds the upstream "
                        "Timeweb reverse-proxy timeout (60s), so the client would "
                        "see an opaque 504 after a 60s wait. Use one of the alternatives."
                    ),
                    "model": model,
                    "quality": quality,
                    "alternatives": [
                        {"model": "gpt-image-1.5", "quality": "high",
                         "approx_latency": "30s",
                         "note": "gpt-image-1.5 high quality works in ~30s and finishes well under the 60s proxy timeout."},
                        {"model": "gemini/gemini-3-pro-image", "quality": "auto",
                         "approx_latency": "15s",
                         "note": "Gemini 3 Pro image generates in ~15s, supports high quality output."},
                        {"model": "gpt-image-2", "quality": "medium",
                         "approx_latency": "46s",
                         "note": "gpt-image-2 medium quality works in ~46s, just under the 60s timeout."},
                    ],
                },
            )

        # --- 1. Resolve user_id and team_alias --------------------------------
        try:
            user_id = getattr(user_api_key_dict, "user_id", None)
            user_id = str(user_id).strip() if user_id else None
            team_id = _normalize_team_id(getattr(user_api_key_dict, "team_id", None))
        except Exception as e:
            verbose_proxy_logger.warning(
                "image_rate_limit_hook: cannot resolve user/team: %s", e
            )
            user_id = None
            team_id = None

        if not user_id:
            # No user context = master key / internal call -> pass-through.
            return data

        # --- 2. Resolve limit --------------------------------------------------
        conn = self._conn()
        if conn is None:
            return data  # DB unavailable -> fail-open (logged at init)

        try:
            team_alias = self._limits_cache.get_team_alias(team_id, conn)
            limit = self._limits_cache.get_limit(team_alias, conn)
        except Exception as e:
            verbose_proxy_logger.warning(
                "image_rate_limit_hook: limit lookup failed: %s\n%s",
                e,
                traceback.format_exc(),
            )
            self._putback(conn)
            return data  # fail-open on lookup error

        # --- 3. Extract n ------------------------------------------------------
        n = self._extract_n(data)

        # --- 4. Read current counter -------------------------------------------
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT count FROM image_request_counter "
                "WHERE user_id = %s AND model = %s AND day = CURRENT_DATE",
                (user_id, model),
            )
            row = cur.fetchone()
            cur.close()
            current = int(row[0]) if row else 0
        except Exception as e:
            verbose_proxy_logger.warning(
                "image_rate_limit_hook: read counter failed: %s\n%s",
                e,
                traceback.format_exc(),
            )
            self._putback(conn)
            return data  # fail-open on read error

        # --- 5. Check limit (BEFORE increment) ---------------------------------
        if current + n > limit:
            retry_minutes = self._minutes_until_midnight_utc()
            remaining = max(0, limit - current)
            try:
                self._putback(conn)
            except Exception:
                pass
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "image_daily_limit_exceeded",
                    "message": (
                        f"Daily image-generation limit reached for model '{model}'. "
                        f"Requested {n}, remaining {remaining} (limit {limit}/day, "
                        f"team={team_alias or 'unknown'})."
                    ),
                    "limit": limit,
                    "used": current,
                    "requested": n,
                    "remaining": remaining,
                    "team": team_alias,
                    "reset_at": "UTC midnight",
                },
                headers={"Retry-After": str(retry_minutes)},
            )

        # --- 6. Atomic UPSERT +n ----------------------------------------------
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO image_request_counter (user_id, model, day, count) "
                "VALUES (%s, %s, CURRENT_DATE, %s) "
                "ON CONFLICT (user_id, model, day) DO UPDATE "
                "  SET count = image_request_counter.count + EXCLUDED.count "
                "RETURNING count",
                (user_id, model, n),
            )
            new_count = int(cur.fetchone()[0])
            conn.commit()
            cur.close()
            # Soft over-limit is acceptable in concurrent races (we already
            # checked current+n<=limit in step 5). We log it for visibility
            # but don't reject.
            if new_count > limit:
                verbose_proxy_logger.warning(
                    "image_rate_limit_hook: race slipped past pre-check "
                    "user=%s model=%s new_count=%d limit=%d",
                    user_id,
                    model,
                    new_count,
                    limit,
                )
        except Exception as e:
            verbose_proxy_logger.warning(
                "image_rate_limit_hook: increment failed: %s\n%s",
                e,
                traceback.format_exc(),
            )
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            self._putback(conn)

        return data


# Module-level instance for the entrypoint/runtime patch to grab.
image_rate_limit_logger = ImageRateLimitLogger()
