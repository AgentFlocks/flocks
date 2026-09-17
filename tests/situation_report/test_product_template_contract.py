"""Synthetic format regressions reproducing the supplied template's syntax, not report data."""

import pytest

from flocks.situation_report.product.template_contract import extract_template_contract, report_headings
from flocks.situation_report.product.workspace import _heading_sequence_issue


def test_explicit_fenced_outline_excludes_instruction_sections():
    template = """## 使用目标
说明，不是报告内容。
## 输入与证据边界
说明。
## 输出结构契约
正文恰好包含以下两个二级标题，文字和顺序保持一致：
```markdown
## 01｜自定义结论
## 02｜自定义行动
```
## 五章内容要求
### 01｜自定义结论
示例说明。
"""
    contract = extract_template_contract(template)
    assert contract["requiredH2"] == ["01｜自定义结论", "02｜自定义行动"]
    assert contract["headingCheck"] == "enforced"


def test_only_scoped_top_level_chapter_list_is_used():
    template = """## 使用目标
1. **不要当作章节**
## 报告结构与各章要求
1. **Summary**（必有）
   1. **Nested instruction**
2. **ATT\\&CK MAP（可空）**
## 写作要求
1. **也不是章节**
"""
    assert extract_template_contract(template)["requiredH2"] == ["Summary", "ATT&CK MAP"]


@pytest.mark.parametrize(
    "expected,actual",
    [
        (r"ATT\&CK MAP", "ATT&CK MAP"),
        ("**Action**", "Action"),
        ("A &amp; B", "A & B"),
        ("`Evidence`", "Evidence"),
    ],
)
def test_equivalent_markdown_headings_do_not_require_revision(expected, actual):
    assert _heading_sequence_issue([expected], [actual]) is None


def test_rendered_headings_ignore_examples_and_support_closing_hashes():
    assert report_headings("## **Summary** ##\n\n```markdown\n## example\n```\n\n> ## quote\n") == {
        "h1": [],
        "h2": ["Summary"],
    }


@pytest.mark.parametrize("actual", [["B", "A"], ["A"], ["A", "B", "C"], ["A", "A", "B"]])
def test_real_missing_extra_duplicate_or_reordered_chapters_still_fail(actual):
    assert _heading_sequence_issue(["A", "B"], actual)


def test_instruction_only_and_conflicting_outlines_are_explicitly_unresolved():
    for template in [
        "## 使用目标\n说明\n## 输入与证据边界\n说明",
        "Report H2 headings:\n```md\n## One\n```\nReport H2 headings:\n```md\n## Two\n```",
    ]:
        result = extract_template_contract(template)
        assert result["requiredH2"] == [] and result["headingCheck"] == "not_enforced"
        assert result["warnings"][0]["code"] == "template_structure_unresolved"


def test_plain_user_outline_remains_supported_and_examples_are_not_contracts():
    template = "## Executive Context\nText\n## Decision Matrix\nExample:\n```md\n## Ignored example\n```"
    assert extract_template_contract(template)["requiredH2"] == ["Executive Context", "Decision Matrix"]


def test_literal_markdown_punctuation_in_heading_is_not_normalized_twice():
    contract = extract_template_contract(r"## \*Literal\*")
    assert contract["requiredH2"] == ["*Literal*"]
    assert _heading_sequence_issue(contract["requiredH2"], ["Literal"], normalized=True)
