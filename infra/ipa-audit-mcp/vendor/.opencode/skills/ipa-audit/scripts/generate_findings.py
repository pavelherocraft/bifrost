#!/usr/bin/env python3
"""generate_findings.py -- Phase 9.2: triage.json + evidence -> findings.json.

Design contract (D4): the LLM supplies ONLY judgement (verdicts, severities,
confidence, categories, narrative); this script computes every number:

  counts.raw_hits_total          <- sum(evidence/p4_binary/hits/_summary.json)
  false_positives[].hits         <- _summary.json (whole pack) or counted from
                                    the hits file headers (pattern-qualified)
  counts.raw_false_positive_hits <- sum(false_positives[].hits)
  summary.<severity>             <- per-severity counts of findings with
                                    verdict != false_positive
  coverage.*                     <- strings_manifest / classdump / hosts_all /
                                    assets.json / _summary packs count

Schema validation (D6) is fail-fast: any violation -> ERROR + exit 3.
IDs are assigned here per the category prefix map (PAY-001, KYC-001, ...).
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
    lief_version, ok, read_lines, warn, write_json, wrote,
)

DATA_DIR = SCRIPT_DIR.parent / "data"
SEVERITIES = ["critical", "high", "medium", "low", "info"]

# Load the canonical schema once; every enum/pattern/required-field below is
# taken from it so the script cannot drift from findings_schema.json (D6).
SCHEMA = json.loads((DATA_DIR / "findings_schema.json").read_text(encoding="utf-8"))
CATEGORY_PREFIX = SCHEMA["_custom_rules"]["id_prefix_by_category"]
_FINDING_ITEM = SCHEMA["properties"]["findings"]["items"]
FINDING_REQUIRED = _FINDING_ITEM["required"]
_FINDING_PROPS = _FINDING_ITEM["properties"]
CONFIDENCE_ENUM = _FINDING_PROPS["confidence"]["enum"]
CATEGORY_ENUM = _FINDING_PROPS["category"]["enum"]
VERDICT_ENUM = _FINDING_PROPS["verdict"]["enum"]
REACHABLE_ENUM = _FINDING_PROPS["reachable"]["enum"]
GATED_BY_ENUM = _FINDING_PROPS["gated_by"]["items"]["enum"]
PHASE_PATTERN = _FINDING_PROPS["phase"]["pattern"]
ID_PATTERN = _FINDING_PROPS["id"]["pattern"]
DEFAULT_CONFIDENCE = "lead"   # safest schema-valid enum when the LLM omits it
MIN_EXCERPT_LEN = 40          # schema _custom_rules.semantic / validate_audit V4


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def collect_build(audit: Path) -> dict:
    info_p = audit / "evidence" / "p2_plist_entitlements" / "info.json"
    info = json.loads(info_p.read_text(encoding="utf-8")) if info_p.exists() else {}
    sha = ""
    sums = audit / "original" / "SHA256SUMS.txt"
    if sums.exists():
        first = read_lines(sums)[0] if read_lines(sums) else ""
        m = re.match(r"^([0-9a-fA-F]{64})\s", first)
        sha = m.group(1) if m else ""
    cryptid = 0
    bt = audit / "evidence" / "p1_structure" / "build_type.txt"
    if bt.exists():
        for line in read_lines(bt):
            if line.startswith("cryptid:"):
                cryptid = int(line.split(":", 1)[1].strip())
    hybrid = "none"
    hm = audit / "evidence" / "p1_structure" / "hybrid_markers.json"
    if hm.exists():
        hybrid = json.loads(hm.read_text(encoding="utf-8")).get("hybrid", "none")
    return {
        "app": str(info.get("CFBundleDisplayName") or info.get("CFBundleName") or "unknown"),
        "bundle_id": str(info.get("CFBundleIdentifier") or ""),
        "version": str(info.get("CFBundleShortVersionString") or "0"),
        "build_number": str(info.get("CFBundleVersion") or "0"),
        "ipa_sha256": sha,
        "cryptid": cryptid,
        "hybrid": hybrid,
        "audit_date": _dt.date.today().isoformat(),
        "auditor": "audit_IPA",
        "tools": {
            "python": sys.version.split()[0],
            "lief": lief_version(),
            "ipsw": None,
            "mobsf": None,
        },
    }


def collect_coverage(audit: Path) -> dict:
    p4 = audit / "evidence" / "p4_binary"
    manifest = {}
    mp = p4 / "strings_manifest.json"
    if mp.exists():
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    strings_total = sum(v.get("n_strings", 0) for v in manifest.values() if isinstance(v, dict))
    objc_total = sum(v.get("objc_classes", 0) for v in manifest.values() if isinstance(v, dict))
    il2cpp = any(v.get("il2cpp") for v in manifest.values() if isinstance(v, dict))
    hosts = 0
    hp = audit / "evidence" / "p6_webview_urls" / "hosts_all.txt"
    if hp.exists():
        hosts = sum(1 for ln in read_lines(hp) if ln.strip() and not ln.startswith("#"))
    assets_names = 0
    aj = audit / "evidence" / "p5_assets_l10n" / "assets.json"
    if aj.exists():
        data = json.loads(aj.read_text(encoding="utf-8"))
        s = data.get("_summary")
        if isinstance(s, dict):
            assets_names = int(s.get("names_total", 0))
    packs = 0
    sm = p4 / "hits" / "_summary.json"
    if sm.exists():
        packs = len(json.loads(sm.read_text(encoding="utf-8")))
    return {
        "mach_o_targets_analysed": sum(1 for v in manifest.values()
                                       if isinstance(v, dict) and not v.get("il2cpp")),
        "strings_extracted": strings_total,
        "signature_packs": packs,
        "distinct_urls_hosts": hosts,
        "objc_classes_dumped": objc_total,
        "assets_car_names": assets_names,
        "il2cpp_metadata_scanned": il2cpp,
    }


def fp_hits(audit: Path, fp: dict, summary: dict) -> int:
    pack = str(fp.get("pack", ""))
    m = re.match(r"^([a-z_0-9]+)\.txt$", pack)
    key = m.group(1) if m else pack
    if "pattern" in fp and fp["pattern"]:
        hp = audit / "evidence" / "p4_binary" / "hits" / f"{key}_hits.txt"
        if hp.exists():
            n = 0
            for ln in read_lines(hp):
                if ln.startswith("=== ") and f"pattern={fp['pattern']}" in ln:
                    n += 1
            return n
        return 0
    return int(summary.get(key, 0))


def validate_semantics(findings: list[dict], audit: Path) -> list[str]:
    errs: list[str] = []
    ids: set[str] = set()
    for f in findings:
        fid = f.get("id", "")
        if fid in ids:
            errs.append(f"duplicate id {fid}")
        ids.add(fid)
        # --- schema-required fields (findings_schema.json items.required) -----
        for req in FINDING_REQUIRED:
            if req not in f:
                errs.append(f"{fid}: missing required field '{req}'")
        # --- enum membership (schema is the source of truth) ------------------
        if f.get("severity") not in SEVERITIES:
            errs.append(f"{fid}: severity must be one of {SEVERITIES} (got {f.get('severity')!r})")
        if f.get("confidence") not in CONFIDENCE_ENUM:
            errs.append(f"{fid}: confidence must be one of {CONFIDENCE_ENUM} (got {f.get('confidence')!r})")
        if f.get("category") not in CATEGORY_ENUM:
            errs.append(f"{fid}: category must be one of the schema enum (got {f.get('category')!r})")
        if f.get("verdict") not in VERDICT_ENUM:
            errs.append(f"{fid}: verdict must be one of {VERDICT_ENUM} (got {f.get('verdict')!r})")
        if not re.match(ID_PATTERN, str(fid)):
            errs.append(f"{fid}: id does not match schema pattern {ID_PATTERN}")
        cat = f.get("category", "")
        expected = CATEGORY_PREFIX.get(cat)
        if expected and not str(fid).startswith(expected + "-"):
            errs.append(f"{fid}: id prefix does not match category {cat} (expected {expected}-*)")
        if not isinstance(f.get("phase", ""), str):
            errs.append(f"{fid}: phase must be a string like '4.4'")
        elif not re.match(PHASE_PATTERN, f["phase"]):
            errs.append(f"{fid}: phase {f['phase']!r} does not match schema pattern {PHASE_PATTERN}")
        if f.get("reachable") not in REACHABLE_ENUM:
            errs.append(f"{fid}: reachable must be one of {REACHABLE_ENUM} (got {f.get('reachable')!r})")
        # writer/reviewer contract (validate_audit V1 lines 149-152): pattern is
        # always required and non-empty; remediation is required non-empty
        # except for false_positive findings (FP needs no remediation steps).
        if not isinstance(f.get("pattern"), str) or not f.get("pattern"):
            errs.append(f"{fid}: pattern must be a non-empty string")
        if f.get("verdict") != "false_positive" and \
                (not isinstance(f.get("remediation"), str) or not f.get("remediation").strip()):
            errs.append(f"{fid}: remediation must be a non-empty string (verdict != false_positive)")
        if not isinstance(f.get("gated_by"), list):
            errs.append(f"{fid}: gated_by must be an array")
        else:
            bad_gate = [g for g in f["gated_by"] if g not in GATED_BY_ENUM]
            if bad_gate:
                errs.append(f"{fid}: gated_by has values outside the schema enum: {bad_gate}")
        if f.get("confidence") == "confirmed" and f.get("reachable") == "unknown" \
                and not (f.get("notes") or "").strip():
            errs.append(f"{fid}: confidence confirmed requires reachable != unknown OR notes justification")
        ev = f.get("evidence") or {}
        ef = ev.get("file", "")
        if not ef or re.match(r"(?i)^\w:[\\/]", ef) or re.search(r"(?i)Temp", ef):
            errs.append(f"{fid}: evidence.file must be a non-empty relative Temp-free path")
        # schema _custom_rules.semantic (rule 3) + validate_audit V3: every
        # evidence.context_ref must be relative and Temp-free (empty allowed,
        # matching V3 which only inspects non-empty values).
        cref = str(ev.get("context_ref", "") or "")
        if cref and (re.match(r"(?i)^\w:[\\/]", cref) or re.search(r"(?i)Temp", cref)):
            errs.append(f"{fid}: evidence.context_ref must be a relative Temp-free path")
        # schema _custom_rules.semantic + validate_audit V4: the evidence file
        # must exist for EVERY finding (regardless of excerpt length), while
        # the verbatim-substring check only applies to excerpts >=40 chars
        # (short excerpts are allowed by the contract and must NOT hard-fail).
        excerpt = norm_ws(str(ev.get("excerpt", "")))
        target = audit / ef
        if ef:
            if not target.exists():
                errs.append(f"{fid}: evidence file does not exist: {ef}")
            elif len(excerpt) >= MIN_EXCERPT_LEN:
                body = norm_ws(target.read_text(encoding="utf-8", errors="replace"))
                if excerpt not in body:
                    errs.append(f"{fid}: evidence.excerpt not found verbatim in {ef}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-dir", required=True)
    ap.add_argument("--triage", required=True, help="triage.json written by the agent")
    args = ap.parse_args()

    audit = Path(args.audit_dir)
    triage_path = Path(args.triage)
    if not triage_path.is_file():
        print(f"ERROR: triage file not found: {triage_path}")
        return 2
    triage = json.loads(triage_path.read_text(encoding="utf-8"))

    summary_path = audit / "evidence" / "p4_binary" / "hits" / "_summary.json"
    if not summary_path.exists():
        print(f"ERROR: {summary_path} missing -- run grep_packs.py first")
        return 2
    pack_summary = json.loads(summary_path.read_text(encoding="utf-8"))

    # --- findings: defaults, ID assignment -----------------------------------
    defaults = json.loads((DATA_DIR / "pack_defaults.json").read_text(encoding="utf-8"))["packs"]
    findings = triage.get("findings", [])
    if not isinstance(findings, list):
        print(f"ERROR: schema violation: triage.findings must be an array (got {type(findings).__name__})")
        print("ERROR: fix triage.json and re-run (exit 3)")
        return 3
    counters: dict[str, int] = {}
    for f in findings:
        if not isinstance(f, dict):
            print(f"ERROR: schema violation: each finding must be an object (got {type(f).__name__})")
            print("ERROR: fix triage.json and re-run (exit 3)")
            return 3
        cat = f.get("category") or ""
        if not cat and f.get("pack") in defaults:
            cat = defaults[f["pack"]].get("category") or ""
            if cat:
                f["category"] = cat
        if not cat:
            # pack_defaults.generic_payment_words carries category "" by design
            cat = "payment_sdk"
            f["category"] = cat
        # schema-required fields the LLM may omit -> pack default, else safe enum.
        # severity is never silently invented: if neither supplied nor defaultable
        # it is a schema violation (handled in validate_semantics, no KeyError).
        for k in ("severity", "verdict"):
            if not f.get(k) and f.get("pack") in defaults:
                f[k] = defaults[f["pack"]].get(k, "")
        if not f.get("confidence"):
            f["confidence"] = DEFAULT_CONFIDENCE
        if not f.get("pattern"):
            f["pattern"] = str(f.get("candidate_ref") or f.get("pack") or f.get("id") or "")
        prefix = CATEGORY_PREFIX.get(cat, "PAY")
        counters[prefix] = counters.get(prefix, 0) + 1
        if not f.get("id"):
            f["id"] = f"{prefix}-{counters[prefix]:03d}"
        f.setdefault("phase", "4.4")
        f["phase"] = str(f["phase"])
        f.setdefault("declared_in_app_description", None)
        f.setdefault("reachable", "unknown")
        f.setdefault("gated_by", ["none"] if not f.get("gated_by") else f["gated_by"])
        f.setdefault("verdict", "needs_review")
        f.setdefault("remediation", "")
        f.setdefault("notes", "")
        f.setdefault("resolved_in_build", None)
        f["evidence"] = f.get("evidence") or {}
        f["evidence"].setdefault("file", "")
        f["evidence"].setdefault("excerpt", "")
        f["evidence"].setdefault("context_ref", "")
        cref = str(f["evidence"].get("context_ref") or "")
        m_sep = re.search(r"[ (]", cref)
        if m_sep:
            cpath = cref[:m_sep.start()]
            annotation = cref[m_sep.start():].strip()
            parenthesized = (len(annotation) > 1 and annotation.startswith("(")
                             and annotation.endswith(")"))
            if parenthesized:
                annotation = annotation[1:-1].strip()
            if annotation and (parenthesized or re.search(r":\d+$", cpath)):
                f["evidence"]["context_ref"] = cpath
                existing = str(f.get("notes") or "").strip()
                ctx = f"Контекст: {annotation}"
                f["notes"] = f"{existing} {ctx}".strip() if existing else ctx

    # --- gating_matrix: remap candidate_ref -> assigned finding id (V10) --------
    # triage.json rows may reference findings by their candidate_ref (e.g.
    # "feature_flags#yoomoneyEnabled"); validate_audit V10 requires REAL ids
    # (FLAG-001, ...). Build the ref->id map from the triage findings and
    # rewrite every gating row; an unresolvable reference is a hard error.
    refmap: dict[str, str] = {}
    for f in findings:
        cr = str(f.get("candidate_ref") or "").strip()
        if cr:
            refmap[cr] = str(f["id"])
    assigned_ids = {str(f["id"]) for f in findings}
    gating_matrix: list[dict] = []
    gating_errs: list[str] = []
    for row in triage.get("gating_matrix", []):
        if not isinstance(row, dict):
            gating_errs.append(f"gating_matrix row must be an object (got {type(row).__name__})")
            continue
        row = dict(row)
        ref = str(row.get("finding", "") or "").strip()
        if ref and ref not in assigned_ids:
            mapped = refmap.get(ref)
            if mapped:
                row["finding"] = mapped
            else:
                gating_errs.append(
                    f"gating_matrix references unknown finding {ref!r} "
                    f"(not an assigned id and not a triage candidate_ref)")
        gating_matrix.append(row)

    # --- computed numbers -------------------------------------------------------
    summary_counts = {sev: 0 for sev in SEVERITIES}
    fp_findings = 0
    for f in findings:
        if f.get("verdict") == "false_positive":
            fp_findings += 1
        else:
            # .get() keeps this from raising KeyError on a missing/invalid
            # severity -- validate_semantics reports it and we exit 3 instead.
            summary_counts[f.get("severity")] = summary_counts.get(f.get("severity"), 0) + 1
    summary_counts["false_positive"] = fp_findings

    false_positives = []
    for fp in triage.get("false_positives", []):
        hits = fp_hits(audit, fp, pack_summary)
        false_positives.append({"pack": fp.get("pack", ""), "hits": hits,
                                "explanation": fp.get("explanation", "")})
    raw_fp_hits = sum(x["hits"] for x in false_positives)
    raw_hits_total = sum(pack_summary.values())

    build = collect_build(audit)
    coverage = collect_coverage(audit)
    counts = {
        "total_findings": len(findings),
        "raw_hits_total": raw_hits_total,
        "raw_false_positive_hits": raw_fp_hits,
        "false_positive_categories_filtered": len(false_positives),
    }

    result = {
        "schema_version": "2.0",
        "build": build,
        "toolchain": triage.get("toolchain", {
            "planned_unavailable": ["ipsw", "wsl2", "docker/mobsf", "binutils strings/grep"],
            "used_instead": [
                f"Python {sys.version.split()[0]} + LIEF {lief_version()} (plan-listed fallback)",
                "pure-Python strings extractor (binutils replacement)",
                "regex pack engine (grep -E -i -f replacement)",
            ],
        }),
        "phases_completed": triage.get("phases_completed", [0, 1, 2, 3, 4, 5, 6, 7, 9]),
        "phases_skipped": triage.get("phases_skipped", {}),
        "coverage": coverage,
        "summary": summary_counts,
        "counts": counts,
        "verdict": triage.get("verdict", ""),
        "headline": triage.get("headline", ""),
        "findings": findings,
        "false_positives": false_positives,
        "gating_matrix": gating_matrix,
        "analytics_inventory": triage.get("analytics_inventory", []),
        "suppressions_applied": triage.get("suppressions_applied", []),
        "artifacts_created": triage.get("artifacts_created", []),
        "requires_docs_update": bool(triage.get("requires_docs_update", False)),
    }

    # --- gate: arithmetic + semantics (D6) --------------------------------------
    errs = validate_semantics(findings, audit)
    errs.extend(gating_errs)
    if raw_hits_total != sum(pack_summary.values()):
        errs.append("internal: raw_hits_total mismatch")
    if raw_fp_hits != sum(x["hits"] for x in false_positives):
        errs.append("internal: raw_false_positive_hits mismatch")
    recomputed = {sev: 0 for sev in SEVERITIES}
    rfp = 0
    for f in findings:
        if f.get("verdict") == "false_positive":
            rfp += 1
        else:
            recomputed[f.get("severity")] = recomputed.get(f.get("severity"), 0) + 1
    recomputed["false_positive"] = rfp
    if recomputed != summary_counts:
        errs.append("internal: summary counts mismatch")
    if errs:
        for e in errs:
            print(f"ERROR: schema/semantic violation: {e}")
        print("ERROR: fix triage.json and re-run (exit 3)")
        return 3

    write_json(audit / "findings.json", result)
    wrote(audit / "findings.json", audit)
    ok(f"findings.json: {len(findings)} findings, raw_hits={raw_hits_total}, "
       f"fp_hits={raw_fp_hits}, gating_rows={len(gating_matrix)}, verdict={result['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
