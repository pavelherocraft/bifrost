"""FastAPI router for selective payload logging UI/API.

Mounted by litellm_entrypoint.sh patch (marker _PAYLOAD_LOGS_ROUTER_PATCH_):
    from admin.payload_logs_route import router as _payload_logs_router
    app.include_router(_payload_logs_router)

Endpoints (all require admin auth via Depends(user_api_key_auth)):
    GET    /admin/payload-logs/api/allowlist           -> allowlist rows
    POST   /admin/payload-logs/api/allowlist           -> {user_id, note?} add
    POST   /admin/payload-logs/api/allowlist/toggle    -> {user_id, enabled}
    DELETE /admin/payload-logs/api/allowlist/{user_id} -> remove
    GET    /admin/payload-logs/api/logs                -> ?user_id=&hours=&limit=&offset=
    GET    /admin/payload-logs/api/stats               -> counters
    GET    /admin/payload-logs/                        -> static HTML page (UI)
"""
import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
import psycopg2
import psycopg2.extras

router = APIRouter(tags=["payload-logs"])

_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "payload_logs.html")


def _json(data, status_code: int = 200):
    """JSON response safe for datetime/Decimal/UUID values."""
    return Response(
        content=json.dumps(data, ensure_ascii=False, default=str),
        status_code=status_code,
        media_type="application/json",
    )


def _user_api_key_auth():
    """Lazy import to avoid loading litellm at module import time."""
    from litellm.proxy.proxy_server import user_api_key_auth
    return user_api_key_auth


def _read_database_url() -> Optional[str]:
    env_path = os.environ.get("PAYLOAD_LOG_ENV", "/app/.env")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


def _conn():
    url = _read_database_url()
    if not url:
        raise HTTPException(status_code=500, detail="DATABASE_URL not configured")
    try:
        return psycopg2.connect(url, connect_timeout=5)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB connect failed: {e}")


def _query(sql: str, params: tuple = (), fetch: str = "all"):
    conn = _conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        if cur.description is not None:
            rows = cur.fetchall() if fetch == "all" else cur.fetchone()
        else:
            rows = None  # INSERT/UPDATE/DELETE without RETURNING
        conn.commit()
        cur.close()
        return rows
    except HTTPException:
        raise
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"query failed: {e}")
    finally:
        conn.close()


class AllowlistAdd(BaseModel):
    user_id: str
    note: Optional[str] = None


class AllowlistToggle(BaseModel):
    user_id: str
    enabled: bool


def _user_emails(uids):
    """Map user_id -> email from LiteLLM_UserTable (empty string if unknown)."""
    uids = [u for u in set(uids) if u]
    if not uids:
        return {}
    try:
        rows = _query(
            'SELECT user_id, user_email FROM "LiteLLM_UserTable" '
            "WHERE user_id = ANY(%s)",
            (uids,),
        )
    except HTTPException:
        return {}
    return {r["user_id"]: (r.get("user_email") or "") for r in rows}


@router.get("/admin/payload-logs/api/allowlist",
            dependencies=[Depends(_user_api_key_auth())])
def get_allowlist():
    rows = _query(
        'SELECT user_id, enabled, note, created_at, updated_at '
        'FROM "PayloadLogAllowlist" ORDER BY created_at DESC'
    )
    emails = _user_emails([r["user_id"] for r in rows])
    out = []
    for r in rows:
        d = dict(r)
        d["email"] = emails.get(r["user_id"], "")
        out.append(d)
    return _json(out)


@router.post("/admin/payload-logs/api/allowlist",
             dependencies=[Depends(_user_api_key_auth())])
def add_allowlist(req: AllowlistAdd):
    uid = req.user_id.strip()
    if not uid:
        raise HTTPException(status_code=400, detail="user_id is required")
    _query(
        'INSERT INTO "PayloadLogAllowlist" (user_id, note) VALUES (%s, %s) '
        'ON CONFLICT (user_id) DO UPDATE SET note = EXCLUDED.note, '
        'enabled = true, updated_at = NOW()',
        (uid, req.note),
    )
    return _json({"status": "ok", "user_id": uid, "enabled": True})


@router.post("/admin/payload-logs/api/allowlist/toggle",
             dependencies=[Depends(_user_api_key_auth())])
def toggle_allowlist(req: AllowlistToggle):
    rows = _query(
        'UPDATE "PayloadLogAllowlist" SET enabled = %s, updated_at = NOW() '
        'WHERE user_id = %s RETURNING user_id, enabled',
        (req.enabled, req.user_id.strip()),
        fetch="one",
    )
    if not rows:
        raise HTTPException(status_code=404, detail="user_id not in allowlist")
    return _json(dict(rows))


@router.delete("/admin/payload-logs/api/allowlist/{user_id}",
               dependencies=[Depends(_user_api_key_auth())])
def delete_allowlist(user_id: str):
    _query('DELETE FROM "PayloadLogAllowlist" WHERE user_id = %s', (user_id,))
    return _json({"status": "deleted", "user_id": user_id})


@router.get("/admin/payload-logs/api/logs",
            dependencies=[Depends(_user_api_key_auth())])
def get_logs(user_id: Optional[str] = None, hours: int = 24,
             limit: int = 50, offset: int = 0):
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    hours = max(1, min(hours, 24 * 30))
    where = ['created_at > NOW() - (%s || \' hours\')::interval']
    params = [hours]
    if user_id:
        where.append("user_id = %s")
        params.append(user_id)
    rows = _query(
        'SELECT id, request_id, user_id, key_alias, key_hash, model, '
        'user_messages, start_time, latency_ms, created_at '
        'FROM "UserRequestLogs" WHERE ' + " AND ".join(where) +
        " ORDER BY created_at DESC LIMIT %s OFFSET %s",
        tuple(params) + (limit, offset),
    )
    out = []
    emails = _user_emails({r["user_id"] for r in rows})
    for r in rows:
        d = dict(r)
        d["created_at"] = d["created_at"].isoformat() if d.get("created_at") else None
        d["start_time"] = d["start_time"].isoformat() if d.get("start_time") else None
        d["email"] = emails.get(d.get("user_id"), "")
        out.append(d)
    return _json(out)


@router.get("/admin/payload-logs/api/stats",
            dependencies=[Depends(_user_api_key_auth())])
def get_stats():
    stats = _query(
        'SELECT count(*) AS total, '
        'count(*) FILTER (WHERE created_at > NOW() - interval \'24 hours\') AS last24h, '
        'count(DISTINCT user_id) AS distinct_users '
        'FROM "UserRequestLogs"',
        fetch="one",
    )
    known = _query(
        'SELECT DISTINCT user_id FROM "UserRequestLogs" '
        'ORDER BY user_id LIMIT 200'
    )
    emails = _user_emails([r["user_id"] for r in known])
    return _json({
        "stats": dict(stats) if stats else {},
        "known_users": [
            {"user_id": r["user_id"], "email": emails.get(r["user_id"], "")}
            for r in known
        ],
    })


@router.get("/admin/payload-logs/api/users",
            dependencies=[Depends(_user_api_key_auth())])
def list_users(q: Optional[str] = None, limit: int = 500):
    """Search LiteLLM users (for the add-by-email helper)."""
    limit = max(1, min(limit, 1000))
    params = []
    sql = ('SELECT user_id, user_email, user_alias FROM "LiteLLM_UserTable"')
    if q:
        like = f"%{q}%"
        sql += (" WHERE user_email ILIKE %s OR user_id ILIKE %s "
                "OR user_alias ILIKE %s")
        params += [like, like, like]
    sql += " ORDER BY user_email NULLS LAST, user_id LIMIT %s"
    params.append(limit)
    rows = _query(sql, tuple(params))
    return _json([dict(r) for r in rows])


@router.get("/admin/payload-logs/", include_in_schema=False)
@router.get("/admin/payload-logs", include_in_schema=False)
def get_ui():
    if not os.path.isfile(_HTML_PATH):
        raise HTTPException(status_code=500, detail="payload_logs.html missing on server")
    return FileResponse(_HTML_PATH, media_type="text/html")
