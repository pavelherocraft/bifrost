---
name: ipa-audit-review
description: 'Internal IPA audit review toolkit for the review_audit_IPA agent. validate_audit.py runs the V1-V10 machine checks (schema, counts reconciliation vs _summary.json, evidence existence, verbatim excerpt check, path hygiene, provenance chain, pack completeness, report completeness incl. section 6b, boundary-aware host re-extraction, gating-matrix references) and writes validation_report.json. Independent implementation -- shares no code with the ipa-audit skill. Hidden from all other agents by skill permissions.'
---

# IPA Audit Review Toolkit

Internal toolkit loaded by the `review_audit_IPA` subagent. Pure Python 3,
zero dependencies, zero shared code with the executor skill (a reviewer that
imports the executor's library inherits its bugs — D-check).

## Usage

All paths are relative to the PROJECT ROOT (the agent's working directory):

```powershell
$ROOT = (Get-Location).Path
$V = Join-Path $ROOT ".opencode\skills\ipa-audit-review\scripts\validate_audit.py"
$SIG = if ($env:IPA_SIGNATURES_DIR) { $env:IPA_SIGNATURES_DIR } else { Join-Path $ROOT "tools\signatures" }

python $V --audit-dir "<path to audits/<build>>" --signatures-dir $SIG
```

(`--signatures-dir` defaults to `<project_root>/tools/signatures` inside the script.)

Output:
- `<audit_dir>/validation_report.json` — machine-readable checks
- stdout: `CHECK V<n>: PASS|FAIL|WARN — detail` lines + `MACHINE_VERDICT: PASS|FAIL`
- exit code is always 0 (the reviewer decides), 2 only on usage errors.

## The checks (V1-V10)

| # | Check | Blocker candidate? |
|---|---|---|
| V1 | findings.json parses + validates against findings_schema.json (enums, id prefixes, reachable/gated_by/phase types) | yes (schema drift = unreviewable) |
| V2 | counts reconcile: `raw_hits_total == sum(_summary.json)`; `raw_false_positive_hits == sum(false_positives[].hits)`; per-severity summary recount; coverage vs strings_manifest | **yes** (reference audit B1: 3,433 vs 4,612 vs 9,955) |
| V3 | every evidence.file / context_ref exists (context_ref may carry `:line`), relative, non-empty; no Temp/absolute paths in artifacts | **yes** (B3 Temp paths) |
| V4 | every evidence.excerpt (≥40 chars) appears verbatim (whitespace-normalized) in the referenced file | **yes** (CRIT-001 excerpt defect) |
| V5 | phases_completed ↔ non-empty evidence dirs; skipped phases have reasons; Phase 7.6 has an explicit negative-result file | concern (B3) |
| V6 | original/SHA256SUMS.txt is UTF-8, `^[0-9a-fA-F]{64}  ` format, hash re-verified against App.ipa; PROVENANCE.md exists | **yes** (UTF-16 defect, missing provenance) |
| V7 | 14 packs normalized; _summary covers the 10 binary packs; no confirmed+reachable:unknown finding without notes | concern (HIGH-002 mislabel class) |
| V8 | AUDIT_REPORT.md has all template headings incl. mandatory §6b and §8; sign-off checklist; verdict consistent across findings.json / report / summary.txt | concern |
| V9 | hosts re-extraction (boundary-aware, concatenated-URL split) reproduces hosts_all.txt exactly | concern (B2) |
| V10 | gating_matrix references existing finding ids; date/test_account/ab gates have p7 evidence files | concern |

Interpretation for REVIEW.md: every FAIL is at least a `concern`;
V2/V3/V4/V6 failures are `blocker` candidates. WARN items (e.g. V9 without
strings dumps) go to §D as required actions.

## REVIEW.md skeleton (you are .md-whitelisted; write incrementally — 32K output cap)

```
# REVIEW — <verdict: PASS | NEEDS_REVISION (severity: X) | FAIL>
## A. What validates correctly (table)
## B. Issues found (### B<n> — severity — title; each with file:line + what is wrong + required action)
## C. Answers to the 9 validation criteria (one line each)
## D. Required actions before sign-off (numbered, actionable, addressed to audit_IPA rework mode)
```

## Rules

- Run validate_audit.py FIRST, before reading anything else.
- NEVER modify findings.json / AUDIT_REPORT.md / summary.txt / evidence —
  report, don't fix. Corrections go into §D; the orchestrator re-invokes
  audit_IPA with the fix list.
- Do not re-run the audit; validate artifacts as delivered (V9 re-extraction
  is the only recomputation allowed).
- Every issue: severity + file:line + concrete fix. Max 4 non-blocker issues
  per run; blockers unlimited. No "lgtm"-style phrasing.
