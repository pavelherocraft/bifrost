#!/usr/bin/env python3
"""ipa_lib.py -- shared helpers for the ipa-audit skill scripts.

Pure Python 3.10+ (no POSIX deps). Optional: lief (Mach-O parsing), lzfse
(LZFSE IPA extraction), yaml (suppressions).

Conventions (git-commit skill precedent):
  - stdout lines: OK: / WARN: / ERROR: / WROTE: <relpath>
  - exit codes:   0 ok, 2 usage/env error, 3 gate violation
  - every written file is UTF-8 (no BOM), LF or platform default per artifact
  - artifact paths stored in manifests are RELATIVE to the audit dir
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

MIN_STR_LEN = 4
CHUNK_SIZE = 8 * 1024 * 1024

# ---------------------------------------------------------------------------
# stdout / exit conventions
# ---------------------------------------------------------------------------

def ok(msg: str) -> None:
    print(f"OK: {msg}")

def warn(msg: str) -> None:
    print(f"WARN: {msg}")

def error(msg: str) -> None:
    print(f"ERROR: {msg}")

def wrote(path: Path, audit_dir: Path | None = None) -> None:
    rel = path if audit_dir is None else rel_to(path, audit_dir)
    print(f"WROTE: {rel}")

def die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[valid-type]
    error(msg)
    sys.exit(code)

# ---------------------------------------------------------------------------
# path hygiene (D5)
# ---------------------------------------------------------------------------

def rel_to(path: Path | str, audit_dir: Path | str) -> str:
    p = Path(path)
    a = Path(audit_dir).resolve()
    try:
        return p.resolve().relative_to(a).as_posix()
    except ValueError:
        return p.as_posix()

def assert_clean_rel(rel: str) -> None:
    """Fail on absolute paths or Temp leakage in stored artifact paths."""
    if not rel:
        raise ValueError("empty path")
    if re.search(r"(?i)^\w:[\\/]", rel):
        raise ValueError(f"absolute path in artifact: {rel}")
    if re.search(r"(?i)(^|[\\/])Temp([\\/]|$)|AppData\.Local\.Temp", rel):
        raise ValueError(f"Temp path leakage in artifact: {rel}")

def sanitize_dir_name(name: str) -> str:
    return re.sub(r"\s+", "_", name.strip())


def win_long(p: Path | str) -> Path:
    """\\\\?\\ extended-length prefix for deep Windows paths (edge case 11).

    Uses os.path.abspath (no filesystem resolution) because Path.resolve()
    raises OSError on Windows for paths beyond MAX_PATH -- the very case this
    helper exists to handle.
    """
    s = os.path.abspath(str(p))
    if os.name == "nt" and not s.startswith("\\\\?\\"):
        if s.startswith("\\\\"):
            return Path("\\\\?\\UNC\\" + s.lstrip("\\"))
        return Path("\\\\?\\" + s)
    return Path(s)


def logical(p: Path | str) -> Path:
    """Strip the \\\\?\\ prefix back to a logical path."""
    return Path(str(p).removeprefix("\\\\?\\"))

# ---------------------------------------------------------------------------
# file IO helpers (UTF-8 no BOM everywhere)
# ---------------------------------------------------------------------------

def write_text(path: Path, text: str, end_newline: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if end_newline and not text.endswith("\n"):
        text += "\n"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)

def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

def read_json(path: Path) -> object:
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().upper()

def write_negative_result(path: Path, label: str) -> None:
    """Empty evidence is still evidence: '0 hits' header (edge case 5)."""
    write_text(path, f"# 0 hits -- negative result ({label})\n")

# ---------------------------------------------------------------------------
# strings extractor (D1: pure Python ASCII + UTF-16LE, proven design)
# ---------------------------------------------------------------------------

_ASCII_OK = lambda b: 0x20 <= b <= 0x7E  # noqa: E731

def extract_strings(data: bytes, min_len: int = MIN_STR_LEN) -> Iterator[str]:
    """Yield printable ASCII and UTF-16LE strings of >= min_len chars.

    Single pass: at each offset a UTF-16LE run is attempted first; if it is
    too short the same offset is retried as an ASCII run. This is the
    behaviour of the classic `strings -a` blend used by the reference audit.
    """
    n = len(data)
    i = 0
    while i < n:
        b = data[i]
        # UTF-16LE: printable char followed by 0x00
        if b != 0 and i + 1 < n and data[i + 1] == 0 and _ASCII_OK(b):
            j = i
            start = i
            while j + 1 < n and data[j + 1] == 0 and _ASCII_OK(data[j]):
                j += 2
            run_len = (j - start) // 2
            if run_len >= min_len:
                yield data[start:j:2].decode("ascii", "replace")
                i = j
                continue
        # ASCII run
        if _ASCII_OK(b):
            j = i
            while j < n and _ASCII_OK(data[j]):
                j += 1
            if j - i >= min_len:
                yield data[i:j].decode("ascii", "replace")
                i = j
                continue
        i += 1

def strings_dump(data: bytes, min_len: int = MIN_STR_LEN) -> list[str]:
    """Extract, dedupe (first seen) and codepoint-sort -- matches the
    reference audit's .strings dumps (sorted-unique line per string)."""
    seen: set[str] = set()
    out: list[str] = []
    for s in extract_strings(data, min_len):
        if s not in seen:
            seen.add(s)
            out.append(s)
    out.sort()
    return out

def read_lines(path: Path) -> list[str]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()

# ---------------------------------------------------------------------------
# signature pack engine (D8)
# ---------------------------------------------------------------------------

@dataclass
class PackLine:
    pattern: str            # verbatim regex line from *.clean.txt
    status: str             # "confirmed" | "lead"
    regex: "re.Pattern[str]" = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.regex = compile_ere(self.pattern)

def ere_to_python(pattern: str) -> str:
    """Translate the POSIX-ERE dialect used by the packs to Python re.

    Packs use plain ERE (\\., \\b, alternation, classes). Only the POSIX
    bracket classes need mapping; anything else passes through.
    """
    posix = {
        "[:space:]": r"\s", "[:digit:]": r"\d", "[:alpha:]": "a-zA-Z",
        "[:alnum:]": "a-zA-Z0-9", "[:upper:]": "A-Z", "[:lower:]": "a-z",
    }
    out = pattern
    for k, v in posix.items():
        out = out.replace(k, v)
    return out

_ERE_CACHE: dict[str, "re.Pattern[str]"] = {}

def compile_ere(pattern: str) -> "re.Pattern[str]":
    rx = _ERE_CACHE.get(pattern)
    if rx is None:
        try:
            rx = re.compile(ere_to_python(pattern), re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"BAD ERE: {pattern!r}: {exc}") from exc
        _ERE_CACHE[pattern] = rx
    return rx

def load_pack(signatures_dir: Path, pack: str) -> list[PackLine]:
    """Load a normalized pack (<pack>.clean.txt + <pack>.status.tsv)."""
    clean = signatures_dir / f"{pack}.clean.txt"
    status_f = signatures_dir / f"{pack}.status.tsv"
    if not clean.exists() or not status_f.exists():
        die(f"pack {pack} not normalized (missing {clean.name} / {status_f.name}) in {signatures_dir}", 2)
    status_map: dict[str, str] = {}
    for line in read_lines(status_f):
        if "\t" in line:
            pat, st = line.split("\t", 1)
            status_map[pat.strip()] = st.strip()
    lines: list[PackLine] = []
    for pat in read_lines(clean):
        pat = pat.rstrip("\r")
        if not pat:
            continue
        try:
            lines.append(PackLine(pat, status_map.get(pat, "confirmed")))
        except ValueError as exc:
            die(str(exc), 3)
    if not lines:
        die(f"pack {pack} is empty after normalization", 3)
    return lines

BINARY_PACKS = [
    "payment", "kyc_banking", "nordic_payment", "analytics_purchase",
    "crypto_web3", "pos_ble_nfc", "feature_flags", "webview_url",
    "generic_payment_words", "secrets",
]
ALL_PACKS = BINARY_PACKS + ["plist_keys", "entitlements", "localization_phrases", "asset_names"]

def grep_lines(lines: Iterable[str], pack: list[PackLine]) -> Iterator[tuple[int, PackLine, str]]:
    """grep -E -i -f semantics: an input line is reported ONCE, with the
    FIRST pack line (file order) that matches it."""
    for idx, line in enumerate(lines, 1):
        for pl in pack:
            if pl.regex.search(line):
                yield idx, pl, line
                break

# ---------------------------------------------------------------------------
# Mach-O helpers (LIEF optional)
# ---------------------------------------------------------------------------

def lief_available() -> bool:
    try:
        import lief  # noqa: F401
        return True
    except ImportError:
        return False

def lief_version() -> str:
    try:
        import lief
        return str(getattr(lief, "__version__", "unknown"))
    except ImportError:
        return "unavailable"

def open_macho_arm64(path: Path):
    """Open a (possibly fat) Mach-O and return the arm64 slice (or best)."""
    import lief
    fat = lief.MachO.parse(str(path))
    if not fat:
        return None
    binaries = list(fat)
    if len(binaries) == 1:
        return binaries[0]
    for b in binaries:
        try:
            if "ARM64" in str(b.header.cpu_type):
                return b
        except Exception:
            continue
    return binaries[0]

CPU_TYPE_ARM64 = 0x0100000C
LC_ENCRYPTION_INFO = 0x21
LC_ENCRYPTION_INFO_64 = 0x2C
LC_LOAD_DYLIB = 0x0C
LC_LOAD_WEAK_DYLIB = 0x18 | 0x80000000
FAT_MAGIC = 0xCAFEBABE
MH_MAGIC_64 = 0xFEEDFACF
MH_MAGIC = 0xFEEDFACE


def macho_info_pure(path: Path) -> tuple[int, str, list[str]]:
    """(cryptid, arch, dylib install names) from a pure-Python Mach-O walk.

    Replaces LIEF on this hot path: LIEF 1.0.0-dev was observed to hard-crash
    (access violation in commands iteration) depending on process state. The
    parser handles fat containers (arm64 slice preferred) and thin binaries.
    """
    data = path.read_bytes()
    if len(data) < 32:
        return -1, "too-small", []
    magic_be = int.from_bytes(data[0:4], "big")
    offset = 0
    arch = "unknown"
    if magic_be == FAT_MAGIC:
        nfat = int.from_bytes(data[4:8], "big")
        chosen = None
        for i in range(nfat):
            base = 8 + i * 20
            if base + 20 > len(data):
                break
            cputype = int.from_bytes(data[base:base + 4], "big")
            off = int.from_bytes(data[base + 8:base + 12], "big")
            if cputype == CPU_TYPE_ARM64:
                chosen = off
                arch = "CPU_TYPE.ARM64"
                break
            if chosen is None:
                chosen = off
                arch = f"CPU_TYPE.{cputype:#x}"
        if chosen is None:
            return -1, "no-slices", []
        offset = chosen
    m = int.from_bytes(data[offset:offset + 4], "little")
    if m not in (MH_MAGIC_64, MH_MAGIC):
        m_be = int.from_bytes(data[offset:offset + 4], "big")
        if m_be in (MH_MAGIC_64, MH_MAGIC):
            endian = "big"
            m = m_be
        else:
            return -1, "not-macho", []
    else:
        endian = "little"
    if arch == "unknown":
        cputype = int.from_bytes(data[offset + 4:offset + 8], "little")
        arch = "CPU_TYPE.ARM64" if cputype == CPU_TYPE_ARM64 else f"CPU_TYPE.{cputype:#x}"
    is64 = m == MH_MAGIC_64
    ncmds = int.from_bytes(data[offset + 16:offset + 20], endian)
    cursor = offset + (32 if is64 else 28)
    cryptid = -1
    dylibs: list[str] = []
    for _ in range(ncmds):
        if cursor + 8 > len(data):
            break
        cmd = int.from_bytes(data[cursor:cursor + 4], endian)
        cmdsize = int.from_bytes(data[cursor + 4:cursor + 8], endian)
        if cmdsize < 8 or cursor + cmdsize > len(data):
            break
        if cmd in (LC_ENCRYPTION_INFO, LC_ENCRYPTION_INFO_64):
            cryptid = int.from_bytes(data[cursor + 16:cursor + 20], endian)
        elif cmd in (LC_LOAD_DYLIB, 0x18, 0x1F, 0x20):  # LOAD/WEAK/REEXPORT_DYLIB
            name_off = int.from_bytes(data[cursor + 8:cursor + 12], endian)
            end = data.find(b"\x00", cursor + name_off)
            if end == -1 or end > cursor + cmdsize:
                end = cursor + cmdsize
            dylibs.append(data[cursor + name_off:end].decode("utf-8", "replace"))
        cursor += cmdsize
    return cryptid, arch, dylibs


LC_CODE_SIGNATURE = 0x1D


def macho_codesign_dataoff(path: Path) -> int:
    """LC_CODE_SIGNATURE dataoff via the pure parser (0 when absent/stripped)."""
    data = path.read_bytes()
    if len(data) < 32:
        return 0
    offset = 0
    magic_be = int.from_bytes(data[0:4], "big")
    if magic_be == FAT_MAGIC:
        nfat = int.from_bytes(data[4:8], "big")
        for i in range(nfat):
            base = 8 + i * 20
            if base + 20 > len(data):
                break
            cputype = int.from_bytes(data[base:base + 4], "big")
            off = int.from_bytes(data[base + 8:base + 12], "big")
            if cputype == CPU_TYPE_ARM64:
                offset = off
                break
    m = int.from_bytes(data[offset:offset + 4], "little")
    if m not in (MH_MAGIC_64, MH_MAGIC):
        return 0
    endian = "little"
    is64 = m == MH_MAGIC_64
    ncmds = int.from_bytes(data[offset + 16:offset + 20], endian)
    cursor = offset + (32 if is64 else 28)
    for _ in range(ncmds):
        if cursor + 8 > len(data):
            break
        cmd = int.from_bytes(data[cursor:cursor + 4], endian)
        cmdsize = int.from_bytes(data[cursor + 4:cursor + 8], endian)
        if cmdsize < 8 or cursor + cmdsize > len(data):
            break
        if cmd == LC_CODE_SIGNATURE:
            return int.from_bytes(data[cursor + 8:cursor + 12], endian)
        cursor += cmdsize
    return 0


def macho_cryptid(path: Path) -> tuple[int, str, list[str]]:
    """Return (cryptid, arch, dylib install names). Pure parser (LIEF-free)."""
    try:
        return macho_info_pure(path)
    except OSError as exc:
        warn(f"cannot read {path}: {exc}")
        return -1, "unknown", []

LC_SEGMENT_64 = 0x19


def macho_section_bytes(path: Path, wanted: set[str]) -> dict[str, bytes]:
    """Read named sections from a (fat) Mach-O via a pure load-command walk.

    LIEF-free: its native section API was observed to hard-crash on this host
    depending on process state. Returns {section_name: raw bytes}.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return {}
    if len(data) < 32:
        return {}
    offset = 0
    magic_be = int.from_bytes(data[0:4], "big")
    if magic_be == FAT_MAGIC:
        nfat = int.from_bytes(data[4:8], "big")
        for i in range(nfat):
            base = 8 + i * 20
            if base + 20 > len(data):
                break
            if int.from_bytes(data[base:base + 4], "big") == CPU_TYPE_ARM64:
                offset = int.from_bytes(data[base + 8:base + 12], "big")
                break
    if int.from_bytes(data[offset:offset + 4], "little") != MH_MAGIC_64:
        return {}
    ncmds = int.from_bytes(data[offset + 16:offset + 20], "little")
    cursor = offset + 32
    out: dict[str, bytes] = {}
    for _ in range(ncmds):
        if cursor + 8 > len(data):
            break
        cmd = int.from_bytes(data[cursor:cursor + 4], "little")
        cmdsize = int.from_bytes(data[cursor + 4:cursor + 8], "little")
        if cmdsize < 8 or cursor + cmdsize > len(data):
            break
        if cmd == LC_SEGMENT_64:
            nsects = int.from_bytes(data[cursor + 64:cursor + 68], "little")
            sec_cursor = cursor + 72
            for s in range(nsects):
                sbase = sec_cursor + s * 80
                if sbase + 80 > len(data):
                    break
                sectname = data[sbase:sbase + 16].split(b"\x00")[0].decode("ascii", "replace")
                if sectname in wanted and sectname not in out:
                    sec_off = int.from_bytes(data[sbase + 48:sbase + 52], "little")
                    sec_size = int.from_bytes(data[sbase + 40:sbase + 48], "little")
                    out[sectname] = data[sec_off:sec_off + sec_size]
        cursor += cmdsize
    return out


def objc_classnames(path: Path) -> list[str]:
    """Class names from the __objc_classname section (sorted unique)."""
    blob = macho_section_bytes(path, {"__objc_classname"}).get("__objc_classname", b"")
    names: set[str] = set()
    for name in blob.split(b"\x00"):
        if name:
            try:
                names.add(name.decode("utf-8"))
            except UnicodeDecodeError:
                pass
    return sorted(names)

# ---------------------------------------------------------------------------
# suppression handling (per-binary scope only -- PLAN.md ред.2 warning)
# ---------------------------------------------------------------------------

def load_suppressions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = data.get("suppressions", [])
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
    except ImportError:
        # tiny fallback parser: "- binary: X\n  pattern: Y\n  reason: Z"
        items: list[dict] = []
        cur: dict = {}
        for line in read_lines(path):
            m = re.match(r"^\s*-\s+(\w+):\s*(.*)$", line)
            if m:
                if cur:
                    items.append(cur)
                cur = {m.group(1): m.group(2).strip('"')}
                continue
            m = re.match(r"^\s+(\w+):\s*(.*)$", line)
            if m and cur:
                cur[m.group(1)] = m.group(2).strip('"')
        if cur:
            items.append(cur)
        return items

def is_suppressed(suppressions: list[dict], binary: str, pattern: str) -> Optional[dict]:
    for s in suppressions:
        if s.get("pattern") == pattern:
            scope = s.get("binary", "")
            if scope in ("*", binary, "") and scope != "":
                return s
    return None

# ---------------------------------------------------------------------------
# URL extraction (boundary-aware, REVIEW.md §D-2 fix for B2)
# ---------------------------------------------------------------------------
URL_RE = re.compile(r"https?://[^\s\"'<>()\[\]`{}|\\^]+", re.IGNORECASE)
URL_SCHEME_RE = re.compile(r"https?://", re.IGNORECASE)
HOST_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)+$")


def extract_urls(text: str) -> Iterator[str]:
    """Boundary-aware URL extraction (the B2 fix, REVIEW.md section D-2).

    IL2CPP/Unity metadata stores URL literals back-to-back with no separator
    (`.../api/ymoneyhttps://next-host/...`), so a naive greedy regex swallows
    every URL after the first. Split the text at EVERY scheme occurrence and
    terminate each candidate at whitespace/quotes/brackets.
    """
    starts = [m.start() for m in URL_SCHEME_RE.finditer(text)]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        chunk = text[start:end]
        m = re.match(r"https?://[^\s\"'<>()\[\]`{}|\\^]+", chunk, re.IGNORECASE)
        if m:
            yield m.group(0).rstrip(".,;:'\"")

def url_host(url: str) -> str:
    m = re.match(r"https?://([^/:?#]+)", url, re.IGNORECASE)
    return m.group(1).lower() if m else ""

# ---------------------------------------------------------------------------
# default signatures dir (portable: no hardcoded absolute paths)
#   <project_root>/tools/signatures, overridable via $IPA_SIGNATURES_DIR.
#   Project root is derived from this file: <root>/.opencode/skills/ipa-audit/scripts
# ---------------------------------------------------------------------------

SKILL_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SKILL_DIR.parent.parent.parent
DEFAULT_SIGNATURES_DIR = str(
    Path(os.environ.get("IPA_SIGNATURES_DIR") or (PROJECT_ROOT / "tools" / "signatures"))
)
