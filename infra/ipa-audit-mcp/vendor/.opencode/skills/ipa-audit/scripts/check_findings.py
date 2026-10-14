#!/usr/bin/env python3
"""check_findings.py -- Phase 9 / CI pre-submission gate (PLAN.md:1208, 1277).

Exit codes:
  0  gate green
  1  gate red: any confirmed critical/high with verdict=violation, OR any
     analytics_ad finding with empty/needs_review verdict (cannot submit with
     an unclosed analytics finding)
  2  usage error (findings.json missing / unreadable)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-dir", required=True)
    ap.add_argument("--fail-on", default="critical,high",
                    help="comma list of severities that block submission")
    args = ap.parse_args()

    fj_path = Path(args.audit_dir) / "findings.json"
    if not fj_path.exists():
        print(f"ERROR: {fj_path} not found")
        return 2
    try:
        fj = json.loads(fj_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: findings.json is not valid JSON: {exc}")
        return 2

    fail_sevs = {s.strip() for s in args.fail_on.split(",") if s.strip()}
    blocking = []
    for f in fj.get("findings", []):
        if f.get("verdict") == "violation" and f.get("confidence") == "confirmed" \
                and f.get("severity") in fail_sevs:
            blocking.append(f"{f['id']} [{f['severity']}] {f['pattern']}")
        if f.get("category") == "analytics_ad" and f.get("verdict") in ("", None, "needs_review"):
            blocking.append(f"{f['id']} [analytics_ad not closed] {f['pattern']}")

    counts = fj.get("summary", {})
    print(f"GATE: findings={len(fj.get('findings', []))} critical={counts.get('critical')} "
          f"high={counts.get('high')} verdict={fj.get('verdict')}")
    if blocking:
        for b in blocking:
            print(f"BLOCK: {b}")
        print("CI GATE: RED — do not submit")
        return 1
    print("CI GATE: GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
