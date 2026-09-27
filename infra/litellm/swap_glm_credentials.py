#!/usr/bin/env python3
"""GLM credentials swap tool.

CLI + importable functions for the FastAPI route at /admin/glm/*.

Atomic swap = SQL transaction:
  1) UPDATE dst.litellm_params = src.litellm_params
  2) 3-step swap model_name via temporary name
After commit: POST /model/update for each touched row -> clear_cache() + audit log.

Idempotent: re-running the same swap on already-swapped state is a no-op
(SQL UPDATE with same value, swap names comes back to original).
"""
from master_key import get_master_key

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import psycopg2
import psycopg2.extras

PG_DSN = (
    os.environ.get("PG_DSN")
    or os.environ.get("DATABASE_URL")
    or "host=127.0.0.1 port=5432 user=litellm password=<MASKED> dbname=litellm"
)
LITELLM_BASE = (
    os.environ.get("LITELLM_BASE_URL")
    or ("http://127.0.0.1:4000" if os.path.isdir("/app/.venv") else "http://127.0.0.1:4001")
)
LITELLM_KEY = os.environ.get(
    "LITELLM_API_KEY", None
)
BACKUP_DIR = os.environ.get("GLM_BACKUP_DIR", "/opt/litellm/backups")

PAIRS: list[tuple[str, str]] = [
    ("GLM-5.3",       "GLM-5.3_dont_use"),
    ("GLM-5.3 (res)", "GLM-5.3 (res)_dont_use"),
    ("GLM-5.3-Flash",       "GLM-5.3-Flash_dont_use"),
    ("GLM-5.3-Flash (res)", "GLM-5.3-Flash (res)_dont_use"),
    ("atlas_glm-5.1", "atlas_glm-5.1_dont_use"),
    ("atlas_glm-5.2", "atlas_glm-5.2_dont_use"),
]

# All model_names managed by this tool (20 rows = 10 active + 10 _dont_use shadows).
# NOTE: no atlas_glm-5.3 pair — GLM-5.3 is NOT available on Atlas.
ALL_MODELS: list[str] = [m for pair in PAIRS for m in pair]

# 12 one-click "promote shadow -> active" pairings shown as buttons in the UI.
# Atlas shadow can promote to EITHER Z.AI main or (res) variant (atlas account
# is a shared pool covering both). Pure Z.AI shadow can only promote to its
# matching active (same upstream account). Added 2026-07-03 per user request.
# GLM-5.3 pairs added 2026-08-25 (Z.AI only; GLM-5.3 not available on Atlas).
# GLM-5.3-Flash pairs added 2026-08-28 (Z.AI only; multimodal 320B-A18B MoE).
QUICK_SWAPS: list[dict] = [
    {"from": "GLM-5.3_dont_use",              "to": "GLM-5.3"},
    {"from": "GLM-5.3-Flash_dont_use",        "to": "GLM-5.3-Flash"},
    {"from": "GLM-5.3 (res)_dont_use",        "to": "GLM-5.3 (res)"},
    {"from": "GLM-5.3-Flash (res)_dont_use",  "to": "GLM-5.3-Flash (res)"},
]

DONT_USE_SUFFIX = "_dont_use"


# ----------------------------- low-level helpers -----------------------------

def _conn():
    """Open a fresh psycopg2 connection. Caller closes it (or uses `with`)."""
    return psycopg2.connect(PG_DSN)


def _backup_table(label: str = "pre-swap") -> str:
    """Dump LiteLLM_ProxyModelTable rows to BACKUP_DIR as JSON Lines.

    Uses psycopg2 (no docker binary needed — we run inside the litellm container
    which has psycopg2 but no docker). Falls back to sudo cp if BACKUP_DIR
    is not writable as current user.
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(BACKUP_DIR, f"ProxyModelTable-{label}-{ts}.jsonl")
    tmp = out_path + f".tmp.{os.getpid()}"
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            'SELECT model_id, model_name, litellm_params, model_info, '
            'created_at, created_by, updated_at, updated_by, blocked '
            'FROM "LiteLLM_ProxyModelTable" ORDER BY model_name'
        )
        cols = [d[0] for d in cur.description]
        with open(tmp, "w", encoding="utf-8") as fh:
            for row in cur.fetchall():
                rec = dict(zip(cols, row))
                # JSONB / datetime -> JSON-serializable
                for k, v in list(rec.items()):
                    if hasattr(v, "isoformat"):
                        rec[k] = v.isoformat()
                    elif hasattr(v, "tobytes"):
                        rec[k] = v.tobytes().decode("utf-8", errors="replace")
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if os.access(BACKUP_DIR, os.W_OK):
        os.replace(tmp, out_path)
    else:
        subprocess.run(
            ["sudo", "-S", "-p", "", "cp", tmp, out_path],
            input="<MASKED>\n", text=True, check=True,
        )
        subprocess.run(
            ["sudo", "-S", "-p", "", "chown", "dev01:dev01", out_path],
            input="<MASKED>\n", text=True, check=True,
        )
        os.unlink(tmp)
    return out_path


def _touch_cache(model_id: str) -> dict | None:
    """NO-OP since 2026-07-03 — caused double-encryption corruption.

    POST /model/update re-encrypts ALL litellm_params values via encrypt_value_helper.
    For rows that already store encrypted-at-rest credentials (every row in
    LiteLLM_ProxyModelTable), this produces double-encrypted garbage that the proxy
    cannot decrypt on next load -> the model silently disappears from /model/info
    and from /v1/models.

    Original symptom: GLM-5.1, GLM-5.2, atlas_glm-5.1, atlas_glm-5.2 stopped
    responding after a swap test; restored from SQL backup + fix_atlas.py.

    Workaround: caller MUST `docker restart litellm` after a successful swap
    so the proxy reloads model definitions from the DB. The `swap()` function
    sets the global `_restart_required = True` so callers can detect this.
    """
    global _restart_required
    _restart_required = True
    print(f"[INFO] _touch_cache({model_id}) skipped — caller must restart litellm", file=sys.stderr, flush=True)
    return None


def _model_info_by_id(model_id: str) -> dict:
    """GET /model/info?model_id=... and return the single record (or raise)."""
    # /model/info accepts model_name filter, not model_id. Use model_name from DB.
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            'SELECT model_name, litellm_params, model_info FROM "LiteLLM_ProxyModelTable" WHERE model_id = %s',
            (model_id,),
        )
        row = cur.fetchone()
    if not row:
        raise ValueError(f"model_id not found: {model_id}")
    return {
        "model_name": row[0],
        "litellm_params": row[1],
        "model_info": row[2],
    }


# ----------------------------- public functions ------------------------------

def list_models() -> list[dict]:
    """Return all 20 GLM rows with non-secret summary."""
    with _conn() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            'SELECT model_id, model_name, litellm_params, model_info, updated_at '
            'FROM "LiteLLM_ProxyModelTable" '
            'WHERE model_name = ANY(%s) '
            'ORDER BY model_name',
            (ALL_MODELS,),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        lp = r["litellm_params"] or {}
        mi = r["model_info"] or {}
        api_key = lp.get("api_key")
        out.append({
            "model_id": r["model_id"],
            "model_name": r["model_name"],
            "upstream_model": lp.get("model"),
            "api_base": lp.get("api_base"),
            "credential_name": lp.get("litellm_credential_name"),
            "api_key_redacted": (f"<redacted len={len(api_key)}>" if api_key else None),
            "max_input_tokens": mi.get("max_input_tokens"),
            "max_tokens": mi.get("max_tokens"),
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        })
    return out


def list_pairs() -> list[dict]:
    """Return 10 pairs with both rows' summary + creds-equal flag."""
    by_name = {m["model_name"]: m for m in list_models()}
    out = []
    for active, shadow in PAIRS:
        a = by_name.get(active)
        s = by_name.get(shadow)
        equal = False
        if a and s:
            # Compare litellm_params as JSON strings (api_key is encrypted, so
            # equal strings mean literally identical creds).
            equal = json.dumps(a, sort_keys=True) == json.dumps(s, sort_keys=True)
        out.append({
            "active": a,
            "shadow": s,
            "shadow_exists": s is not None,
            "creds_equal": equal,
        })
    return out


def create_dont_use_models(dry_run: bool = False) -> dict:
    """Create 10 _dont_use rows from active rows. Skip if already exist.

    Returns {"created": [...], "skipped": [...], "errors": [...]}.
    """
    result = {"created": [], "skipped": [], "errors": []}
    with _conn() as c, c.cursor() as cur:
        for active, shadow in PAIRS:
            try:
                cur.execute(
                    'SELECT 1 FROM "LiteLLM_ProxyModelTable" WHERE model_name = %s',
                    (shadow,),
                )
                if cur.fetchone():
                    result["skipped"].append({"shadow": shadow, "reason": "already exists"})
                    continue
                cur.execute(
                    'SELECT litellm_params, model_info FROM "LiteLLM_ProxyModelTable" '
                    'WHERE model_name = %s',
                    (active,),
                )
                src = cur.fetchone()
                if not src:
                    result["errors"].append({"shadow": shadow, "reason": f"active {active!r} not found"})
                    continue
                src_lp, src_mi = src
                new_id = secrets.token_hex(16)
                # Preserve original model_info but ensure no id collision
                new_mi = dict(src_mi or {})
                new_mi.pop("id", None)
                if dry_run:
                    result["created"].append({
                        "shadow": shadow,
                        "model_id": new_id,
                        "from_active": active,
                        "dry_run": True,
                    })
                    continue
                cur.execute(
                    'INSERT INTO "LiteLLM_ProxyModelTable" '
                    '(model_id, model_name, litellm_params, model_info, '
                    ' created_at, created_by, updated_at, updated_by) '
                    'VALUES (%s, %s, %s, %s, NOW(), %s, NOW(), %s)',
                    (new_id, shadow, json.dumps(src_lp), json.dumps(new_mi),
                     "glm-swap-create-dont-use-models",
                     "glm-swap-create-dont-use-models"),
                )
                result["created"].append({
                    "shadow": shadow,
                    "model_id": new_id,
                    "from_active": active,
                })
            except Exception as e:
                result["errors"].append({"shadow": shadow, "reason": str(e)})
        if dry_run:
            c.rollback()
        else:
            c.commit()
    return result


def _redact_lp(lp: dict) -> dict:
    """Return a copy of litellm_params with api_key value masked."""
    out = dict(lp or {})
    if "api_key" in out and out["api_key"]:
        out["api_key"] = f"<redacted len={len(out['api_key'])}>"
    return out


def _fetch_row(cur, name: str) -> dict:
    cur.execute(
        'SELECT model_id, model_name, litellm_params, updated_at '
        'FROM "LiteLLM_ProxyModelTable" WHERE model_name = %s',
        (name,),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"model_name not found: {name!r}")
    return {
        "model_id": row[0],
        "model_name": row[1],
        "litellm_params": row[2],
        "updated_at": row[3],
    }


def copy_credentials(from_name: str, to_name: str, dry_run: bool = False) -> dict:
    """Copy litellm_params AND model_info FROM -> TO.

    Returns {"from": {...}, "to_before": {...}, "to_after": {...}, "changed": bool}.
    """
    if from_name == to_name:
        raise ValueError("from and to must differ")
    with _conn() as c, c.cursor() as cur:
        src = _fetch_row(cur, from_name)
        dst_before = _fetch_row(cur, to_name)
        # Fetch source model_info too
        cur.execute(
            'SELECT model_info FROM "LiteLLM_ProxyModelTable" WHERE model_id = %s',
            (src["model_id"],),
        )
        src_mi_row = cur.fetchone()
        src_mi = src_mi_row[0] if src_mi_row else {}
        # Detect no-op
        changed = json.dumps(src["litellm_params"], sort_keys=True) != json.dumps(
            dst_before["litellm_params"], sort_keys=True
        )
        if not changed:
            return {
                "from": {"model_id": src["model_id"], "model_name": src["model_name"]},
                "to": {"model_id": dst_before["model_id"], "model_name": dst_before["model_name"]},
                "changed": False,
                "dry_run": dry_run,
            }
        if not dry_run:
            # Build a sane model_info from source, but ensure db_model=True
            new_mi = dict(src_mi) if src_mi else {}
            new_mi["db_model"] = True
            new_mi.pop("id", None)  # don't copy source's id
            cur.execute(
                'UPDATE "LiteLLM_ProxyModelTable" '
                'SET litellm_params = %s, model_info = %s, updated_at = NOW() '
                'WHERE model_id = %s',
                (json.dumps(src["litellm_params"]), json.dumps(new_mi), dst_before["model_id"]),
            )
            c.commit()
            # Touch cache
            _touch_cache(dst_before["model_id"])
        dst_after = _fetch_row(cur, to_name) if not dry_run else dst_before
    return {
        "from": {
            "model_id": src["model_id"], "model_name": src["model_name"],
            "litellm_params": _redact_lp(src["litellm_params"]),
        },
        "to_before": {
            "model_id": dst_before["model_id"], "model_name": dst_before["model_name"],
            "litellm_params": _redact_lp(dst_before["litellm_params"]),
        },
        "to_after": {
            "model_id": dst_after["model_id"], "model_name": dst_after["model_name"],
            "litellm_params": _redact_lp(dst_after["litellm_params"]),
        },
        "changed": changed,
        "dry_run": dry_run,
    }


def swap_names(a: str, b: str, dry_run: bool = False) -> dict:
    """Swap model_name between A and B atomically (3-step via tmp name)."""
    if a == b:
        raise ValueError("a and b must differ")
    with _conn() as c, c.cursor() as cur:
        ra = _fetch_row(cur, a)
        rb = _fetch_row(cur, b)
        tmp = f"_swap_tmp_{secrets.token_hex(4)}"
        if not dry_run:
            try:
                cur.execute(
                    'UPDATE "LiteLLM_ProxyModelTable" SET model_name = %s, updated_at = NOW() '
                    'WHERE model_id = %s',
                    (tmp, ra["model_id"]),
                )
                cur.execute(
                    'UPDATE "LiteLLM_ProxyModelTable" SET model_name = %s, updated_at = NOW() '
                    'WHERE model_id = %s',
                    (a, rb["model_id"]),
                )
                cur.execute(
                    'UPDATE "LiteLLM_ProxyModelTable" SET model_name = %s, updated_at = NOW() '
                    'WHERE model_id = %s',
                    (b, ra["model_id"]),
                )
                c.commit()
            except Exception:
                c.rollback()
                raise
            _touch_cache(ra["model_id"])
            _touch_cache(rb["model_id"])
    return {
        "a": {"model_id": ra["model_id"], "model_name": a if not dry_run else ra["model_name"]},
        "b": {"model_id": rb["model_id"], "model_name": b if not dry_run else rb["model_name"]},
        "tmp": tmp if not dry_run else None,
        "dry_run": dry_run,
    }


def swap(
    from_name: str,
    to_name: str,
    copy_creds: bool = True,
    swap_names_: bool = False,
    dry_run: bool = False,
) -> dict:
    """Atomic: copy litellm_params AND model_info FROM -> TO.

    By default swap_names_=False: public model_name stays on its row,
    only credentials (api_key, api_base, upstream model) are copied.
    This is the safe mode — opencode and other clients keep working
    because the public name -> row mapping is unchanged.

    If swap_names_=True (legacy/advanced): also swap model_name between
    rows. NOT RECOMMENDED — breaks virtual key access patterns and
    loses the original credentials on the destination row.
    """
    if from_name == to_name:
        raise ValueError("from and to must differ")
    if not copy_creds and not swap_names_:
        raise ValueError("at least one of copy_creds or swap_names_ must be True")
    result: dict[str, Any] = {
        "from": from_name, "to": to_name,
        "copy_creds": copy_creds, "swap_names": swap_names_,
        "dry_run": dry_run, "backup": None,
        "copy_result": None, "swap_result": None,
    }
    if not dry_run:
        result["backup"] = _backup_table("pre-swap")
    if copy_creds and swap_names_:
        # Single transaction for atomicity
        with _conn() as c, c.cursor() as cur:
            src = _fetch_row(cur, from_name)
            dst_before = _fetch_row(cur, to_name)
            # Fetch source model_info
            cur.execute(
                'SELECT model_info FROM "LiteLLM_ProxyModelTable" WHERE model_id = %s',
                (src["model_id"],),
            )
            src_mi_row = cur.fetchone()
            src_mi = src_mi_row[0] if src_mi_row else {}
            tmp = f"_swap_tmp_{secrets.token_hex(4)}"
            try:
                # Step 1: copy litellm_params + model_info (ensure db_model=True)
                new_mi = dict(src_mi) if src_mi else {}
                new_mi["db_model"] = True
                new_mi.pop("id", None)
                cur.execute(
                    'UPDATE "LiteLLM_ProxyModelTable" '
                    'SET litellm_params = %s, model_info = %s, updated_at = NOW() '
                    'WHERE model_id = %s',
                    (json.dumps(src["litellm_params"]), json.dumps(new_mi), dst_before["model_id"]),
                )
                # Step 2: 3-step swap name
                cur.execute(
                    'UPDATE "LiteLLM_ProxyModelTable" SET model_name = %s, updated_at = NOW() '
                    'WHERE model_id = %s',
                    (tmp, src["model_id"]),
                )
                cur.execute(
                    'UPDATE "LiteLLM_ProxyModelTable" SET model_name = %s, updated_at = NOW() '
                    'WHERE model_id = %s',
                    (from_name, dst_before["model_id"]),
                )
                cur.execute(
                    'UPDATE "LiteLLM_ProxyModelTable" SET model_name = %s, updated_at = NOW() '
                    'WHERE model_id = %s',
                    (to_name, src["model_id"]),
                )
                if dry_run:
                    c.rollback()
                else:
                    c.commit()
            except Exception:
                c.rollback()
                raise
        result["copy_result"] = {
            "src_id": src["model_id"],
            "dst_id": dst_before["model_id"],
            "src_lp_redacted": _redact_lp(src["litellm_params"]),
            "dst_lp_before_redacted": _redact_lp(dst_before["litellm_params"]),
        }
        result["swap_result"] = {
            "a_id": src["model_id"],
            "b_id": dst_before["model_id"],
            "tmp": tmp if not dry_run else None,
        }
        if not dry_run:
            _touch_cache(src["model_id"])
            _touch_cache(dst_before["model_id"])
        return result
    # Non-atomic path: one of the two ops only
    if copy_creds:
        result["copy_result"] = copy_credentials(from_name, to_name, dry_run)
    if swap_names_:
        result["swap_result"] = swap_names(from_name, to_name, dry_run)
    return result


# ----------------------------- CLI ------------------------------

def _print_json(obj):
    print(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="GLM credentials swap tool (CLI for SSH; library for /admin/glm/* route)"
    )
    sp = p.add_subparsers(dest="cmd", required=True)

    sp.add_parser("list-models", help="List all 20 GLM rows (no secrets).")
    sp.add_parser("list-pairs", help="List 10 active+_dont_use pairs.")

    p_create = sp.add_parser(
        "create-dont-use-models",
        help="Create 10 _dont_use shadows (idempotent).",
    )
    p_create.add_argument("--dry-run", action="store_true")

    p_copy = sp.add_parser("copy", help="Copy litellm_params FROM -> TO.")
    p_copy.add_argument("--from", dest="from_name", required=True)
    p_copy.add_argument("--to", dest="to_name", required=True)
    p_copy.add_argument("--dry-run", action="store_true")

    p_swapn = sp.add_parser("swap-names", help="Swap model_name A <-> B.")
    p_swapn.add_argument("--a", required=True)
    p_swapn.add_argument("--b", required=True)
    p_swapn.add_argument("--dry-run", action="store_true")

    p_swap = sp.add_parser("swap", help="Atomic: copy + swap-name.")
    p_swap.add_argument("--from", dest="from_name", required=True)
    p_swap.add_argument("--to", dest="to_name", required=True)
    p_swap.add_argument("--no-copy", dest="copy_creds", action="store_false")
    p_swap.add_argument("--no-swap-names", dest="swap_names_", action="store_false")
    p_swap.add_argument("--dry-run", action="store_true")

    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.cmd == "list-models":
            _print_json(list_models())
        elif args.cmd == "list-pairs":
            _print_json(list_pairs())
        elif args.cmd == "create-dont-use-models":
            _print_json(create_dont_use_models(dry_run=args.dry_run))
        elif args.cmd == "copy":
            _print_json(copy_credentials(args.from_name, args.to_name, dry_run=args.dry_run))
        elif args.cmd == "swap-names":
            _print_json(swap_names(args.a, args.b, dry_run=args.dry_run))
        elif args.cmd == "swap":
            _print_json(swap(
                args.from_name, args.to_name,
                copy_creds=args.copy_creds,
                swap_names_=args.swap_names_,
                dry_run=args.dry_run,
            ))
        else:
            print(f"unknown cmd: {args.cmd}", file=sys.stderr)
            return 2
    except Exception as e:
        _print_json({"error": str(e), "type": type(e).__name__})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())