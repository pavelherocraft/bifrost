#!/usr/bin/env python3
"""analyze_frameworks.py -- Phase 3.1-3.5: frameworks + linked dylibs.

Outputs (evidence/p3_frameworks/):
  frameworks_list.txt         every file directly under Frameworks/ (3.1)
  frameworks_dir.txt          directory listing of Frameworks/
  linked_dylibs.json          {binary: {arch, dylibs[]}} via LIEF (3.2)
  frameworks_<pack>_hits.txt  pack greps over the framework listing (3.3-3.3c)
                              packs: payment, kyc_banking, crypto_web3,
                              pos_ble_nfc, nordic_payment, analytics_purchase,
                              openbanking
  libswift_count.txt          number of libswift*.dylib entries (3.5)

Tink/TinkLink discrimination (PLAN.md:393-398) is flagged in the hits file
notes, not decided here -- classification stays with the LLM.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from ipa_lib import (  # noqa: E402
    DEFAULT_SIGNATURES_DIR, compile_ere, grep_lines, load_pack, macho_cryptid,
    ok, warn, write_json, write_negative_result, write_text, wrote,
)

FRAMEWORK_PACKS = ["payment", "kyc_banking", "crypto_web3", "pos_ble_nfc",
                   "nordic_payment", "analytics_purchase"]

OPENBANKING_PATTERNS = [
    r"TinkLink", r"Tink\.openUrl", r"link\.tink\.com", r"api\.tink\.com",
    r"TrueLayerPaymentsSDK", r"truelayer\.com", r"pay\.truelayer\.com",
    r"api\.yapily\.com", r"fastlink\.yodlee\.com", r"node\.yodlee\.com",
    r"FinicityConnect", r"connect2\.finicity\.com", r"\bmx\.com", r"atrium\.mx\.com",
    r"StripeFinancialConnections", r"\bPlaid\b", r"pay\.alfabank\.ru",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-dir", required=True)
    ap.add_argument("--signatures-dir", default=DEFAULT_SIGNATURES_DIR)
    args = ap.parse_args()

    audit = Path(args.audit_dir)
    sdir = Path(args.signatures_dir)
    payload = audit / "extracted" / "Payload"
    if not payload.is_dir():
        print(f"ERROR: {payload} not found -- run extract_ipa.py first")
        return 2
    out = audit / "evidence" / "p3_frameworks"
    out.mkdir(parents=True, exist_ok=True)

    apps = sorted((d for d in payload.glob("*.app")), key=lambda p: len(p.parts))

    # --- 3.1: framework listing ------------------------------------------------
    all_entries: list[str] = []
    fw_dirs: list[str] = []
    for app in apps:
        fws = app / "Frameworks"
        if fws.is_dir():
            for entry in sorted(fws.iterdir()):
                all_entries.append(f"{entry.name}  ({'dir' if entry.is_dir() else 'file'}, "
                                   f"{entry.stat().st_size if entry.is_file() else '-'}) "
                                   f"[{app.name}]")
                if entry.is_dir():
                    fw_dirs.append(entry.name)
    write_text(out / "frameworks_list.txt",
               "\n".join(all_entries) if all_entries else "# no Frameworks/ dirs found")
    write_text(out / "frameworks_dir.txt",
               "\n".join(sorted(fw_dirs)) if fw_dirs else "# no framework dirs")
    wrote(out / "frameworks_list.txt", audit)
    wrote(out / "frameworks_dir.txt", audit)

    # --- 3.2: linked dylibs per Mach-O -----------------------------------------
    linked: dict[str, object] = {}
    for app in apps:
        binaries = [p for p in app.rglob("*") if p.is_file() and p.suffix == "" and p.parent.name.endswith(".framework")]
        main_exe = None
        try:
            import plistlib
            info = plistlib.load(open(app / "Info.plist", "rb"))
            main_exe = app / str(info.get("CFBundleExecutable", ""))
        except Exception:
            main_exe = None
        if main_exe and main_exe.is_file():
            binaries.append(main_exe)
        seen: set[Path] = set()
        for b in sorted(binaries, key=lambda p: p.name):
            if b in seen:
                continue
            seen.add(b)
            cryptid, arch, dylibs = macho_cryptid(b)
            linked[str(b.relative_to(audit / 'extracted')).replace("\\", "/")] = {
                "arch": arch,
                "cryptid": cryptid,
                "dylibs": dylibs,
            }
    write_json(out / "linked_dylibs.json", linked)
    wrote(out / "linked_dylibs.json", audit)

    # --- 3.3-3.3c: pack greps over the listing -----------------------------------
    listing_lines = sorted({e.split("  (")[0] for e in all_entries} | set(fw_dirs))
    for pack_name in FRAMEWORK_PACKS:
        pack = load_pack(sdir, pack_name)
        hits = [f"{line}  <- matched {pl.pattern} [{pl.status}]"
                for _, pl, line in grep_lines(listing_lines, pack)]
        if hits:
            write_text(out / f"frameworks_{pack_name}_hits.txt", "\n".join(hits))
        else:
            write_negative_result(out / f"frameworks_{pack_name}_hits.txt",
                                  f"{pack_name} pack over framework listing")
        wrote(out / f"frameworks_{pack_name}_hits.txt", audit)

    ob_hits = [ln for ln in listing_lines
               if any(compile_ere(p).search(ln) for p in OPENBANKING_PATTERNS)]
    ob_body = "\n".join(ob_hits) if ob_hits else "# 0 hits"
    ob_body += ("\n# NOTE (3.3c): bare `Tink` = Google crypto (FP trap); open-banking "
                "Tink requires the TinkLink cluster (TinkLink.framework / Tink.openUrl / link.tink.com).\n")
    write_text(out / "frameworks_openbanking_hits.txt", ob_body)
    wrote(out / "frameworks_openbanking_hits.txt", audit)

    # --- 3.5: libswift count ------------------------------------------------------
    n_swift = 0
    for info in linked.values():
        for d in info["dylibs"]:  # type: ignore[index]
            if "libswift" in d:
                n_swift += 1
    write_text(out / "libswift_count.txt", f"libswift* dylib references: {n_swift}")
    wrote(out / "libswift_count.txt", audit)

    ok(f"Phase 3 done: {len(linked)} Mach-O described, {len(fw_dirs)} framework dirs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
