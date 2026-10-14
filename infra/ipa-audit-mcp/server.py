"""IPA Audit MCP — toolkit distributor behind LiteLLM (no server-side exec).

The VM server does NOT run audits. It only:
  - serves the skill bundle (scripts + signature packs + agent prompts)
    via GET /ipa-audit-bundle.zip, gated by a LiteLLM virtual key,
  - exposes reference data (fp_traps, schema, templates) as a tool so
    local agents can fetch it without unpacking,
  - ships the local-audit workflow as an MCP prompt.

The whole audit then runs on the caller's machine — the IPA and all
evidence never leave it.

Registered in LiteLLM mcp_servers as `ipa_audit` with
extra_headers authorization so the caller's VK is forwarded.

Deploy layout (this file's parent dir):
    /opt/ipa-audit-mcp/
      server.py
      .opencode/skills/ipa-audit/{scripts,data}
      .opencode/skills/ipa-audit-review/scripts
      .opencode/agents/{audit_IPA,review_audit_IPA}.md
      tools/signatures/
"""

import hashlib
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Annotated

import httpx
from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

LITELLM = "http://127.0.0.1:4001"
PUBLIC_BASE = "https://hcbifrost.herocraft.com"
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / ".opencode" / "skills" / "ipa-audit" / "data"
MAX_READ_CHARS = 128 * 1024

mcp = FastMCP("IPA Audit MCP", host="0.0.0.0", port=9102,
              streamable_http_path="/mcp")


def _vk(ctx: Context) -> str:
    """Extract caller's LiteLLM VK forwarded by the gateway."""
    req = getattr(ctx.request_context, "request", None)
    headers = getattr(req, "headers", None) or {}
    auth = headers.get("authorization") or headers.get("x-litellm-api-key") or ""
    tok = auth.replace("Bearer ", "").strip() if auth.lower().startswith("bearer") else auth
    if not tok:
        raise ValueError(
            "No LiteLLM virtual key forwarded — call this server through "
            "LiteLLM (/litellm/ipa_audit/mcp) with a Bearer VK.")
    return tok


# ---------- tools ----------

@mcp.tool()
async def ipa_audit_toolkit(ctx: Context) -> dict:
    """Download info for the local-audit skill bundle (.opencode/skills +
    .opencode/agents + tools/signatures). Fetch it with your Bearer VK,
    unzip into the target project root, then run the whole audit locally —
    the IPA and all evidence never leave your machine.
    Follow the 'ipa-audit-local-workflow' prompt."""
    _vk(ctx)
    return {
        "bundle_url": f"{PUBLIC_BASE}/ipa-audit-bundle.zip",
        "auth": "Authorization: Bearer <your LiteLLM VK>",
        "layout": [
            ".opencode/skills/ipa-audit/{scripts,data} — pipeline scripts",
            ".opencode/skills/ipa-audit-review/scripts — V1-V10 validator",
            ".opencode/agents/{audit_IPA,review_audit_IPA}.md",
            "tools/signatures/ — 14 signature packs",
        ],
        "requirements": "Python 3.10+ (optional: pip install lief pyyaml lzfse)",
        "then": "load prompt 'ipa-audit-local-workflow' and run phases via "
                "local shell per SKILL.md",
    }


@mcp.tool()
async def ipa_audit_reference(
    ctx: Context,
    name: Annotated[str, Field(
        description="reference doc: fp_traps | findings_schema | "
                    "pack_defaults | report_template | suppressions")],
) -> dict:
    """Reference data the triage needs: known false-positive traps, the
    findings schema, pack defaults, the report template, suppressions.
    Same content ships inside the bundle — this tool just saves a local
    read when you only need one doc."""
    _vk(ctx)
    files = {
        "fp_traps": DATA_DIR / "fp_traps.json",
        "findings_schema": DATA_DIR / "findings_schema.json",
        "pack_defaults": DATA_DIR / "pack_defaults.json",
        "report_template": DATA_DIR / "report_template.md",
        "suppressions": DATA_DIR / "suppressions.yaml",
    }
    p = files.get(name)
    if not p or not p.is_file():
        raise ValueError(f"name must be one of {sorted(files)}")
    txt = p.read_text(encoding="utf-8", errors="replace")
    return {"name": name, "content": txt[:MAX_READ_CHARS],
            "truncated": len(txt) > MAX_READ_CHARS}


# ---------- prompts ----------

@mcp.prompt("ipa-audit-local-workflow")
def local_workflow() -> str:
    return """Fully-local IPA audit (nothing leaves your machine):

1. ipa_audit_toolkit() → bundle URL.
2. Download: curl -H "Authorization: Bearer <VK>" -o ipa-audit-bundle.zip
   https://hcbifrost.herocraft.com/ipa-audit-bundle.zip
   unzip into the PROJECT ROOT (keeps .opencode/ + tools/ layout).
3. Then work locally per .opencode/skills/ipa-audit/SKILL.md —
   scripts resolve the project root from their own path:
     python .opencode/skills/ipa-audit/scripts/extract_ipa.py --ipa app.ipa --audits-root audits
   Phase gates: extract → plist → frameworks → strings → grep → assets
   → flags → your triage → generate_findings → generate_report →
   check_findings; review via ipa-audit-review/scripts/validate_audit.py.
   Optional: pip install lief pyyaml lzfse.
   During triage use ipa_audit_reference('fp_traps') etc. instead of
   reading the data files.
The audit dir, report and evidence stay under <project>/audits/ locally."""


# ---------- HTTP routes ----------

_BUNDLE_CACHE: dict = {}


def _build_bundle() -> bytes:
    """Zip the vendored toolkit (.opencode/ + tools/) for local audits."""
    items = sorted(p for p in ROOT.rglob("*") if p.is_file()
                   and "venv" not in p.parts and "incoming" not in p.parts
                   and "audits" not in p.parts and "__pycache__" not in p.parts
                   and p.name not in ("server.py", "requirements.txt",
                                      "registry.json", "rate_limits.sqlite3")
                   and not p.name.endswith(".pyc"))
    latest = max((p.stat().st_mtime for p in items), default=0)
    if _BUNDLE_CACHE.get("mtime") == latest:
        return _BUNDLE_CACHE["data"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in items:
            z.writestr(p.relative_to(ROOT).as_posix(), p.read_bytes())
    _BUNDLE_CACHE.update({"mtime": latest, "data": buf.getvalue()})
    return _BUNDLE_CACHE["data"]


@mcp.custom_route("/ipa-audit-bundle.zip", methods=["GET"])
async def ipa_audit_bundle(request: Request):
    """Download the skill bundle for fully-local audits.
    Auth: Bearer LiteLLM VK (validated against the proxy)."""
    auth = request.headers.get("authorization", "")
    tok = auth.replace("Bearer ", "").strip()
    if not tok:
        return JSONResponse({"error": "missing Bearer VK"}, status_code=401)
    async with httpx.AsyncClient(base_url=LITELLM, timeout=20) as c:
        vr = await c.get("/v1/models", headers={"Authorization": f"Bearer {tok}"})
    if vr.status_code != 200:
        return JSONResponse({"error": "invalid VK"}, status_code=401)
    return Response(_build_bundle(), media_type="application/zip",
                    headers={"Content-Disposition":
                             "attachment; filename=ipa-audit-bundle.zip"})


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
