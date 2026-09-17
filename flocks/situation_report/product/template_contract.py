"""Conservative report heading extraction, without a built-in report outline."""

from __future__ import annotations

from html.parser import HTMLParser
import re
import string
import unicodedata

from markdown import Markdown


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []

    def handle_data(self, data):
        self.text.append(data)


def normalize_heading(value: str) -> str:
    parser = _Text()
    parser.feed(_render(value))
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", "".join(parser.text))).strip()


class _Headings(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.headings: dict[str, list[str]] = {"h1": [], "h2": []}
        self.ignored = 0
        self.tag = ""
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"pre", "blockquote"}:
            self.ignored += 1
        if tag in self.headings and not self.ignored:
            self.tag, self.text = tag, []

    def handle_data(self, data):
        if self.tag:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == self.tag:
            self.headings[tag].append(re.sub(r"\s+", " ", unicodedata.normalize("NFC", "".join(self.text))).strip())
            self.tag = ""
        if tag in {"pre", "blockquote"}:
            self.ignored = max(0, self.ignored - 1)


def report_headings(markdown: str) -> dict[str, list[str]]:
    parser = _Headings()
    parser.feed(_render(markdown))
    return parser.headings


def _render(value: str) -> str:
    md = Markdown(extensions=["fenced_code", "tables"])
    # CommonMark allows backslash escaping all ASCII punctuation; Python-Markdown
    # defaults to a smaller set (notably excluding '&'). Do not mutate its class list.
    md.ESCAPED_CHARS = list(dict.fromkeys([*md.ESCAPED_CHARS, *string.punctuation]))
    return md.convert(value)


_STRUCTURE = re.compile(
    r"(?:报告|输出|正文).*(?:结构|目录)|report\s+(?:structure|outline)|output\s+(?:structure|contract)", re.I
)
_INSTRUCTIONS = re.compile(
    r"使用目标|输入与证据|内容要求|写作要求|报告设定|写作规则|instructions?|writing requirements", re.I
)
_DECLARATION = re.compile(
    r"(?:正文|报告|输出).{0,100}(?:二级标题|二级章节|H2)|(?:report|body|output).{0,100}(?:H2|headings|sections)", re.I
)


def extract_template_contract(template: str) -> dict:
    """Prefer an explicit H2 outline, then a scoped chapter list, then a plain outline.

    Instructional or conflicting structures never become a guessed hard constraint.
    The complete template remains available to the Agent in every case.
    """
    lines = template.splitlines()
    visible: list[str] = []
    outlines: list[list[str]] = []
    fence: str | None = None
    content: list[str] = []
    prefix = ""
    for line in lines:
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})(.*)$", line)
        if fence is not None:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                nonempty = [x.strip() for x in content if x.strip()]
                if nonempty and all(re.fullmatch(r"##\s+.+", x) for x in nonempty) and _DECLARATION.search(prefix):
                    outlines.append([normalize_heading(re.sub(r"\s+#+\s*$", "", x[3:])) for x in nonempty])
                fence = None
            else:
                content.append(line)
            continue
        if marker:
            fence, content = marker[1], []
            prefix = " ".join(visible[-4:])
            continue
        visible.append(line)

    if outlines:
        if all(value == outlines[0] for value in outlines) and len(set(outlines[0])) == len(outlines[0]):
            return {
                "requiredH2": outlines[0],
                "headingSource": "explicit_h2_outline",
                "headingCheck": "enforced",
                "warnings": [],
            }
        return _uncertain("Conflicting or duplicate explicit report headings")

    sections: list[list[str]] = []
    current: list[str] | None = None
    level = 0
    for line in visible:
        heading = re.match(r"^(#{1,6})\s+(.+)", line)
        if heading:
            if current is not None and len(heading[1]) <= level:
                current = None
            if _STRUCTURE.search(normalize_heading(heading[2])):
                current, level = [], len(heading[1])
                sections.append(current)
        elif current is not None:
            current.append(line)
    candidates = [_chapter_list(section) for section in sections]
    candidates = [value for value in candidates if value]
    if candidates:
        if all(value == candidates[0] for value in candidates) and len(set(candidates[0])) == len(candidates[0]):
            return {
                "requiredH2": candidates[0],
                "headingSource": "scoped_chapter_list",
                "headingCheck": "enforced",
                "warnings": [],
            }
        return _uncertain("Conflicting or duplicate report chapter lists")

    headings = report_headings("\n".join(visible))["h2"]
    if not headings or any(_STRUCTURE.search(x) or _INSTRUCTIONS.search(x) for x in headings):
        return _uncertain("No unambiguous report outline; do not use template instruction headings as report chapters")
    if len(set(headings)) != len(headings):
        return _uncertain("Duplicate template headings")
    return {"requiredH2": headings, "headingSource": "plain_h2_outline", "headingCheck": "enforced", "warnings": []}


def _chapter_list(lines: list[str]) -> list[str]:
    values = []
    for line in lines:
        match = re.match(r"^\d+[.)]\s+\*\*([^*]+)\*\*", line)
        if match:
            value = re.sub(r"\s*[（(](?:必有|可空|可选|required|optional)[）)]\s*$", "", match[1], flags=re.I)
            values.append(normalize_heading(value))
    return values


def _uncertain(detail: str) -> dict:
    return {
        "requiredH2": [],
        "headingSource": "unresolved",
        "headingCheck": "not_enforced",
        "warnings": [{"code": "template_structure_unresolved", "detail": detail}],
    }
