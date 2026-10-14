#!/usr/bin/env python3
"""detect_feature_flags.py -- Phase 7.1-7.9: gating detection.

Every output file in evidence/p7_flags/ is written even with 0 hits
("# 0 hits" header): a missing file is a gap, an empty file with a header is
negative evidence (exactly the Phase 7.6 reference-audit defect, REVIEW.md D-3).

  rc_sdk_hits.txt                feature_flags pack over strings (7.1-7.2)
  flag_candidates.txt            remote-config-ish keys near payment words
  geo_hits.txt                   countryCode/storefront/region (7.4)
  date_gating_keys.txt           date-gate key regex (7.6a)
  timestamp_constants.txt        10-digit unix constants, converted (7.6b)
  date_api_hits.txt              date-comparison APIs (7.6c)
  test_account_gating.txt        test-account regex (7.7a)
  hardcoded_emails.txt           hardcoded emails, head 80 (7.7b)
  ab_gating_hits.txt             A/B experiment markers (7.8)
  qonversion_ab.txt              Qonversion-specific A/B (7.8b)
  gating_matrix_candidates.json  aggregate candidates (7.9) -- interpretation
                                 stays with the LLM (adjacency != branch logic)

Timestamp conversion uses Python datetime (no GNU date -d, D1). Range filter
1_400_000_000..2_000_000_000 per PLAN.md:703-704.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from ipa_lib import (  # noqa: E402
    DEFAULT_SIGNATURES_DIR, grep_lines, load_pack, ok, read_lines, warn,
    write_json, write_text, wrote,
)

DATE_KEY_RE = re.compile(
    r"(launch|release|active|live|enable|feature|availability)[_a-z]*(date|after|from)"
    r"|available_?after|go_?live|launchDate|releaseDate|featureStartDate", re.IGNORECASE)
TS_RE = re.compile(r"\b1[4-9][0-9]{8}\b")
TS_MIN, TS_MAX = 1_400_000_000, 2_000_000_000
DATE_API_RE = re.compile(
    r"timeIntervalSince1970|Date\(timeIntervalSince1970|NSDate dateWithTimeIntervalSince1970"
    r"|NSCalendar|dateByAdding|DateTime\.FromBinary|DateTimeOffset", re.IGNORECASE)
TEST_ACCOUNT_RE = re.compile(
    r"isTest(ing)?Account|test_?account|internalUser|internal_user|betaTester"
    r"|tester(email|Id)s?|allowlist|whitelist|isReviewAccount|appleReviewer"
    r"|demo_?account|review_?account|staffOnly|debug_?user", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@(example\.com|test\.[a-z]+|[a-z0-9.-]+\.(com|io|ru|net|dev))")
AB_RE = re.compile(
    r"experiment|variant|ab_?test|abtest|control_?group|treatment|\bbucket\b"
    r"|getExperiment|getGate|OptimizelyClient|OptimizelySwiftSDK|setUserProperty", re.IGNORECASE)
QONVERSION_RE = re.compile(r"Qonversion|QN[A-Z][A-Za-z]*|qonversion", re.IGNORECASE)
GEO_RE = re.compile(r"countryCode|storefront|country_code|isoCountryCode|regionCode|CheckCountry|CountryCheck", re.IGNORECASE)
FLAG_CANDIDATE_RE = re.compile(
    r"(?i)(remote.?config|is[A-Z]\w*Enabled|\w+Enabled$|featureFlag|feature_flag|killSwitch|ConfigValue)")


def iter_string_lines(audit: Path):
    strings_dir = audit / "evidence" / "p4_binary" / "strings"
    if not strings_dir.is_dir():
        return
    for f in sorted(strings_dir.glob("*.strings")):
        for i, ln in enumerate(read_lines(f), 1):
            yield f, i, ln


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-dir", required=True)
    ap.add_argument("--signatures-dir", default=DEFAULT_SIGNATURES_DIR)
    ap.add_argument("--declarations", default=None,
                    help="optional declarations dir (remote_flags.txt)")
    args = ap.parse_args()

    audit = Path(args.audit_dir)
    sdir = Path(args.signatures_dir)
    p7 = audit / "evidence" / "p7_flags"
    p7.mkdir(parents=True, exist_ok=True)

    all_lines = [(f, i, ln) for f, i, ln in iter_string_lines(audit)]
    if not all_lines:
        print("ERROR: no strings dumps -- run extract_strings.py first")
        return 2

    def fmt(rows):
        return "\n".join(f"{f.name}:{i}  {ln}" for f, i, ln in rows) if rows else None

    def out(name: str, rows: list, note: str = "") -> None:
        body = fmt(rows)
        if body is None:
            body = f"# 0 hits -- negative result ({name})"
            if note:
                body += f"\n# {note}"
        write_text(p7 / name, body)
        wrote(p7 / name, audit)

    # 7.1-7.2: feature-flag SDK markers
    pack = load_pack(sdir, "feature_flags")
    rc_rows: list[tuple[Path, int, str]] = []
    pack_lines = [pl.pattern for pl in pack]
    seen_rc: set[str] = set()
    for f, i, ln in all_lines:
        for pl in pack:
            if pl.regex.search(ln):
                key = f"{f.name}:{ln}"
                if key not in seen_rc:
                    seen_rc.add(key)
                    rc_rows.append((f, i, f"{ln}  <- matched {pl.pattern} [{pl.status}]"))
    out("rc_sdk_hits.txt", rc_rows)

    # flag candidates: RemoteConfig-ish keys (case-insensitive)
    flag_rows = [(f, i, ln) for f, i, ln in all_lines if FLAG_CANDIDATE_RE.search(ln)]
    out("flag_candidates.txt", flag_rows,
        "candidates only -- cluster + context review required before a finding")

    # 7.4: geo gating
    geo_rows = [(f, i, ln) for f, i, ln in all_lines if GEO_RE.search(ln)]
    out("geo_hits.txt", geo_rows)

    # 7.6a: date-gating keys
    date_rows = [(f, i, ln) for f, i, ln in all_lines if DATE_KEY_RE.search(ln)]
    out("date_gating_keys.txt", date_rows,
        "mandatory negative assertion when empty (REVIEW.md D-3)")

    # 7.6b: timestamp constants with conversion
    ts_rows: list[tuple[Path, int, str]] = []
    for f, i, ln in all_lines:
        for m in TS_RE.finditer(ln):
            v = int(m.group(0))
            if TS_MIN <= v <= TS_MAX:
                iso = _dt.datetime.fromtimestamp(v, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
                ts_rows.append((f, i, f"{m.group(0)} -> {iso}   ({ln})"))
                break
    out("timestamp_constants.txt", ts_rows,
        "violation ONLY if a date condition gates a payment/banking screen (manual triage)")

    # 7.6c: date APIs
    api_rows = [(f, i, ln) for f, i, ln in all_lines if DATE_API_RE.search(ln)]
    out("date_api_hits.txt", api_rows)

    # 7.7a: test accounts
    ta_rows = [(f, i, ln) for f, i, ln in all_lines if TEST_ACCOUNT_RE.search(ln)]
    out("test_account_gating.txt", ta_rows)

    # 7.7b: hardcoded emails
    emails = sorted({m.group(0) for _, _, ln in all_lines for m in EMAIL_RE.finditer(ln)})
    write_text(p7 / "hardcoded_emails.txt",
               "\n".join(emails[:80]) if emails else "# 0 hits -- no hardcoded emails")
    wrote(p7 / "hardcoded_emails.txt", audit)

    # 7.8: A/B gating
    ab_rows = [(f, i, ln) for f, i, ln in all_lines if AB_RE.search(ln)]
    out("ab_gating_hits.txt", ab_rows)
    q_rows = [(f, i, ln) for f, i, ln in all_lines if QONVERSION_RE.search(ln)]
    out("qonversion_ab.txt", q_rows)

    # declarations cross-check (7.5)
    declared_flags: list[str] = []
    if args.declarations:
        rf = Path(args.declarations) / "remote_flags.txt"
        if rf.is_file():
            declared_flags = [ln.strip() for ln in read_lines(rf) if ln.strip() and not ln.startswith("#")]

    # 7.9: gating matrix candidates (LLM interprets)
    matrix: list[dict] = []
    def push(condition: str, rows: list[tuple[Path, int, str]], sample_re: "re.Pattern[str]"):
        interesting = [r for r in rows if sample_re.search(r[2])]
        for f, i, ln in interesting[:40]:
            matrix.append({
                "condition_type": condition,
                "candidate": ln[:200],
                "evidence": f"evidence/p4_binary/strings/{f.name}:{i}",
            })
    pay_ctx = re.compile(r"(?i)pay|purchase|checkout|shop|billing|yoo|card|bank")
    push("feature_flag", flag_rows, pay_ctx)
    push("region", geo_rows, pay_ctx)
    push("date", date_rows + ts_rows, pay_ctx)
    push("test_account", ta_rows, pay_ctx)
    push("ab_experiment", ab_rows, pay_ctx)
    write_json(p7 / "gating_matrix_candidates.json", {
        "candidates": matrix,
        "declared_remote_flags": declared_flags,
        "note": "adjacency is NOT branch logic -- final gating interpretation belongs to the LLM (REVIEW.md:69)",
    })
    wrote(p7 / "gating_matrix_candidates.json", audit)

    ok(f"Phase 7 done: matrix candidates={len(matrix)}, declared_flags={len(declared_flags)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
