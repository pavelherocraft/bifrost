"""IPA Audit MCP — thin FastMCP wrapper around the ipa-audit script pipeline.

Exposes the 9-phase App Store 2.3.1(a) audit pipeline (extract → plist →
frameworks → strings → pack greps → assets/urls → feature flags → findings →
report → CI gate) plus the independent V1-V10 review validator as MCP tools.

All scripts run server-side and stay deterministic; the calling agent only
does triage judgement (triage.json) and writes the final review narrative.

LiteLLM aborts MCP tool calls after ~60s, while pipeline phases on large
IPAs take minutes — so every heavy tool (start / run / finish / review)
spawns a server-side job, returns {"status": "running"} past the wait
window, and is re-invoked (same call) to collect the result.

Runs on 0.0.0.0:9102 (streamable HTTP at /mcp). Registered in LiteLLM
mcp_servers as `ipa_audit` with extra_headers authorization so the caller's
virtual key is forwarded for auth + per-key daily audit quotas.

Deploy layout (this file's parent dir = project root expected by scripts):
    /opt/ipa-audit-mcp/
      server.py
      .opencode/skills/ipa-audit/{scripts,data}
      .opencode/skills/ipa-audit-review/scripts
      tools/signatures/
      incoming/          # IPA downloads/uploads
      audits/            # audit workspaces
      registry.json
"""

import asyncio
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import httpx
from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

LITELLM = "http://127.0.0.1:4001"
PUBLIC_BASE = "https://hcbifrost.herocraft.com"
ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / ".opencode" / "skills" / "ipa-audit" / "scripts"
REVIEW_SCRIPT = (ROOT / ".opencode" / "skills" / "ipa-audit-review"
                 / "scripts" / "validate_audit.py")
DATA_DIR = SCRIPTS.parent / "data"
SIGNATURES_DIR = ROOT / "tools" / "signatures"
INCOMING = ROOT / "incoming"
AUDITS = ROOT / "audits"
REGISTRY = ROOT / "registry.json"
RATE_DB = ROOT / "rate_limits.sqlite3"
AUDIT_DAILY_LIMIT = int(os.environ.get("MCP_AUDIT_DAILY_LIMIT", "10"))
MAX_READ_LINES = 1000
MAX_READ_CHARS = 128 * 1024
SCRIPT_TIMEOUT = int(os.environ.get("MCP_SCRIPT_TIMEOUT", "1800"))
JOB_WAIT = float(os.environ.get("MCP_JOB_WAIT", "45"))  # < LiteLLM 60s cutoff

mcp = FastMCP("IPA Audit MCP", host="0.0.0.0", port=9102,
              streamable_http_path="/mcp")

PHASE_STEPS = {
    "plist": "parse_info_plist.py",
    "frameworks": "analyze_frameworks.py",
    "strings": "extract_strings.py",
    "grep": "grep_packs.py",
    "assets": "analyze_assets_urls.py",
    "flags": "detect_feature_flags.py",
}
_JOBS: dict = {}  # (audit_id, name) -> asyncio.Task


# ---------- helpers ----------

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


def _check_rate(vk: str, category: str, limit: int) -> int:
    """Per-VK per-day counter (UTC day). Raises when over limit."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    kh = hashlib.sha256(vk.encode()).hexdigest()[:32]
    with sqlite3.connect(RATE_DB) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS counters "
            "(k TEXT, cat TEXT, day TEXT, n INTEGER, PRIMARY KEY(k,cat,day))")
        n = db.execute(
            "INSERT INTO counters VALUES (?,?,?,1) "
            "ON CONFLICT(k,cat,day) DO UPDATE SET n=n+1 RETURNING n",
            (kh, category, day)).fetchone()[0]
        db.commit()
    if n > limit:
        raise ValueError(
            f"Daily {category} limit reached ({limit}/day per key, "
            "resets 00:00 UTC).")
    return limit - n


def _load_registry() -> dict:
    if REGISTRY.is_file():
        try:
            return json.loads(REGISTRY.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_registry(reg: dict) -> None:
    tmp = REGISTRY.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(REGISTRY)


def _audit_entry(audit_id: str) -> dict:
    ent = _load_registry().get(audit_id)
    if not ent:
        raise ValueError(f"Unknown audit_id {audit_id!r} — call ipa_audit_list().")
    return ent


def _audit_dir(audit_id: str) -> Path:
    ent = _audit_entry(audit_id)
    if not ent.get("dir"):
        raise ValueError(
            f"audit {audit_id} still {ent.get('status','starting')} — "
            "poll ipa_audit_status(audit_id)")
    d = AUDITS / ent["dir"]
    if not d.is_dir():
        raise ValueError(f"Audit dir gone for {audit_id}: {ent['dir']}")
    return d


def _safe_join(base: Path, rel: str) -> Path:
    rel = rel.strip().lstrip("/").replace("\\", "/")
    p = (base / rel).resolve()
    if not str(p).startswith(str(base.resolve()) + os.sep):
        raise ValueError("path escapes the audit directory")
    return p


async def _run(script: Path, *args: str, timeout: int = SCRIPT_TIMEOUT) -> tuple:
    """Run a pipeline script; returns (rc, combined_output)."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(script), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(ROOT))
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, f"{script.name} timed out after {timeout}s"
    return proc.returncode, out.decode("utf-8", "replace")


def _tail(text: str, n: int = 8000) -> str:
    return text if len(text) <= n else "…" + text[-n:]


def _evidence_files(audit_dir: Path, limit: int = 300) -> list:
    ev = audit_dir / "evidence"
    if not ev.is_dir():
        return []
    out = []
    for p in sorted(ev.rglob("*")):
        if p.is_file():
            out.append({"path": p.relative_to(audit_dir).as_posix(),
                        "bytes": p.stat().st_size})
            if len(out) >= limit:
                break
    return out


def _file_url(audit_id: str, rel: str) -> str:
    return f"{PUBLIC_BASE}/ipa-files/{audit_id}/{rel}"


def _snapshot_dirs() -> set:
    return {p.name for p in AUDITS.iterdir() if p.is_dir()} if AUDITS.is_dir() else set()


def _update_entry(audit_id: str, **kw) -> None:
    reg = _load_registry()
    if audit_id in reg:
        reg[audit_id].update(kw)
        _save_registry(reg)


def _job_set(audit_id: str, name: str, result: dict) -> None:
    reg = _load_registry()
    if audit_id in reg:
        reg[audit_id].setdefault("jobs", {})[name] = result
        _save_registry(reg)


async def _run_job(audit_id: str, name: str, coro) -> dict:
    """Launch a job, wait up to JOB_WAIT, then let it finish in the
    background. Same call again returns the stored result."""
    key = (audit_id, name)
    ent = _audit_entry(audit_id)
    stored = (ent.get("jobs") or {}).get(name)
    if stored and stored.get("done"):
        return stored

    task = _JOBS.get(key)
    if task is None or task.done():
        async def _wrap():
            try:
                res = await coro
            except Exception as ex:
                res = {"rc": -1, "error": f"{type(ex).__name__}: {ex}"}
            res["done"] = True
            res["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            _job_set(audit_id, name, res)
            return res
        task = asyncio.ensure_future(_wrap())
        _JOBS[key] = task

    try:
        return await asyncio.wait_for(asyncio.shield(task), JOB_WAIT)
    except asyncio.TimeoutError:
        return {"audit_id": audit_id, "job": name, "status": "running",
                "done": False,
                "hint": f"still running — call this tool again in ~30s "
                        f"or poll ipa_audit_status('{audit_id}')"}


# ---------- job bodies ----------

async def _extract_job(audit_id: str, ipa_url, ipa_name,
                       provenance_source: str, provenance_notes: str) -> dict:
    ent = _audit_entry(audit_id)
    try:
        if ipa_url:
            _update_entry(audit_id, status="fetching")
            name = re.sub(r"[^A-Za-z0-9._-]", "_",
                          os.path.basename(ipa_url.split("?")[0]) or "app.ipa")[:80]
            if not name.lower().endswith(".ipa"):
                name += ".ipa"
            dest = INCOMING / f"{uuid.uuid4().hex}-{name}"
            async with httpx.AsyncClient(timeout=1800,
                                         follow_redirects=True) as c:
                async with c.stream("GET", ipa_url) as r:
                    if r.status_code >= 400:
                        raise ValueError(f"download failed: HTTP {r.status_code}")
                    with open(dest, "wb") as f:
                        async for chunk in r.aiter_bytes(1 << 20):
                            f.write(chunk)
            ipa_path = dest
        else:
            ipa_path = _safe_join(INCOMING, ipa_name)
            if not ipa_path.is_file():
                raise ValueError(f"{ipa_name!r} not found in the inbox")
        _update_entry(audit_id, ipa=ipa_path.name)

        _update_entry(audit_id, status="extracting")
        before = _snapshot_dirs()
        rc, out = await _run(
            SCRIPTS / "extract_ipa.py",
            "--ipa", str(ipa_path),
            "--audits-root", str(AUDITS),
            "--provenance-source", provenance_source,
            "--provenance-notes", provenance_notes)
        new = _snapshot_dirs() - before
        if not new:
            _update_entry(audit_id, status="failed")
            return {"rc": rc, "stage": "extract",
                    "error": f"extract_ipa failed:\n{_tail(out)}"}
        dir_name = sorted(new)[0]
        audit_dir = AUDITS / dir_name

        rc2, out2 = await _run(
            SCRIPTS / "normalize_packs.py",
            "--signatures-dir", str(SIGNATURES_DIR))

        sha = ""
        sf = audit_dir / "original" / "SHA256SUMS.txt"
        if sf.is_file():
            sha = sf.read_text(encoding="utf-8",
                               errors="replace").split()[0]

        cryptid_blocked = rc == 3 or bool(
            re.search(r"cryptid[^0-9\n]*[1-9]", out))
        _update_entry(audit_id,
                      dir=dir_name,
                      status="fairplay_blocked" if cryptid_blocked else "ready",
                      sha256=sha,
                      steps={"extract": rc, "normalize_packs": rc2})
        return {
            "audit_id": audit_id, "audit_dir": dir_name, "sha256": sha,
            "extract_rc": rc, "normalize_rc": rc2,
            "fairplay_encrypted": cryptid_blocked,
            "output_tail": _tail(out),
            "next": ("STOP — binary is FairPlay-encrypted (risk R2), report it"
                     if cryptid_blocked else
                     "ipa_audit_run 'plist' → 'frameworks' → 'strings' → 'grep' "
                     "→ 'assets' → 'flags' → triage → ipa_audit_finish"),
        }
    except Exception as ex:
        _update_entry(audit_id, status="failed")
        return {"rc": -1, "stage": "extract",
                "error": f"{type(ex).__name__}: {ex}"}


async def _step_job(audit_id: str, step: str, packs: str, grep_target: str,
                    hits_subdir, declarations) -> dict:
    audit = _audit_dir(audit_id)
    args = ["--audit-dir", str(audit)]
    if step == "grep":
        args += ["--packs", packs, "--target", grep_target,
                 "--signatures-dir", str(SIGNATURES_DIR)]
        if hits_subdir:
            args += ["--hits-subdir", hits_subdir]
    elif step in ("plist", "frameworks", "assets", "flags"):
        args += ["--signatures-dir", str(SIGNATURES_DIR)]
    if step == "flags" and declarations:
        d = _safe_join(INCOMING, declarations)
        if not d.is_dir():
            raise ValueError(f"declarations dir {declarations!r} not in inbox")
        args += ["--declarations", str(d)]

    before = {f["path"] for f in _evidence_files(audit)}
    rc, out = await _run(SCRIPTS / PHASE_STEPS[step], *args)
    new_files = [f for f in _evidence_files(audit) if f["path"] not in before]

    reg = _load_registry()
    if audit_id in reg:
        reg[audit_id].setdefault("steps", {})[step] = rc
        _save_registry(reg)

    return {
        "audit_id": audit_id, "step": step, "rc": rc,
        "output_tail": _tail(out),
        "new_evidence": new_files,
        "triage_ready": any(
            (audit / p).is_file() for p in (
                "evidence/p4_binary/hits/triage_input.json",
                "hits/triage_input.json")),
    }


async def _finish_job(audit_id: str, triage: dict) -> dict:
    audit = _audit_dir(audit_id)
    (audit / "triage.json").write_text(
        json.dumps(triage, indent=2, ensure_ascii=False), encoding="utf-8")

    rc, out = await _run(SCRIPTS / "generate_findings.py",
                         "--audit-dir", str(audit),
                         "--triage", str(audit / "triage.json"))
    if rc != 0:
        return {"rc": rc, "stage": "generate_findings",
                "error": _tail(out),
                "hint": "fix triage.json per the validation errors and retry "
                        "ipa_audit_finish"}

    rc2, out2 = await _run(SCRIPTS / "generate_report.py",
                           "--audit-dir", str(audit))
    rc3, out3 = await _run(SCRIPTS / "check_findings.py",
                           "--audit-dir", str(audit))

    summary = ""
    sf = audit / "summary.txt"
    if sf.is_file():
        summary = sf.read_text(encoding="utf-8", errors="replace")

    reg = _load_registry()
    if audit_id in reg:
        reg[audit_id].setdefault("steps", {}).update(
            {"findings": rc, "report": rc2, "check": rc3})
        _save_registry(reg)

    return {
        "audit_id": audit_id,
        "findings_rc": rc, "report_rc": rc2, "check_rc": rc3,
        "check_output": _tail(out3),
        "summary": summary,
        "artifacts": {
            "report": _file_url(audit_id, "AUDIT_REPORT.md"),
            "summary": _file_url(audit_id, "summary.txt"),
            "findings": _file_url(audit_id, "findings.json"),
        },
        "next": "ipa_audit_review(audit_id) for the independent V1-V10 check",
    }


async def _review_job(audit_id: str) -> dict:
    audit = _audit_dir(audit_id)
    rc, out = await _run(REVIEW_SCRIPT,
                         "--audit-dir", str(audit),
                         "--signatures-dir", str(SIGNATURES_DIR))
    reg = _load_registry()
    if audit_id in reg:
        reg[audit_id].setdefault("steps", {})["review"] = rc
        _save_registry(reg)
    return {
        "audit_id": audit_id, "rc": rc,
        "checks": _tail(out, 24000),
        "validation_report": _file_url(audit_id, "validation_report.json"),
    }


# ---------- tools ----------

@mcp.tool()
async def ipa_audit_start(
    ctx: Context,
    ipa_url: Annotated[Optional[str], Field(
        description="HTTP(S) URL to download the .ipa from (server fetches it).")] = None,
    ipa_name: Annotated[Optional[str], Field(
        description="Filename already present in the server inbox "
                    "(uploaded via POST /ipa-upload or dropped by admin).")] = None,
    provenance_source: Annotated[str, Field(
        description="Where the IPA came from (e.g. 'App Store via ipatool', "
                    "'CI build #123'). Recorded in PROVENANCE.md.")] = "mcp upload",
    provenance_notes: Annotated[str, Field(
        description="Extra provenance notes for PROVENANCE.md.")] = "",
) -> dict:
    """Start an IPA audit: fetch the .ipa, unpack it, record SHA-256 +
    provenance, run Phase 1 (structure) and normalize signature packs.

    Long-running: returns {"status": "running"} past the wait window —
    call again (or ipa_audit_status) until it returns the result with
    audit_id, sha256 and the FairPlay cryptid gate (encrypted → STOP, R2).
    """
    vk = _vk(ctx)
    if not ipa_url and not ipa_name:
        raise ValueError("pass ipa_url (server downloads) or ipa_name "
                         "(file already in the inbox)")

    reg = _load_registry()
    # idempotent re-call for the same IPA: resume/collect the existing job
    pending = ipa_url or ipa_name
    for aid, e in reg.items():
        if e.get("pending_ipa") == pending:
            return await _run_job(
                aid, "start",
                _extract_job(aid, ipa_url, ipa_name,
                             provenance_source, provenance_notes))

    remaining = _check_rate(vk, "audit", AUDIT_DAILY_LIMIT)
    INCOMING.mkdir(parents=True, exist_ok=True)
    audit_id = uuid.uuid4().hex[:24]
    reg[audit_id] = {
        "dir": None,
        "status": "created",
        "pending_ipa": ipa_url or ipa_name,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "vk_hash": hashlib.sha256(vk.encode()).hexdigest()[:12],
        "steps": {},
    }
    _save_registry(reg)

    res = await _run_job(audit_id, "start",
                         _extract_job(audit_id, ipa_url, ipa_name,
                                      provenance_source, provenance_notes))
    res.setdefault("audit_id", audit_id)
    res.setdefault("audits_remaining_today", remaining)
    return res


@mcp.tool()
async def ipa_audit_run(
    ctx: Context,
    audit_id: Annotated[str, Field(description="audit_id from ipa_audit_start")],
    step: Annotated[str, Field(
        description="one of: plist, frameworks, strings, grep, assets, flags")],
    packs: Annotated[str, Field(
        description="grep only: pack selector, default 'all-binary'")] = "all-binary",
    grep_target: Annotated[str, Field(
        description="grep only: strings | dll-strings | js")] = "strings",
    hits_subdir: Annotated[Optional[str], Field(
        description="grep only: sandbox subdir for experimental runs")] = None,
    declarations: Annotated[Optional[str], Field(
        description="flags only: dir name under incoming/ with declaration "
                    "files (app_description.txt, review_notes.txt, ...)")] = None,
    force: Annotated[bool, Field(
        description="re-run the step even if its job already completed")] = False,
) -> dict:
    """Run one analysis phase. Order: plist → frameworks → strings → grep →
    assets → flags. Long phases return {"status": "running"} — re-call the
    same tool to collect. Read evidence via ipa_audit_read windows, never
    bulk-dump hit files."""
    _vk(ctx)
    if step not in PHASE_STEPS:
        raise ValueError(f"step must be one of {sorted(PHASE_STEPS)}")
    _audit_dir(audit_id)
    if force:
        reg = _load_registry()
        if audit_id in reg:
            reg[audit_id].get("jobs", {}).pop(step, None)
            _save_registry(reg)
        _JOBS.pop((audit_id, step), None)
    return await _run_job(
        audit_id, step,
        _step_job(audit_id, step, packs, grep_target, hits_subdir,
                  declarations))


@mcp.tool()
async def ipa_audit_read(
    ctx: Context,
    audit_id: str,
    path: Annotated[str, Field(
        description="audit-dir-relative path, e.g. "
                    "'evidence/p2_plist_entitlements/info.json'")],
    offset: Annotated[int, Field(description="1-based start line")] = 1,
    max_lines: Annotated[int, Field(
        description="cap 1000; evidence files can be 66k lines")] = 200,
) -> dict:
    """Bounded read of a file inside the audit workspace — use ≤200-line
    windows instead of dumping whole hit files."""
    _vk(ctx)
    audit = _audit_dir(audit_id)
    p = _safe_join(audit, path)
    if not p.is_file():
        raise ValueError(f"{path!r} not found in audit dir")
    max_lines = min(max_lines, MAX_READ_LINES)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    chunk = lines[max(0, offset - 1): offset - 1 + max_lines]
    return {
        "path": path, "total_lines": len(lines),
        "bytes": p.stat().st_size,
        "shown": f"{offset}-{offset + len(chunk) - 1}",
        "content": "\n".join(chunk)[:MAX_READ_CHARS],
    }


@mcp.tool()
async def ipa_audit_context(
    ctx: Context,
    audit_id: str,
    pack: Annotated[str, Field(description="signature pack name, e.g. 'payment'")],
    pattern: Annotated[str, Field(description="grep pattern")],
    lines: Annotated[int, Field(description="context lines around hits")] = 20,
) -> dict:
    """Inspect the surroundings of signature-pack hits — the only sanctioned
    way to view hit context. Wraps grep_packs --context."""
    _vk(ctx)
    audit = _audit_dir(audit_id)
    rc, out = await _run(
        SCRIPTS / "grep_packs.py",
        "--audit-dir", str(audit),
        "--signatures-dir", str(SIGNATURES_DIR),
        "--context", "--context-pack", pack,
        "--context-pattern", pattern,
        "--context-lines", str(lines))
    return {"rc": rc, "output": _tail(out, 24000)}


@mcp.tool()
async def ipa_audit_triage_input(ctx: Context, audit_id: str) -> dict:
    """Return hits/triage_input.json — the compact candidate list the agent
    triages into triage.json. Call after the 'grep' step."""
    _vk(ctx)
    audit = _audit_dir(audit_id)
    for rel in ("evidence/p4_binary/hits/triage_input.json",
                "hits/triage_input.json",
                "evidence/hits/triage_input.json"):
        p = audit / rel
        if p.is_file():
            txt = p.read_text(encoding="utf-8", errors="replace")
            return {"path": rel,
                    "content": txt[:MAX_READ_CHARS],
                    "truncated": len(txt) > MAX_READ_CHARS}
    hits = list(audit.rglob("triage_input.json"))
    if hits:
        p = hits[0]
        txt = p.read_text(encoding="utf-8", errors="replace")
        return {"path": p.relative_to(audit).as_posix(),
                "content": txt[:MAX_READ_CHARS],
                "truncated": len(txt) > MAX_READ_CHARS}
    raise ValueError("triage_input.json not found — run step 'grep' first")


@mcp.tool()
async def ipa_audit_finish(
    ctx: Context,
    audit_id: str,
    triage: Annotated[dict, Field(
        description="triage.json object per the ipa-audit contract: headline, "
                    "verdict, findings[], false_positives[], gating_matrix[], "
                    "analytics_inventory[], phases_completed[]")],
) -> dict:
    """Write the agent's triage.json, then generate findings.json +
    AUDIT_REPORT.md + summary.txt and run the CI gate. Long-running —
    re-call to collect. rc=3 at generate_findings = triage failed
    validation: fix and retry."""
    _vk(ctx)
    audit = _audit_dir(audit_id)
    if not isinstance(triage, dict) or "findings" not in triage:
        raise ValueError("triage must be an object with a 'findings' array")
    reg = _load_registry()
    job = reg.get(audit_id, {}).get("jobs", {}).get("finish")
    running = (audit_id, "finish") in _JOBS and not _JOBS[(audit_id, "finish")].done()
    if job and job.get("done") and not running:
        pass  # previous finish completed — fresh triage re-runs below
    elif running:
        return await _run_job(audit_id, "finish",
                              _finish_job(audit_id,
                                          reg[audit_id].get("pending_triage",
                                                            triage)))
    if audit_id in reg:
        reg[audit_id]["pending_triage"] = triage
        reg[audit_id].get("jobs", {}).pop("finish", None)
        _save_registry(reg)
    _JOBS.pop((audit_id, "finish"), None)
    return await _run_job(audit_id, "finish", _finish_job(audit_id, triage))


@mcp.tool()
async def ipa_audit_review(ctx: Context, audit_id: str) -> dict:
    """Run the independent V1-V10 audit validation (schema, count
    reconciliation, verbatim excerpts, provenance, pack completeness,
    report completeness, host re-extraction, gating refs). Long-running —
    re-call to collect. The calling agent writes REVIEW.md itself."""
    _vk(ctx)
    _audit_dir(audit_id)
    return await _run_job(audit_id, "review", _review_job(audit_id))


@mcp.tool()
async def ipa_audit_status(ctx: Context, audit_id: str) -> dict:
    """Audit status: lifecycle state, per-step exit codes, running jobs,
    evidence count, verdict and report URL when done."""
    _vk(ctx)
    ent = _audit_entry(audit_id)
    running = [name for (aid, name), t in _JOBS.items()
               if aid == audit_id and not t.done()]
    verdict = None
    evidence_count = 0
    report_url = None
    if ent.get("dir"):
        audit = AUDITS / ent["dir"]
        if audit.is_dir():
            evidence_count = len(_evidence_files(audit))
            fj = audit / "findings.json"
            if fj.is_file():
                try:
                    verdict = json.loads(
                        fj.read_text(encoding="utf-8")).get("verdict")
                except Exception:
                    pass
            if (audit / "AUDIT_REPORT.md").is_file():
                report_url = _file_url(audit_id, "AUDIT_REPORT.md")
    jobs = {k: {"done": v.get("done", False), "rc": v.get("rc")}
            for k, v in (ent.get("jobs") or {}).items()}
    return {
        "audit_id": audit_id,
        "status": ent.get("status"),
        "dir": ent.get("dir"),
        "created": ent.get("created"),
        "ipa": ent.get("ipa"),
        "sha256": ent.get("sha256"),
        "steps": ent.get("steps", {}),
        "jobs": jobs,
        "running": running,
        "verdict": verdict,
        "evidence_files": evidence_count,
        "report_url": report_url,
    }


@mcp.tool()
async def ipa_audit_list(ctx: Context) -> dict:
    """List all audit workspaces known to this server."""
    _vk(ctx)
    reg = _load_registry()
    return {"audits": [
        {"audit_id": k,
         **{kk: v.get(kk) for kk in ("dir", "created", "ipa", "status")},
         "steps": v.get("steps", {})}
        for k, v in sorted(reg.items(),
                           key=lambda kv: kv[1].get("created", ""),
                           reverse=True)]}


@mcp.tool()
async def ipa_audit_reference(
    ctx: Context,
    name: Annotated[str, Field(
        description="reference doc: fp_traps | findings_schema | "
                    "pack_defaults | report_template | suppressions")],
) -> dict:
    """Reference data the triage needs: known false-positive traps, the
    findings schema, pack defaults, the report template, suppressions."""
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


@mcp.tool()
async def ipa_audit_toolkit(ctx: Context) -> dict:
    """Local-audit mode: returns the download info for the skill bundle
    (.opencode/skills + tools/signatures + agent .md files). Download it
    with your Bearer VK, unzip into the target project root, then run the
    whole audit locally — the IPA and all evidence never leave your
    machine. Follow the 'ipa-audit-local-workflow' prompt."""
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


# ---------- prompts ----------

@mcp.prompt("ipa-audit-workflow")
def audit_workflow() -> str:
    return """You are the IPA Audit Executor driving the ipa_audit_* MCP tools.

Tools return {"status": "running"} past the gateway's ~60s window — just
call the SAME tool again (or ipa_audit_status) until the result arrives.

Phase gates — do not skip, do not reorder:
1. ipa_audit_start(ipa_url | ipa_name, provenance_source) → audit_id.
   GATE: fairplay_encrypted → STOP, report risk R2 (FairPlay).
2. ipa_audit_run(audit_id, 'plist') → p2 (entitlements, profile,
   privacy manifests, url schemes).
3. ipa_audit_run(audit_id, 'frameworks') → p3.
4. ipa_audit_run(audit_id, 'strings') → p4 strings (Mach-O + IL2CPP auto).
5. ipa_audit_run(audit_id, 'grep') → hits + triage_input.json.
6. ipa_audit_run(audit_id, 'assets') → p5/p6 (assets, l10n, URLs).
7. ipa_audit_run(audit_id, 'flags') → p7 (date/test-account/AB gating).
8. TRIAGE — your judgement, the core value-add:
   - ipa_audit_triage_input(audit_id) — compact candidate list.
   - NEVER bulk-read hits; use ipa_audit_context(audit_id, pack, pattern,
     lines) or ipa_audit_read windows ≤200 lines.
   - ipa_audit_reference('fp_traps') — known traps (mobilepay⊂Square,
     Tink≠TinkLink, sepa⊂separate, QN*⊂System.Xml, vendored iProov, ...).
   - Always escalate: nordic hits, date/test-account/AB gating, appex,
     associated-domains, '# lead' patterns.
9. ipa_audit_finish(audit_id, triage_json) → findings + AUDIT_REPORT.md +
   summary.txt + CI gate. rc≠0 at generate_findings: fix triage, retry.
10. ipa_audit_review(audit_id) → V1-V10 machine checks; then state your
    REVIEW verdict (PASS/NEEDS_REVISION/FAIL).
Final answer: verdict, counts, top-3 findings, report URL."""


@mcp.prompt("ipa-audit-review-workflow")
def review_workflow() -> str:
    return """You are the IPA Audit Reviewer for an existing audit_id.
1. ipa_audit_status(audit_id) — confirm phases + artifacts exist.
2. ipa_audit_review(audit_id) — machine checks V1-V10 (long-running:
   re-call to collect). Every FAIL is at least a 'concern';
   V2/V3/V4/V6 failures are blockers.
3. Spot-check with ipa_audit_read / ipa_audit_context — verify 3-5
   evidence excerpts yourself, do not trust the report's wording.
4. Deliver REVIEW: verdict PASS | NEEDS_REVISION | FAIL, concerns and
   blockers with evidence paths, required actions.
   Rework path: ipa_audit_run(..., force=True) for affected steps only,
   then ipa_audit_finish again."""


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
The audit dir, report and evidence stay under <project>/audits/ locally."""


# ---------- HTTP routes ----------

@mcp.custom_route("/ipa-upload", methods=["POST"])
async def ipa_upload(request: Request):
    """Upload an .ipa body straight into the inbox (bypasses MCP payload
    limits). Auth: Bearer LiteLLM VK. Returns ipa_name for ipa_audit_start."""
    auth = request.headers.get("authorization", "")
    tok = auth.replace("Bearer ", "").strip()
    if not tok:
        return JSONResponse({"error": "missing Bearer VK"}, status_code=401)
    async with httpx.AsyncClient(base_url=LITELLM, timeout=20) as c:
        vr = await c.get("/v1/models", headers={"Authorization": f"Bearer {tok}"})
    if vr.status_code != 200:
        return JSONResponse({"error": "invalid VK"}, status_code=401)
    INCOMING.mkdir(parents=True, exist_ok=True)
    fname = request.query_params.get("name", "app.ipa")
    fname = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(fname))[:80]
    if not fname.lower().endswith(".ipa"):
        fname += ".ipa"
    dest = INCOMING / f"{uuid.uuid4().hex}-{fname}"
    size = 0
    with open(dest, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
            size += len(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        return JSONResponse({"error": "empty body"}, status_code=400)
    return JSONResponse({"ipa_name": dest.name, "bytes": size})


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
    from starlette.responses import Response
    return Response(_build_bundle(), media_type="application/zip",
                    headers={"Content-Disposition":
                             "attachment; filename=ipa-audit-bundle.zip"})


@mcp.custom_route("/ipa-files/{audit_id}/{path:path}", methods=["GET"])
async def ipa_file(request: Request):
    """Serve audit artifacts (report, findings, evidence) by unguessable
    audit_id — same security model as /media-files."""
    audit_id = request.path_params["audit_id"]
    rel = request.path_params["path"]
    if not re.fullmatch(r"[0-9a-f]{24}", audit_id):
        return JSONResponse({"error": "bad audit_id"}, status_code=404)
    try:
        audit = _audit_dir(audit_id)
        p = _safe_join(audit, rel)
    except ValueError:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(str(p))


if __name__ == "__main__":
    INCOMING.mkdir(parents=True, exist_ok=True)
    AUDITS.mkdir(parents=True, exist_ok=True)
    mcp.run(transport="streamable-http")
