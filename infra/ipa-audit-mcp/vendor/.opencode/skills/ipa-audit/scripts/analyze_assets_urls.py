#!/usr/bin/env python3
"""analyze_assets_urls.py -- Phases 5.1-5.5 (assets/l10n) + 6.1-6.6 (webview/URLs).

Outputs under evidence/p5_assets_l10n/ and evidence/p6_webview_urls/.
Every output file is written even when empty ("# 0 hits" header) -- negative
results are evidence (edge case 5).

URL extraction is boundary-aware (REVIEW.md section D-2 fix for B2):
    https?://[^\\s"'<>()\\[\\]]+
with host split + dedupe -> hosts_all.txt ("host  source-binaries").

Assets.car names are best-effort (no ipsw/assetutil on Windows, R5/R8):
the ACT1 string table is scanned; assets.json carries
{"method": "best-effort", "complete": false} honesty markers.
aasa_check.txt records SKIPPED (no network in audit sandbox).
"""
from __future__ import annotations

import argparse
import plistlib
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from ipa_lib import (  # noqa: E402
    DEFAULT_SIGNATURES_DIR, HOST_RE, extract_strings, extract_urls, grep_lines,
    load_pack, logical, ok, read_lines, url_host, warn, win_long, write_json,
    write_negative_result, write_text, wrote,
)

WEBVIEW_MARKER_RE = re.compile(
    r"WKWebView|SFSafariViewController|ASWebAuthenticationSession|UIWebView"
    r"|evaluateJavaScript|loadHTMLString|SFAuthenticationSession")
OPENBANKING_HOST_RE = re.compile(
    r"link\.tink\.com|api\.tink\.com|truelayer\.com|api\.yapily\.com|mx\.com"
    r"|fastlink\.yodlee\.com|node\.yodlee\.com|FinicityConnect|finicity\.com"
    r"|pay\.alfabank\.ru|alfabank\.ru", re.IGNORECASE)
ANALYTICS_HOST_RE = re.compile(
    r"onelink\.me|appsflyer\.com|app\.adjust\.com|api2\.branch\.io"
    r"|singular\.net|tenjin|adapty\.io|qonversion\.io|revenuecat\.com", re.IGNORECASE)
NORDIC_SCHEME_RE = re.compile(
    r"bankid://|mitid|nemid|swish://|getswish|vipps://|mobilepay://", re.IGNORECASE)
DEEPLINK_RE = re.compile(r"canOpenURL|openURL|onOpenURL|openURLContexts|handleOpenURL", re.IGNORECASE)
DYNAMIC_LINK_RE = re.compile(r"page\.link|dynamic[-_ ]?link|FirebaseDynamicLinks", re.IGNORECASE)
WEBVIEW_WIDGET_RE = re.compile(
    r"CheckoutWebView|PaymentWebView|BankIDWebView|YMWebView|checkoutwebview", re.IGNORECASE)
TEXT_SUFFIXES = {".plist", ".json", ".js", ".html", ".htm", ".txt", ".xml", ".strings", ".md"}


def collect_urls(audit: Path) -> tuple[list[str], list[str], dict[str, set[str]]]:
    """Boundary-aware URL sweep over strings dumps + Payload text files."""
    raw: list[tuple[str, str, int]] = []  # (url, source, line)
    host_sources: dict[str, set[str]] = {}
    strings_dir = audit / "evidence" / "p4_binary" / "strings"
    dumps: list[tuple[str, Path]] = []
    if strings_dir.is_dir():
        dumps = [(f.name.removesuffix(".strings"), f) for f in sorted(strings_dir.glob("*.strings"))]
    for label, path in dumps:
        for i, line in enumerate(read_lines(path), 1):
            for u in extract_urls(line):
                raw.append((u, label, i))
                h = url_host(u)
                if h:
                    host_sources.setdefault(h, set()).add(label)
    payload = audit / "extracted" / "Payload"
    for f in win_long(payload).rglob("*"):
        if not f.is_file() or f.suffix.lower() not in TEXT_SUFFIXES:
            continue
        label = f.suffix.lstrip(".").upper() + ":" + f.name
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for u in extract_urls(line):
                raw.append((u, label, i))
                h = url_host(u)
                if h:
                    host_sources.setdefault(h, set()).add(label)
    urls = sorted({u for u, _, _ in raw})
    raw_lines = sorted(f"{u}  [{src}:{ln}]" for u, src, ln in raw)
    return urls, raw_lines, host_sources


def parse_assets_car(path: Path) -> dict:
    """Best-effort ACT1 name extraction (R5/R8 documented limitation)."""
    try:
        data = path.read_bytes()
    except OSError:
        return {"error": "unreadable"}
    names = sorted({s for s in extract_strings(data, 5)
                    if re.fullmatch(r"[A-Za-z0-9_./ @-]{3,120}", s)})
    return {
        "file": path.name,
        "magic": data[:4].decode("ascii", "replace"),
        "method": "best-effort",
        "complete": False,
        "name_count": len(names),
        "names": names,
        "limitation": "Assets.car name table extracted by string scan; no ipsw/assetutil on Windows (R5/R8)",
    }


def load_lproj_strings(f: Path) -> dict:
    try:
        return plistlib.load(open(f, "rb"))
    except Exception:
        out: dict[str, str] = {}
        for line in f.read_bytes().decode("utf-16", "replace").splitlines():
            m = re.match(r'^"(.+?)"\s*=\s*"(.+?)";', line)
            if m:
                out[m.group(1)] = m.group(2)
        return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-dir", required=True)
    ap.add_argument("--signatures-dir", default=DEFAULT_SIGNATURES_DIR)
    args = ap.parse_args()

    audit = Path(args.audit_dir)
    sdir = Path(args.signatures_dir)
    payload = audit / "extracted" / "Payload"
    if not payload.is_dir():
        print("ERROR: extracted payload not found -- run extract_ipa.py first")
        return 2

    p5 = audit / "evidence" / "p5_assets_l10n"
    p6 = audit / "evidence" / "p6_webview_urls"
    p5.mkdir(parents=True, exist_ok=True)
    p6.mkdir(parents=True, exist_ok=True)

    # ================= Phase 5: assets + localizations ========================
    # 5.1 asset filename inventory + Assets.car
    asset_files: list[str] = []
    assets_json: dict[str, object] = {}
    for f in sorted(win_long(payload).rglob("*")):
        if f.is_file() and any(k in f.parts for k in (".xcassets", "Assets.car")):
            asset_files.append(str(logical(f).relative_to(logical(payload))).replace("\\", "/"))
            if f.name == "Assets.car":
                assets_json[str(logical(f).relative_to(logical(payload))).replace("\\", "/")] = parse_assets_car(f)
    write_text(p5 / "asset_filenames.txt",
               "\n".join(asset_files) if asset_files else "# no .xcassets/Assets.car entries found")
    wrote(p5 / "asset_filenames.txt", audit)
    if assets_json:
        car_names = []
        for v in assets_json.values():
            if isinstance(v, dict):
                car_names.extend(v.get("names", []))
        assets_json["_summary"] = {
            "car_files": len([v for v in assets_json.values() if isinstance(v, dict)]),
            "names_total": len(car_names),
            "method": "best-effort",
            "complete": False,
        }
        write_json(p5 / "assets.json", assets_json)
        name_lines = sorted(set(car_names))
    else:
        write_json(p5 / "assets.json", {"found": False, "method": "file-name-inventory-only"})
        name_lines = asset_files
    wrote(p5 / "assets.json", audit)

    pack = load_pack(sdir, "asset_names")
    hits = [f"{ln}  <- matched {pl.pattern} [{pl.status}]"
            for _, pl, ln in grep_lines(sorted(set(name_lines)), pack)]
    write_text(p5 / "asset_name_hits.txt",
               "\n".join(hits) if hits else "# 0 hits -- asset_names pack")
    wrote(p5 / "asset_name_hits.txt", audit)

    # 5.3-5.4: localizations
    lprojs = sorted({str(logical(p).relative_to(logical(payload))).replace("\\", "/")
                     for p in payload.rglob("*.lproj")})
    write_text(p5 / "lproj_list.txt", "\n".join(lprojs) if lprojs else "# no .lproj dirs")
    wrote(p5 / "lproj_list.txt", audit)
    dump_lines: list[str] = []
    l10n_pairs: list[str] = []
    for lp in win_long(payload).rglob("*.lproj"):
        for f in sorted(lp.glob("*.strings")):
            data = load_lproj_strings(f)
            rel = str(logical(f).relative_to(logical(payload))).replace("\\", "/")
            for k, v in data.items():
                dump_lines.append(f"{rel}: {k} = {v}")
                l10n_pairs.append(str(v))
    write_text(p5 / "all_strings_dump.txt",
               "\n".join(dump_lines) if dump_lines else "# no .strings localization files")
    wrote(p5 / "all_strings_dump.txt", audit)
    pack = load_pack(sdir, "localization_phrases")
    hits = [f"{ln}  <- matched {pl.pattern} [{pl.status}]"
            for _, pl, ln in grep_lines(sorted(set(l10n_pairs)), pack)]
    write_text(p5 / "l10n_hits.txt", "\n".join(hits) if hits else "# 0 hits -- localization_phrases pack")
    wrote(p5 / "l10n_hits.txt", audit)

    # 5.5: loose files + nibs
    loose = sorted(str(logical(f).relative_to(logical(payload))).replace("\\", "/")
                   for f in payload.rglob("*")
                   if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf", ".svg"))
    write_text(p5 / "loose_files.txt", "\n".join(loose[:2000]) if loose else "# no loose media files")
    wrote(p5 / "loose_files.txt", audit)
    nibs = sorted(str(logical(f).relative_to(logical(payload))).replace("\\", "/")
                  for f in payload.rglob("*.nib") if f.is_dir() or f.suffix == ".nib")
    write_text(p5 / "nibs.txt", "\n".join(nibs) if nibs else "# no .nib storyboards")
    wrote(p5 / "nibs.txt", audit)
    nib_texts: list[str] = []
    for nib in win_long(payload).rglob("*.nib"):
        nib_texts.extend(extract_strings(nib.read_bytes(), 5))
    nib_hits = [t for t in sorted(set(nib_texts))
                if re.search(r"(?i)pay|card|checkout|bank|swish|vipps|bankid", t)]
    write_text(p5 / "nib_hits.txt", "\n".join(nib_hits) if nib_hits else "# 0 hits -- payment-ish strings in nibs")
    wrote(p5 / "nib_hits.txt", audit)
    referenced = set(dump_lines)
    orphans = [n for n in name_lines
               if not any(n.split("/")[-1].rsplit(".", 1)[0] in r for r in referenced)][:500]
    write_text(p5 / "orphaned_candidates.txt",
               "\n".join(orphans) if orphans else "# no orphaned asset candidates (best-effort check)")
    wrote(p5 / "orphaned_candidates.txt", audit)

    # ================= Phase 6: webview + URLs =================================
    strings_dir = audit / "evidence" / "p4_binary" / "strings"
    wm_rows: list[str] = []
    if strings_dir.is_dir():
        for f in sorted(strings_dir.glob("*.strings")):
            for i, ln in enumerate(read_lines(f), 1):
                if WEBVIEW_MARKER_RE.search(ln):
                    wm_rows.append(f"{f.name}:{i}  {ln}")
    write_text(p6 / "webview_markers.txt",
               "\n".join(wm_rows) if wm_rows else "# 0 hits -- webview API markers")
    wrote(p6 / "webview_markers.txt", audit)

    urls, raw_lines, host_sources = collect_urls(audit)
    write_text(p6 / "urls_all_raw.txt", "\n".join(raw_lines) if raw_lines else "# no URLs extracted")
    write_text(p6 / "urls_all.txt", "\n".join(urls) if urls else "# no URLs extracted")
    wrote(p6 / "urls_all.txt", audit)
    hosts = sorted(h for h in host_sources if HOST_RE.match(h))
    write_text(p6 / "hosts_all.txt",
               "\n".join(f"{h}  {','.join(sorted(host_sources[h]))}" for h in hosts)
               if hosts else "# no hosts extracted")
    wrote(p6 / "hosts_all.txt", audit)
    ok(f"hosts: {len(hosts)}")

    pack = load_pack(sdir, "webview_url")
    hits = [f"{u}  <- matched {pl.pattern} [{pl.status}]"
            for _, pl, u in grep_lines(urls, pack)]
    write_text(p6 / "urls_suspicious.txt", "\n".join(hits) if hits else "# 0 hits -- webview_url pack")
    wrote(p6 / "urls_suspicious.txt", audit)

    ob = [u for u in urls if OPENBANKING_HOST_RE.search(u)]
    write_text(p6 / "openbanking_host_hits.txt", "\n".join(sorted(set(ob))) if ob else "# 0 hits -- open-banking hosts")
    wrote(p6 / "openbanking_host_hits.txt", audit)
    an = [u for u in urls if ANALYTICS_HOST_RE.search(u)]
    write_text(p6 / "analytics_host_hits.txt", "\n".join(sorted(set(an))) if an else "# 0 hits -- analytics hosts")
    wrote(p6 / "analytics_host_hits.txt", audit)

    # 6.3: schemes + deeplinks
    schemes_path = audit / "evidence" / "p2_plist_entitlements" / "url_schemes.json"
    schemes_txt = read_lines(schemes_path) if schemes_path.exists() else []
    write_text(p6 / "url_schemes.txt",
               "\n".join(schemes_txt) if schemes_txt else "# url_schemes.json not found (run parse_info_plist.py)")
    wrote(p6 / "url_schemes.txt", audit)

    dl_rows: list[str] = []
    nordic_rows: list[str] = []
    if strings_dir.is_dir():
        for f in sorted(strings_dir.glob("*.strings")):
            for i, ln in enumerate(read_lines(f), 1):
                if DEEPLINK_RE.search(ln):
                    dl_rows.append(f"{f.name}:{i}  {ln}")
                if NORDIC_SCHEME_RE.search(ln):
                    nordic_rows.append(f"{f.name}:{i}  {ln}")
    write_text(p6 / "deeplink_handlers.txt", "\n".join(dl_rows) if dl_rows else "# 0 hits -- deeplink handlers")
    wrote(p6 / "deeplink_handlers.txt", audit)
    write_text(p6 / "nordic_appswitch_hits.txt",
               "\n".join(nordic_rows) if nordic_rows else "# 0 hits -- Nordic app-switch schemes (6.3b)")
    wrote(p6 / "nordic_appswitch_hits.txt", audit)

    dyn = [u for u in urls if DYNAMIC_LINK_RE.search(u)]
    write_text(p6 / "dynamic_links.txt", "\n".join(sorted(set(dyn))) if dyn else "# 0 hits -- dynamic links")
    wrote(p6 / "dynamic_links.txt", audit)

    wg_rows = [r for r in wm_rows if WEBVIEW_WIDGET_RE.search(r)]
    write_text(p6 / "webview_widget_markers.txt",
               "\n".join(wg_rows) if wg_rows else "# 0 hits -- payment/webview widget markers (6.5)")
    wrote(p6 / "webview_widget_markers.txt", audit)
    write_text(p6 / "aasa_check.txt",
               "# SKIPPED -- network access not available in the audit sandbox;\n"
               "# associated-domain AASA files must be checked manually (Phase 8.4 / R10).")
    wrote(p6 / "aasa_check.txt", audit)

    ok("Phases 5-6 done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
