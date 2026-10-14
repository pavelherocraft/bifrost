#!/usr/bin/env python3
"""generate_report.py -- Phase 9.4: findings.json -> AUDIT_REPORT.md + summary.txt.

D2: this script is the ONLY thing that writes AUDIT_REPORT.md (the agent never
touches .md with edit/write tools -- the plugin bans it). Narrative prose comes
from findings.json (headline / notes / remediation written by the LLM into
triage.json); all numbers are taken verbatim from the script-computed fields.

Verdict vocabulary is fixed: "READY TO SUBMIT" or "BLOCKED (N critical, M high)".
The report contains all mandatory sections of data/report_template.md,
including section 6b (analytics inventory, mandatory even at 0 violations) and
the section 8 Re-audit placeholder.

Section 6b sources, in merge order (first match wins per SDK):
  1. findings.json "analytics_inventory" (optional structured list the LLM
     may supply in triage.json);
  2. findings with category == analytics_ad;
  3. deterministic evidence scan: known ad/analytics SDK markers vs the
     audit dir binary names + analytics_purchase_hits.txt lines (so the
     inventory is filled even when the triage supplied no list).

Section 7 renders Option A / Option B per finding (verdict violation or
needs_review) from data/pack_defaults.json "remediation_templates".

Markdown table cells escape "|" (GFM cell boundary) -- pattern cells with
alternation regexes would otherwise shred every table (audit 2026-10-13 defect).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from ipa_lib import ok, read_lines, write_text, wrote  # noqa: E402

DATA_DIR = SCRIPT_DIR.parent / "data"
SEV_ORDER = ["critical", "high", "medium", "low", "info"]
FP_SUBSECTIONS = [
    ("(а)", "mobilepay", "mobilepay ⊂ SquareMobilePaymentsSDK"),
    ("(б)", "Tink", "Tink = Google crypto ≠ TinkLink"),
    ("(в)", "sepa", "sepa = separate"),
    ("(г)", "swish", "swish = английское слово"),
    ("(д)", "Frames", "Frames = generic UI"),
    ("(е)", "zoom", "zoom ≠ FaceTec zoOm"),
    ("(ж)", "analytics", "аналитические purchase-события (§4.17 — фиксация с обоснованием)"),
]

# Known ad/analytics SDK markers (case-blind substrings -- adapter class names
# embed them mid-word: "ALVungleMediationAdapter", "OMIDAppodealNodeProcessor",
# so word boundaries must NOT be required; every token is an unambiguous
# product name, safe for plain substring matching).
# Used ONLY to fill the §6b inventory from evidence -- detection, not findings.
AD_SDK_MARKERS: list[tuple[str, list[str]]] = [
    ("AppLovin", ["applovin", "alsdk", "alcore"]),
    ("Meta Audience Network", ["fbaudiencenetwork", "fbadnetwork", "fbadconfig", "fbad"]),
    ("InMobi", ["inmobi"]),
    ("Moloco", ["moloco"]),
    ("Tenjin", ["tenjin"]),
    ("Unity Ads", ["unityads", "unityadvertisement"]),
    ("Vungle", ["vungle", "liftoff"]),
    ("Pangle (TikTok/ByteDance)", ["pangle", "buad", "tiktok"]),
    ("Mintegral", ["mintegral"]),
    ("Yandex (Ads/Metrica)", ["yandex"]),
    ("Bigo Ads", ["bigoads", r"\bbigo\b"]),
    ("inner-active", ["inner-?active"]),
    ("Rayjump", ["rayjump"]),
    ("devtodev", ["devtodev"]),
    ("Appodeal", ["appodeal"]),
    ("IAB Open Measurement (OMSDK)", ["omsdk"]),
    ("Google AdMob/Ads", ["admob", "gadad", "mediation.goog", "gadsettings"]),
    ("ironSource", ["ironsource", "iron_?source"]),
    ("AppsFlyer", ["appsflyer"]),
    ("Adjust", ["adjustsdk", "app.adjust.com", r"\badjust\.com"]),
    ("Firebase Analytics", ["firebaseanalytics"]),
]

# Marker-scan binary-name sources (relative to the audit dir).
INVENTORY_NAME_SOURCES = [
    "evidence/p4_binary/strings_manifest.json",
    "evidence/p3_frameworks/frameworks_list.txt",
]


# ---------------------------------------------------------------------------
# Markdown table-cell helpers (GFM: "|" is the cell boundary; inside a table
# a literal pipe must be escaped as \| EVEN inside code spans).
# ---------------------------------------------------------------------------
def md_text(s: object) -> str:
    """Prose cell: collapse newlines, escape pipes (no code span)."""
    return str(s).replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def md_code(s: object, limit: int = 0) -> str:
    """Code-span cell: truncate -> escape pipes -> wrap in backticks."""
    s = str(s).replace("\r", " ").replace("\n", " ").strip()
    if limit and len(s) > limit:
        s = s[:limit - 3].rstrip() + "..."
    s = s.replace("|", "\\|")
    return s if "`" in s else f"`{s}`"


def truncate_words(s: object, limit: int) -> str:
    """Word-boundary truncate: never cut mid-word, append '...' when cut."""
    s = str(s)
    if len(s) <= limit:
        return s
    last_space = s.rfind(" ", 0, limit)
    if last_space > 0:
        return s[:last_space] + "..."
    return s[:limit] + "..."


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Russian pluralization: 1 строка / 2 строки / 5 строк."""
    if n % 100 in range(11, 15):
        return many
    if n % 10 == 1:
        return one
    if n % 10 in (2, 3, 4):
        return few
    return many


def load(audit: Path) -> dict:
    fp = audit / "findings.json"
    if not fp.exists():
        print(f"ERROR: {fp} not found -- run generate_findings.py first")
        raise SystemExit(2)
    return json.loads(fp.read_text(encoding="utf-8"))


def evidence_files(audit: Path) -> list[str]:
    root = audit / "evidence"
    rows = []
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rows.append(str(p.relative_to(audit)).replace("\\", "/"))
    return rows


# ---------------------------------------------------------------------------
# §6b: analytics / ad SDK inventory
# ---------------------------------------------------------------------------
def _binary_names(audit: Path) -> list[str]:
    names: list[str] = []
    for rel in INVENTORY_NAME_SOURCES:
        p = audit / rel
        if not p.exists():
            continue
        if p.suffix == ".json":
            try:
                names += [str(k) for k in json.loads(p.read_text(encoding="utf-8")).keys()]
            except (json.JSONDecodeError, OSError):
                pass
        else:
            # frameworks_list.txt rows look like "Name.framework  (dir, -) [app]" --
            # keep only the framework name token
            names += [ln.split()[0] for ln in read_lines(p) if ln.strip()]
    cd = audit / "evidence" / "p4_binary" / "classdump"
    if cd.is_dir():
        names += [f.name.removesuffix(".objc_classes.txt") for f in cd.glob("*.objc_classes.txt")]
    # dedupe case-insensitively, keep the shortest (cleanest) spelling
    uniq: dict[str, str] = {}
    for n in names:
        key = n.lower()
        if key not in uniq or len(n) < len(uniq[key]):
            uniq[key] = n
    return list(uniq.keys())


def detect_ad_sdks(audit: Path) -> list[dict]:
    """Deterministic §6b fallback: marker scan over binary names + the
    analytics_purchase hits file. Returns [] when the evidence is absent."""
    names = _binary_names(audit)
    hits_p = audit / "evidence" / "p4_binary" / "hits" / "analytics_purchase_hits.txt"
    hit_lines: list[str] = []
    if hits_p.exists():
        hit_lines = [ln.lower() for ln in read_lines(hits_p)]
    if not names and not hit_lines:
        return []
    rows: list[dict] = []
    for sdk, toks in AD_SDK_MARKERS:
        rxs = [re.compile(t, re.IGNORECASE) for t in toks]
        n_lines = sum(1 for ln in hit_lines if any(rx.search(ln) for rx in rxs))
        bins = sorted({nm for nm in names if any(rx.search(nm) for rx in rxs)})
        if n_lines == 0 and not bins:
            continue
        rows.append({
            "sdk": sdk,
            "markers": (f"`evidence/p4_binary/hits/analytics_purchase_hits.txt`: "
                        f"{n_lines} {plural_ru(n_lines, 'строка', 'строки', 'строк')} hit'ов"
                        if n_lines else "") +
                       (("; " if n_lines and bins else "") + f"выделенный бинарник: {', '.join(bins)}"
                        if bins else ""),
            "purchase": (f"да — {n_lines} {plural_ru(n_lines, 'строка', 'строки', 'строк')} purchase-событий"
                         if n_lines else "purchase-события не найдены"),
            "decision": "задокументировать в App Description / Notes for Review (Вариант A3-1)"
                        if n_lines else "задокументировать (только аналитика, purchase-события не найдены)",
        })
    return rows


def _declared_cell(fj: dict) -> str:
    vals = [f.get("declared_in_app_description") for f in fj.get("findings", [])]
    if any(v is None for v in vals):
        return "неизвестно — декларации не предоставлены"
    if any(v is False for v in vals):
        return "не задекларировано"
    return "задекларировано"


def build_analytics_inventory(audit: Path, fj: dict) -> str:
    # The template already renders the §6b table header -- data rows only.
    anl_rows: list[str] = []
    seen: set[str] = set()
    n_rows = 0

    # 1. structured inventory supplied by the LLM in triage.json
    for x in fj.get("analytics_inventory") or []:
        if not isinstance(x, dict):
            continue
        sdk = str(x.get("sdk", "")).strip()
        if not sdk:
            continue
        seen.add(sdk.lower())
        anl_rows.append(f"| {md_text(sdk)} | {md_text(x.get('markers', ''))} | "
                        f"{md_text(x.get('purchase_tracking', ''))} | "
                        f"{md_text(x.get('declared', ''))} | {md_text(x.get('decision', ''))} |")
        n_rows += 1

    # 2. analytics_ad findings
    for f in fj.get("findings", []):
        if f.get("category") != "analytics_ad":
            continue
        seen.add(str(f.get("pattern", "")).lower())
        anl_rows.append(f"| {md_code(f['pattern'], 40)} | "
                        f"{md_code((f.get('evidence') or {}).get('file', ''), 60)} | "
                        f"см. notes {f['id']} | {md_text(f.get('declared_in_app_description'))} | "
                        f"{md_text(f.get('remediation', '')[:80])} |")
        n_rows += 1

    # 3. deterministic evidence scan
    for s in detect_ad_sdks(audit):
        if s["sdk"].lower() in seen:
            continue
        anl_rows.append(f"| {md_text(s['sdk'])} | {md_text(s['markers'])} | "
                        f"{md_text(s['purchase'])} | {_declared_cell(fj)} | {md_text(s['decision'])} |")
        n_rows += 1

    if n_rows == 0:
        anl_rows.append("| (аналитические/рекламные SDK не обнаружены или 0 violations) | — | — | — | — |")
        return "\n".join(anl_rows)

    summary_pack = ""
    sm = audit / "evidence" / "p4_binary" / "hits" / "_summary.json"
    if sm.exists():
        try:
            summary_pack = f"; analytics_purchase pack raw hits: {json.loads(sm.read_text(encoding='utf-8')).get('analytics_purchase', 'n/a')}"
        except (json.JSONDecodeError, OSError):
            pass
    anl_rows.append("")
    anl_rows.append(f"Инвентаризация: обнаружено {n_rows} аналитических/рекламных SDK по именам бинарников + "
                    f"`evidence/p4_binary/hits/analytics_purchase_hits.txt`{summary_pack}. "
                    f"Согласно PLAN.md §4.17 каждый аналитический SDK фиксируется здесь, даже если его "
                    f"hits оттриажены как ложные срабатывания.")
    return "\n".join(anl_rows)


# ---------------------------------------------------------------------------
# §7: remediation plan (Option A / Option B per finding)
# ---------------------------------------------------------------------------
def build_remediation(audit: Path, fj: dict) -> str:
    defaults = json.loads((DATA_DIR / "pack_defaults.json").read_text(encoding="utf-8"))
    templates = defaults.get("remediation_templates", {})
    fallback = templates.get("_default", {"option_a": "Полностью удалить функциональность по слоям и пересобрать проект, затем повторить аудит.",
                                          "option_b": ""})
    order = {s: i for i, s in enumerate(SEV_ORDER)}
    rows = [f for f in fj.get("findings", [])
            if f.get("verdict") in ("violation", "needs_review")]
    rows.sort(key=lambda f: (order.get(f.get("severity"), 99), str(f.get("id"))))
    if not rows:
        return "- (нет violations / needs_review — remediation не требуется)"
    blocks: list[str] = []
    for f in rows:
        tpl = templates.get(str(f.get("category", "")), fallback)
        ev_file = (f.get("evidence") or {}).get("file", "")
        blocks += [
            f"### {f['id']} — {f.get('category')} ({f['severity']}, {f['verdict']})",
            f"- Паттерн: {md_code(f.get('pattern', ''), 80)}",
            f"- Доказательства: {md_code(ev_file, 80)} (фаза {f.get('phase')}); "
            f"достижимо: {f.get('reachable')}; управляется: {', '.join(f.get('gated_by') or [])}",
            f"- Конкретное действие (из триажа): {f.get('remediation', '')}",
            f"- **Вариант A (удаление — рекомендуется):** {tpl.get('option_a', fallback['option_a'])}",
        ]
        opt_b = tpl.get("option_b") or fallback.get("option_b", "")
        if opt_b:
            blocks.append(f"- **Вариант B (задекларировать — только если функциональность нужна продукту):** {opt_b}")
        blocks += [
            f"- Гейт повторного аудита: перезапустить фазы 1-9 на исправленной сборке; "
            f"кластер паттерна {f['id']} должен отсутствовать в strings/classdump/hits "
            f"(§8 diff по finding-ID).",
            "",
        ]
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
def render_report(audit: Path, fj: dict) -> str:
    tpl = (DATA_DIR / "report_template.md").read_text(encoding="utf-8")
    b = fj["build"]
    summary = fj["summary"]

    findings = fj["findings"]
    top3 = findings[:3]
    top3_lines = "\n".join(f"- **{f['id']}** [{f['severity']}] {f['pattern']} — "
                           f"{truncate_words(f['notes'], 180) or truncate_words(f['remediation'], 180)}"
                           for f in top3) or "- (находок нет)"

    ft_rows = []
    for f in findings:
        ft_rows.append(
            f"| {f['id']} | {f['severity']} | {f['confidence']} | {f['category']} | "
            f"{md_code(f['pattern'], 60)} | "
            f"{md_code(str((f['evidence'] or {}).get('file', '')) + ':' + str(f['phase']), 70)} | "
            f"{f['declared_in_app_description']} | {f['reachable']} | "
            f"{','.join(f['gated_by'])} | {f['verdict']} | {md_code(f['remediation'], 80)} |")
    findings_table = "\n".join(ft_rows) or "| (нет) | | | | | | | | | | |"

    mismatch = ["| SDK/функционал | есть в бинарнике? | в App Description? | в Review Notes? | гейтится? |",
                "|---|---|---|---|---|"]
    for f in findings:
        mismatch.append(f"| {md_code(f['pattern'], 50)} | да | {f['declared_in_app_description']} | н/д | "
                        f"{','.join(f['gated_by'])} |")
    if not findings:
        mismatch.append("| (расхождений не зафиксировано) | | | | |")

    ev_rows = [f"- {r} ({len(read_lines(audit / r)) if (audit / r).exists() else '?'} "
               f"{plural_ru(len(read_lines(audit / r)) if (audit / r).exists() else 0, 'строка', 'строки', 'строк')})"
               for r in evidence_files(audit)]
    evidence_appendix = "\n".join(ev_rows) or "- (файлов доказательств нет)"

    fp_rows = ["| паттерн | hits | почему FP (по §триаж) |", "|---|---|---|"]
    for x in fj["false_positives"]:
        fp_rows.append(f"| {md_code(x['pack'])} | {x['hits']} | {md_text(x['explanation'])} |")
    if not fj["false_positives"]:
        fp_rows.append("| (нет) | 0 | |")
    for tag, key, label in FP_SUBSECTIONS:
        covered = [x for x in fj["false_positives"] if key.lower() in x["pack"].lower() or key.lower() in x["explanation"].lower()]
        state = f"покрыто: {', '.join(x['pack'] for x in covered)}" if covered else "не применимо / не найдено"
        fp_rows.append(f"| {tag} {label} | — | {md_text(state)} |")
    false_positives = "\n".join(fp_rows)

    analytics_inventory = build_analytics_inventory(audit, fj)
    remediation = build_remediation(audit, fj)

    tools = ", ".join(f"{k}: {v}" if v else f"{k}: n/a" for k, v in b["tools"].items())
    prov = audit / "original" / "PROVENANCE.md"
    prov_note = "см. original/PROVENANCE.md" if prov.exists() else "PROVENANCE.md ОТСУТСТВУЕТ (пробел)"

    crit = summary["critical"]
    high = summary["high"]
    verdict = fj["verdict"] or ("READY TO SUBMIT" if crit == 0 and high == 0 else f"BLOCKED ({crit} critical, {high} high)")

    repl = {
        "app_name": b["app"], "version": b["version"], "build_number": b["build_number"],
        "audit_date": b["audit_date"], "verdict": verdict, "top3": top3_lines,
        "diff_prev": "(первый аудит этого приложения — нет предыдущего билда)",
        "ipa_sha256": b["ipa_sha256"], "ipa_size": "-", "cryptid": b["cryptid"],
        "arch": "arm64 (arm64 slice of fat/thin)", "provenance": prov_note,
        "tools": tools, "hybrid": b["hybrid"],
        "findings_table": findings_table, "mismatch_matrix": "\n".join(mismatch),
        "evidence_appendix": evidence_appendix, "false_positives": false_positives,
        "analytics_inventory": analytics_inventory, "remediation": remediation,
    }
    out = tpl
    for k, v in repl.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def render_summary(audit: Path, fj: dict) -> str:
    b = fj["build"]
    s = fj["summary"]
    c = fj["counts"]
    crit = s["critical"]
    verdict = fj["verdict"] or ("READY TO SUBMIT" if crit == 0 else "BLOCKED")
    findings = fj["findings"]
    sev_lines = []
    for sev in SEV_ORDER:
        rows = [f for f in findings if f["severity"] == sev and f["verdict"] != "false_positive"]
        if rows:
            sev_lines.append(f"{sev.upper():<8} {len(rows):<3} " + "; ".join(r["pattern"][:40] for r in rows[:6]))
    top = [f for f in findings if f["verdict"] != "false_positive"]
    headline = fj["headline"] or (top[0]["notes"] if top else "")
    return "\n".join([
        f"СВОДКА АУДИТА — {b['app']} {b['version']} ({b['build_number']})   |   {b['audit_date']}",
        f"SHA-256: {b['ipa_sha256']}",
        f"Bundle:  {b.get('bundle_id','')}",
        f"Hybrid:  {b['hybrid']}",
        "",
        f"ВЕРДИКТ: {verdict}",
        "",
        "-" * 86,
        "ОСНОВНАЯ НАХОДКА",
        "-" * 86,
        headline,
        "",
        "-" * 86,
        "СЧЁТ ПО СЕРЬЁЗНОСТИ",
        "-" * 86,
        *sev_lines,
        f"ИТОГО  {len(findings)} находок   |   {c['raw_false_positive_hits']} hits оттриажено как ложные "
        f"срабатывания по {c['false_positive_categories_filtered']} классам пакетов "
        f"(сырых hits по пакетам: {c['raw_hits_total']})",
        "",
        "-" * 86,
        "ЧЕГО ЗДЕСЬ НЕТ",
        "-" * 86,
        "(полная таблица ложных срабатываний — в AUDIT_REPORT.md, раздел 6)",
        "",
        "-" * 86,
        "ОГОВОРКИ",
        "-" * 86,
        f"cryptid={b['cryptid']}; замены инструментов — по toolchain в findings.json;",
        "кросс-проверка MobSF не выполнена (нет Docker).",
        "",
        "-" * 86,
        "ОБЪЁМ / АРТЕФАКТЫ",
        "-" * 86,
        f"файлов доказательств: {len(evidence_files(audit))}; findings.json; AUDIT_REPORT.md; этот файл",
    ])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-dir", required=True)
    args = ap.parse_args()
    audit = Path(args.audit_dir)
    fj = load(audit)

    report = render_report(audit, fj)
    write_text(audit / "AUDIT_REPORT.md", report)
    wrote(audit / "AUDIT_REPORT.md", audit)

    summary_txt = render_summary(audit, fj)
    write_text(audit / "summary.txt", summary_txt)
    wrote(audit / "summary.txt", audit)
    ok("AUDIT_REPORT.md + summary.txt rendered from findings.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
