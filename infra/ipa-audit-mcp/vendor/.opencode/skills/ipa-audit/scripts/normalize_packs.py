#!/usr/bin/env python3
"""normalize_packs.py -- Phase 0.2c: normalize signature packs.

Python port of the planned normalize_pack.sh (PLAN.md:861-865), replacing the
sed/awk pair:

    <pack>.txt  ->  <pack>.clean.txt    patterns without inline comments
                ->  <pack>.status.tsv   map "pattern<TAB>confirmed|lead"

Semantics (matching the existing canonical files in tools/signatures):
  - strip  [[:space:]]*#.*$   from every line (inline comments)
  - drop lines that are empty or start with '#' after stripping
  - status = "lead" when the stripped comment contains the word "lead",
    otherwise "confirmed"
  - every surviving pattern must compile as a case-insensitive regex
    (BAD ERE -> ERROR + exit 3, PLAN.md:1259-1271 "rc>=2 only" rule)

Files are written UTF-8 (no BOM) with LF endings and a trailing newline.
Re-running on already-normalized packs is idempotent for .clean.txt/.status.tsv.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from ipa_lib import (  # noqa: E402
    DEFAULT_SIGNATURES_DIR, compile_ere, error, ok, warn, write_text,
)

COMMENT_RE = None  # compiled lazily to keep import cheap


def normalize_pack(txt_path: Path) -> tuple[int, int]:
    """Returns (n_patterns, n_lead). Writes .clean.txt and .status.tsv."""
    import re

    raw_lines = txt_path.read_text(encoding="utf-8-sig").splitlines()
    clean: list[str] = []
    status_rows: list[tuple[str, str]] = []
    bad: list[str] = []

    for line in raw_lines:
        stripped = re.sub(r"[ \t]*#.*$", "", line.replace("\r", ""))
        if not stripped.strip() or stripped.lstrip().startswith("#"):
            continue
        comment = line[len(stripped):] if len(line) > len(stripped) else ""
        st = "lead" if "lead" in comment else "confirmed"
        clean.append(stripped)
        status_rows.append((stripped, st))
        try:
            compile_ere(stripped)
        except ValueError as exc:
            bad.append(str(exc))

    if bad:
        for b in bad:
            error(b)
        raise SystemExit(3)

    # canonical naming: payment.txt -> payment.clean.txt / payment.status.tsv
    stem = txt_path.name[:-4] if txt_path.name.endswith(".txt") else txt_path.name
    clean_path = txt_path.with_name(stem + ".clean.txt")
    status_path = txt_path.with_name(stem + ".status.tsv")

    write_text(clean_path, "\n".join(clean))
    write_text(status_path, "\n".join(f"{p}\t{s}" for p, s in status_rows))
    n_lead = sum(1 for _, s in status_rows if s == "lead")
    return len(clean), n_lead


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--signatures-dir", default=DEFAULT_SIGNATURES_DIR,
                    help=f"directory with *.txt packs (default: {DEFAULT_SIGNATURES_DIR})")
    ap.add_argument("--pack", action="append",
                    help="normalize only named packs (repeatable); default: all *.txt excluding .clean.txt/.status.tsv")
    args = ap.parse_args()

    sdir = Path(args.signatures_dir)
    if not sdir.is_dir():
        error(f"signatures dir not found: {sdir}")
        return 2

    if args.pack:
        packs = [sdir / f"{p}.txt" for p in args.pack]
        missing = [p for p in packs if not p.exists()]
        if missing:
            error("missing packs: " + ", ".join(str(p) for p in missing))
            return 2
    else:
        packs = sorted(p for p in sdir.glob("*.txt")
                       if not p.name.endswith((".clean.txt", ".status.tsv")))

    if not packs:
        error(f"no packs found in {sdir}")
        return 2

    total = lead_total = 0
    for p in packs:
        try:
            n, nlead = normalize_pack(p)
        except SystemExit as exc:
            return int(exc.code or 3)
        total += n
        lead_total += nlead
        ok(f"{p.name}: {n} patterns ({nlead} lead) -> {p.name[:-4]}.clean.txt / .status.tsv")

    ok(f"normalized {len(packs)} packs, {total} patterns, {lead_total} lead")
    if len(packs) != 14:
        warn(f"expected 14 packs, found {len(packs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
