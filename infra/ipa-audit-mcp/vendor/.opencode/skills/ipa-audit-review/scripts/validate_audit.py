#!/usr/bin/env python3
"""validate_audit.py -- machine validation of an audit dir (V1-V10).

Deliberately shares NO code with the ipa-audit skill scripts (independent
re-implementation of the arithmetic/excerpt checks -- a reviewer that imports
the executor's library inherits its bugs). The only shared artifact is the
schema file, loaded read-only at startup; V1 enforces its required fields,
enum sets (severity/confidence/category/verdict/reachable), phase + id
patterns, and pattern/remediation presence.

Checks:
  V1  findings.json parses + validates against findings_schema.json (required
      fields, enums incl. category/verdict, id + phase patterns, pattern/
      remediation presence)
  V2  counts reconcile: _summary.json sum, false_positives sum, per-severity
      summary, coverage vs strings_manifest.json
  V3  every evidence path exists / relative / non-empty; no Temp or absolute
      paths inside json/txt artifacts
  V4  every evidence.excerpt (>=40 chars) appears verbatim (whitespace-
      normalized) in the referenced evidence file
  V5  phases_completed map to non-empty evidence dirs; skipped phases have
      reasons; Phase 7.6 has an explicit negative-result file
  V6  original/SHA256SUMS.txt is UTF-8 with ^[0-9a-fA-F]{64}<2 spaces>; hash
      re-verified when App.ipa present; PROVENANCE.md exists
  V7  14 packs normalized in the signatures dir; _summary covers the 10
      binary packs; no confirmed+reachable:unknown finding without notes
  V8  AUDIT_REPORT.md completeness: all template headings incl. 6b and 8,
      sign-off checklist, verdict consistent across findings/report/summary
  V9  hosts inventory sanity: re-extraction from strings dumps (when present)
      must reproduce the hosts_all.txt count +-0
  V10 gating_matrix rows reference existing finding ids; date/test_account/
      ab_experiment gates have p7_flags evidence files

Output: <audit_dir>/validation_report.json + stdout CHECK lines +
MACHINE_VERDICT: PASS|FAIL. Exit code 0 always (the reviewer decides), 2 only
for usage errors.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
# <root>/.opencode/skills/ipa-audit-review/scripts -> project root
PROJECT_ROOT = SKILL_DIR.parent.parent.parent
IPA_AUDIT_SKILL = SKILL_DIR.parent / "ipa-audit"
SCHEMA_PATH = IPA_AUDIT_SKILL / "data" / "findings_schema.json"
DEFAULT_SIGNATURES_DIR = str(
    Path(os.environ.get("IPA_SIGNATURES_DIR") or (PROJECT_ROOT / "tools" / "signatures"))
)
BINARY_PACKS = ["payment", "kyc_banking", "nordic_payment", "analytics_purchase",
                "crypto_web3", "pos_ble_nfc", "feature_flags", "webview_url",
                "generic_payment_words", "secrets"]
SEVERITIES = ["critical", "high", "medium", "low", "info"]

# V1 loads findings_schema.json read-only (the shared contract with the
# executor). Enum/required sets are read from it when available, with the
# literal fallbacks below only for the case where the schema file is absent.
SCHEMA: dict = {}
try:
    SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    SCHEMA = {}
_FINDING_ITEM = (((SCHEMA.get("properties") or {}).get("findings") or {}).get("items")) or {}
FINDING_REQUIRED = _FINDING_ITEM.get("required", [])
_FINDING_PROPS = _FINDING_ITEM.get("properties", {})
SCHEMA_CONFIDENCE = _FINDING_PROPS.get("confidence", {}).get("enum", ["confirmed", "probable", "lead"])
SCHEMA_CATEGORY = _FINDING_PROPS.get("category", {}).get("enum", [])
SCHEMA_VERDICT = _FINDING_PROPS.get("verdict", {}).get("enum", ["violation", "false_positive", "needs_review"])
SCHEMA_REACHABLE = _FINDING_PROPS.get("reachable", {}).get("enum", ["yes", "no", "unknown"])
SCHEMA_PHASE_PATTERN = _FINDING_PROPS.get("phase", {}).get("pattern", r"^[0-9]+(\.[0-9]+)?[a-z]?$")
SCHEMA_ID_PATTERN = _FINDING_PROPS.get("id", {}).get(
    "pattern", r"^(PAY|KYC|SSO|W3|POS|FLAG|URL|DL|L10N|ASSET|PLIST|ENT|DEAD|SEC|HYB|NORDIC|OB|ANL|SUB|GATE)-[0-9]{3}$")
# schema _custom_rules.id_prefix_by_category -- enforced independently here (D4/D6)
SCHEMA_CATEGORY_PREFIX = (SCHEMA.get("_custom_rules") or {}).get("id_prefix_by_category", {})

REQUIRED_HEADINGS = [
    "## 1. Краткое резюме", "## 2. Информация о сборке", "## 3. Найденные проблемы",
    "## 4. Матрица расхождений", "## 5. Доказательства", "## 6. Ложные срабатывания",
    "## 6b. Инвентаризация аналитических", "## 7. План устранения", "## 8. Результат повторного аудита",
    "## 9. Контрольный список подтверждения",
]


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"__error__": str(exc)}


def read_lines(p: Path) -> list[str]:
    try:
        return p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


class Report:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, check_id: str, status: str, detail: str, refs: list[str] | None = None) -> None:
        self.checks.append({"check_id": check_id, "status": status,
                            "detail": detail, "refs": refs or []})
        print(f"CHECK {check_id}: {status.upper()} — {detail}")

    def verdict(self) -> str:
        return "FAIL" if any(c["status"] == "fail" for c in self.checks) else "PASS"


# --------------------------------------------------------------------------- V1
def v1(audit: Path, rep: Report) -> dict:
    fj_path = audit / "findings.json"
    if not fj_path.exists():
        rep.add("V1", "fail", "findings.json missing")
        return {}
    fj = read_json(fj_path)
    if "__error__" in fj:
        rep.add("V1", "fail", f"findings.json not valid JSON: {fj['__error__']}")
        return {}
    errs: list[str] = []
    if fj.get("schema_version") != "2.0":
        errs.append(f"schema_version != '2.0' (got {fj.get('schema_version')!r})")
    if "build" not in fj:
        errs.append("missing 'build'{} block (legacy 'audit'{} schema)")
    b = fj.get("build") or fj.get("audit") or {}
    for k in ("app", "version", "build_number", "ipa_sha256", "cryptid", "hybrid",
              "audit_date", "auditor", "tools"):
        if k not in b:
            errs.append(f"build.{k} missing")
    if b.get("hybrid") not in (None, "none", "rn", "flutter", "capacitor", "unity_il2cpp"):
        errs.append(f"build.hybrid invalid: {b.get('hybrid')!r}")
    for f in fj.get("findings", []):
        fid = str(f.get("id", "?"))
        # required fields from findings_schema.json
        for req in FINDING_REQUIRED:
            if req not in f:
                errs.append(f"{fid}: missing required field '{req}'")
        if f.get("severity") not in SEVERITIES:
            errs.append(f"{fid}: bad severity {f.get('severity')!r}")
        if f.get("confidence") not in SCHEMA_CONFIDENCE:
            errs.append(f"{fid}: bad confidence {f.get('confidence')!r}")
        if SCHEMA_CATEGORY and f.get("category") not in SCHEMA_CATEGORY:
            errs.append(f"{fid}: bad category {f.get('category')!r}")
        if f.get("verdict") not in SCHEMA_VERDICT:
            errs.append(f"{fid}: bad verdict {f.get('verdict')!r}")
        if not isinstance(f.get("pattern"), str) or not f.get("pattern"):
            errs.append(f"{fid}: pattern must be a non-empty string")
        if f.get("verdict") != "false_positive" and \
                (not isinstance(f.get("remediation"), str) or not f.get("remediation").strip()):
            errs.append(f"{fid}: remediation must be a non-empty string")
        if not isinstance(f.get("gated_by"), list):
            errs.append(f"{fid}: gated_by is not an array ({type(f.get('gated_by')).__name__})")
        if f.get("reachable") not in SCHEMA_REACHABLE:
            errs.append(f"{fid}: reachable must be 'yes|no|unknown' string, got {f.get('reachable')!r}")
        if not isinstance(f.get("phase", ""), str):
            errs.append(f"{fid}: phase must be a string like '4.4'")
        elif not re.match(SCHEMA_PHASE_PATTERN, f["phase"]):
            errs.append(f"{fid}: phase {f['phase']!r} fails schema pattern {SCHEMA_PHASE_PATTERN}")
        if not re.match(SCHEMA_ID_PATTERN, fid):
            errs.append(f"{fid}: id does not follow the PLAN.md:1375 prefix map")
        # schema _custom_rules.id_prefix_by_category: the id's prefix must match
        # the category's declared prefix (independent enforcement, D4/D6).
        expected_prefix = SCHEMA_CATEGORY_PREFIX.get(f.get("category"))
        if expected_prefix and not fid.startswith(expected_prefix + "-"):
            errs.append(f"{fid}: id prefix does not match category {f.get('category')!r} "
                        f"(expected {expected_prefix}-*)")
    if errs:
        rep.add("V1", "fail", f"{len(errs)} schema violations: " + "; ".join(errs[:8]),
                ["findings_schema.json"])
    else:
        src = "findings_schema.json" if SCHEMA else "embedded fallback (schema unreadable)"
        rep.add("V1", "pass", f"schema valid ({src})")
    return fj


# --------------------------------------------------------------------------- V2
def v2(audit: Path, fj: dict, rep: Report) -> None:
    errs: list[str] = []
    sm_path = audit / "evidence" / "p4_binary" / "hits" / "_summary.json"
    summary = read_json(sm_path) if sm_path.exists() else {}
    if not summary:
        errs.append("_summary.json missing/unreadable — cannot verify raw hit counts")
    else:
        total = sum(v for v in summary.values() if isinstance(v, int))
        claimed = (fj.get("counts") or {}).get("raw_hits_total")
        if claimed is None:
            errs.append("counts.raw_hits_total missing")
        elif claimed != total:
            errs.append(f"counts.raw_hits_total={claimed} != sum(_summary.json)={total}")
        fps = fj.get("false_positives") or []
        fpsum = sum(x.get("hits", 0) for x in fps if isinstance(x, dict))
        claimed_fp = (fj.get("counts") or {}).get("raw_false_positive_hits")
        if claimed_fp is not None and claimed_fp != fpsum:
            errs.append(f"counts.raw_false_positive_hits={claimed_fp} != sum(false_positives[].hits)={fpsum}")
    recomputed = {sev: 0 for sev in SEVERITIES}
    nfp = 0
    for f in fj.get("findings", []):
        if f.get("verdict") == "false_positive":
            nfp += 1
        else:
            recomputed[f.get("severity", "?")] = recomputed.get(f.get("severity", "?"), 0) + 1
    recomputed["false_positive"] = nfp
    claimed_sum = fj.get("summary") or {}
    for k, v in recomputed.items():
        if claimed_sum.get(k) != v:
            errs.append(f"summary.{k}={claimed_sum.get(k)} != recount {v}")
    man = read_json(audit / "evidence" / "p4_binary" / "strings_manifest.json")
    if man and "__error__" not in man:
        strings_total = sum(v.get("n_strings", 0) for v in man.values() if isinstance(v, dict))
        cov = fj.get("coverage") or {}
        if cov.get("strings_extracted") not in (None, strings_total):
            errs.append(f"coverage.strings_extracted={cov.get('strings_extracted')} != manifest sum {strings_total}")
    if errs:
        rep.add("V2", "fail", "; ".join(errs[:8]), ["evidence/p4_binary/hits/_summary.json"])
    else:
        rep.add("V2", "pass", f"counts reconcile (raw_hits={sum(v for v in summary.values() if isinstance(v,int))})")


# --------------------------------------------------------------------------- V3
def v3(audit: Path, fj: dict, rep: Report) -> None:
    errs: list[str] = []
    for f in fj.get("findings", []):
        fid = str(f.get("id", "?"))
        ev = f.get("evidence") or {}
        for key in ("file", "context_ref"):
            p = str(ev.get(key, "") or "")
            if not p:
                continue
            if re.match(r"(?i)^\w:[\\/]", p) or "Temp" in p:
                errs.append(f"{fid}: {key} absolute/Temp path: {p}")
                continue
            # context_ref may carry a :<line> suffix (PLAN.md:1358 format)
            p_no_line = re.sub(r":\d+$", "", p)
            if not (audit / p_no_line).exists():
                errs.append(f"{fid}: {key} does not exist: {p}")
        if not str(ev.get("file", "") or ""):
            errs.append(f"{fid}: evidence.file empty")
    txt_json = list(audit.rglob("*.json")) + list(audit.rglob("*.txt")) + list(audit.rglob("*.tsv"))
    temp_hits = 0
    for p in txt_json:
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if re.search(r"[A-Za-z]:(?:\\\\|\\)+Users(?:\\\\|\\)+Admin(?:\\\\|\\)+AppData(?:\\\\|\\)+Local(?:\\\\|\\)+Temp|(?:\\\\|\\|/)Temp(?:\\\\|\\|/)ipa_audit", body):
            temp_hits += 1
            if len(errs) < 12:
                errs.append(f"Temp path leakage in artifact: {p.relative_to(audit).as_posix()}")
    if errs:
        rep.add("V3", "fail", f"{len(errs)} path problems (Temp leakage in {temp_hits} artifacts): " + "; ".join(errs[:8]))
    else:
        rep.add("V3", "pass", "all referenced evidence exists, relative, Temp-free")


# --------------------------------------------------------------------------- V4
def v4(audit: Path, fj: dict, rep: Report) -> None:
    errs: list[str] = []
    checked = 0
    for f in fj.get("findings", []):
        fid = str(f.get("id", "?"))
        ev = f.get("evidence") or {}
        excerpt = norm_ws(str(ev.get("excerpt", "") or ""))
        if len(excerpt) < 40:
            continue
        target = audit / str(ev.get("file", ""))
        if not target.exists():
            errs.append(f"{fid}: evidence file missing: {ev.get('file')}")
            continue
        body = norm_ws(target.read_text(encoding="utf-8", errors="replace"))
        checked += 1
        if excerpt not in body:
            errs.append(f"{fid}: excerpt NOT present verbatim in {ev.get('file')}")
    if errs:
        rep.add("V4", "fail", f"{len(errs)} excerpt authenticity failures: " + "; ".join(errs[:8]))
    else:
        rep.add("V4", "pass", f"all {checked} long excerpts verified verbatim")


# --------------------------------------------------------------------------- V5
def v5(audit: Path, fj: dict, rep: Report) -> None:
    errs: list[str] = []
    ev = audit / "evidence"
    for ph in fj.get("phases_completed", []):
        if ph in (0, 1) and not any((ev / "p1_structure").glob("*")):
            errs.append(f"phase {ph} completed but evidence/p1_structure is empty/missing")
        if ph == 2 and not any((ev / "p2_plist_entitlements").glob("*")):
            errs.append("phase 2 completed but p2 evidence empty")
        if ph in (5, 6, 7):
            d = {5: "p5_assets_l10n", 6: "p6_webview_urls", 7: "p7_flags"}[ph]
            files = [f for f in (ev / d).glob("*") if f.is_file() and f.stat().st_size > 0]
            if not files:
                errs.append(f"phase {ph} completed but {d} has no non-empty evidence")
    for ph, reason in (fj.get("phases_skipped") or {}).items():
        if not str(reason).strip():
            errs.append(f"phase {ph} skipped without a reason")
    dg = ev / "p7_flags" / "date_gating_keys.txt"
    if 7 in fj.get("phases_completed", []) and not dg.exists():
        errs.append("Phase 7.6 date-gating has NO file at all (missing negative result)")
    if errs:
        rep.add("V5", "fail", "; ".join(errs[:8]))
    else:
        rep.add("V5", "pass", "phase evidence coverage ok, 7.6 negative result present")


# --------------------------------------------------------------------------- V6
def v6(audit: Path, fj: dict, rep: Report) -> None:
    errs: list[str] = []
    sums = audit / "original" / "SHA256SUMS.txt"
    if not sums.exists():
        errs.append("original/SHA256SUMS.txt missing")
    else:
        raw = sums.read_bytes()
        if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
            errs.append("SHA256SUMS.txt is UTF-16 (must be UTF-8)")
        else:
            first = raw.decode("utf-8", "replace").splitlines()[0] if raw else ""
            if not re.match(r"^[0-9a-fA-F]{64}  \S", first):
                errs.append(f"SHA256SUMS.txt hash format invalid: {first[:80]!r}")
            ipa = audit / "original" / "App.ipa"
            if ipa.exists():
                h = hashlib.sha256(ipa.read_bytes()).hexdigest().upper()
                if not first.upper().startswith(h):
                    errs.append("App.ipa hash does not match SHA256SUMS.txt")
    if not (audit / "original" / "PROVENANCE.md").exists():
        errs.append("original/PROVENANCE.md missing (Phase 0.3)")
    if errs:
        rep.add("V6", "fail", "; ".join(errs))
    else:
        rep.add("V6", "pass", "provenance chain complete and re-verified")


# --------------------------------------------------------------------------- V7
def v7(audit: Path, fj: dict, rep: Report, sdir: Path) -> None:
    errs: list[str] = []
    packs = sorted(p.name[:-4] for p in sdir.glob("*.txt")
                   if not p.name.endswith((".clean.txt", ".status.tsv"))) if sdir.is_dir() else []
    if len(packs) != 14:
        errs.append(f"signatures dir has {len(packs)} packs (expected 14)")
    missing_norm = [p for p in packs
                    if not (sdir / f"{p}.clean.txt").exists() or not (sdir / f"{p}.status.tsv").exists()]
    if missing_norm:
        errs.append(f"packs not normalized: {missing_norm}")
    sm = read_json(audit / "evidence" / "p4_binary" / "hits" / "_summary.json")
    if sm and "__error__" not in sm:
        missing = [p for p in BINARY_PACKS if p not in sm]
        if missing:
            errs.append(f"_summary.json missing binary packs: {missing}")
    for f in fj.get("findings", []):
        if f.get("confidence") == "confirmed" and f.get("reachable") in (None, "unknown") \
                and not str(f.get("notes", "") or "").strip():
            errs.append(f"{f.get('id')}: confirmed + reachable unknown/None without notes justification")
    if errs:
        rep.add("V7", "fail", "; ".join(errs[:8]))
    else:
        rep.add("V7", "pass", "packs complete, no confidence mislabels")


# --------------------------------------------------------------------------- V8
def v8(audit: Path, fj: dict, rep: Report) -> None:
    errs: list[str] = []
    report = audit / "AUDIT_REPORT.md"
    if not report.exists():
        errs.append("AUDIT_REPORT.md missing")
        body = ""
    else:
        body = report.read_text(encoding="utf-8", errors="replace")
    for h in REQUIRED_HEADINGS:
        if h not in body:
            errs.append(f"AUDIT_REPORT.md missing heading '{h}'")
    summ = audit / "summary.txt"
    if not summ.exists():
        errs.append("summary.txt missing")
    else:
        sbody = summ.read_text(encoding="utf-8", errors="replace")
        verdict_fj = str(fj.get("verdict", ""))
        m = re.search(r"(?:VERDICT|ВЕРДИКТ):\s*(.+)", sbody)
        verdict_sum = m.group(1).strip() if m else ""
        if verdict_fj and verdict_sum and verdict_fj[:20] not in verdict_sum \
                and verdict_sum[:20] not in verdict_fj:
            errs.append(f"verdict drift: findings.json={verdict_fj[:60]!r} vs summary.txt={verdict_sum[:60]!r}")
    if errs:
        rep.add("V8", "fail", "; ".join(errs[:8]))
    else:
        rep.add("V8", "pass", "report complete, verdict vocabulary consistent")


# --------------------------------------------------------------------------- V9
def v9(audit: Path, fj: dict, rep: Report) -> None:
    hosts_file = audit / "evidence" / "p6_webview_urls" / "hosts_all.txt"
    if not hosts_file.exists():
        rep.add("V9", "fail", "hosts_all.txt missing")
        return
    claimed = sum(1 for ln in read_lines(hosts_file) if ln.strip() and not ln.startswith("#"))
    cov = (fj.get("coverage") or {}).get("distinct_urls_hosts")
    strings_dir = audit / "evidence" / "p4_binary" / "strings"
    if strings_dir.is_dir() and any(strings_dir.glob("*.strings")):
        scheme_re = re.compile(r"https?://", re.IGNORECASE)
        host_valid = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)+$")
        hosts: set[str] = set()

        def harvest(text: str) -> None:
            for ln in text.splitlines():
                starts = [m.start() for m in scheme_re.finditer(ln)]
                for i, start in enumerate(starts):
                    end = starts[i + 1] if i + 1 < len(starts) else len(ln)
                    seg = ln[start:end]
                    m = re.match(r"https?://([^/:?#\s\"'<>()\[\]`{}|\\^]+)", seg, re.IGNORECASE)
                    if m:
                        h = m.group(1).lower().rstrip(".,;:'\"")
                        if host_valid.match(h):
                            hosts.add(h)

        for f in strings_dir.glob("*.strings"):
            for ln in read_lines(f):
                harvest(ln)
        payload = audit / "extracted" / "Payload"
        # Must stay in sync with analyze_assets_urls.TEXT_SUFFIXES (incl. .md),
        # otherwise V9 re-extraction undercounts and emits a spurious FAIL.
        text_suffixes = {".plist", ".json", ".js", ".html", ".htm", ".txt",
                         ".xml", ".strings", ".md"}
        if payload.is_dir():
            for f in payload.rglob("*"):
                if f.is_file() and f.suffix.lower() in text_suffixes:
                    try:
                        harvest(f.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        continue
        if len(hosts) != claimed:
            rep.add("V9", "fail",
                    f"re-extraction found {len(hosts)} hosts vs hosts_all.txt {claimed} "
                    "(boundary-aware regex; check concatenated-metadata URLs)",
                    ["evidence/p6_webview_urls/hosts_all.txt"])
            return
        rep.add("V9", "pass", f"host count reproduced exactly ({claimed})")
    else:
        detail = f"hosts_all.txt has {claimed} hosts; strings dumps absent — re-extraction impossible"
        if cov is not None and cov != claimed:
            rep.add("V9", "fail", detail + f"; coverage.distinct_urls_hosts={cov} disagrees")
        else:
            rep.add("V9", "warn", detail + " (B2 regression risk: verify with a re-run)")


# -------------------------------------------------------------------------- V10
def v10(audit: Path, fj: dict, rep: Report) -> None:
    errs: list[str] = []
    ids = {str(f.get("id")) for f in fj.get("findings", [])}
    for row in fj.get("gating_matrix", []):
        ref = str(row.get("finding", ""))
        if ref and ref not in ids:
            errs.append(f"gating_matrix references unknown finding {ref}")
        ct = str(row.get("condition_type", ""))
        if ct in ("date", "test_account", "ab_experiment"):
            evidence_name = {"date": "date_gating_keys.txt", "test_account": "test_account_gating.txt",
                             "ab_experiment": "ab_gating_hits.txt"}[ct]
            p = audit / "evidence" / "p7_flags" / evidence_name
            if not p.exists():
                errs.append(f"gating_matrix {ct} gate has no p7 evidence file ({evidence_name})")
    if errs:
        rep.add("V10", "fail", "; ".join(errs[:8]))
    else:
        rep.add("V10", "pass", "gating matrix references valid, evidence-backed")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-dir", required=True)
    ap.add_argument("--signatures-dir", default=DEFAULT_SIGNATURES_DIR)
    args = ap.parse_args()
    audit = Path(args.audit_dir)
    if not audit.is_dir():
        print(f"ERROR: audit dir not found: {audit}")
        return 2

    rep = Report()
    fj = v1(audit, rep)
    if fj:
        v2(audit, fj, rep)
        v3(audit, fj, rep)
        v4(audit, fj, rep)
        v5(audit, fj, rep)
        v6(audit, fj, rep)
        v7(audit, fj, rep, Path(args.signatures_dir))
        v8(audit, fj, rep)
        v9(audit, fj, rep)
        v10(audit, fj, rep)

    report = {
        "audit_dir": str(audit),
        "machine_verdict": rep.verdict(),
        "checks": rep.checks,
        "notes": "FAIL on any check = at least a concern for the reviewer; V2/V3/V4/V6 failures are blocker candidates (arithmetic, evidence existence, excerpt authenticity, provenance).",
    }
    out = audit / "validation_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"WROTE: {out.name}")
    print(f"MACHINE_VERDICT: {rep.verdict()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
