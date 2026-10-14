#!/usr/bin/env python3
"""parse_info_plist.py -- Phase 2.1-2.7: Info.plist + entitlements + profile.

Outputs (under evidence/p2_plist_entitlements/):
  info.json                  full main Info.plist (sorted keys)
  info_<appex>.json          per-extension Info.plist (2.1b)
  entitlements.json          entitlements from the embedded LC_CODE_SIGNATURE
                             SuperBlob (custom parser, ipsw-free) per binary
  entitlements_der.xml       DER entitlements blob if present (2.1a)
  entitlements_hits.txt      entitlements.txt pack vs entitlements text (2.3)
  profile.json               embedded.mobileprovision plist (no openssl)
  plist_key_hits.txt         plist_keys.txt pack vs Info.plist text (2.5b)
  privacy_manifests.json     PrivacyInfo.xcprivacy inventory (2.6)
  infoplist_strings.txt      per-lproj InfoPlist.strings dump (2.7)

When the binary signature is stripped (dataoff=0, re-signed IPA), entitlements
fall back to the provisioning profile and a WARN is emitted.
"""
from __future__ import annotations

import argparse
import json
import plistlib
import struct
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from ipa_lib import (  # noqa: E402
    DEFAULT_SIGNATURES_DIR, grep_lines, load_pack, ok, read_lines, warn,
    write_json, write_negative_result, write_text, wrote,
)

CSSLOT_CODEDIRECTORY = 0
CSSLOT_ENTITLEMENTS = 5
CSSLOT_DER_ENTITLEMENTS = 17
SUPERBLOB_MAGIC = 0xFADE0CC0
BLOB_MAGIC_ENTITLEMENTS = 0xFADE7171
BLOB_MAGIC_DER_ENTITLEMENTS = 0xFADE7553


def superblob_entitlements(binary_path: Path) -> tuple[dict | None, bytes | None, int]:
    """Walk the LC_CODE_SIGNATURE SuperBlob. Returns (plist_dict, der_bytes, dataoff).

    LIEF-free: the dataoff comes from the pure load-command parser (LIEF's
    commands iteration was observed to hard-crash on this host).
    """
    from ipa_lib import macho_codesign_dataoff
    dataoff = macho_codesign_dataoff(binary_path)
    if not dataoff:
        return None, None, 0
    ents: dict | None = None
    der: bytes | None = None
    with open(binary_path, "rb") as fh:
        fh.seek(dataoff)
        hdr = fh.read(12)
        if len(hdr) < 12:
            return None, None, dataoff
        magic, length, count = struct.unpack(">III", hdr)
        if magic != SUPERBLOB_MAGIC:
            return None, None, dataoff
        index = []
        for _ in range(count):
            entry = fh.read(8)
            if len(entry) < 8:
                break
            index.append(struct.unpack(">II", entry))
        for slot, offset in index:
            fh.seek(dataoff + offset)
            bh = fh.read(8)
            if len(bh) < 8:
                continue
            bmagic, blen = struct.unpack(">II", bh)
            payload = fh.read(max(0, blen - 8))
            if slot in (CSSLOT_ENTITLEMENTS,) and bmagic == BLOB_MAGIC_ENTITLEMENTS:
                try:
                    ents = plistlib.loads(payload)
                except Exception:
                    ents = None
            elif slot == CSSLOT_DER_ENTITLEMENTS and bmagic == BLOB_MAGIC_DER_ENTITLEMENTS:
                der = payload
    return ents, der, dataoff


def normalize_profile(obj: dict) -> dict:
    import datetime as _dt

    def conv(v):
        if isinstance(v, (bytes, bytearray)):
            return str(bytes(v))  # matches the reference profile.json representation
        if isinstance(v, (_dt.datetime, _dt.date)):
            return v.isoformat(sep=" ") if isinstance(v, _dt.datetime) else v.isoformat()
        return v

    out = {}
    for k, v in obj.items():
        if isinstance(v, list):
            out[k] = [conv(x) for x in v]
        elif isinstance(v, dict):
            out[k] = {ik: conv(iv) for ik, iv in v.items()}
        else:
            out[k] = conv(v)
    return {k: out[k] for k in sorted(out)}


def extract_profile_plist(path: Path) -> dict | None:
    raw = path.read_bytes()
    start = raw.find(b"<plist")
    end = raw.find(b"</plist>")
    if start == -1 or end == -1:
        return None
    try:
        return plistlib.loads(raw[start:end + len(b"</plist>")])
    except Exception:
        return None


def json_to_lines(obj) -> list[str]:
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False).splitlines()


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
    out = audit / "evidence" / "p2_plist_entitlements"
    out.mkdir(parents=True, exist_ok=True)

    app_dirs = sorted((d for d in payload.glob("*.app")), key=lambda p: len(p.parts))

    # --- 2.1 / 2.1b: Info.plist per app/appex --------------------------------
    info_all: dict[str, dict] = {}
    for app in app_dirs:
        ip = app / "Info.plist"
        if not ip.is_file():
            continue
        try:
            data = plistlib.load(open(ip, "rb"))
        except Exception as exc:
            warn(f"cannot parse {ip}: {exc}")
            continue
        key = "info.json" if app == app_dirs[0] else f"info_{app.name.removesuffix('.app')}.json"
        write_json(out / key, data)
        wrote(out / key, audit)
        info_all[key] = data

    main_info = info_all.get("info.json", {})

    # --- 2.1: entitlements per binary ----------------------------------------
    ent_lines: list[str] = []
    ent_json: dict[str, object] = {}
    for app in app_dirs:
        exe_name = (app.name.removesuffix(".app"))
        info = main_info if app == app_dirs[0] else {}
        try_ip = app / str((info_all.get("info.json" if app == app_dirs[0] else f"info_{exe_name}.json", {}) or {}).get("CFBundleExecutable", exe_name))
        binary_path = try_ip if try_ip.is_file() else app / exe_name
        if not binary_path.is_file():
            candidates = [p for p in app.iterdir() if p.is_file() and not p.name.endswith((".plist", ".strings", ".json"))]
            binary_path = candidates[0] if candidates else None
        if binary_path is None or not binary_path.is_file():
            continue
        ents, der, dataoff = superblob_entitlements(binary_path)
        rel = binary_path.relative_to(audit).as_posix()
        ent_json[binary_path.name] = {
            "binary": rel,
            "code_signature_dataoff": dataoff,
            "entitlements": ents if ents is not None else {},
        }
        if ents:
            ent_lines.extend(json_to_lines(ents))
        if der is not None:
            (out / "entitlements_der.xml").write_bytes(der)
            wrote(out / "entitlements_der.xml", audit)
        if dataoff == 0:
            warn(f"signature stripped (dataoff=0): {binary_path.name} -- using profile entitlements")
    write_json(out / "entitlements.json", ent_json)
    wrote(out / "entitlements.json", audit)

    # --- embedded.mobileprovision -> profile.json ----------------------------
    profile_path = app_dirs[0] / "embedded.mobileprovision"
    profile: dict | None = None
    if profile_path.is_file():
        profile = extract_profile_plist(profile_path)
    if profile is None:
        write_json(out / "profile.json", {"found": False})
        warn("no embedded.mobileprovision found")
    else:
        write_json(out / "profile.json", normalize_profile(profile))
        wrote(out / "profile.json", audit)
        prof_ents = profile.get("Entitlements") or {}
        if prof_ents and not any(v.get("entitlements") for v in ent_json.values()):
            ent_lines.extend(json_to_lines(prof_ents))
        gta = (profile.get("Entitlements") or {}).get("get-task-allow")
        if gta:
            warn(f"get-task-allow={gta} (development/team profile -- provenance concern)")

    # --- 2.3: entitlements pack vs entitlements text --------------------------
    ent_text = sorted(set(ent_lines))
    if ent_text:
        pack = load_pack(sdir, "entitlements")
        hits = [f"{line}  <- matched {pl.pattern} [{pl.status}]"
                for _, pl, line in grep_lines(ent_text, pack)]
        if hits:
            write_text(out / "entitlements_hits.txt", "\n".join(hits))
            warn(f"entitlements pack hits: {len(hits)}")
        else:
            write_negative_result(out / "entitlements_hits.txt", "entitlements pack, 0 hits")
    else:
        write_negative_result(out / "entitlements_hits.txt", "no entitlements blobs recovered")
    wrote(out / "entitlements_hits.txt", audit)

    # --- 2.5b: plist_keys pack vs Info.plist text ------------------------------
    plist_lines: list[str] = []
    for key, data in info_all.items():
        for line in json_to_lines(data):
            plist_lines.append(f"{key}: {line}")
    plist_lines = sorted(set(plist_lines))
    pack = load_pack(sdir, "plist_keys")
    hits = [f"{line}  <- matched {pl.pattern} [{pl.status}]"
            for _, pl, line in grep_lines(plist_lines, pack)]
    if hits:
        write_text(out / "plist_key_hits.txt", "\n".join(hits))
    else:
        write_negative_result(out / "plist_key_hits.txt", "plist_keys pack over all Info.plists")
    wrote(out / "plist_key_hits.txt", audit)

    # --- 2.6: privacy manifests -------------------------------------------------
    from ipa_lib import win_long, logical
    manifests: dict[str, object] = {}
    for pm in win_long(payload).rglob("PrivacyInfo.xcprivacy"):
        try:
            manifests[logical(pm).relative_to(audit).as_posix()] = plistlib.load(open(pm, "rb"))
        except Exception as exc:
            manifests[logical(pm).relative_to(audit).as_posix()] = {"error": str(exc)}
    write_json(out / "privacy_manifests.json", manifests if manifests else {"found": False})
    wrote(out / "privacy_manifests.json", audit)

    # --- 2.7: InfoPlist.strings per lproj ---------------------------------------
    ips: list[str] = []
    for f in win_long(payload).rglob("InfoPlist.strings"):
        rel = logical(f).relative_to(audit).as_posix()
        try:
            data = plistlib.load(open(f, "rb"))
            body = "\n".join(f"{k} = {v};" for k, v in data.items())
        except Exception:
            body = f.read_bytes().decode("utf-8", "replace")
        ips.append(f"### {rel}\n{body}")
    write_text(out / "infoplist_strings.txt",
               "\n\n".join(ips) if ips else "# no InfoPlist.strings found")
    wrote(out / "infoplist_strings.txt", audit)

    # --- schemes quick view (used by Phase 6.3) ---------------------------------
    schemes: dict[str, list[str]] = {
        "CFBundleURLSchemes": [],
        "LSApplicationQueriesSchemes": [],
    }
    for s in main_info.get("CFBundleURLTypes") or []:
        for scheme in s.get("CFBundleURLSchemes") or []:
            schemes["CFBundleURLSchemes"].append(str(scheme))
    schemes["LSApplicationQueriesSchemes"] = [str(s) for s in
                                              main_info.get("LSApplicationQueriesSchemes") or []]
    write_json(out / "url_schemes.json", schemes)
    wrote(out / "url_schemes.json", audit)

    l10n = main_info.get("CFBundleLocalizations") or []
    ok(f"Phase 2 done: {len(app_dirs)} app dirs, schemes={schemes}, localizations={l10n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
