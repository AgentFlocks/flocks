#!/usr/bin/env python3
"""
Check the WebUI source against the Flocks UI spec (webui/UI_SPEC.md).

The canonical theme tokens must match the explicit light/dark values in the
design HTML. This contract is always checked, including for Pro overlay scans,
and cannot be waived by the legacy-usage baseline.

New interface code must use the semantic tokens (bg-surface, text-fg-secondary,
border-line, bg-action, text-success ...) instead of raw Tailwind palette
classes, literal colours, ad-hoc font sizes, off-spec radii or a second icon
set.  Existing pages carry a lot of the old style, so this works as a ratchet:
every occurrence is counted per file and compared with a recorded baseline.
A new file with any hit, or an old file whose count went up, fails the check;
lowering the baseline is how the pages get migrated.
Semantic colour utilities cannot use opacity suffixes (bg-surface/50); use a
dedicated token such as bg-overlay-backdrop when translucency is required.

Usage:
    python3 scripts/ui_spec_check.py              # check webui/src against the baseline
    python3 scripts/ui_spec_check.py --list       # print every occurrence
    python3 scripts/ui_spec_check.py --update     # rewrite the baseline (only when hits went down)
    python3 scripts/ui_spec_check.py --root ../flockspro/webui-overlay/src \\
        --baseline ../flockspro/scripts/ui_spec_baseline.json          # another tree, own baseline

A root outside this repository is keyed relative to the root itself, so it must be
the whole tree that the baseline describes (not a subdirectory of it) when updating.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOTS = ("webui/src",)
DEFAULT_BASELINE = REPO_ROOT / "scripts" / "ui_spec_baseline.json"
DESIGN_SOURCE = REPO_ROOT / "webui" / "design" / "flocks-ui-spec-v1.4.html"
TOKEN_FILE = REPO_ROOT / "webui" / "src" / "styles" / "tokens.css"

SCAN_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".css"}
SKIP_DIR_NAMES = {"node_modules", "dist", "build", ".git", "coverage"}
# Only the canonical token file may contain unchecked literal colours. A second
# file named tokens.css in an overlay must not bypass the source rules.

_PALETTES = (
    "gray|zinc|slate|neutral|stone|red|rose|pink|fuchsia|purple|violet|indigo|"
    "blue|sky|cyan|teal|emerald|green|lime|yellow|amber|orange"
)
_UTILITIES = (
    "bg|text|border|ring|divide|placeholder|from|to|via|fill|stroke|outline|"
    "shadow|accent|caret|decoration|ring-offset|border-[trblxy]|border-[se]"
)
_SEMANTIC_COLOURS = (
    "canvas|surface|subtle|hover|selected|focus|disabled|overlay-backdrop|"
    "fg(?:-(?:secondary|muted|placeholder|link|disabled))?|line(?:-control)?|"
    "action(?:-(?:hover|pressed|fg|danger|danger-fg))?|switch-(?:on|thumb)|"
    "(?:success|warning|danger|info|ai|data|brand)(?:-soft)?"
)

#: rule name -> (pattern, applies to css files, applies to script files)
RULES = {
    # These variables contain complete CSS colours, not an RGB channel tuple.
    # Tailwind cannot apply /50 to var(--bg-surface); the utility is not emitted.
    "semantic-opacity": (
        re.compile(
            rf"(?<![\w/-])(?:[\w-]+:)*(?:{_UTILITIES})-(?:{_SEMANTIC_COLOURS})/(?:\d{{1,3}}|\[[^\]]+\])(?![\w-])"
        ),
        True,
        True,
    ),
    # bg-gray-100, dark:hover:text-red-600, border-t-zinc-800/50 ...
    "palette": (
        re.compile(
            rf"(?<![\w/-])(?:[\w-]+:)*(?:{_UTILITIES})-(?:{_PALETTES})-(?:50|[1-9]00|950)(?:/\d{{1,3}})?(?![\w-])"
        ),
        True,
        True,
    ),
    # literal colours outside tokens.css
    "hex-colour": (
        re.compile(r"(?<![\w&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![a-zA-Z0-9-])"),
        True,
        True,
    ),
    "rgb-colour": (re.compile(r"(?<![\w-])(?:rgba?|hsla?)\("), True, True),
    # a second icon set next to @tabler/icons-react
    "icon-set": (
        re.compile(r"""from\s+['"](?:lucide-react|react-icons(?:/[\w-]+)?|@heroicons/[\w/-]+|@mui/icons-material)['"]"""),
        False,
        True,
    ),
    # text-[13px], leading-[1.15] ... the type scale is fixed
    "arbitrary-type": (re.compile(r"(?<![\w-])(?:text|leading)-\[\d[^\]]*\]"), True, True),
    # cards and overlays are 12px; nothing in the spec is rounder, and no ad-hoc radii
    "radius": (re.compile(r"(?<![\w-])rounded(?:-[trblxyse]{1,2})?-(?:2xl|3xl|\[[^\]]+\])(?![\w-])"), True, True),
    # bg-white / text-black are not theme-aware: use bg-surface / text-fg
    "plain-white-black": (
        re.compile(rf"(?<![\w/-])(?:[\w-]+:)*(?:{_UTILITIES})-(?:white|black)(?:/\d{{1,3}})?(?![\w-])"),
        False,
        True,
    ),
}

#: Matches inside these are fine: a hex in a comment or an anchor is not a colour.
_COMMENT_LINE = re.compile(r"^\s*(?://|/\*|\*(?!\s*[{,:.\[]))")
#: A compatibility layer has to name the legacy palette classes it restyles.  Such a
#: block opts out explicitly; anything else in the file is still checked.
_CHECK_OFF = re.compile(r"ui-spec-check:\s*off")
_CHECK_ON = re.compile(r"ui-spec-check:\s*on")


@dataclass(frozen=True)
class Occurrence:
    path: str
    line: int
    rule: str
    text: str


class _DesignStyles(HTMLParser):
    """Read actual style elements, never colour examples in the document body."""

    def __init__(self) -> None:
        super().__init__()
        self.in_style = False
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "style":
            self.in_style = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "style":
            self.in_style = False

    def handle_data(self, data: str) -> None:
        if self.in_style:
            self.parts.append(data)


def _top_level_css_blocks(css: str) -> Iterable[tuple[str, str]]:
    """Do not treat a token under @media/@supports as an unconditional value."""
    depth = 0
    selector_start = body_start = 0
    selector = ""
    # Quoted braces belong to a value/selector, not the stylesheet structure.
    for match in re.finditer(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[{}]''', css):
        if match.group() == "{":
            if depth == 0:
                selector = css[selector_start:match.start()]
                body_start = match.end()
            depth += 1
        elif match.group() == "}":
            depth -= 1
            if depth == 0:
                yield selector, css[body_start:match.start()]
                selector_start = match.end()


def theme_tokens(css: str, *, design: bool) -> Dict[str, Dict[str, str]]:
    """Read the explicit theme token blocks in source order.

    This is deliberately a token-block reader, not a CSS cascade evaluator. The
    design's system-theme media fallback, component overrides and presentation
    helpers are not the explicit light/dark contract used by html.dark.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    light: Dict[str, str] = {}
    dark: Dict[str, str] = {}
    light_selectors = {":root"}
    dark_selectors = {".dark", "html.dark"}
    if design:
        light_selectors.update({'[data-theme="light"]', '.pane[data-theme="light"]'})
        dark_selectors = {'[data-theme="dark"]', '.pane[data-theme="dark"]'}
    for selector, body in _top_level_css_blocks(css):
        selectors = {part.strip().replace("'", '"') for part in selector.split(",")}
        declarations = {
            name: value.strip()
            for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;{}]+)", body)
            if not name.startswith("--spec-")
        }
        if selectors & light_selectors:
            light.update(declarations)
        if selectors & dark_selectors:
            dark.update(declarations)
    # Fonts and geometry are intentionally inherited in dark mode. A missing
    # dark colour still fails whenever the required value differs from light.
    return {"light": light, "dark": {**light, **dark}}


def normalized_css_value(value: str) -> str:
    """Ignore formatting, hex case and equivalent decimal spellings (.40/.4)."""
    value = re.sub(r"\s+", " ", value.strip()).replace("'", '"')
    value = re.sub(r"\s*([(),])\s*", r"\1", value)
    value = re.sub(r"#[0-9a-fA-F]+", lambda match: match.group().lower(), value)
    return re.sub(
        r"(?<![\w.-])(?:\d+\.\d+|\.\d+)(?![\w.-])",
        lambda match: format(Decimal(match.group()).normalize(), "f"),
        value,
    )


def check_design_tokens(design_path: Path, token_path: Path) -> List[str]:
    """The design is authoritative; a palette baseline can never waive drift."""
    try:
        design = _DesignStyles()
        design.feed(design_path.read_text(encoding="utf-8"))
        expected = theme_tokens("\n".join(design.parts), design=True)
        actual = theme_tokens(token_path.read_text(encoding="utf-8"), design=False)
    except (OSError, UnicodeDecodeError) as exc:
        return [f"cannot read design token contract: {exc}"]

    required = {"--font-sans", "--control-md", "--bg-canvas", "--text-primary", "--accent-ai"}
    missing = required - expected["light"].keys()
    if missing:
        return ["design source is missing required tokens: " + ", ".join(sorted(missing))]
    if expected["dark"].get("--bg-canvas") == expected["light"].get("--bg-canvas"):
        return ["design source is missing the explicit dark theme contract"]

    problems: List[str] = []
    for theme in ("light", "dark"):
        for name, expected_value in sorted(expected[theme].items()):
            actual_value = actual[theme].get(name)
            if actual_value is None or normalized_css_value(actual_value) != normalized_css_value(expected_value):
                problems.append(
                    f"{token_path.name} [{theme}] {name}: expected {expected_value}, "
                    f"found {actual_value if actual_value is not None else '<missing>'}"
                )
    return problems


def iter_files(roots: Sequence[Path]) -> Iterable[tuple[Path, Path]]:
    for root in roots:
        if root.is_file():
            if root.resolve() != TOKEN_FILE:
                yield root, root
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
                continue
            if path.resolve() == TOKEN_FILE:
                continue
            if any(part in SKIP_DIR_NAMES for part in path.relative_to(root).parts):
                continue
            yield path, root


def display_path(path: Path, root: Path) -> str:
    """Repo-relative inside this repository, root-relative for a tree elsewhere (keeps the baseline portable)."""
    if path.is_relative_to(REPO_ROOT):
        return path.relative_to(REPO_ROOT).as_posix()
    base = root if root.is_dir() else root.parent
    return path.relative_to(base).as_posix()


def scan_file(path: Path, root: Path) -> List[Occurrence]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    is_css = path.suffix == ".css"
    rel = display_path(path, root)
    found: List[Occurrence] = []
    checking = True
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _CHECK_OFF.search(line):
            checking = False
        elif _CHECK_ON.search(line):
            checking = True
        if not checking or _COMMENT_LINE.match(line):
            continue
        for rule, (pattern, in_css, in_script) in RULES.items():
            if is_css and not in_css:
                continue
            if not is_css and not in_script:
                continue
            for match in pattern.finditer(line):
                found.append(Occurrence(rel, lineno, rule, match.group(0)))
    return found


def counts_by_file(occurrences: Iterable[Occurrence]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for occurrence in occurrences:
        counts[occurrence.path] = counts.get(occurrence.path, 0) + 1
    return dict(sorted(counts.items()))


def counts_by_rule(occurrences: Iterable[Occurrence]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for occurrence in occurrences:
        counts[occurrence.rule] = counts.get(occurrence.rule, 0) + 1
    return dict(sorted(counts.items()))


def load_baseline(path: Path) -> Dict[str, int]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    files = raw.get("files") if isinstance(raw, dict) else None
    return {str(k): int(v) for k, v in (files or {}).items()}


def write_baseline(path: Path, counts: Dict[str, int], by_rule: Dict[str, int]) -> None:
    path.write_text(
        json.dumps(
            {
                "_comment": (
                    "Off-spec UI usages remaining per file (raw palette classes, literal "
                    "colours, second icon set, arbitrary type sizes, off-spec radii). "
                    "New or increased counts fail scripts/ui_spec_check.py; lower these "
                    "while migrating pages to the tokens in webui/UI_SPEC.md."
                ),
                "total": sum(counts.values()),
                "by_rule": by_rule,
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
            problems.append(f"{path}: {count} off-spec usage(s), baseline allows {allowed}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", action="append", help="directory (or file) to scan; repeatable. Default: webui/src")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE, help="baseline json to compare with")
    parser.add_argument("--list", action="store_true", help="print every occurrence")
    parser.add_argument("--update", action="store_true", help="rewrite the baseline from the current counts")
    parser.add_argument("--allow-increase", action="store_true", help="with --update: accept a baseline that grew")
    args = parser.parse_args()

    roots = [Path(r).resolve() for r in (args.root or [str(REPO_ROOT / r) for r in DEFAULT_ROOTS])]
    missing = [str(r) for r in roots if not r.exists()]
    if missing:
        print("scan root not found: " + ", ".join(missing), file=sys.stderr)
        return 2

    contract_problems = check_design_tokens(DESIGN_SOURCE, TOKEN_FILE)
    if contract_problems:
        print("UI design token contract failed (webui/design/flocks-ui-spec-v1.4.html):")
        for problem in contract_problems:
            print("  " + problem)
        print("Fix the token implementation; baseline updates cannot waive design drift.")
        return 1

    occurrences: List[Occurrence] = []
    for path, root in iter_files(roots):
        occurrences.extend(scan_file(path, root))
    counts = counts_by_file(occurrences)
    by_rule = counts_by_rule(occurrences)

    if args.list:
        for occurrence in occurrences:
            print(f"{occurrence.path}:{occurrence.line}: [{occurrence.rule}] {occurrence.text}")
        print()

    baseline = load_baseline(args.baseline)
    if args.update:
        # A partial scan (--root somewhere inside the tree) must not wipe the records
        # of files it did not look at, but everything under a scanned directory is
        # authoritative: files that disappeared drop out, so a later file at the same
        # path counts as new again.  A single file outside the repository has no
        # stable key (just its basename), so it cannot update a baseline.
        for root in roots:
            if root.is_file() and not root.is_relative_to(REPO_ROOT):
                print(f"--update needs a directory root or a file inside the repository: {root}", file=sys.stderr)
                return 2
        def covered_by(key: str) -> bool:
            for root in roots:
                if not root.is_dir():
                    continue
                if root.is_relative_to(REPO_ROOT):
                    prefix = root.relative_to(REPO_ROOT).as_posix() + "/"
                    if key.startswith(prefix):
                        return True
                else:
                    # an external tree is keyed relative to the root itself: all of it was scanned
                    return True
            return False

        merged = {k: v for k, v in baseline.items() if not covered_by(k) and k not in counts}
        merged.update(counts)
        merged = dict(sorted(merged.items()))
        scanned_all = all(root.is_dir() and covered_by(k) for root in roots for k in baseline) if baseline else True
        if baseline and not args.allow_increase:
            worse = compare(counts, baseline)
            if worse:
                print("refusing to update the baseline: these files got worse (or are new)", file=sys.stderr)
                for line in worse:
                    print("  " + line, file=sys.stderr)
                print("fix the new usages or pass --allow-increase deliberately", file=sys.stderr)
                return 1
        merged_rules = by_rule if scanned_all else {}
        write_baseline(args.baseline, merged, merged_rules)
        print(f"baseline updated: {sum(merged.values())} off-spec usage(s) in {len(merged)} file(s) -> {args.baseline}")
        return 0

    problems = compare(counts, baseline)
    total = sum(counts.values())
    allowed = sum(baseline.values())
    summary = ", ".join(f"{rule} {n}" for rule, n in by_rule.items()) or "none"
    if problems:
        print("UI spec check failed (webui/UI_SPEC.md):")
        for line in problems:
            print("  " + line)
        print(f"\n{total} off-spec usage(s) in {len(counts)} file(s); baseline allows {allowed}. By rule: {summary}")
        print("Use semantic tokens / ui-* classes for new code; run with --list to see each hit.")
        return 1
    print(f"UI spec check OK: {total} off-spec usage(s) in {len(counts)} file(s) (baseline {allowed}). By rule: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
