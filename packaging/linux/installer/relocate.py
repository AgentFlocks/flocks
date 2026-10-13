#!/usr/bin/env python3
"""Move the prebuilt virtualenv of the offline package to another install root.

A venv is not relocatable: uv writes the absolute path it was built at into the
console-script shebangs, the activate scripts, pyvenv.cfg, the editable install's
.pth / direct_url.json and the bin/python symlink. The package is built at one root
(install_root in versions.json, /opt/flocks by default); installing it anywhere else
means rewriting exactly those places.

  relocate.py scan <payload_root> <old_root> <list_file>
      build time: list every file and symlink under flocks/.venv and tools/ that
      names old_root. Fails when a binary file other than a .pyc names it: a path
      inside a binary cannot be rewritten safely.
  relocate.py apply <payload_root> <list_file> <old_root> <new_root>
      install time: rewrite the listed files and symlinks, then check that none of
      them still names old_root.

.pyc files are left alone: when CPython loads a .pyc next to its source it replaces
the compiled-in file name with the real one (_imp._fix_co_filename).
"""

from __future__ import annotations

import os
import re
import stat
import sys
import tempfile

SCAN_DIRS = ("flocks/.venv", "tools")
LIST_HEADER = "# flocks relocate list, root "


def root_pattern(root: str) -> re.Pattern[bytes]:
    # the root as a whole path: "/opt/flocks" must not match inside "/srv/opt/flocks"
    # or "/opt/flocks-old", only where it stands on its own
    return re.compile(rb"(?<![\w.-])" + re.escape(os.fsencode(root)) + rb"(?![\w.-])")


def die(message: str) -> None:
    print(f"relocate: {message}", file=sys.stderr)
    sys.exit(1)


def scan(payload_root: str, old_root: str, list_file: str) -> None:
    pattern = root_pattern(old_root)
    needle = os.fsencode(old_root)
    texts: list[str] = []
    links: list[str] = []
    binaries: list[str] = []
    pycs = 0
    for top in SCAN_DIRS:
        base = os.path.join(payload_root, top)
        if not os.path.isdir(base):
            die(f"{base} is missing")
        for dirpath, dirnames, filenames in os.walk(base):
            for name in dirnames + filenames:
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, payload_root)
                if "\n" in rel:
                    die(f"file name with a newline: {rel!r}")
                if os.path.islink(path):
                    if pattern.search(os.fsencode(os.readlink(path))):
                        links.append(rel)
                    continue
                if name in dirnames or not os.path.isfile(path):
                    continue
                with open(path, "rb") as fh:
                    data = fh.read()
                if needle not in data or not pattern.search(data):
                    continue
                if name.endswith(".pyc"):
                    pycs += 1
                elif b"\0" in data:
                    binaries.append(rel)
                else:
                    texts.append(rel)
    if binaries:
        die(f"binary files name {old_root} and cannot be relocated: " + ", ".join(sorted(binaries)))
    with open(list_file, "w", encoding="utf-8") as out:
        out.write(f"{LIST_HEADER}{old_root}\n")
        for rel in sorted(texts):
            out.write(f"text {rel}\n")
        for rel in sorted(links):
            out.write(f"link {rel}\n")
    print(f"relocate list: {len(texts)} files, {len(links)} symlinks ({pycs} .pyc left to CPython)")


def read_list(list_file: str, old_root: str) -> list[tuple[str, str]]:
    with open(list_file, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    if not lines or lines[0] != f"{LIST_HEADER}{old_root}":
        die(f"{list_file} was not made for root {old_root}")
    entries = []
    for line in lines[1:]:
        kind, _, rel = line.partition(" ")
        if kind not in ("text", "link") or not rel or rel.startswith("/") or ".." in rel.split("/"):
            die(f"bad entry in {list_file}: {line!r}")
        entries.append((kind, rel))
    return entries


def replace_file(path: str, data: bytes) -> None:
    st = os.lstat(path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".relocate-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            os.fchmod(fh.fileno(), stat.S_IMODE(st.st_mode))
            if hasattr(os, "fchown") and os.geteuid() == 0:
                os.fchown(fh.fileno(), st.st_uid, st.st_gid)
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


def replace_link(path: str, target: str) -> None:
    tmp = os.path.join(os.path.dirname(path), f".relocate-{os.getpid()}-{os.path.basename(path)}")
    os.symlink(target, tmp)
    try:
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


def apply(payload_root: str, list_file: str, old_root: str, new_root: str) -> None:
    old = root_pattern(old_root)
    new_bytes = os.fsencode(new_root)
    entries = read_list(list_file, old_root)
    files = links = 0
    for kind, rel in entries:
        path = os.path.join(payload_root, rel)
        if kind == "link":
            if not os.path.islink(path):
                die(f"{rel}: expected a symlink")
            target = os.fsencode(os.readlink(path))
            new_target, count = old.subn(lambda _m: new_bytes, target)
            if count == 0:
                die(f"{rel}: symlink does not point into {old_root}")
            replace_link(path, os.fsdecode(new_target))
            links += 1
        else:
            if os.path.islink(path) or not os.path.isfile(path):
                die(f"{rel}: expected a regular file")
            with open(path, "rb") as fh:
                data = fh.read()
            if b"\0" in data:
                die(f"{rel}: binary file in the relocate list")
            new_data, count = old.subn(lambda _m: new_bytes, data)
            if count == 0:
                die(f"{rel}: does not name {old_root}")
            replace_file(path, new_data)
            files += 1
    # read everything back: nothing may still point at the build root
    for kind, rel in entries:
        path = os.path.join(payload_root, rel)
        if kind == "link":
            content = os.fsencode(os.readlink(path))
        else:
            with open(path, "rb") as fh:
                content = fh.read()
        if old.search(content) or new_bytes not in content:
            die(f"{rel}: rewrite did not take")
    print(f"relocated {files} files and {links} symlinks from {old_root} to {new_root}")


def main(argv: list[str]) -> None:
    if len(argv) == 5 and argv[1] == "scan":
        scan(argv[2], argv[3].rstrip("/"), argv[4])
    elif len(argv) == 6 and argv[1] == "apply":
        old_root, new_root = argv[4].rstrip("/"), argv[5].rstrip("/")
        for root in (old_root, new_root):
            if not root.startswith("/") or "\n" in root:
                die(f"not an absolute path: {root!r}")
        if old_root == new_root or new_root.startswith(old_root + "/") or old_root.startswith(new_root + "/"):
            # nested roots would leave the old one inside every rewritten path
            die(f"{new_root} and {old_root} overlap")
        apply(argv[2], argv[3], old_root, new_root)
    else:
        die("usage: relocate.py scan <payload_root> <old_root> <list_file> | "
            "apply <payload_root> <list_file> <old_root> <new_root>")


if __name__ == "__main__":
    main(sys.argv)
