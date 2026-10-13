#!/usr/bin/env python3
"""Fail when a binary in the offline payload needs a newer glibc / libstdc++ than the target.

The package has to run on the oldest distribution it is built for (RHEL 8: glibc 2.28,
libstdc++ GLIBCXX_3.4.25 / CXXABI_1.3.11). A wheel or tool binary that needs more fails
only on the customer's machine, e.g. ``version `GLIBC_2.30' not found``, so the build
reads the ELF version requirements (``.gnu.version_r``) of every file under the given
roots and compares them with the floor in versions.manifest.json (``glibc_floor``).

GLIBC_* needs count whatever library they name; GLIBCXX_* / CXXABI_* only when they name
the system ``libstdc++.so.6`` (a wheel that vendors its own, renamed libstdc++ satisfies
those needs itself). Stdlib only; runs with the bundled Python.

Usage: glibc_floor.py [--manifest FILE] [--floor NAME=VERSION ...] ROOT [ROOT ...]
Exit status 0 = everything fits, 1 = at least one file needs more or is an ELF file whose
requirements cannot be read completely (truncated or inconsistent tables, a dynamically
linked object without section headers), 2 = usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
from pathlib import Path

SHT_GNU_VERNEED = 0x6FFFFFFE
PT_DYNAMIC = 2
VERSION_NAME = re.compile(r"^(GLIBC|GLIBCXX|CXXABI)_(\d+(?:\.\d+)*)$")
SYSTEM_LIBSTDCXX = "libstdc++.so.6"


class ELFError(ValueError):
    """An ELF file whose version requirements cannot be read completely (counts as a violation)."""


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _cstr(table: bytes, offset: int) -> str:
    end = table.find(b"\0", offset) if offset < len(table) else -1
    if end < 0:
        raise ELFError("string offset outside its string table")
    return table[offset:end].decode("ascii", "replace")


def _read_exact(handle, offset: int, size: int, what: str) -> bytes:
    handle.seek(offset)
    data = handle.read(size)
    if len(data) != size:
        raise ELFError(f"truncated {what}")
    return data


def version_needs(path: str) -> list[tuple[str, str]] | None:
    """(library, version name) pairs the file needs; None when it is not an ELF file.

    Raises ELFError when the file is an ELF object but its version requirements cannot be
    read completely (truncated tables, dangling links or offsets, chains shorter than their
    counts, a dynamic object without section headers): a gate must not read that as "none".
    """
    with open(path, "rb") as handle:
        ident = handle.read(16)
        if ident[:4] != b"\x7fELF":
            return None
        if len(ident) < 16:
            raise ELFError("truncated ELF identification")
        if ident[4] not in (1, 2) or ident[5] not in (1, 2):
            raise ELFError("unknown ELF class or byte order")
        end = "<" if ident[5] == 1 else ">"
        if ident[4] == 2:
            header_fmt, section_fmt = end + "HHIQQQIHHHHHH", end + "IIQQQQIIQQ"
        else:
            header_fmt, section_fmt = end + "HHIIIIIHHHHHH", end + "IIIIIIIIII"
        fields = struct.unpack(header_fmt, _read_exact(handle, 16, struct.calcsize(header_fmt), "ELF header"))
        ph_offset, sh_offset = fields[4], fields[5]
        ph_entsize, ph_num, sh_entsize, sh_num = fields[8], fields[9], fields[10], fields[11]
        section_size = struct.calcsize(section_fmt)
        if sh_offset and sh_num == 0:
            # more than 0xff00 sections: the real count sits in the first header's sh_size
            if sh_entsize < section_size:
                raise ELFError(f"section header entries of {sh_entsize} bytes")
            sh_num = struct.unpack_from(section_fmt, _read_exact(handle, sh_offset, section_size, "section header table"))[5]
        if not sh_offset or sh_num == 0:
            # no section headers: fine for a static object, not for one that loads shared libraries
            if ph_num:
                if ph_entsize < 4:
                    raise ELFError(f"program header entries of {ph_entsize} bytes")
                table = _read_exact(handle, ph_offset, ph_entsize * ph_num, "program header table")
                for index in range(ph_num):
                    if struct.unpack_from(end + "I", table, index * ph_entsize)[0] == PT_DYNAMIC:
                        raise ELFError("dynamically linked but has no section headers to read")
            return []
        if sh_entsize < section_size:
            raise ELFError(f"section header entries of {sh_entsize} bytes")
        table = _read_exact(handle, sh_offset, sh_entsize * sh_num, "section header table")
        sections = [struct.unpack_from(section_fmt, table, index * sh_entsize) for index in range(sh_num)]
        needs: list[tuple[str, str]] = []
        # section header fields: name, type, flags, addr, offset, size, link, info, ...
        for section in sections:
            if section[1] != SHT_GNU_VERNEED:
                continue
            if section[6] >= len(sections):
                raise ELFError(f"version requirements link to missing section {section[6]}")
            strtab = sections[section[6]]
            strings = _read_exact(handle, strtab[4], strtab[5], "version string table")
            data = _read_exact(handle, section[4], section[5], "version requirements")
            claimed: list[int] = []

            def claim(start: int, what: str) -> None:
                # every Verneed / Vernaux record is 16 bytes; records that overlap (or a chain
                # that loops back) would be read as something they are not
                if start + 16 > len(data):
                    raise ELFError(f"{what} outside its section")
                if any(abs(start - other) < 16 for other in claimed):
                    raise ELFError(f"overlapping version records ({what} at offset {start})")
                claimed.append(start)

            entries, offset = section[7], 0  # sh_info = number of Verneed entries
            for entry in range(entries):
                claim(offset, "version requirement entry")
                _ver, count, file_off, aux, next_off = struct.unpack_from(end + "HHIII", data, offset)
                library = _cstr(strings, file_off)
                aux_off = offset + aux
                for number in range(count):
                    claim(aux_off, "version name entry")
                    _hash, _flags, _other, name_off, aux_next = struct.unpack_from(end + "IHHII", data, aux_off)
                    needs.append((library, _cstr(strings, name_off)))
                    if number < count - 1:
                        if not aux_next:
                            raise ELFError("version name list shorter than its count")
                        aux_off += aux_next
                if entry < entries - 1:
                    if not next_off:
                        raise ELFError("version requirement list shorter than its count")
                    offset += next_off
        return needs


def load_floor(manifest: str | None, overrides: list[str]) -> dict[str, tuple[int, ...]]:
    floor: dict[str, tuple[int, ...]] = {}
    if manifest:
        for name, value in json.load(open(manifest, encoding="utf-8"))["glibc_floor"].items():
            if name in ("GLIBC", "GLIBCXX", "CXXABI"):
                floor[name] = _version(str(value))
    for item in overrides:
        name, _, value = item.partition("=")
        if name not in ("GLIBC", "GLIBCXX", "CXXABI") or not re.fullmatch(r"\d+(?:\.\d+)*", value):
            raise SystemExit(f"glibc_floor: bad --floor {item!r} (expected GLIBC=2.28, GLIBCXX=3.4.25 or CXXABI=1.3.11)")
        floor[name] = _version(value)
    if "GLIBC" not in floor:
        raise SystemExit("glibc_floor: no GLIBC floor given (manifest glibc_floor.GLIBC or --floor GLIBC=...)")
    return floor


def scan(roots: list[str], floor: dict[str, tuple[int, ...]]) -> tuple[int, dict, list[str]]:
    """(ELF files read, highest need per kind {kind: (version, path)}, violations)."""
    elf_count = 0
    highest: dict[str, tuple[tuple[int, ...], str]] = {}
    violations: list[str] = []
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(dirpath, name)
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                try:
                    needs = version_needs(path)
                except (OSError, struct.error, ELFError) as exc:
                    violations.append(f"{path}: cannot read its version requirements ({exc})")
                    continue
                if needs is None:
                    continue
                elf_count += 1
                worst: dict[str, tuple[int, ...]] = {}
                for library, version_name in needs:
                    match = VERSION_NAME.match(version_name)
                    if not match:
                        continue  # GLIBC_PRIVATE, GCC_*, XCRYPT_*, CXXABI_TM_1 ...
                    kind, version = match.group(1), _version(match.group(2))
                    if kind != "GLIBC" and library != SYSTEM_LIBSTDCXX:
                        continue
                    if version > worst.get(kind, ()):
                        worst[kind] = version
                for kind, version in worst.items():
                    if version > highest.get(kind, ((), ""))[0]:
                        highest[kind] = (version, path)
                    if kind in floor and version > floor[kind]:
                        shown = ".".join(map(str, version))
                        limit = ".".join(map(str, floor[kind]))
                        violations.append(f"{path}: needs {kind}_{shown} (target has {kind}_{limit})")
    return elf_count, highest, violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--manifest", default=str(Path(__file__).with_name("versions.manifest.json")))
    parser.add_argument("--floor", action="append", default=[], metavar="NAME=VERSION")
    parser.add_argument("roots", nargs="+")
    args = parser.parse_args(argv)
    floor = load_floor(args.manifest if args.manifest else None, args.floor)
    missing = [root for root in args.roots if not os.path.isdir(root)]
    if missing:
        print(f"glibc_floor: not a directory: {', '.join(missing)}", file=sys.stderr)
        return 2
    elf_count, highest, violations = scan(args.roots, floor)
    limits = ", ".join(f"{kind}_{'.'.join(map(str, version))}" for kind, version in sorted(floor.items()))
    print(f"glibc_floor: {elf_count} ELF files checked against {limits}")
    for kind in ("GLIBC", "GLIBCXX", "CXXABI"):
        if kind in highest:
            version, path = highest[kind]
            print(f"glibc_floor: highest {kind}_{'.'.join(map(str, version))} needed by {path}")
    for line in violations:
        print(f"glibc_floor: {line}", file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
