#!/usr/bin/env python3
"""opencode-setup API.

Resolves a LiteLLM user_id -> the model list + MCP server list that user
can actually access. Returns model objects with name, context-window limits
(max_input_tokens / max_tokens) and vision capability (supports_vision),
all sourced from LiteLLM's /model/info — single source of truth for both
config and opencode.json.

For MCP servers: source of truth is the user's Teams. We aggregate
object_permission.mcp_servers + mcp_access_groups across all teams the
user belongs to, then intersect with the set of registered MCP servers.
Per-tool filtering happens server-side at request time.

Endpoints
  GET /models?user=<UUID>      -> 200 {"models": [{"id","context","output","vision","image","image_edit"}]}
  GET /models?vk=sk-...        -> resolves VK -> user_id, then 200 (same shape)
  GET /models?vkh=<sha256>     -> resolves VK by key hash -> user_id, then 200
  GET /mcp?user=<UUID>         -> 200 {"servers": [{"name","url","description"}]}
  GET /mcp?vk=sk-...           -> resolves VK -> user_id, then 200 (same shape)
  GET /mcp?vkh=<sha256>        -> resolves VK by key hash -> user_id, then 200
  GET /vk-info?vk=sk-...       -> 200 {"user_id","key_alias","team_id"} or 404
  GET /vk-info?vkh=<sha256>    -> same shape, looked up by key hash
  GET /limits?user|vk|vkh=...  -> 200 {"providers":[{id,label,models,windows}]}
                                 remaining token-plan quota for providers the
                                 key/user actually has models for
  GET /health                  -> 200 {"ok": true}
"""
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LITELLM_BASE = os.environ.get("LITELLM_BASE", "http://127.0.0.1:4001")
ENV_FILE = "/opt/litellm/.env"
LISTEN = ("127.0.0.1", 9000)
EXTERNAL_BASE_URL = os.environ.get(
    "EXTERNAL_BASE_URL", "https://hcbifrost.herocraft.com/litellm"
)

_MASTER_KEY = None
_MODEL_INFO = None
_ALIASES = None

# Models that support reasoning/thinking output. Used to emit
# `reasoning: true` in /models response so the opencode.json generator
# (index.html buildCfg) can add `options.thinking` per model.
# Source: official docs (zhipu/bigmodel.cn for GLM, dashscope for Qwen,
# moonshot for Kimi, custom_openai passthrough for MiniMax-M3).
_REASONING_CAPABLE = {
    "glm-4.5", "glm-4.5-air", "glm-4.6",
    "GLM-4.7", "GLM-4.7 (res)",
    "GLM-5.3", "GLM-5.3 (res)",
    "GLM-5.3-Flash", "GLM-5.3-Flash (res)",
    "tencent/glm-5-3 (reserved - use when main is exhausted)",
    "tencent/glm5-3flash (reserved - use when main is exhausted)",
    "openrouter/deepseek-v4.1-flash",
    "xiaomi/mimo-v2.6-pro", "xiaomi/mimo-v2.6-flash",
    "tencent/mimo-v2.6-pro (reserved - use when main is exhausted)",
    "tencent/mimo-v2.6-flash (reserved - use when main is exhausted)",
    "stepfun/step-5-preview", "stepfun/step-3.5-flash-2603", "stepfun/step-3.7-flash",
    "tencent/Kimi K3 (reserved - use when main is exhausted)",
    "tencent/Hy4",
    "tencent/Hy4 (reserved - use when main is exhausted)",
    "tencent/Hy3",
    "kimi-k2-0905-preview", "kimi-k2-turbo-preview",
    "Kimi K2.6", "Kimi K2.7", "Kimi K3",
    "Kimi K3-256K", "Kimi K2.8",
    "MiniMax-M2.5", "MiniMax-M3", "MiniMax-M3.1-Flash-Preview",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/deepseek-v4.1-flash (free)", "nvidia/glm-5.3 (free)",
    "nvidia/glm-5.3-flash (free)", "nvidia/kimi-k3 (free)",
    "Qwen3.5-plus", "QWEN3.7-plus",
    "qwen3.7-max", "qwen3.6-flash",
    "qwen3-coder-plus", "qwen3-coder-flash", "qwen3-max",
    "mimo-v2.5", "mimo-v2.5-pro",
    "deepseek-v4-pro", "deepseek-ai/deepseek-v3.2",
}

# Models that support `reasoningEffort` cycling via opencode `variants` cycle keybind.
# Cycles {low, high, max} for k3-256k (also kimi-for-coding would be drop-down-driven by
# its own Thinking always-ON flag, not selected here). All others inherit reasoning=True
# but no variants from LiteLLM (e.g. K2.6 was removed, Moonshot k2.x echoes reasoning
# in completion but not as a configurable effort parameter in /chat/completions).
_REASONING_VARIANTS = {
    "Kimi K3": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "Kimi K3-256K": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "MiniMax-M3": {
        "low":    {"reasoningEffort": "low"},
        "medium": {"reasoningEffort": "medium"},
        "high":   {"reasoningEffort": "high"},
        "xhigh":  {"reasoningEffort": "xhigh"},
        "max":    {"reasoningEffort": "max"},
    },
    "MiniMax-M3.1-Flash-Preview": {
        "low":    {"reasoningEffort": "low"},
        "medium": {"reasoningEffort": "medium"},
        "high":   {"reasoningEffort": "high"},
        "xhigh":  {"reasoningEffort": "xhigh"},
        "max":    {"reasoningEffort": "max"},
    },
    "GLM-5.3": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    # Tencent token-plan mimo has the same limits as xiaomi upstream:
    # max/xhigh -> 400 (verified 2026-10-08).
    "tencent/mimo-v2.6-pro (reserved - use when main is exhausted)": {
        "off":    {"reasoningEffort": "none"},
        "low":    {"reasoningEffort": "low"},
        "medium": {"reasoningEffort": "medium"},
        "high":   {"reasoningEffort": "high"},
    },
    "tencent/mimo-v2.6-flash (reserved - use when main is exhausted)": {
        "off":    {"reasoningEffort": "none"},
        "low":    {"reasoningEffort": "low"},
        "medium": {"reasoningEffort": "medium"},
        "high":   {"reasoningEffort": "high"},
    },
    "openrouter/deepseek-v4.1-flash": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    # Xiaomi upstream only accepts low/medium/high/none — xhigh/max get a
    # bare 400 "Invalid request parameters" (verified 2026-10-08).
    "xiaomi/mimo-v2.6-pro": {
        "off":    {"reasoningEffort": "none"},
        "low":    {"reasoningEffort": "low"},
        "medium": {"reasoningEffort": "medium"},
        "high":   {"reasoningEffort": "high"},
    },
    "xiaomi/mimo-v2.6-flash": {
        "off":    {"reasoningEffort": "none"},
        "low":    {"reasoningEffort": "low"},
        "medium": {"reasoningEffort": "medium"},
        "high":   {"reasoningEffort": "high"},
    },
    "stepfun/step-5-preview": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "stepfun/step-3.5-flash-2603": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "stepfun/step-3.7-flash": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "tencent/Kimi K3 (reserved - use when main is exhausted)": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "GLM-5.3 (res)": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "GLM-5.3-Flash": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "GLM-5.3-Flash (res)": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "tencent/glm-5-3 (reserved - use when main is exhausted)": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    "tencent/glm5-3flash (reserved - use when main is exhausted)": {
        "low":  {"reasoningEffort": "low"},
        "high": {"reasoningEffort": "high"},
        "max":  {"reasoningEffort": "max"},
    },
    # Mistral upstream accepts only high/none — low/medium/xhigh/max -> 400
    # error 3051; `thinking` param -> 422 extra_forbidden (verified 2026-10-10).
    "mistral/mistral-large-4": {
        "off":  {"reasoningEffort": "none"},
        "high": {"reasoningEffort": "high"},
    },
    "mistral/mistral-medium-3.5": {
        "off":  {"reasoningEffort": "none"},
        "high": {"reasoningEffort": "high"},
    },
    "mistral/mistral-small-2603": {
        "off":  {"reasoningEffort": "none"},
        "high": {"reasoningEffort": "high"},
    },
}

# Modes that must not appear in the chat-model list (embeddings are called
# via /v1/embeddings, not /chat/completions — listing them as chat models
# makes opencode offer a model that always fails).
_BLOCKED_MODES = {"embedding", "audio_transcription", "audio_speech"}

# Token-plan providers whose remaining quota can be reported on the limits
# page. Each provider is reachable through a LiteLLM passthrough endpoint
# (the upstream plan key lives in config.yaml — we never touch it here).
# `models` = deployment names that ride this plan; a provider is only
# reported when the caller's resolved model set intersects it.
# `normalize` maps the upstream response to a common window shape:
#   [{"name": ..., "interval": {...}, "weekly": {...}}]
def _norm_minimax_remains(raw):
    """MiniMax /v1/token_plan/remains -> normalized window list.

    Upstream gives per-"model" rows (in practice one "general" unified bar)
    with current_* (5-hour rolling window) and weekly_* fields. Counts are
    zero in the new API — percent fields carry the real info:
    current_interval_remaining_percent / current_weekly_remaining_percent.
    """
    out = []
    for e in raw.get("model_remains") or []:
        out.append({
            "name": e.get("model_name") or "general",
            "interval": {
                "remaining_percent": e.get("current_interval_remaining_percent"),
                "status": e.get("current_interval_status"),
                "total_count": e.get("current_interval_total_count"),
                "usage_count": e.get("current_interval_usage_count"),
                "resets_in_ms": e.get("remains_time"),
                "end_time": e.get("end_time"),
            },
            "weekly": {
                "remaining_percent": e.get("current_weekly_remaining_percent"),
                "status": e.get("current_weekly_status"),
                "total_count": e.get("current_weekly_total_count"),
                "usage_count": e.get("current_weekly_usage_count"),
                "resets_in_ms": e.get("weekly_remains_time"),
                "end_time": e.get("weekly_end_time"),
            },
        })
    return out


def _ms_until(epoch_ms):
    if not epoch_ms:
        return None
    # Rolling windows can return a reset time that just elapsed — clamp so
    # the UI never renders a negative countdown.
    return max(epoch_ms - int(time.time() * 1000), 0)


def _db_dsn():
    """DATABASE_URL from litellm .env, with the docker-network host resolved.

    Postgres is not published on the host — the URL's `litellm-pg` hostname
    only resolves inside the docker network. Resolve it to the container IP
    via `docker inspect` (api runs as dev01, which is in the docker group);
    fall back to the literal host for non-docker deployments.
    """
    url = None
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                m = re.match(r'^DATABASE_URL\s*=\s*["\']?([^"\'#\s]+)', line)
                if m:
                    url = m.group(1)
                    break
    if not url:
        raise RuntimeError(f"DATABASE_URL not found in {ENV_FILE}")
    if "litellm-pg" in url:
        try:
            out = subprocess.check_output(
                ["docker", "inspect", "litellm-pg", "--format",
                 "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
                timeout=5, text=True).strip()
            if out:
                url = url.replace("litellm-pg", out)
        except Exception:
            pass
    return url


def _db_count_requests(model_groups, interval_sql):
    """Count LiteLLM_SpendLogs rows for the given model_groups in a window."""
    import psycopg2
    conn = psycopg2.connect(_db_dsn(), connect_timeout=8)
    try:
        cur = conn.cursor()
        cur.execute(
            'SELECT count(*) FROM "LiteLLM_SpendLogs" '
            'WHERE "startTime" > now() - interval %s '
            'AND model_group = ANY(%s)',
            (interval_sql, list(model_groups)))
        return cur.fetchone()[0]
    finally:
        conn.close()


def _db_oldest_in_window(model_groups, interval_sql):
    """Oldest request timestamp (ms epoch) inside the window — for rolling
    windows this is when usage starts to drain."""
    import psycopg2
    conn = psycopg2.connect(_db_dsn(), connect_timeout=8)
    try:
        cur = conn.cursor()
        cur.execute(
            'SELECT min("startTime") FROM "LiteLLM_SpendLogs" '
            'WHERE "startTime" > now() - interval %s '
            'AND model_group = ANY(%s)',
            (interval_sql, list(model_groups)))
        ts = cur.fetchone()[0]
        if not ts:
            return None
        # SpendLogs timestamps are stored as naive UTC
        return int(ts.replace(tzinfo=timezone.utc).timestamp() * 1000)
    finally:
        conn.close()


def _next_monday_utc8_ms():
    """Alibaba coding-plan weekly reset: Monday 00:00 (UTC+8)."""
    now = datetime.now(timezone.utc)
    days = (7 - now.weekday()) % 7
    nxt = (now + timedelta(days=days)).replace(
        hour=16, minute=0, second=0, microsecond=0)  # 00:00 +8 == 16:00 UTC
    if nxt <= now:
        nxt += timedelta(days=7)
    return int(nxt.timestamp() * 1000)


def _db_plan_windows(p):
    """Build normalized windows for a request-count-based plan (no upstream
    quota API — usage is counted from our own SpendLogs)."""
    groups = p["db_groups"]
    lim = p["request_limits"]

    used5 = _db_count_requests(groups, "5 hours")
    oldest = _db_oldest_in_window(groups, "5 hours")
    interval_reset = (oldest + 5 * 3600 * 1000) if oldest else None
    used_w = _db_count_requests(groups, "7 days")
    weekly_reset = _next_monday_utc8_ms()

    def mk(used, total, reset_ms):
        return {
            "remaining_percent": (round(100 - used / total * 100, 1)
                                  if total else None),
            "usage_count": used,
            "total_count": total,
            "resets_in_ms": _ms_until(reset_ms),
            "end_time": reset_ms,
        }

    return [{
        "name": "request counters",
        "interval": mk(used5, lim.get("interval"), interval_reset),
        "weekly": mk(used_w, lim.get("weekly"), weekly_reset),
        "extra": [{
            "title": "Месяц (30 дней)",
            **mk(_db_count_requests(groups, "30 days"), lim.get("monthly"), None),
        }] if lim.get("monthly") else [],
    }]


# Bailian console CLI gateway — the same endpoint the `bl` CLI
# (bailian-cli) uses for `bl usage token-plan` / `bl usage coding-plan`.
# Auth is a console access_token captured via `bl auth login --console`
# (or the /console-login?notice=<host:port> browser callback). The token
# lives in /opt/litellm/.env as ALIYUN_CONSOLE_TOKEN; when it expires the
# upstream answers with a NotLogined errorCode and the browser flow must
# be repeated (refresh requires OpenAPI AK/SK which we don't have).
_ALIYUN_GW = {
    ("cn-beijing", "domestic"): (
        "bailian-cs.console.aliyun.com", "BroadScopeAspnGateway"),
    ("cn-beijing", "international"): (
        "bailian-cs.console.alibabacloud.com", "BroadScopeAspnGateway"),
    ("ap-southeast-1", "domestic"): (
        "modelstudio-cs.console.aliyun.com", "IntlBroadScopeAspnGateway"),
    ("ap-southeast-1", "international"): (
        "bailian-singapore-cs.alibabacloud.com", "IntlBroadScopeAspnGateway"),
}


def _aliyun_console_cfg():
    # The console token rotates via the browser flow, so it lives in a
    # dev01-writable file next to api.py rather than the root-owned
    # litellm .env (still supported as a fallback source).
    vals = {}
    for path in ("/opt/opencode-setup/aliyun_console.env", ENV_FILE):
        if not os.path.exists(path):
            continue
        with open(path) as f:
            for line in f:
                m = re.match(
                    r'^(ALIYUN_CONSOLE_\w+)\s*=\s*["\']?([^"\'#\n]+)', line)
                if m:
                    vals[m.group(1)] = m.group(2).strip()
        if vals.get("ALIYUN_CONSOLE_TOKEN"):
            break
    if not vals.get("ALIYUN_CONSOLE_TOKEN"):
        raise RuntimeError("ALIYUN_CONSOLE_TOKEN not set in .env")
    region = vals.get("ALIYUN_CONSOLE_REGION", "ap-southeast-1")
    site = vals.get("ALIYUN_CONSOLE_SITE", "international")
    host, action = _ALIYUN_GW.get(
        (region, site), _ALIYUN_GW[("cn-beijing", "domestic")])
    agent = vals.get("ALIYUN_CONSOLE_SWITCH_AGENT")
    return {"token": vals["ALIYUN_CONSOLE_TOKEN"], "region": region,
            "host": host, "action": action,
            "agent": int(agent) if agent else None}


def _aliyun_console_call(api_name, data=None):
    """POST the console gateway (region + params form body) and unwrap the
    DataV2 double envelope down to the inner data object.

    NOTE: the gateway answers an empty 200 to some HTTP stacks (plain curl
    is filtered by Alibaba WAF); urllib/httpx fingerprints pass fine.
    """
    cfg = _aliyun_console_cfg()
    cornerstone = {
        "protocol": "V2",
        "console": "ONE_CONSOLE",
        "productCode": "p_efm",
        "switchUserType": 3,
        "consoleSite": "BAILIAN_ALIYUN",
    }
    if cfg["agent"] is not None:
        cornerstone["switchAgent"] = cfg["agent"]
    params = json.dumps({
        "Api": api_name, "V": "1.0",
        "Data": {**(data or {}), "cornerstoneParam": cornerstone}})
    body = urllib.parse.urlencode(
        {"params": params, "region": cfg["region"]}).encode()
    url = (f"https://{cfg['host']}/cli/api.json?action={cfg['action']}"
           f"&product=sfm_bailian&api={urllib.parse.quote(api_name)}")
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {cfg['token']}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        j = json.loads(r.read().decode())
    inner = j.get("data") or {}
    if inner.get("success") is False and inner.get("errorCode"):
        raise RuntimeError(f"aliyun console: {inner['errorCode']}")
    payload = (inner.get("DataV2") or {}).get("data") or {}
    return payload.get("data") or payload


def _alitoken_windows(p):
    """Alibaba Token Plan quota via the console gateway.

    Inner data carries per5Hour*/per1Week*/per1Month* pairs of
    <window>Percentage (fraction USED, 0..1) + <window>ResetTime (epoch ms).
    Windows that are absent mean "unlimited" on this plan — ours exposes
    only the monthly quota, so it lands in the interval slot with a title
    (same card shape as MiMo).
    """
    d = _aliyun_console_call(
        "zeldaHttp.apikeyMgr./tokenplan/personal/api/v2/usage")

    def win(pct, ms):
        return {
            "remaining_percent": (round(100 - pct * 100, 1)
                                  if pct is not None else None),
            "resets_in_ms": _ms_until(ms),
            "end_time": ms,
        }

    interval = (win(d.get("per5HourPercentage"), d.get("per5HourResetTime"))
                if d.get("per5HourPercentage") is not None else None)
    weekly = (win(d.get("per1WeekPercentage"), d.get("per1WeekResetTime"))
              if d.get("per1WeekPercentage") is not None else None)
    monthly = (win(d.get("per1MonthPercentage"), d.get("per1MonthResetTime"))
               if d.get("per1MonthPercentage") is not None else None)
    # None windows are skipped by the frontend — our plan only has the
    # monthly quota, so no empty "Недельное окно" bar should render.
    out = {"name": "token plan", "interval": interval, "weekly": weekly}
    if monthly:
        if interval or weekly:
            out["extra"] = [{"title": "Месячная квота", **monthly}]
        else:
            out["interval"] = {"title": "Месячная квота", **monthly}
    return [out]


def _alicoding_windows(p):
    """Alibaba Coding Plan quota — console gateway first (authoritative
    per-window counters + reset times + instance tier), SpendLogs request
    counting as fallback when the console token is missing/expired."""
    try:
        d = _aliyun_console_call(
            "zeldaEasy.broadscope-bailian.codingPlan."
            "queryCodingPlanInstanceInfoV2",
            {"queryCodingPlanInstanceInfoRequest": {
                "commodityCode": "sfm_codingplan_public_intl",
                "onlyLatestOne": True}})
        inst = next((i for i in d.get("codingPlanInstanceInfos") or []
                     if i.get("status") == "VALID"), None)
        if inst:
            q = inst.get("codingPlanQuotaInfo") or {}

            def win(pref):
                used = q.get(f"{pref}UsedQuota")
                total = q.get(f"{pref}TotalQuota")
                ms = q.get(f"{pref}QuotaNextRefreshTime")
                if used is None and total is None and ms is None:
                    return None
                return {
                    "remaining_percent": (
                        round(100 - used / total * 100, 1)
                        if used is not None and total else None),
                    "usage_count": used,
                    "total_count": total,
                    "resets_in_ms": _ms_until(ms),
                    "end_time": ms,
                }

            monthly = win("perBillMonth")
            return [{
                "name": inst.get("instanceType") or "coding plan",
                "interval": win("per5Hour"),
                "weekly": win("perWeek"),
                "extra": ([{"title": "Месяц (30 дней)", **monthly}]
                          if monthly else []),
            }]
    except Exception:
        pass
    return _db_plan_windows(p)


def _iso_to_ms(s):
    """'2026-10-15T22:57:49.500651Z' -> epoch ms (naive-safe)."""
    if not s:
        return None
    try:
        dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _norm_zai_quota(raw):
    """Z.AI /api/monitor/usage/quota/limit -> normalized window list.

    limits[] rows: TOKENS_LIMIT unit=3 is the 5-hour window, unit=6 is the
    weekly window; TIME_LIMIT unit=5 is the monthly MCP-tool budget (shown
    under `extra`, not one of the two token bars). `percentage` is the share
    USED, so remaining = 100 - percentage. nextResetTime is epoch ms.
    """
    data = raw.get("data") or {}
    interval, weekly, extra = {}, {}, None
    for e in data.get("limits") or []:
        rst = e.get("nextResetTime")
        pct = e.get("percentage")
        win = {
            "remaining_percent": (100 - pct) if pct is not None else None,
            "usage_count": e.get("currentValue"),
            "total_count": e.get("usage"),
            "resets_in_ms": _ms_until(rst),
            "end_time": rst,
        }
        t, u = e.get("type"), e.get("unit")
        if t == "TOKENS_LIMIT" and u == 3:
            interval = win
        elif t == "TOKENS_LIMIT" and u == 6:
            weekly = win
        elif t == "TIME_LIMIT":
            extra = win
    out = {
        "name": data.get("level") or "coding plan",
        "interval": interval,
        "weekly": weekly,
    }
    if extra:
        out["extra"] = [{"title": "Поиск и ридеры (месяц)", **extra}]
    return [out]


def _norm_kimi_usages(raw):
    """Kimi Code /coding/v1/usages -> normalized window list.

    usages.limit_5h / limit_7d carry used_ratio (0..1) and an ISO reset_time.
    """
    u = raw.get("usages") or {}

    def win(block):
        b = u.get(block) or {}
        r = b.get("used_ratio")
        ms = _iso_to_ms(b.get("reset_time"))
        return {
            "remaining_percent": (round(100 - r * 100, 1)
                                  if r is not None else None),
            "resets_in_ms": _ms_until(ms),
            "end_time": ms,
        }

    return [{"name": "kimi code",
             "interval": win("limit_5h"), "weekly": win("limit_7d")}]


def _norm_mimo_usage(raw):
    """Xiaomi MiMo tokenPlan usage (+ detail) -> normalized window list.

    data.monthUsage.items[]: name/used/limit/percent (percent = fraction
    USED, 0..1). The plan is a single monthly Credits window — no 5h/weekly
    bars, so the monthly bar takes the interval slot with a proper title.
    data.currentPeriodEnd ("2026-11-06 23:59:59", Beijing time) is fetched
    from tokenPlan/detail when the provider supplies endpoint_detail.
    """
    usage = raw.get("usage_resp") or raw
    detail = raw.get("detail_resp") or {}
    items = ((usage.get("data") or {}).get("monthUsage") or {}).get("items") or []
    end_ms = None
    d = detail.get("data") or {}
    if d.get("currentPeriodEnd"):
        # "2026-11-06 23:59:59" is Beijing-local — _iso_to_ms assumes UTC,
        # so shift by 8 hours.
        ms = _iso_to_ms(d["currentPeriodEnd"].replace(" ", "T"))
        if ms:
            end_ms = ms - 8 * 3600 * 1000
    out = []
    plan_name = d.get("planName")
    for e in items:
        used, limit, pct = e.get("used"), e.get("limit"), e.get("percent")
        remaining = (round(100 - pct * 100, 2) if pct is not None
                     else (round(100 - used / limit * 100, 2)
                           if used and limit else None))
        out.append({
            "name": plan_name or e.get("name") or "credits",
            "interval": {
                "title": "Месячная квота (кредиты)",
                "remaining_percent": remaining,
                "usage_count": used,
                "total_count": limit,
                "resets_in_ms": _ms_until(end_ms),
                "end_time": end_ms,
            },
            "weekly": None,
        })
    return out


_LIMIT_PROVIDERS = {
    "minimax": {
        "label": "MiniMax Token Plan",
        "models": {
            "MiniMax-M2.1-highspeed", "MiniMax-M2.5", "MiniMax-M2.5-highspeed",
            "MiniMax-M2.7", "MiniMax-M2.7 (res)", "MiniMax-M2.7-highspeed",
            "MiniMax-M3", "Minimax-M3", "MiniMax-M3.1-Flash-Preview",
        },
        "endpoint": "/minimax/v1/token_plan/remains",
        "normalize": _norm_minimax_remains,
    },
    "zai": {
        "label": "GLM Coding Plan (Z.AI)",
        "models": {"GLM-5.3", "GLM-5.3-Flash"},
        "endpoint": "/zai/api/monitor/usage/quota/limit",
        "normalize": _norm_zai_quota,
    },
    "zaires": {
        # Second Z.AI account — the "(res)" deployments ride it as a fallback
        # for the main GLM models, so anyone with GLM access sees both bars.
        "label": "GLM Coding Plan (Z.AI, резерв)",
        "models": {
            "GLM-5.3", "GLM-5.3-Flash",
            "GLM-5.3 (res)", "GLM-5.3-Flash (res)",
        },
        "endpoint": "/zaires/api/monitor/usage/quota/limit",
        "normalize": _norm_zai_quota,
    },
    "kimi": {
        # Both Kimi credentials are keys on the same Kimi Code account, so
        # all four deployments share one quota — a single card is enough.
        "label": "Kimi Code",
        "models": {"Kimi K2.7", "Kimi K3", "Kimi K2.8", "Kimi K3-256K"},
        "endpoint": "/kimi/coding/v1/usages",
        "normalize": _norm_kimi_usages,
    },
    "mimo": {
        # Xiaomi MiMo Token Plan. Quota lives behind the platform's web-SSO
        # session — the passthrough replays the session Cookie from config.
        # Cookie will eventually expire -> replace in config.yaml.
        "label": "MiMo Token Plan (Xiaomi)",
        "models": {
            "mimo-v2.5", "mimo-v2.5-pro",
            "xiaomi/mimo-v2.6-flash", "xiaomi/mimo-v2.6-pro",
            "voice/xiaomi/mimo-v2.5-asr", "voice/xiaomi/mimo-v2.5-tts",
            "voice/xiaomi/mimo-v2.5-tts-voiceclone",
            "voice/xiaomi/mimo-v2.5-tts-voicedesign",
        },
        "endpoint": "/mimo/api/v1/tokenPlan/usage",
        "endpoint_detail": "/mimo/api/v1/tokenPlan/detail",
        "normalize": _norm_mimo_usage,
    },
    "alicoding": {
        # Alibaba Cloud Coding Plan (coding-intl.dashscope.aliyuncs.com).
        # Authoritative counters come from the Bailian console gateway
        # (same API as `bl usage coding-plan`); when the console token is
        # missing or expired we fall back to counting the plan's own
        # traffic in LiteLLM_SpendLogs. Pro tier: 6000 req/5h, 45000/week,
        # 90000/month.
        "label": "Alibaba Coding Plan",
        "models": {"QWEN3.7-plus", "Qwen3.5-plus"},
        "db_groups": {"QWEN3.7-plus", "Qwen3.5-plus"},
        "request_limits": {"interval": 6000, "weekly": 45000,
                           "monthly": 90000},
        "local": _alicoding_windows,
    },
    "alitoken": {
        # Alibaba Token Plan (token-plan.ap-southeast-1.maas.aliyuncs.com).
        # Quota lives only in the Bailian console — read through the
        # console CLI gateway with ALIYUN_CONSOLE_TOKEN from .env.
        "label": "Alibaba Token Plan",
        "models": {
            "AlibabaTokenPlan/deepseek-v4-flash-0731",
            "AlibabaTokenPlan/deepseek-v4-pro-0813",
            "aliyun/qwen3.8-flash", "qwen3.6-flash",
            "qwen3.7-max", "qwen3.8-max",
        },
        "local": _alitoken_windows,
    },
}

# Models that support image generation (text-to-image) via /v1/images/generations.
# Litellm model_info.mode == 'image_generation' identifies these.
# All image models in DB currently have mode = 'image_generation' (set during
# model creation); the gate is enforced at LiteLLM proxy level.

# Models that ALSO support image-to-image editing via /v1/images/edits.
# - gpt-image-1.5 / gpt-image-2: OpenAI image edits endpoint, native support.
# - gemini/gemini-3.1-flash-image: routed to native Gemini :generateContent with
#   multimodal input (text + image parts), model must be configured with
#   custom_llm_provider='gemini' (NOT 'openai') so LiteLLM uses the Gemini
#   image_edit transformation instead of OpenAI's.
# - gemini/gemini-3-pro-image: multimodal Gemini 3 Pro with image generation,
#   supports both text-to-image AND image-to-image via native :generateContent.
#   Upstream gemini-3-pro-image (NOT imagen-4). Routed to native provider.
_IMAGE_EDIT_CAPABLE = {
    "gpt-image-1.5",
    "gpt-image-2",
    "gemini/gemini-3.1-flash-image",
    "gemini/gemini-3-pro-image",
    "gpt-image-2.5-sunburst", "gpt-image-2.5-flare",
}


def load_master_key():
    global _MASTER_KEY
    if _MASTER_KEY:
        return _MASTER_KEY
    if not os.path.exists(ENV_FILE):
        raise RuntimeError(f"missing {ENV_FILE}")
    with open(ENV_FILE) as f:
        for line in f:
            m = re.match(r'^LITELLM_MASTER_KEY\s*=\s*["\']?([^"\'#\s]+)', line)
            if m:
                _MASTER_KEY = m.group(1)
                return _MASTER_KEY
    raise RuntimeError(f"LITELLM_MASTER_KEY not found in {ENV_FILE}")


def litellm_get(path):
    key = load_master_key()
    req = urllib.request.Request(
        LITELLM_BASE + path,
        headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def litellm_post(path, payload):
    key = load_master_key()
    req = urllib.request.Request(
        LITELLM_BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _passthrough_get(path, vk_token):
    """Call a LiteLLM passthrough endpoint authenticated by a real VK.

    Passthrough routes reject the master key (block_master_key_hook), so we
    must authenticate with an actual virtual key — either the caller's own
    or a short-lived probe key created on the fly.
    """
    req = urllib.request.Request(
        LITELLM_BASE + path,
        headers={"Authorization": "Bearer " + vk_token,
                 "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _create_probe_key(user_id):
    """Short-lived VK used to authenticate passthrough quota lookups when the
    caller came in by ?user= (no raw VK at hand). Deleted right after use."""
    doc = litellm_post("/key/generate", {
        "user_id": user_id,
        "duration": "2m",
        "key_alias": "limits-probe",
    })
    return doc.get("key")


def _delete_key(token):
    try:
        litellm_post("/key/delete", {"keys": [token]})
    except Exception:
        pass


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

# Virtual key prefixes supported by LiteLLM (see litellm.proxy.common_utils._keys).
# A VK lookup must start with one of these, otherwise it's clearly not a key token.
VK_PREFIX_RE = re.compile(r"^(sk-|hkg-)")

# Key hash: 64-char lowercase hex (SHA-256). LiteLLM stores keys internally
# as hashes (token_sha256_hash / token in VerificationToken). LiteLLM
# /key/info accepts ?key=<hash> as a secure lookup that doesn't expose the
# raw sk-/hkg- prefix.
VK_HASH_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)

def _to_int(v):
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


# Cache VK -> user_id lookups. Keys are rarely rotated, so a 5-minute cache
# dramatically reduces load when the opencode-setup page is open in many tabs.
_VK_CACHE = {}
_VK_CACHE_TTL_S = 300


def resolve_vk_to_user(vk_token, is_hash=False):
    """Resolve a virtual-key token (sk-.../hkg-...) or its SHA-256 hash to
    {user_id, key_alias, team_id, models, user_email, key_name} via LiteLLM
    /key/info. Returns None if the key is invalid/unknown.

    Result is cached in-memory for 5 minutes. Use force=True to bypass.

    LiteLLM's /key/info endpoint accepts both the raw token AND its hash.
    Using hashes is safer (no need to ship sk-... strings through the page)
    and is what the Admin UI itself uses internally.

    The `models` field is the per-key whitelist (VerificationToken.models).
    If empty, the key inherits models from user + teams.
    """
    if not vk_token:
        return None
    if not is_hash and not VK_PREFIX_RE.match(vk_token):
        return None
    if is_hash and not VK_HASH_RE.match(vk_token):
        return None
    now = __import__("time").time()
    cached = _VK_CACHE.get(vk_token)
    if cached and cached[0] > now - _VK_CACHE_TTL_S:
        return cached[1]
    try:
        doc = litellm_get("/key/info?key=" + urllib.parse.quote(vk_token, safe=""))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _VK_CACHE[vk_token] = (now, None)
            return None
        raise
    info = doc.get("info") or doc
    user_id = info.get("user_id") or None
    if isinstance(user_id, str) and not UUID_RE.match(user_id):
        # Some keys are scoped to a team without a user_id. Fall back to
        # the team owner's user_id by reading the team membership list.
        user_id = None
    key_alias = info.get("key_alias") or ""
    team_id = info.get("team_id") or None
    user_email = info.get("user_email") or None
    key_name = info.get("key_name") or None
    # Per-key model whitelist (VerificationToken.models). May be None / [].
    raw_models = info.get("models")
    if raw_models is None:
        key_models = []
    elif isinstance(raw_models, list):
        key_models = [m for m in raw_models if isinstance(m, str)]
    else:
        key_models = []
    result = {
        "user_id": user_id,
        "key_alias": key_alias,
        "team_id": team_id,
        "user_email": user_email,
        "key_name": key_name,
        "key_models": key_models,  # [] means "inherit from user + teams"
    }
    _VK_CACHE[vk_token] = (now, result)
    return result


def resolve_target(qs):
    """Return user_id (str) given parsed query-string from /models or /mcp.

    Accepts either ?user=<UUID> (or user_id=<UUID>), ?vk=sk-... (or hkg-...),
    or ?vkh=<64-hex SHA-256>. Raises ValueError with a human-readable
    message when none is supplied or all are bad.

    When called from /models or /mcp, also stashes the VK info in
    _VK_CONTEXT so resolve_models() can intersect with the key's own
    model whitelist. When the key has its own models list, that's the
    AUTHORITATIVE list — user/team models are ignored.
    """
    global _VK_CONTEXT
    vk = (qs.get("vk") or [""])[0]
    vkh = (qs.get("vkh") or [""])[0]
    uid = (qs.get("user") or qs.get("user_id") or [""])[0]
    # VK parameters take priority over plain user lookup. The setup-opencode
    # page forwards both ?user=<resolved-uid> and ?vkh=... (so a refresh
    # works even after the key row rotates). We must honor VK context so
    # resolve_models() applies team-only / whitelist rules — otherwise the
    # owner's other teams' models would leak into the answer.
    if vk or vkh:
        if vk:
            info = resolve_vk_to_user(vk)
            if not info or not info.get("user_id"):
                raise LookupError(f"virtual key not found or has no user_id: {vk[:12]}...")
        else:
            info = resolve_vk_to_user(vkh, is_hash=True)
            if not info or not info.get("user_id"):
                raise LookupError("virtual key (hash) not found or has no user_id")
        if not info.get("user_email"):
            try:
                udoc = litellm_get("/user/info?user_id=" + urllib.parse.quote(info["user_id"]))
                uinfo = udoc.get("user_info") or udoc
                info["user_email"] = uinfo.get("user_email")
            except Exception:
                pass
        _VK_CONTEXT = info
        via = "vk" if vk else "vkh"
        return info["user_id"], {"via": via, "user_id": info["user_id"],
                                  "vk": vk or None, "vkh": vkh or None,
                                  "key_alias": info.get("key_alias"),
                                  "user_email": info.get("user_email"),
                                  "key_name": info.get("key_name"),
                                  "key_models": info.get("key_models") or []}
    if uid:
        if not UUID_RE.match(uid):
            raise ValueError(
                f"user param must be a UUID, got: {uid!r}. " +
                "API keys / virtual keys (sk-...) are NOT user_ids."
            )
        _VK_CONTEXT = None  # No VK context for plain user lookup.
        return uid, {"via": "user", "user_id": uid,
                     "vk": None, "vkh": None, "key_alias": None,
                     "user_email": None, "key_name": None,
                     "key_models": []}
    raise ValueError("missing user, vk or vkh param")


# Set by resolve_target() when the request was scoped to a VK. Read by
# resolve_models() so it can intersect with the per-key model whitelist.
_VK_CONTEXT = None


def _extract_meta(m):
    """Read context/output limits, modality flags and image capability from a
    /model/info entry.

    Returns (ctx, out, vision, video, audio, image, image_edit, blocked) where
    the modality booleans come from model_info.supports_vision /
    supports_video_input / supports_audio_input (all optional, default False).

    `image` is True when mode='image_generation' (text-to-image via
    /v1/images/generations). `image_edit` is True when the model is in
    _IMAGE_EDIT_CAPABLE (image-to-image via /v1/images/edits). `blocked` is
    True when LiteLLM has flagged the model as blocked (admin-disabled).
    """
    lp = m.get("litellm_params") or {}
    mi = m.get("model_info") or {}
    ctx = _to_int(lp.get("max_input_tokens") or mi.get("max_input_tokens"))
    out = _to_int(lp.get("max_tokens") or mi.get("max_tokens"))
    vision = bool(mi.get("supports_vision"))
    video = bool(mi.get("supports_video_input"))
    audio = bool(mi.get("supports_audio_input"))
    image = (mi.get("mode") == "image_generation")
    mn = m.get("model_name") or ""
    image_edit = image and (mn in _IMAGE_EDIT_CAPABLE)
    blocked = bool(mi.get("blocked"))
    return ctx, out, vision, video, audio, image, image_edit, blocked


def build_model_info():
    global _MODEL_INFO, _ALIASES
    if _MODEL_INFO is not None:
        return _MODEL_INFO
    info = {}
    aliases = {}
    try:
        doc = litellm_get("/model/info")
        for m in doc.get("data", []):
            mn = m.get("model_name")
            if not mn:
                continue
            ctx, out, vision, video, audio, image, image_edit, blocked = _extract_meta(m)
            if blocked:
                continue
            info[mn] = {
                "context": ctx,
                "output": out,
                "vision": vision,
                "video": video,
                "audio": audio,
                "image": image,
                "image_edit": image_edit,
                "mode": (m.get("model_info") or {}).get("mode"),
            }
            aliases[mn] = mn
            # Alias by upstream `model` field (LiteLLM may store the openai-style
            # name there for OpenAI-compat providers).
            lm = (m.get("litellm_params") or {}).get("model", "")
            if lm and lm not in aliases:
                aliases[lm] = mn
            # For model_name like "gemini/gemini-3.1-flash-image", alias the
            # bare "gemini-3.1-flash-image" too, because users see that name in
            # team.model lists (LiteLLM strips the provider prefix there).
            if "/" in mn:
                bare = mn.split("/", 1)[1]
                if bare not in aliases:
                    aliases[bare] = mn
    except Exception:
        pass
    _MODEL_INFO = info
    _ALIASES = aliases
    return info


def resolve_model_name(raw):
    build_model_info()
    return (_ALIASES or {}).get(raw, raw)


def resolve_user_teams(user_id):
    """Return list of team_info dicts the user belongs to."""
    teams = []
    try:
        udoc = litellm_get(f"/user/info?user_id={urllib.parse.quote(user_id)}")
    except urllib.error.HTTPError:
        return teams
    info = udoc.get("user_info") or udoc
    for team_id in info.get("teams") or []:
        try:
            tdoc = litellm_get(f"/team/info?team_id={urllib.parse.quote(team_id)}")
        except urllib.error.HTTPError:
            continue
        tinfo = tdoc.get("team_info") or tdoc
        if isinstance(tinfo, dict):
            teams.append(tinfo)
    return teams


def resolve_team(team_id):
    """Return a single team_info dict for the given team_id, or None on failure."""
    try:
        tdoc = litellm_get(f"/team/info?team_id={urllib.parse.quote(team_id)}")
    except urllib.error.HTTPError:
        return None
    tinfo = tdoc.get("team_info") or tdoc
    return tinfo if isinstance(tinfo, dict) else None


def resolve_models(user_id):
    """Aggregate model access according to LiteLLM's actual model resolution.

    Resolution priority (matches LiteLLM proxy behavior for VerificationToken
    auth, verified empirically):

      1. Per-key whitelist (VerificationToken.models) is AUTHORITATIVE when it
         contains REAL model names. Special values like "all-team-models" mean
         "inherit from team only" — that's a different case (handled below).
      2. If key_models == ["all-team-models"] (team-scoped key with sentinel):
         use ONLY the models of the team the VK is BOUND to (key.team_id).
         User.personal models are NOT applied. The user's other teams are NOT
         applied — only the one the VK is scoped to.
      3. If key_models is empty (None / []): no per-key whitelist. For a
         user-scoped VK, inherit user.models + all user's teams. For a
         team-scoped VK (team_id set), the model set is empty — those keys
         MUST have models=['all-team-models'] to be useful.
      4. Otherwise (real whitelist): use the whitelist verbatim.

    Returns a list of model dicts sorted by id. Side-channel info about the
    resolution path is stashed in _VK_CONTEXT so /models can report it back
    to the UI (e.g. "this VK inherits from team Agents only").
    """
    try:
        udoc = litellm_get(f"/user/info?user_id={urllib.parse.quote(user_id)}")
    except urllib.error.HTTPError:
        return []
    info = udoc.get("user_info") or udoc

    user_models = set(info.get("models") or [])

    # Teams the user belongs to (used for non-VK lookups).
    user_team_models = set()
    for team in resolve_user_teams(user_id):
        user_team_models.update(team.get("models") or [])

    raw_models = None
    resolution_mode = None  # one of: "user+team", "team-only", "explicit", "none"
    if _VK_CONTEXT:
        km = _VK_CONTEXT.get("key_models") or []
        vk_team_id = _VK_CONTEXT.get("team_id")
        if km == ["all-team-models"]:
            # Team-scoped key with sentinel. LiteLLM uses ONLY the team the
            # key is BOUND to (VerificationToken.team_id), NOT the user's
            # other teams or personal models.
            bound_team = resolve_team(vk_team_id) if vk_team_id else None
            raw_models = set((bound_team or {}).get("models") or [])
            resolution_mode = "team-only"
        elif km:
            # Explicit per-key whitelist — overrides everything.
            raw_models = set(km)
            resolution_mode = "explicit"
        else:
            # Empty key whitelist on a user-scoped VK: inherit user + all teams.
            if vk_team_id:
                # Team-scoped but no whitelist — LiteLLM allows nothing.
                # (This shouldn't happen in practice; team keys always have
                # 'all-team-models'. We surface this honestly.)
                bound_team = resolve_team(vk_team_id)
                raw_models = set((bound_team or {}).get("models") or [])
                resolution_mode = "team-only"
            else:
                raw_models = user_models | user_team_models
                resolution_mode = "user+team"
    else:
        # Not a VK request: just user + all teams.
        raw_models = user_models | user_team_models
        resolution_mode = "user+team"

    # Surface resolution info via the (mutable) _VK_CONTEXT so /models can
    # echo it back to the UI. Existing fields stay intact.
    if _VK_CONTEXT is not None:
        _VK_CONTEXT["resolution_mode"] = resolution_mode

    table = build_model_info()
    out = []
    for raw in raw_models:
        canonical = resolve_model_name(raw)
        entry = table.get(canonical)
        if entry and entry.get("mode") in _BLOCKED_MODES:
            continue
        limits = entry or {
            "context": 0, "output": 0,
            "vision": False, "video": False, "audio": False,
            "image": False, "image_edit": False,
        }
        out.append({
            "id": canonical,
            "context": limits["context"],
            "output": limits["output"],
            "vision": limits.get("vision", False),
            "video": limits.get("video", False),
            "audio": limits.get("audio", False),
            "image": limits.get("image", False),
            "image_edit": limits.get("image_edit", False),
            "reasoning": canonical in _REASONING_CAPABLE,
            "variants": _REASONING_VARIANTS.get(canonical),
        })
    out.sort(key=lambda x: x["id"])
    return out


def _collect_team_mcp_ids(teams):
    """Aggregate mcp_servers + mcp_access_groups across teams.

    Returns (server_id_set, access_group_set). server_ids may be raw
    server_id UUIDs OR alias names (LiteLLM stores both formats).
    """
    server_ids = set()
    access_groups = set()
    for team in teams:
        op = team.get("object_permission") or {}
        if not isinstance(op, dict):
            continue
        for sid in op.get("mcp_servers") or []:
            if sid:
                server_ids.add(sid)
        for ag in op.get("mcp_access_groups") or []:
            if ag:
                access_groups.add(ag)
    return server_ids, access_groups


def _list_mcp_servers():
    """Return the raw list of registered MCP server objects from LiteLLM."""
    try:
        doc = litellm_get("/v1/mcp/server")
    except (urllib.error.HTTPError, Exception):
        return []
    if isinstance(doc, dict):
        return doc.get("data") or []
    return doc or []


def resolve_user_mcp_servers(user_id):
    """Resolve MCP servers accessible to a user via Teams.

    Strategy:
      1. Resolve all teams the user belongs to (via /user/info).
      2. Aggregate object_permission.mcp_servers (LiteLLM stores these
         as server_name OR alias) and object_permission.mcp_access_groups
         across teams.
      3. List registered MCP servers via /v1/mcp/server.
      4. Keep only servers whose server_name (or alias) matches the
         team's allowed set, OR that belong to one of the allowed
         access_groups.
      5. URL-namespaced through LiteLLM proxy; per-tool filtering is
         applied server-side at request time.
    """
    teams = resolve_user_teams(user_id)
    allowed_ids, allowed_groups = _collect_team_mcp_ids(teams)

    if not allowed_ids and not allowed_groups:
        return []

    registered = _list_mcp_servers()
    matched = []
    for s in registered:
        if not isinstance(s, dict):
            continue
        sid = s.get("server_id")
        sname = s.get("server_name")
        alias = s.get("alias")
        server_groups = set(s.get("mcp_access_groups") or [])

        # Match by server_id (LiteLLM stores mcp_servers as UUIDs in
        # object_permission) OR by alias/server_name (legacy / manual config).
        if sid and sid in allowed_ids:
            matched.append(s); continue
        if sname and sname in allowed_ids:
            matched.append(s); continue
        if alias and alias in allowed_ids:
            matched.append(s); continue
        if server_groups and (server_groups & allowed_groups):
            matched.append(s); continue

    servers_out = []
    for s in matched:
        sname = s.get("alias") or s.get("server_name")
        if not sname:
            continue
        servers_out.append({
            "name": sname,
            "url": f"{EXTERNAL_BASE_URL}/{sname}/mcp",
            "description": s.get("description", ""),
        })
    return sorted(servers_out, key=lambda s: s["name"])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a, **kw):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            u = urllib.parse.urlparse(self.path)
            if u.path == "/health":
                return self._send(200, {"ok": True})

            if u.path == "/vk-info":
                qs = urllib.parse.parse_qs(u.query)
                vk = (qs.get("vk") or [""])[0]
                vkh = (qs.get("vkh") or [""])[0]
                if not vk and not vkh:
                    return self._send(400, {"error": "missing vk or vkh param"})
                if vk:
                    info = resolve_vk_to_user(vk)
                else:
                    info = resolve_vk_to_user(vkh, is_hash=True)
                if not info:
                    return self._send(404, {"error": "virtual key not found",
                                            "vk_prefix": vk[:12] if vk else None,
                                            "vkh": vkh[:8] + "..." if vkh else None})
                # Enrich with user_email if missing.
                if info.get("user_id") and not info.get("user_email"):
                    try:
                        udoc = litellm_get("/user/info?user_id=" + urllib.parse.quote(info["user_id"]))
                        uinfo = udoc.get("user_info") or udoc
                        info["user_email"] = uinfo.get("user_email")
                    except Exception:
                        pass
                # Enrich with team_alias if team_id present (for the UI).
                if info.get("team_id") and not info.get("team_alias"):
                    try:
                        tdoc = litellm_get("/team/info?team_id=" + urllib.parse.quote(info["team_id"]))
                        tinfo = tdoc.get("team_info") or tdoc
                        info["team_alias"] = tinfo.get("team_alias")
                        info["team_models"] = tinfo.get("models") or []
                    except Exception:
                        pass
                # Explain the special 'all-team-models' sentinel so the UI
                # doesn't treat it as a literal model name.
                km = info.get("key_models") or []
                if km == ["all-team-models"]:
                    info["key_models_meaning"] = "all-team-models"
                elif km:
                    info["key_models_meaning"] = "explicit-whitelist"
                else:
                    info["key_models_meaning"] = "inherited"
                return self._send(200, info)

            if u.path == "/models":
                qs = urllib.parse.parse_qs(u.query)
                try:
                    uid, ctx = resolve_target(qs)
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                except LookupError as e:
                    return self._send(404, {"error": str(e)})
                try:
                    models = resolve_models(uid)
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        return self._send(404, {"error": "user not found"})
                    raise
                body = {"models": models, "resolved_via": ctx["via"]}
                if ctx["via"] in ("vk", "vkh"):
                    body["user_id"] = ctx["user_id"]
                    body["key_alias"] = ctx["key_alias"]
                    body["user_email"] = ctx.get("user_email")
                    body["key_name"] = ctx.get("key_name")
                    if _VK_CONTEXT:
                        body["team_id"] = _VK_CONTEXT.get("team_id")
                        # resolution_mode is set by resolve_models() AFTER
                        # reading _VK_CONTEXT. 'team-only' means key_models ==
                        # ['all-team-models'] — the most common team key pattern.
                        body["resolution_mode"] = _VK_CONTEXT.get("resolution_mode", "user+team")
                    # Pass the raw key_models only when it's an explicit
                    # whitelist (NOT ['all-team-models']) so the UI can show
                    # the actual restriction.
                    km = ctx.get("key_models") or []
                    if km and km != ["all-team-models"]:
                        body["vk_key_models"] = km
                return self._send(200, body)

            if u.path == "/mcp":
                qs = urllib.parse.parse_qs(u.query)
                try:
                    uid, ctx = resolve_target(qs)
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                except LookupError as e:
                    return self._send(404, {"error": str(e)})
                try:
                    servers = resolve_user_mcp_servers(uid)
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        return self._send(404, {"error": "user not found"})
                    raise
                return self._send(200, {"servers": servers,
                                        "resolved_via": ctx["via"]})

            if u.path == "/limits":
                qs = urllib.parse.parse_qs(u.query)
                try:
                    uid, ctx = resolve_target(qs)
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                except LookupError as e:
                    return self._send(404, {"error": str(e)})
                try:
                    models = resolve_models(uid)
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        return self._send(404, {"error": "user not found"})
                    raise
                ids = {m["id"] for m in models}

                # Passthrough quota endpoints need a real VK — master key is
                # blocked on them. Use the caller's VK when present, else a
                # probe key created and deleted within this request.
                vk_token = ctx.get("vk")
                probe = None
                providers = []
                try:
                    for pid, p in _LIMIT_PROVIDERS.items():
                        avail = sorted(ids & p["models"])
                        if not avail:
                            continue
                        entry = {"id": pid, "label": p["label"],
                                 "models": avail}
                        try:
                            if "local" in p:
                                entry["windows"] = p["local"](p)
                            else:
                                if not vk_token:
                                    probe = probe or _create_probe_key(uid)
                                    vk_token = probe
                                raw = _passthrough_get(p["endpoint"], vk_token)
                                if p.get("endpoint_detail"):
                                    raw = {
                                        "usage_resp": raw,
                                        "detail_resp": _passthrough_get(
                                            p["endpoint_detail"], vk_token),
                                    }
                                entry["windows"] = p["normalize"](raw)
                        except Exception as e:
                            entry["error"] = f"{type(e).__name__}: {e}"
                        providers.append(entry)
                finally:
                    if probe:
                        _delete_key(probe)
                body = {"providers": providers, "resolved_via": ctx["via"]}
                if ctx.get("user_id"):
                    body["user_id"] = ctx["user_id"]
                return self._send(200, body)

            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": str(e)})


if __name__ == "__main__":
    print(f"opencode-setup api listening on {LISTEN[0]}:{LISTEN[1]}", flush=True)
    ThreadingHTTPServer(LISTEN, Handler).serve_forever()