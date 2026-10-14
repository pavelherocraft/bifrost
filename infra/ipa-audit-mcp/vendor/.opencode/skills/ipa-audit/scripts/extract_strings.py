#!/usr/bin/env python3
"""extract_strings.py -- Phase 4.1-4.3 + IL2CPP (D7).

For every Mach-O target (main binary, each framework executable, each appex
binary) and -- with IL2CPP enabled -- Data/Managed/Metadata/global-metadata.dat
plus any *.dll under Data/Managed/:

  evidence/p4_binary/strings/<basename>.strings       sorted-unique dump
  evidence/p4_binary/classdump/<basename>.objc_classes.txt
  evidence/p4_binary/strings_manifest.json            RELATIVE paths + counts

The dump format matches the reference audit (dedupe + codepoint sort), which
the pack hit counts and the ±5-line context windows in hits files rely on.

Swift metadata (4.3) is best-effort: __swift5_typenames/__swift5_protos
section strings are dumped when present (no ipsw on this host).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from ipa_lib import (  # noqa: E402
    MIN_STR_LEN, extract_strings, objc_classnames, ok, strings_dump, warn,
    write_json, write_negative_result, write_text, wrote,
)

IL2CPP_MAGIC = 0xFAB11BAF
SWIFT_SECTIONS = ["__swift5_typenames", "__swift5_protos", "__swift5_methnames",
                  "__swift5_fieldmd", "__swift5_reflstr"]


def read_macho_targets(audit: Path) -> list[Path]:
    payload = audit / "extracted" / "Payload"
    targets: list[Path] = []
    apps = sorted((d for d in payload.glob("*.app")), key=lambda p: len(p.parts))
    import plistlib
    for app in apps:
        try:
            info = plistlib.load(open(app / "Info.plist", "rb"))
            exe = app / str(info.get("CFBundleExecutable", ""))
            if exe.is_file():
                targets.append(exe)
        except Exception:
            pass
        fws = app / "Frameworks"
        if fws.is_dir():
            for fw in sorted(fws.glob("*.framework")):
                exe = fw / fw.name.removesuffix(".framework")
                if exe.is_file():
                    targets.append(exe)
        for appex in sorted(app.rglob("*.appex")):
            try:
                ex_info = plistlib.load(open(appex / "Info.plist", "rb"))
                exe = appex / str(ex_info.get("CFBundleExecutable", ""))
                if exe.is_file():
                    targets.append(exe)
            except Exception:
                pass
    return targets


def swift_type_lines(binary_path: Path) -> list[str]:
    """Swift metadata strings via the pure section walker (best-effort, 4.3)."""
    from ipa_lib import macho_section_bytes
    try:
        sections = macho_section_bytes(binary_path, set(SWIFT_SECTIONS))
    except Exception:
        return []
    out: set[str] = set()
    for content in sections.values():
        out.update(extract_strings(content, MIN_STR_LEN))
    return sorted(out)


def il2cpp_targets(audit: Path) -> list[tuple[str, Path]]:
    payload = audit / "extracted" / "Payload"
    found: list[tuple[str, Path]] = []
    for app in payload.glob("*.app"):
        gm = app / "Data" / "Managed" / "Metadata" / "global-metadata.dat"
        if gm.is_file():
            with open(gm, "rb") as fh:
                magic_bytes = fh.read(4)
            magic = int.from_bytes(magic_bytes, "little")
            if magic != IL2CPP_MAGIC:
                warn(f"global-metadata.dat magic {magic:#x} != {IL2CPP_MAGIC:#x} (unsupported version?)")
            found.append(("global-metadata.dat", gm))
        managed = app / "Data" / "Managed"
        if managed.is_dir():
            for dll in sorted(managed.glob("*.dll")):
                found.append((dll.name, dll))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit-dir", required=True)
    ap.add_argument("--il2cpp", choices=["auto", "on", "off"], default="auto")
    args = ap.parse_args()

    audit = Path(args.audit_dir)
    payload = audit / "extracted" / "Payload"
    if not payload.is_dir():
        print("ERROR: extracted payload not found -- run extract_ipa.py first")
        return 2

    strings_dir = audit / "evidence" / "p4_binary" / "strings"
    classdump_dir = audit / "evidence" / "p4_binary" / "classdump"
    swift_dir = audit / "evidence" / "p4_binary" / "swift"
    for d in (strings_dir, classdump_dir, swift_dir):
        d.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, dict] = {}
    total = 0

    machos = read_macho_targets(audit)
    if not machos:
        warn("no Mach-O targets discovered")

    for b in machos:
        data = b.read_bytes()
        dump = strings_dump(data)
        sp = strings_dir / f"{b.name}.strings"
        write_text(sp, "\n".join(dump))
        classes = objc_classnames(b)
        cp = classdump_dir / f"{b.name}.objc_classes.txt"
        if classes:
            write_text(cp, "\n".join(classes))
        else:
            write_negative_result(cp, f"no ObjC class names recoverable from {b.name}")
        sw = swift_type_lines(b)
        if sw:
            write_text(swift_dir / f"{b.name}.swift5.txt", "\n".join(sw))
        rel_bin = b.relative_to(audit).as_posix()
        manifest[b.name] = {
            "path": rel_bin,
            "strings_file": sp.relative_to(audit).as_posix(),
            "n_strings": len(dump),
            "size": len(data),
            "objc_classes": len(classes),
        }
        total += len(dump)
        ok(f"strings: {b.name}: {len(dump)} unique, {len(classes)} ObjC classes")

    if args.il2cpp != "off":
        il = il2cpp_targets(audit) if args.il2cpp == "on" else il2cpp_targets(audit)
        if not il and args.il2cpp == "on":
            warn("--il2cpp on but no IL2CPP metadata found")
        for logical, p in il:
            data = p.read_bytes()
            dump = strings_dump(data)
            name = logical if logical == "global-metadata.dat" else p.stem
            sp = strings_dir / f"{name}.strings"
            write_text(sp, "\n".join(dump))
            manifest[name] = {
                "path": p.relative_to(audit).as_posix(),
                "strings_file": sp.relative_to(audit).as_posix(),
                "n_strings": len(dump),
                "size": len(data),
                "objc_classes": 0,
                "il2cpp": True,
            }
            total += len(dump)
            ok(f"strings(il2cpp): {name}: {len(dump)} unique")

    write_json(audit / "evidence" / "p4_binary" / "strings_manifest.json", manifest)
    wrote(audit / "evidence" / "p4_binary" / "strings_manifest.json", audit)
    ok(f"Phase 4.1-4.3 done: {len(manifest)} targets, {total} unique strings total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
