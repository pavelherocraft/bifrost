#!/usr/bin/env python3
"""grep_packs.py -- the pack grep engine (Phases 4.4/4.4b/4.4c + 3.3/5.x/6.x/7.x/8.x).

Python replacement for `grep -E -i -n -f` over the Phase-4 dumps (D1) with the
agent-facing additions the reference audit lacked (D3/D4):

  hits/<pack>_hits.txt      one header + 11-line window (5 before, match,
                            5 after, indented) per hit; header format
                            === [binary] <strings basename>:<line>  pattern=<pack line>  confidence=<confirmed|lead>
  hits/_summary.json        pack -> hit count (headers), the arithmetic source
  raw_hits.tsv              phase, pack, file, line, pattern, excerpt
  hits/payment_ui_classes.txt     (4.4b escalation evidence)
  hits/nordic_anchored_hits.txt   (4.4c anchored nordic subset)
  triage_input.json         COMPACT per-pack view for the LLM: counts, distinct
                            matched patterns, deduplicated cluster candidates,
                            <=5 samples each with file:line, cross-pack duplicate
                            flags (mobilepay c Square, Tink/TinkLink)

Context mode (the only sanctioned way to look at surroundings):
  grep_packs.py --context --pack payment --pattern "QN[A-Z]" --lines 20

Suppressions (data/suppressions.yaml) are per-binary only; a globally scoped
suppression of an analytics_purchase pattern is refused (PLAN.md ред.2).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from ipa_lib import (  # noqa: E402
    BINARY_PACKS, DEFAULT_SIGNATURES_DIR, compile_ere, grep_lines, is_suppressed,
    load_pack, load_suppressions, ok, read_lines, warn, write_json, write_text,
    wrote,
)

SKILL_DATA = SCRIPT_DIR.parent / "data"
CONTEXT_BEFORE = 5
CONTEXT_AFTER = 5
MAX_SAMPLES_PER_CLUSTER = 5
MAX_CONTEXT_HITS = 50

PAYMENT_UI_RE = re.compile(r"PaymentForm|CardForm|Tokeniz|ThreeDsProcessor|STPCardValidator")
NORDIC_ANCHORED_RE = re.compile(
    r"mobilepay://|MobilePaySDK|\bMobilePay\b|bankid://|app\.bankid\.com|autostarttoken"
    r"|swish://|getswish|SwishSDK|\bSwish\b|vipps://|\bVipps\b|mobilepay\.dk",
    re.IGNORECASE,
)


def target_files(audit: Path, target: str) -> list[tuple[str, Path]]:
    """Return [(binary_label, path)] in deterministic order."""
    p4 = audit / "evidence" / "p4_binary"
    files: list[tuple[str, Path]] = []
    if target == "strings":
        sd = p4 / "strings"
        if not sd.is_dir():
            return []
        for f in sorted(sd.glob("*.strings")):
            files.append((f.name.removesuffix(".strings"), f))
        cd = p4 / "classdump"
        if cd.is_dir():
            for f in sorted(cd.glob("*.objc_classes.txt")):
                files.append((f.name.split(".")[0], f))
    elif target in ("dll-strings", "js"):
        sd = p4 / "strings"
        if sd.is_dir():
            for f in sorted(sd.glob("*.strings")):
                lbl = f.name.removesuffix(".strings")
                if lbl.startswith(("global-metadata", "Assembly-")) or "ScriptingAssemblies" in lbl:
                    files.append((lbl, f))
        extracted = audit / "extracted"
        for pat in ("**/public/**/*.js", "**/www/**/*.js", "**/main.jsbundle",
                    "**/Data/Managed/**/*.dll"):
            for f in sorted(extracted.glob(pat)):
                files.append((f.name, f))
    else:
        for f in sorted((audit / "evidence").rglob("*.txt")):
            files.append((f.parent.name, f))
    return files


def write_hits_file(out_path: Path, hits: list[dict], lines_cache: dict[Path, list[str]],
                    suppressions: list[dict]) -> int:
    """Write header+context per hit; returns number of hits written."""
    n = 0
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        for h in hits:
            lines = lines_cache[h["path"]]
            idx = h["line"] - 1
            lo = max(0, idx - CONTEXT_BEFORE)
            hi = min(len(lines), idx + CONTEXT_AFTER + 1)
            supp = is_suppressed(suppressions, h["binary"], h["pattern"])
            suffix = f"  SUPPRESSED({supp['reason']})" if supp else ""
            fh.write(f"=== [{h['binary']}] {h['path'].name}:{h['line']}  "
                     f"pattern={h['pattern']}  confidence={h['status']}{suffix}\n")
            for j in range(lo, hi):
                marker = "*" if j == idx else " "
                fh.write(f"    {marker} {lines[j]}\n")
            n += 1
    return n


def build_triage_input(audit: Path, pack_names: list[str], hits_by_pack: dict[str, list[dict]]) -> dict:
    """Compact LLM-facing view (D3). Cross-pack duplicate flags (edge case 6)."""
    triage: dict[str, object] = {"packs": {}}
    payment_lines = {h["line_text"] for h in hits_by_pack.get("payment", [])}
    for pack in pack_names:
        hits = hits_by_pack.get(pack, [])
        clusters: dict[str, dict] = {}
        for h in hits:
            key = h["line_text"]
            c = clusters.setdefault(key, {
                "text": key, "files": [], "pattern": h["pattern"],
                "status": h["status"], "count": 0,
            })
            c["count"] += 1
            loc = f"{h['path'].name}:{h['line']}"
            if loc not in c["files"]:
                c["files"].append(loc)
        cluster_list = sorted(clusters.values(), key=lambda c: -c["count"])
        for c in cluster_list:
            c["samples"] = c["files"][:MAX_SAMPLES_PER_CLUSTER]
            del c["files"]
            dup = None
            if pack == "nordic_payment" and re.search(r"(?i)mobilepay", c["text"]):
                if any(re.search(r"(?i)SquareMobilePaymentsSDK", pl) for pl in payment_lines):
                    dup = "mobilepay is a substring of SquareMobilePaymentsSDK (PLAN.md:1240) -- verify before filing"
            if pack in ("payment", "kyc_banking") and re.search(r"(?i)\btink", c["text"]):
                if "TinkLink" not in c["text"]:
                    dup = "bare Tink hit without TinkLink cluster -- likely Google crypto FP (PLAN.md:1088)"
            if dup:
                c["duplicate_of"] = dup
        triage["packs"][pack] = {
            "hit_count": len(hits),
            "distinct_patterns": sorted({h["pattern"] for h in hits}),
            "cluster_count": len(cluster_list),
            "clusters": cluster_list[:100],
        }
    return triage


def run_context_mode(audit: Path, sdir: Path, pack: str, pattern: str,
                     lines: int, target: str) -> int:
    files = target_files(audit, target)
    if not files:
        print(f"ERROR: no target files for --target {target}")
        return 2
    rx = re.compile(pattern, re.IGNORECASE)
    shown = 0
    for label, path in files:
        try:
            content = read_lines(path)
        except OSError:
            continue
        for i, line in enumerate(content):
            if rx.search(line):
                lo = max(0, i - lines)
                hi = min(len(content), i + lines + 1)
                print(f"--- {path.name}:{i + 1} ---")
                for j in range(lo, hi):
                    marker = "*" if j == i else " "
                    print(f"  {marker} {content[j]}")
                shown += 1
                if shown >= MAX_CONTEXT_HITS:
                    warn(f"context output capped at {MAX_CONTEXT_HITS} hits")
                    return 0
    ok(f"context: {shown} hits of {pattern!r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-dir", required=True)
    ap.add_argument("--signatures-dir", default=DEFAULT_SIGNATURES_DIR)
    ap.add_argument("--packs", default="all-binary",
                    help="comma list or 'all-binary' (10 packs) / 'all' (14)")
    ap.add_argument("--target", default="strings",
                    choices=["strings", "dll-strings", "js", "evidence"])
    ap.add_argument("--context", action="store_true")
    ap.add_argument("--context-pack", "--pack", dest="context_pack", default=None)
    ap.add_argument("--context-pattern", "--pattern", dest="context_pattern", default=None)
    ap.add_argument("--context-lines", "--lines", dest="context_lines", type=int, default=20)
    ap.add_argument("--hits-subdir", default=None, metavar="NAME",
                    help="experimental sandbox: write ALL outputs into evidence/p4_binary/hits/NAME/ "
                         "instead of the canonical hits/ (canonical files are never touched)")
    ap.add_argument("--force", action="store_true",
                    help="allow overwriting existing canonical hit files from a NON-canonical "
                         "target (dll-strings/js/evidence). Canonical --target strings reruns "
                         "are always allowed (rework mode).")
    args = ap.parse_args()

    if args.hits_subdir and (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.hits_subdir)
        or args.hits_subdir in (".", "..")
    ):
        print(f"ERROR: --hits-subdir must be a plain directory name (no separators/traversal): "
              f"{args.hits_subdir!r}")
        return 2

    audit = Path(args.audit_dir)
    sdir = Path(args.signatures_dir)

    if args.context:
        if not (args.context_pack and args.context_pattern):
            print("ERROR: --context requires --context-pack and --context-pattern")
            return 2
        return run_context_mode(audit, sdir, args.context_pack,
                                args.context_pattern, args.context_lines, args.target)

    if args.packs == "all-binary":
        pack_names = list(BINARY_PACKS)
    elif args.packs == "all":
        from ipa_lib import ALL_PACKS
        pack_names = list(ALL_PACKS)
    else:
        pack_names = [p.strip() for p in args.packs.split(",") if p.strip()]

    files = target_files(audit, args.target)
    if not files:
        print(f"ERROR: no target files (run extract_strings.py first?) --target={args.target}")
        return 2

    lines_cache: dict[Path, list[str]] = {p: read_lines(p) for _, p in files}
    suppressions = load_suppressions(SKILL_DATA / "suppressions.yaml")
    for s in suppressions:
        if s.get("binary") in ("*", "") and args.target == "strings":
            is_analytics = (s.get("pack") == "analytics_purchase"
                            or "analytics" in str(s.get("pattern", "")).lower())
            if is_analytics:
                print(f"ERROR: global suppression of analytics pattern refused (PLAN.md:1100): {s}")
                return 3

    hits_dir = audit / "evidence" / "p4_binary" / "hits"
    if args.hits_subdir:
        hits_dir = hits_dir / args.hits_subdir
        hits_dir.mkdir(parents=True, exist_ok=True)
    elif args.target != "strings" and not args.force:
        canonical = [p for p in (audit / "evidence" / "p4_binary" / "hits").glob("*_hits.txt")
                     if p.is_file()]
        if canonical:
            print(f"ERROR: refusing to overwrite {len(canonical)} canonical hit file(s) with "
                  f"--target {args.target} (canonical Phase 4.4 run uses --target strings). "
                  f"Use --hits-subdir <name> for experiments or pass --force to overwrite.")
            return 2
    hits_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, int] = {}
    hits_by_pack: dict[str, list[dict]] = {}
    raw_rows: list[str] = ["phase\tpack\tfile\tline\tpattern\texcerpt"]

    for pack_name in pack_names:
        pack = load_pack(sdir, pack_name)
        hits: list[dict] = []
        for label, path in files:
            content = lines_cache[path]
            for idx, pl, line in grep_lines(content, pack):
                hits.append({
                    "binary": label, "path": path, "line": idx,
                    "pattern": pl.pattern, "status": pl.status,
                    "line_text": line,
                })
                raw_rows.append(f"4\t{pack_name}\t{path.name}\t{idx}\t"
                                f"{pl.pattern}\t{line[:200]}")
        hits_by_pack[pack_name] = hits
        hp = hits_dir / f"{pack_name}_hits.txt"
        summary[pack_name] = write_hits_file(hp, hits, lines_cache, suppressions)
        wrote(hp, audit)
        ok(f"{pack_name}: {summary[pack_name]} hits")

    write_json(hits_dir / "_summary.json", summary)
    wrote(hits_dir / "_summary.json", audit)
    # canonical raw_hits.tsv lives at p4_binary/; a sandboxed run keeps its copy
    # inside hits/<subdir>/ so the canonical TSV is never clobbered
    raw_tsv = (audit / "evidence" / "p4_binary" / "raw_hits.tsv") if not args.hits_subdir \
        else (hits_dir / "raw_hits.tsv")
    write_text(raw_tsv, "\n".join(raw_rows))
    wrote(raw_tsv, audit)

    # --- 4.4b: payment UI classes (analytics escalation) -----------------------
    ui_rows = [f"{h['path'].name}:{h['line']}  {h['line_text']}"
               for h in hits_by_pack.get("payment", [])
               if PAYMENT_UI_RE.search(h["line_text"])]
    ui_rows += [f"{label}:{i + 1}  {ln}"
                for label, path in files if path.suffix == ".strings"
                for i, ln in enumerate(lines_cache[path])
                if PAYMENT_UI_RE.search(ln) and not any(
                    r.startswith(f"{path.name}:{i + 1}") for r in ui_rows)]
    write_text(hits_dir / "payment_ui_classes.txt",
               "\n".join(ui_rows) if ui_rows else "# 0 hits -- no payment UI classes (4.4b): analytics purchase hits stay analytics")
    wrote(hits_dir / "payment_ui_classes.txt", audit)

    # --- 4.4c: nordic anchored subset -------------------------------------------
    anchored = [h for h in hits_by_pack.get("nordic_payment", [])
                if NORDIC_ANCHORED_RE.search(h["line_text"])]
    body = [f"=== [{h['binary']}] {h['path'].name}:{h['line']}  pattern={h['pattern']}  confidence={h['status']}"
            for h in anchored]
    write_text(hits_dir / "nordic_anchored_hits.txt",
               "\n".join(body) if body else "# 0 hits -- no anchored Nordic patterns (4.4c)")
    wrote(hits_dir / "nordic_anchored_hits.txt", audit)

    # --- triage_input.json (D3) ---------------------------------------------------
    triage = build_triage_input(audit, pack_names, hits_by_pack)
    write_json(hits_dir / "triage_input.json", triage)
    wrote(hits_dir / "triage_input.json", audit)

    total = sum(summary.values())
    ok(f"grep_packs done: {len(pack_names)} packs, {total} raw hits")
    return 0


if __name__ == "__main__":
    sys.exit(main())
