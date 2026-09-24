#!/usr/bin/env python3
"""
Check that the public source carries no commercial-product branches.

The open repository should read as one complete product with a general
extension mechanism.  A reader should not be able to tell from it that a
separate commercial build exists, which means the source must not name that
build, branch on whether it is installed, or speak a protocol only it uses.

This checker reports every occurrence and compares the count per file against a
recorded baseline.  New occurrences fail; removing them and lowering the
baseline is how the boundary work gets tracked to zero.

Usage:
    python3 scripts/oss_boundary_check.py            # check against baseline
    python3 scripts/oss_boundary_check.py --list     # show every occurrence
    python3 scripts/oss_boundary_check.py --update   # rewrite the baseline
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "scripts" / "oss_boundary_baseline.json"

#: Directories that make up the published source.
SCAN_ROOTS = ("flocks", "webui/src", "tui")

SCAN_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".vue"}

SKIP_DIR_NAMES = {
    "__pycache__",
    "node_modules",
    "dist",
    ".venv",
    "venv",
    ".git",
    "build",
}

#: Names of a separate commercial build, and the shapes that branch on it.
#
#: ``flockspro`` is matched case-insensitively but must not be the start of a
#: longer camelCase word: ``flocksProvider`` and ``flocksProcess`` are ordinary
#: identifiers that happen to put "flocks" next to "Pro", and counting them as
#: boundary violations made the number impossible to drive to zero.
MARKER_PATTERNS = [
    re.compile(r"flockspro(?![a-z])", re.IGNORECASE),
    re.compile(r"flocks[\s_-]+pro\b", re.IGNORECASE),
]


@dataclass(frozen=True)
class Occurrence:
    path: str
    line: int
    text: str


def _iter_source_files() -> Iterable[Path]:
    for root_name in SCAN_ROOTS:
        root = REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            yield path


def scan() -> List[Occurrence]:
    """Every line in the published source that names the commercial build."""

    found: List[Occurrence] = []
    for path in _iter_source_files():
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(content.splitlines(), start=1):
            if any(pattern.search(line) for pattern in MARKER_PATTERNS):
                found.append(
                    Occurrence(
                        path=str(path.relative_to(REPO_ROOT)),
                        line=number,
                        text=line.strip()[:160],
                    )
                )
    return found


def counts_by_file(occurrences: Iterable[Occurrence]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for occurrence in occurrences:
        counts[occurrence.path] = counts.get(occurrence.path, 0) + 1
    return dict(sorted(counts.items()))


def load_baseline() -> Dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    try:
        raw = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    files = raw.get("files") if isinstance(raw, dict) else None
    return {str(k): int(v) for k, v in (files or {}).items()}


def write_baseline(counts: Dict[str, int]) -> None:
    BASELINE_PATH.write_text(
        json.dumps(
            {
                "_comment": (
                    "Occurrences of commercial-build names remaining in the public "
                    "source. New or increased counts fail scripts/oss_boundary_check.py. "
                    "Lower these as the migration proceeds; the target is an empty map."
                ),
                "total": sum(counts.values()),
                "files": counts,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def compare(counts: Dict[str, int], baseline: Dict[str, int]) -> List[str]:
    """Files that are worse than the baseline allows."""

    problems: List[str] = []
    for path, count in counts.items():
        allowed = baseline.get(path, 0)
        if count > allowed:
            problems.append(
                f"{path}: {count} occurrence(s), baseline allows {allowed}"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print every occurrence")
    parser.add_argument("--update", action="store_true", help="rewrite the baseline")
    args = parser.parse_args()

    occurrences = scan()
    counts = counts_by_file(occurrences)

    if args.list:
        for occurrence in occurrences:
            print(f"{occurrence.path}:{occurrence.line}: {occurrence.text}")
        print()

    if args.update:
        write_baseline(counts)
        print(f"baseline updated: {sum(counts.values())} occurrence(s) in {len(counts)} file(s)")
        return 0

    baseline = load_baseline()
    problems = compare(counts, baseline)

    total = sum(counts.values())
    allowed = sum(baseline.values())
    print(f"public source: {total} occurrence(s) in {len(counts)} file(s); baseline allows {allowed}")

    if problems:
        print("\nnew commercial-build references:")
        for problem in problems:
            print(f"  {problem}")
        return 1

    if total < allowed:
        print("baseline is now loose; rerun with --update to lock in the improvement")
    return 0


if __name__ == "__main__":
    sys.exit(main())
