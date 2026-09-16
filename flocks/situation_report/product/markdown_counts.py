"""Conservative structural counts, independent of any built-in report template."""

from __future__ import annotations

import re
from typing import Any
from xml.etree.ElementTree import Element

from markdown import Markdown
from markdown.treeprocessors import Treeprocessor


class _CaptureTree(Treeprocessor):
    root: Element

    def run(self, root: Element) -> None:
        self.root = root


def _text(node: Element) -> str:
    return "".join(node.itertext()).strip()


def _record_count(block: list[Element]) -> int | None:
    # Subheadings contain nested fields/lists/tables; count the records, not their fields.
    subheadings = [i for i, node in enumerate(block) if node.tag == "h4"]
    if subheadings:
        is_subgroup = [
            bool(re.search(r"\d+\s*(?:起|条|项|个|events?|items?|records?)[）)]", _text(block[i]), re.I))
            for i in subheadings
        ]
        if any(is_subgroup):
            # H4 can be a subgroup, not an event. Never count four subgroups as four events.
            if not all(is_subgroup):
                return None
            bounds = [*subheadings, len(block)]
            child_counts = [_record_count(block[start + 1 : end]) for start, end in zip(bounds, bounds[1:])]
            if any(value is None for value in child_counts):
                return None
            return sum(value for value in child_counts if value is not None)
        if all(re.match(r"^(?:(?:事件|event|incident)\s*\d+|\d+[.、)])", _text(block[i]), re.I) for i in subheadings):
            return len(subheadings)
        return None  # Unlabelled H4s may be analysis/impact sub-sections, not event records.
    counts: list[int] = []
    title_headers = {"标题", "事件标题", "title", "event", "event title"}
    for node in block:
        if node.tag == "table":
            headers = {_text(cell).casefold() for cell in node.findall("./thead/tr/th")}
            if headers & title_headers:
                counts.append(len(node.findall("./tbody/tr")))
    heads: list[Element] = []
    for node in block:
        if node.tag in {"ul", "ol"}:
            items = list(node)
        elif node.tag == "p" and re.match(r"^(?:\d+[.、)]|事件\s*\d+|event\s*\d+)", _text(node), re.I):
            items = [node]
        else:
            continue
        for item in items:
            head = item.find("p") if item.find("p") is not None else item
            heads.append(head)
    # Flat field lists repeat "title" once per record; other fields are not more records.
    explicit_titles = sum(
        bool(re.match(r"^(?:标题|事件标题|title|event title)\s*[：:]", _text(head), re.I)) for head in heads
    )
    if explicit_titles:
        counts.append(explicit_titles)
    else:
        list_count = 0
        unknown_items = False
        # Field labels may be bold or plain, including loosely indented Markdown sublists.
        field_labels = {
            "摘要",
            "时间",
            "日期",
            "来源",
            "影响",
            "建议",
            "严重度",
            "受害组织",
            "关联团伙",
            "summary",
            "date",
            "source",
            "severity",
            "victim",
            "victim organization",
            "actor",
        }
        for head in heads:
            if _text(head).split("：", 1)[0].split(":", 1)[0].strip().casefold() in field_labels:
                continue
            strong = head.find("strong")
            if strong is None or (head.text or "").strip():
                unknown_items = True
                continue
            list_count += 1
        if list_count:
            if unknown_items:
                return None  # A partially recognized list cannot be treated as a complete count.
            counts.append(list_count)
    if len(counts) == 1:
        return counts[0]
    if counts or any(_text(node) for node in block):
        return None  # Multiple representations, prose, or unsupported format: not zero.
    return 0


def declared_group_counts(report: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return hard mismatches and non-blocking unrecognized/ambiguous counts separately."""
    md = Markdown(extensions=["tables", "fenced_code"])
    capture = _CaptureTree(md)
    md.treeprocessors.register(capture, "report_count_tree", 0)
    md.convert(report)
    nodes = list(capture.root)
    pattern = re.compile(
        r"^(.+?)[（(]\s*(\d+)\s*(?:起|条|项|个|events?|items?|records?)\s*[）)]\s*$",
        re.I,
    )
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        if node.tag != "h3" or not (match := pattern.match(_text(node))):
            continue
        end = index + 1
        while end < len(nodes) and nodes[end].tag not in {"h1", "h2", "h3"}:
            end += 1
        expected = int(match.group(2))
        actual = _record_count(nodes[index + 1 : end])
        if actual is None:
            warnings.append(
                {
                    "code": "declared_group_count_unrecognized",
                    "heading": _text(node),
                    "expected": expected,
                    "actual": None,
                    "detail": "Count could not be established reliably; verify against the materials. "
                    "This warning is not evidence of zero records or a verified count.",
                }
            )
        elif actual != expected:
            issues.append(
                {
                    "code": "declared_group_count",
                    "heading": _text(node),
                    "expected": expected,
                    "actual": actual,
                    "detail": "The count declared by this report subheading does not match its listed records",
                }
            )
    return issues, warnings
