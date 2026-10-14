---
name: ipa-audit
description: 'Internal IPA audit toolkit for the audit_IPA agent. Python scripts for the 9-phase App Store 2.3.1(a) audit: extract_ipa (unpack+SHA+provenance+p1 structure), parse_info_plist (entitlements/profile/plist packs), analyze_frameworks, extract_strings (Mach-O + Unity IL2CPP global-metadata), normalize_packs + grep_packs (14 signature packs, triage_input.json, context mode), analyze_assets_urls (assets/l10n/URLs with boundary-aware host recovery), detect_feature_flags (date/test-account/AB gating with negative-result files), generate_findings (schema-validated, script-computed counts), generate_report (AUDIT_REPORT.md + summary.txt), check_findings (CI gate). Hidden from all other agents by skill permissions.'
---

# IPA Audit Toolkit

Internal toolkit loaded by the `audit_IPA` subagent. Not advertised to other
agents. Scripts are pure Python 3.10+ (optional: `lief`, `pyyaml`, `lzfse`) —
zero POSIX dependencies (D1). Scripts do ALL arithmetic; the LLM does only
judgement (D4).

Base paths used throughout — all relative to the PROJECT ROOT (the agent's
working directory); no absolute paths anywhere:
- scripts: `.opencode/skills/ipa-audit/scripts/`
- data: `.opencode/skills/ipa-audit/data/`
- signature packs (canonical, project-owned): `tools/signatures`
  (override with `--signatures-dir`, or globally via `$env:IPA_SIGNATURES_DIR`;
  scripts fall back to `<project_root>/tools/signatures` when neither is set)
- audits root: `audits/`

Resolve them at run time instead of hardcoding:

```powershell
$ROOT = (Get-Location).Path            # project root
$SKILL = Join-Path $ROOT ".opencode\skills\ipa-audit"
$S = Join-Path $SKILL "scripts"
$SIG = if ($env:IPA_SIGNATURES_DIR) { $env:IPA_SIGNATURES_DIR } else { Join-Path $ROOT "tools\signatures" }
$AUD = Join-Path $ROOT "audits\<AppName>_<ver>_<build>_<date>"
```

## Usage

```powershell
# $ROOT / $SKILL / $S / $SIG / $AUD are defined above (project-root relative)

# Phase 0-1 (creates the audit dir; refuses to overwrite; cryptid!=0 -> exit 3)
python "$S\extract_ipa.py" --ipa "<path to .ipa>" --audits-root "$ROOT\audits" --provenance-source "<where the IPA came from>"

# Phase 0.2c (normalize the 14 packs; byte-identical to the canonical files)
python "$S\normalize_packs.py" --signatures-dir "$SIG"

# Phase 2
python "$S\parse_info_plist.py" --audit-dir $AUD
# Phase 3
python "$S\analyze_frameworks.py" --audit-dir $AUD
# Phase 4 (Mach-O + Unity IL2CPP global-metadata.dat, auto-detected)
python "$S\extract_strings.py" --audit-dir $AUD
# Phase 4.4 greps (10 binary packs; also --target dll-strings|js for Phase 8)
python "$S\grep_packs.py" --audit-dir $AUD
# Experimental/Phase-8 target runs MUST NOT clobber canonical hits -> sandbox:
python "$S\grep_packs.py" --audit-dir $AUD --target dll-strings --hits-subdir phase8-dll
# (grep_packs refuses non-strings-target overwrites of canonical hits without --force)
# Phases 5-6
python "$S\analyze_assets_urls.py" --audit-dir $AUD
# Phase 7
python "$S\detect_feature_flags.py" --audit-dir $AUD [--declarations <dir>]
# Phase 9.2 (after you wrote triage.json -- see the contract below)
python "$S\generate_findings.py" --audit-dir $AUD --triage "$AUD\triage.json"
# Phase 9.4 (the ONLY thing that writes AUDIT_REPORT.md / summary.txt)
python "$S\generate_report.py" --audit-dir $AUD
# CI gate (informational for you; exit 1 = red)
python "$S\check_findings.py" --audit-dir $AUD
```

Context windows (the only sanctioned way to inspect surroundings of a hit):

```powershell
python "$S\grep_packs.py" --audit-dir $AUD --context --context-pack payment --context-pattern "QN[A-Z]" --context-lines 20
```

## Phase → script map

| Script | Phase(s) | Key outputs |
|---|---|---|
| `normalize_packs.py` | 0.2c | `*.clean.txt`, `*.status.tsv` |
| `extract_ipa.py` | 0.3, 1.1-1.6 | audit dir, `original/{App.ipa,SHA256SUMS.txt,PROVENANCE.md}`, `evidence/p1_structure/*` |
| `parse_info_plist.py` | 2.1-2.7 | `p2_plist_entitlements/{info.json,entitlements.json,profile.json,entitlements_hits.txt,plist_key_hits.txt,privacy_manifests.json,infoplist_strings.txt,url_schemes.json}` |
| `analyze_frameworks.py` | 3.1-3.5 | `p3_frameworks/{frameworks_list.txt,linked_dylibs.json,frameworks_<pack>_hits.txt x7,libswift_count.txt}` |
| `extract_strings.py` | 4.1-4.3 (+D7) | `p4_binary/strings/*.strings` (sorted-unique), `classdump/*.objc_classes.txt`, `strings_manifest.json` (relative paths) |
| `grep_packs.py` | 4.4/4.4b/4.4c | `hits/<pack>_hits.txt`, `hits/_summary.json`, `raw_hits.tsv`, `hits/payment_ui_classes.txt`, `hits/nordic_anchored_hits.txt`, `hits/triage_input.json` |
| `analyze_assets_urls.py` | 5.1-5.5, 6.1-6.6 | `p5_assets_l10n/*`, `p6_webview_urls/*` (incl. boundary-aware `hosts_all.txt`) |
| `detect_feature_flags.py` | 7.1-7.9 | `p7_flags/*` (every file exists even at 0 hits) |
| `generate_findings.py` | 9.2 | `findings.json` (schema 2.0, counts computed by script) |
| `generate_report.py` | 9.4 | `AUDIT_REPORT.md`, `summary.txt` |
| `check_findings.py` | 9/CI | stdout gate, exit 1 on confirmed critical/high violations |

## Workflow for the agent (phase gates — do not skip, do not reorder)

1. `extract_ipa.py` → GATE: SHA-256 recorded; cryptid reported; cryptid != 0 →
   STOP and report risk R2 (FairPlay).
2. `normalize_packs.py` (once per signatures dir).
3. `parse_info_plist.py`.
4. `analyze_frameworks.py`.
5. `extract_strings.py` (IL2CPP auto-detected via `Data/Managed/Metadata/global-metadata.dat`).
6. `grep_packs.py`.
7. `analyze_assets_urls.py`.
8. `detect_feature_flags.py`.
9. TRIAGE (your judgement — the core value-add):
   - read `hits/triage_input.json` (compact). NEVER read `hits/*.txt` wholesale
     (files up to 66k lines); use `--context` or targeted reads ≤200 lines.
   - apply the 6-step FP triage (PLAN.md:1078-1103); known traps live in
     `data/fp_traps.json` (mobilepay⊂Square, Tink≠TinkLink, sepa⊂separate,
     swish, Frames, zoom, TrueSDK, identity, vendored iProov deps, ad-networks,
     generic logEvent/Adjust/ALSdk/IS*, mitid⊂commitID, QN*⊂System.Xml).
   - escalation triggers: nordic hits, date/test-account/AB gating hits, appex
     hits, associated-domains, `# lead` patterns → always manual context review.
   - write `triage.json` (contract below).
10. `generate_findings.py` → if exit 3: fix triage.json, rerun.
11. `generate_report.py` → `check_findings.py` (informational).
12. Final message: audit dir, verdict, counts, top-3 findings, artifacts,
    requires_docs_update, unresolved needs_review items.

Rework mode: read the reviewer's fix list, re-run ONLY the affected scripts,
regenerate findings + report.

## triage.json contract (agent → script)

```json
{
  "headline": "...", "verdict": "BLOCKED (N critical, M high)",
  "findings": [{
    "candidate_ref": "payment#3",
    "severity": "critical", "confidence": "confirmed",
    "category": "payment_sdk", "pattern": "...", "phase": "4.4",
    "evidence": { "file": "evidence/p7_flags/flag_candidates.txt",
                  "excerpt": "<verbatim <=200 chars from that file>",
                  "context_ref": "evidence/p4_binary/hits/payment_hits.txt:42" },
    "declared_in_app_description": false,
    "reachable": "yes", "gated_by": ["region", "feature_flag"],
    "verdict": "violation", "remediation": "remove_sdk:YooMoney",
    "notes": "triage steps with file:line citations"
  }],
  "false_positives": [{ "pack": "kyc_banking.txt", "explanation": "..." }],
  "gating_matrix": [{ "condition_type": "region", "gate": "...",
                      "gated_screen": "...", "finding": "<id or candidate_ref>", "severity": "..." }],
  "analytics_inventory": [{ "sdk": "AppLovin", "markers": "...",
                            "purchase_tracking": "yes — N lines",
                            "declared": "not declared", "decision": "document (A3-1)" }],
  "phases_completed": [0,1,2,3,4,5,6,7,9],
  "phases_skipped": { "8": "reason" },
  "requires_docs_update": false
}
```

Field rules enforced by generate_findings.py:
- `reachable` is the STRING `"yes"|"no"|"unknown"` (never bool/null);
- `gated_by` is always an ARRAY (values: none/region/date/test_account/ab_experiment/server_response/feature_flag);
- `phase` is a STRING like `"4.4"`;
- `confidence: "confirmed"` requires `reachable != "unknown"` OR non-empty notes;
- `evidence.excerpt` must appear VERBATIM (whitespace-normalized) in `evidence.file`;
- IDs are assigned by the script per the category prefix map (PAY-001, KYC-001, ...);
  `false_positives[].hits` are computed from `_summary.json` / hit headers — never hand-write them;
- `gating_matrix[].finding` may hold either an assigned id (`PAY-001`) or the row's
  `candidate_ref` (`feature_flags#yoomoneyEnabled`) — the script remaps candidate refs to
  assigned ids (validate_audit V10 requires real ids); an unresolvable reference exits 3;
- `analytics_inventory` (optional, §6b): structured SDK rows; when omitted, generate_report.py
  fills §6b deterministically from binary names + analytics_purchase_hits.txt markers;
- severity/category defaults come from `data/pack_defaults.json` when omitted.

## Gates enforced by the scripts (do not fight them)

| Gate | Script | Exit |
|---|---|---|
| not a ZIP/IPA / missing file | extract_ipa | 2 |
| LZFSE member without `pip install lzfse` (R1) | extract_ipa | 2 |
| audit dir already exists (use --force) | extract_ipa | 2 |
| cryptid != 0 (FairPlay, R2) | extract_ipa | 3 |
| BAD ERE in a signature pack | normalize_packs, grep_packs | 3 |
| triage.json schema/semantic violation | generate_findings | 3 |
| counts do not reconcile | generate_findings | 3 |
| gating_matrix references an unknown finding/candidate | generate_findings | 3 |
| global analytics suppression | grep_packs | 3 |
| overwrite canonical hits from a non-strings `--target` without `--force` | grep_packs | 2 |
| confirmed critical/high violation at CI | check_findings | 1 |

## Hard rules

- Evidence-first: every claim cites an evidence/ file with a line ref. No
  evidence file → no finding.
- All work inside `audits/<build>/` — NEVER in %TEMP%. Artifact paths relative.
- NEVER create/modify .md via edit/write tools (plugin-banned).
  AUDIT_REPORT.md, summary.txt, PROVENANCE.md are script-written only.
- `original/` is read-only after Phase 0.
- IMPORTANT: never overwrite canonical hit files
  (`evidence/p4_binary/hits/*.txt`, `_summary.json`, `raw_hits.tsv`,
  `triage_input.json`) without an explicit `--force` flag on grep_packs.py.
  Experiments with other targets go to a separate output directory:
  `--hits-subdir <name>` → `evidence/p4_binary/hits/<name>/`.
- Do not invent counts/hashes/excerpts — scripts compute them; you classify.
- A failed phase goes to `phases_skipped` with the error; never silently dropped.

## Known limitations

- Phase 8 network checks (AASA/associated-domains fetch, server-hosted
  payment-form content) require live network access and are skipped in static
  analysis mode — `evidence/p6_webview_urls/aasa_check.txt` records SKIPPED.
  These checks must be performed manually or in a CI environment with network
  access; record the skip reason in `phases_skipped` (never drop it silently).
- Assets.car name extraction is best-effort (no `ipsw`/assetutil on this box):
  `assets.json` carries `method: best-effort, complete: false` when the
  string-table scan is used (plan risks R5/R8).
- MobSF/Docker cross-check is unavailable (no Docker); the report CAVEAT
  section states this.
