"""FastAPI router for media spend logging (/admin/media-spend).

Mounted by litellm_entrypoint.sh patch (marker _MEDIA_SPEND_ROUTER_PATCH_):
    from admin.media_spend_route import router as _media_spend_router
    app.include_router(_media_spend_router)

POST /admin/media-spend   Bearer <VK>
    {"model": "gemini/omni-1.1-flash", "task_id": "v1_...",
     "prompt_tokens": n, "completion_tokens": n, "total_tokens": n,
     "spend": 0.341, "resolution": "720p", "video_seconds": 3.3}

Media generation (MCP media server) bypasses LiteLLM token billing —
this endpoint lets media-mcp record real provider spend into
LiteLLM_SpendLogs so it shows up in /spend reports and dashboards.

Auth: the caller's own LiteLLM virtual key. The bearer token is hashed
(sha256, same as LiteLLM key storage) and looked up in
LiteLLM_VerificationToken — spend is attributed to that key's
user/team. A caller can only inflate their OWN spend.
"""
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
import psycopg2
import psycopg2.extras

router = APIRouter(tags=["media-spend"])


def _json(data, status_code: int = 200):
    return Response(
        content=json.dumps(data, ensure_ascii=False, default=str),
        status_code=status_code,
        media_type="application/json")


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


@router.post("/admin/media-spend")
async def media_spend(request: Request):
    auth = request.headers.get("authorization", "")
    tok = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not tok:
        raise HTTPException(status_code=401, detail="Bearer VK required")

    try:
        body = json.loads(await request.body())
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    model = body.get("model")
    spend = body.get("spend")
    if not model or spend is None:
        raise HTTPException(status_code=400,
                            detail="model and spend are required")

    kh = hashlib.sha256(tok.encode()).hexdigest()
    conn = _conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            'SELECT user_id, team_id, key_alias FROM '
            '"LiteLLM_VerificationToken" WHERE token = %s', (kh,))
        key_row = cur.fetchone()
        if not key_row:
            raise HTTPException(status_code=401, detail="unknown VK")

        rid = f"media-{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        meta = {
            "source": "media-mcp",
            "media_task_id": body.get("task_id"),
            "resolution": body.get("resolution"),
            "video_seconds": body.get("video_seconds"),
            "usage": body.get("usage"),
        }
        cur.execute(
            'INSERT INTO "LiteLLM_SpendLogs" '
            '("request_id","call_type","api_key","spend","total_tokens",'
            '"prompt_tokens","completion_tokens","startTime","endTime",'
            '"model","model_id","model_group","custom_llm_provider",'
            '"api_base","user","team_id","status","metadata","cache_hit") '
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (rid, "media_generation", kh, float(spend),
             int(body.get("total_tokens") or 0),
             int(body.get("prompt_tokens") or 0),
             int(body.get("completion_tokens") or 0),
             now, now,
             model, body.get("model_id") or model,
             body.get("model_group") or model,
             body.get("custom_llm_provider") or "media-mcp",
             body.get("api_base") or "https://generativelanguage.googleapis.com",
             key_row.get("user_id"), key_row.get("team_id"),
             "success",
             psycopg2.extras.Json({k: v for k, v in meta.items()
                                   if v is not None}),
             "false"))
        conn.commit()
    finally:
        conn.close()
    return _json({"ok": True, "request_id": rid})
