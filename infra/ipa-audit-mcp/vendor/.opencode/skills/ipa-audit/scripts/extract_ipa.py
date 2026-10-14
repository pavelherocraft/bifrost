#!/usr/bin/env python3
"""extract_ipa.py -- Phases 0.3 + 1.1-1.6 of the IPA audit.

Unpacks an IPA into a dated audit dir and records Phase-1 structure evidence:

    audits/<AppName>_<ver>_<build>_<YYYYMMDD>/
        original/App.ipa        (read-only copy)
        original/SHA256SUMS.txt (UTF-8, no BOM -- fixes the reference defect)
        original/PROVENANCE.md  (script-written; facts via --provenance-source)
        extracted/Payload/...
        evidence/p1_structure/{tree_dirs.txt,tree_files.txt,biggest_files.txt,
                               build_type.txt,hybrid_markers.json,extensions.txt}

Gates:
  - input must exist and start with PK\\x03\\x04 (else exit 2)
  - LZFSE (method 99) members require `pip install lzfse` (R1) -> exit 2
  - cryptid != 0 -> FairPlay R2 gate: ERROR + exit 3 (stop the audit)
  - audit dir must not already exist (idempotence guard, edge case 10)

All artifact paths stored relative to the audit dir. Never works in %TEMP%.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import stat
import sys
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from ipa_lib import (  # noqa: E402
    MIN_STR_LEN, die, error, extract_strings, lief_version, logical, ok,
    sanitize_dir_name, sha256_file, warn, win_long, write_json, write_text, wrote,
)

HYBRID_MARKERS = {
    "main.jsbundle": "rn",
    "capacitor.config.json": "capacitor",
    "public": "capacitor",
    "www": "capacitor",
}


def preflight(ipa: Path) -> None:
    if not ipa.is_file():
        die(f"IPA not found: {ipa}", 2)
    with open(ipa, "rb") as fh:
        magic = fh.read(4)
    if magic != b"PK\x03\x04":
        die(f"not a ZIP/IPA (magic {magic!r}): {ipa}", 2)


def read_plist_from_zip(zf: zipfile.ZipFile, name: str) -> dict:
    import plistlib
    with zf.open(name) as fh:
        return plistlib.load(fh)


def find_payload_app(zf: zipfile.ZipFile) -> tuple[str, dict]:
    """Return (app dir name inside Payload, Info.plist dict)."""
    names = zf.namelist()
    apps = sorted({n.split("/")[1] for n in names
                   if n.startswith("Payload/") and n.count("/") >= 2
                   and n.split("/")[1].endswith(".app")
                   and n.count("/") == 2 and n.endswith("Info.plist")})
    # robust: any */Info.plist directly under an .app dir
    infos = [n for n in names if n.startswith("Payload/")
             and n.endswith("/Info.plist") and n.count("/") == 2]
    if not infos:
        die("no Payload/<App>.app/Info.plist in archive", 2)
    infos.sort(key=len)
    app_dir = infos[0].split("/")[1]
    info = read_plist_from_zip(zf, infos[0])
    return app_dir, info


def set_readonly(path: Path) -> None:
    os_chmod = getattr(Path, "chmod")
    os_chmod(path, stat.S_IREAD)


def extract_all(zf: zipfile.ZipFile, dest: Path) -> list[str]:
    lzfse = None
    issues: list[str] = []
    members = zf.infolist()
    for m in members:
        if m.filename.endswith("/"):
            continue
        target = win_long(dest / m.filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        if m.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            with zf.open(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
        elif m.compress_type == 99:
            if lzfse is None:
                try:
                    import lzfse  # type: ignore
                    lzfse = lzfse
                except ImportError:
                    die("LZFSE IPA (method 99) requires 'pip install lzfse' (risk R1)", 2)
            data = lzfse.decompress(zf.read(m))
            target.write_bytes(data)
            issues.append(f"lzfse:{m.filename}")
        else:
            die(f"unsupported zip compression type {m.compress_type} for {m.filename}", 2)
    return issues


def hybrid_type(payload: Path) -> tuple[str, dict]:
    app_dirs = [d for d in payload.glob("*.app")]
    markers: dict[str, object] = {}
    hybrid = "none"
    app = app_dirs[0] if app_dirs else payload
    checks = {
        "main.jsbundle": (app / "main.jsbundle").exists(),
        "App.framework": (app / "Frameworks" / "App.framework").exists(),
        "flutter_assets": (app / "flutter_assets").exists(),
        "capacitor.config.json": (app / "capacitor.config.json").exists(),
        "capacitor_public": (app / "public").exists(),
        "capacitor_www": (app / "www").exists(),
        "unity_Data": (app / "Data").is_dir(),
        "unity_UnityFramework": (app / "Frameworks" / "UnityFramework.framework").exists(),
        "il2cpp_global_metadata": (app / "Data" / "Managed" / "Metadata" / "global-metadata.dat").exists(),
    }
    markers = {k: v for k, v in checks.items()}
    if checks["main.jsbundle"]:
        hybrid = "rn"
    elif checks["App.framework"] or checks["flutter_assets"]:
        hybrid = "flutter"
    elif checks["capacitor.config.json"] or checks["capacitor_public"] or checks["capacitor_www"]:
        hybrid = "capacitor"
    elif checks["unity_Data"] or checks["unity_UnityFramework"] or checks["il2cpp_global_metadata"]:
        hybrid = "unity_il2cpp"
    return hybrid, markers


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ipa", required=True)
    ap.add_argument("--audits-root", required=True)
    ap.add_argument("--provenance-source", default="not specified",
                    help="where this IPA came from (required by Phase 0.3)")
    ap.add_argument("--provenance-notes", default="")
    ap.add_argument("--force", action="store_true",
                    help="allow re-use of an existing audit dir (rework)")
    args = ap.parse_args()

    ipa = Path(args.ipa)
    audits_root = Path(args.audits_root)

    preflight(ipa)

    with zipfile.ZipFile(ipa) as zf:
        app_dir, info = find_payload_app(zf)

    app_name = sanitize_dir_name(app_dir[:-4] if app_dir.endswith(".app") else app_dir)
    version = str(info.get("CFBundleShortVersionString", "0"))
    build = str(info.get("CFBundleVersion", "0"))
    today = _dt.date.today().strftime("%Y%m%d")
    audit_name = f"{app_name}_{version}_{build}_{today}"
    audit_dir = audits_root / audit_name

    if audit_dir.exists():
        if args.force:
            warn(f"audit dir exists, continuing (--force): {audit_dir}")
        else:
            die(f"audit dir exists -- use --force or a different --audits-root: {audit_dir}", 2)

    audits_root.mkdir(parents=True, exist_ok=True)
    (audit_dir / "evidence" / "p1_structure").mkdir(parents=True, exist_ok=True)
    (audit_dir / "original").mkdir(parents=True, exist_ok=True)

    # --- Phase 0.3: original/ copy + SHA256SUMS + PROVENANCE -----------------
    dest_ipa = audit_dir / "original" / "App.ipa"
    if not dest_ipa.exists():
        shutil.copy2(ipa, dest_ipa)
        set_readonly(dest_ipa)
    digest = sha256_file(dest_ipa)
    write_text(audit_dir / "original" / "SHA256SUMS.txt",
               f"{digest}  {ipa.name}")
    ok(f"SHA-256 {digest}")

    provenance = [
        "# PROVENANCE",
        "",
        f"- source IPA: {ipa.name}",
        f"- source path: {ipa.resolve().as_posix()}",
        f"- provenance-source: {args.provenance_source}",
        f"- acquisition date (UTC): {_dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds')}",
        f"- SHA-256: {digest}",
        f"- Python: {sys.version.split()[0]}",
        f"- LIEF: {lief_version()}",
        f"- ipsw: not available on this host",
        f"- MobSF: not available on this host (no Docker)",
        f"- app bundle: {app_dir}",
        f"- version: {version} (build {build})",
    ]
    if args.provenance_notes:
        provenance.append(f"- notes: {args.provenance_notes}")
    write_text(audit_dir / "original" / "PROVENANCE.md", "\n".join(provenance))
    wrote(audit_dir / "original" / "PROVENANCE.md", audit_dir)

    # --- Phase 1.1: extraction -----------------------------------------------
    extracted = audit_dir / "extracted"
    if not (extracted / "Payload").exists():
        with zipfile.ZipFile(ipa) as zf:
            issues = extract_all(zf, extracted)
            for i in issues:
                warn(f"special member extracted: {i}")
    ok(f"extracted -> {extracted}")

    payload = extracted / "Payload"

    # --- Phase 1.2: tree evidence --------------------------------------------
    p1 = audit_dir / "evidence" / "p1_structure"
    ex_long = win_long(extracted)
    dirs = sorted(str(logical(d).relative_to(extracted)).replace("\\", "/")
                  for d in ex_long.rglob("*") if d.is_dir())
    files = sorted(str(logical(f).relative_to(extracted)).replace("\\", "/")
                   for f in ex_long.rglob("*") if f.is_file())
    write_text(p1 / "tree_dirs.txt", "\n".join(dirs))
    write_text(p1 / "tree_files.txt", "\n".join(files))
    sized = []
    for f in ex_long.rglob("*"):
        if f.is_file():
            try:
                sized.append((f.stat().st_size, str(logical(f).relative_to(extracted)).replace("\\", "/")))
            except OSError:
                continue
    biggest = sorted(sized, reverse=True)[:50]
    write_text(p1 / "biggest_files.txt", "\n".join(f"{sz}\t{p}" for sz, p in biggest))
    for name in ("tree_dirs.txt", "tree_files.txt", "biggest_files.txt"):
        wrote(p1 / name, audit_dir)

    # --- Phase 1.3: build type / cryptid --------------------------------------
    app_bundle = payload / app_dir
    main_binary = app_bundle / (info.get("CFBundleExecutable") or app_name)
    cryptid, arch, dylibs = -1, "unknown", []
    macho_targets: list[str] = []
    if main_binary.exists():
        from ipa_lib import macho_cryptid
        cryptid, arch, dylibs = macho_cryptid(main_binary)
    build_type = [
        f"main binary: {main_binary.name}",
        f"arch: {arch}",
        f"cryptid: {cryptid}",
        f"LC_LOAD_DYLIB count: {len(dylibs)}",
    ]
    write_text(p1 / "build_type.txt", "\n".join(build_type))
    wrote(p1 / "build_type.txt", audit_dir)

    # Mach-O target inventory (1.5): frameworks + appex + nested apps
    for fw in sorted((app_bundle / "Frameworks").glob("*.framework")) if (app_bundle / "Frameworks").is_dir() else []:
        exe = fw / fw.name.replace(".framework", "")
        if exe.is_file():
            macho_targets.append(str(exe.relative_to(extracted)).replace("\\", "/"))
    appex = sorted(str(logical(p).relative_to(extracted)).replace("\\", "/")
                   for p in win_long(payload).rglob("*.appex"))
    write_text(p1 / "extensions.txt",
               "\n".join(appex) if appex else "# no .appex extensions found")
    wrote(p1 / "extensions.txt", audit_dir)

    # --- Phase 1.4: hybrid markers --------------------------------------------
    hybrid, markers = hybrid_type(payload)
    write_json(p1 / "hybrid_markers.json", {"hybrid": hybrid, "markers": markers,
                                            "mach_o_extra_targets": macho_targets})
    wrote(p1 / "hybrid_markers.json", audit_dir)

    # --- R2 gate ---------------------------------------------------------------
    if cryptid not in (0,):
        error(f"cryptid={cryptid} -- FairPlay-encrypted binary (risk R2): strings are garbage.")
        error("Obtain an unencrypted export from the same build and re-run. Audit STOPPED.")
        return 3

    n_macho = 1 + len(macho_targets)
    ok(f"audit dir: {audit_dir}")
    ok(f"hybrid: {hybrid}; Mach-O targets for Phase 4: {n_macho} (+IL2CPP metadata if present)")
    print(f"MANIFEST: app={app_dir} version={version} build={build} sha256={digest} "
          f"cryptid={cryptid} arch={arch} hybrid={hybrid} mach_o_targets={n_macho}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
