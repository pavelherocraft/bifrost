"""FastAPI router for GLM credentials swap UI/API.

Mounted by litellm_entrypoint.sh patch:
    from admin.glm_swap_route import router as glm_swap_router
    app.include_router(glm_swap_router)

Endpoints (all require admin auth via Depends(user_api_key_auth)):
    GET  /admin/glm/models    -> list of all 20 GLM rows (no secrets)
    GET  /admin/glm/pairs     -> list of 10 (active, shadow) pairs
    POST /admin/glm/swap      -> {from, to, swap_names, copy_creds, dry_run} -> result
    GET  /admin/glm-swap/     -> static HTML page (UI)
"""
import os
import sys
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

# Make the swap_glm_credentials module importable when this file is loaded by proxy_server
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from swap_glm_credentials import (  # noqa: E402
    list_models, list_pairs, swap,
    create_dont_use_models, copy_credentials, swap_names,
    PAIRS, QUICK_SWAPS,
)

router = APIRouter(tags=["glm-swap"])

_HTML_PATH = os.path.join(_HERE, "glm_swap.html")


class SwapRequest(BaseModel):
    from_: str = Field(alias="from")
    to: str
    swap_names: bool = False
    copy_creds: bool = True
    dry_run: bool = False

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "from": "atlas_glm-5.2_dont_use",
                "to": "GLM-5.2",
                "swap_names": False,
                "copy_creds": True,
                "dry_run": True,
            }
        }


def _user_api_key_auth():
    """Lazy import to avoid loading litellm at module import time."""
    from litellm.proxy.proxy_server import user_api_key_auth
    return user_api_key_auth


@router.get("/admin/glm/models", dependencies=[Depends(_user_api_key_auth())])
def get_models():
    return JSONResponse(list_models())


@router.get("/admin/glm/pairs", dependencies=[Depends(_user_api_key_auth())])
def get_pairs():
    return JSONResponse(list_pairs())


@router.get("/admin/glm/quick-swaps", dependencies=[Depends(_user_api_key_auth())])
def get_quick_swaps():
    """12 one-click promote pairings (shadow -> active) for the UI buttons."""
    return JSONResponse(QUICK_SWAPS)


@router.post("/admin/glm/swap", dependencies=[Depends(_user_api_key_auth())])
def post_swap(req: SwapRequest):
    try:
        result = swap(
            req.from_, req.to,
            copy_creds=req.copy_creds,
            swap_names_=req.swap_names,
            dry_run=req.dry_run,
        )
        # Touch-cache is disabled since 2026-07-03 (caused double-encryption
        # corruption). The proxy's in-memory model cache still references the
        # old names until restarted.
        if not req.dry_run:
            result = dict(result)
            result["restart_required"] = True
            result["restart_cmd"] = "docker restart litellm"
            result["warning"] = (
                "litellm cache not auto-refreshed since v1.2; "
                "run `docker restart litellm` to apply the swap in-proxy. "
                "Without restart, /v1/models will still serve OLD model_name -> "
                "api_key mapping until container bounces."
            )
        return JSONResponse(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"swap failed: {e}")


@router.get("/admin/glm-swap/", include_in_schema=False)
@router.get("/admin/glm-swap", include_in_schema=False)
def get_ui():
    if not os.path.isfile(_HTML_PATH):
        raise HTTPException(status_code=500, detail="glm_swap.html missing on server")
    return FileResponse(_HTML_PATH, media_type="text/html")


@router.post("/admin/restart-proxy", dependencies=[Depends(_user_api_key_auth())])
def restart_proxy():
    """Restart the litellm container by sending SIGTERM to PID 1.

    Docker's `unless-stopped` restart policy will automatically restart
    the container. Response is sent before the signal fires.

    The signal is sent from a detached subprocess (start_new_session=True)
    so it survives the main process death.
    """
    import signal
    import subprocess
    import threading
    import time

    def _kill_after_delay(delay: float = 1.5):
        time.sleep(delay)
        # SIGTERM PID 1 (the litellm process via entrypoint exec).
        # Docker restart policy=unless-stopped will relaunch the container.
        try:
            os.kill(1, signal.SIGTERM)
        except ProcessLookupError:
            pass  # already gone
        except PermissionError:
            # Fallback: try via /proc/1/... or sudo kill
            try:
                subprocess.run(["kill", "-TERM", "1"], check=False)
            except Exception:
                pass

    t = threading.Thread(target=_kill_after_delay, daemon=True)
    t.start()

    return JSONResponse({
        "status": "restarting",
        "message": "SIGTERM sent to PID 1; Docker will restart the container",
        "estimated_back_in": "30s",
        "note": "UI will be unavailable for ~30 seconds",
    })