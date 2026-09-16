"""Synthetic structural regressions, not live report-quality examples."""

import pytest

from flocks.situation_report.product.markdown_counts import declared_group_counts


@pytest.mark.parametrize(
    "body",
    [
        "- **第一起**：甲\n- **第二起**：乙",
        "1. **第一起**\n2. **第二起**",
        "**1. 第一事件**\n\n**2. 第二事件**",
        "#### 事件 1\n- **标题**：甲\n- **摘要**：正文\n\n#### 事件 2\n正文",
        "- **第一起**\n\n    - **细节**：甲\n\n- **第二起**\n\n    - **细节**：乙",
        "| Title | Summary |\n|---|---|\n| A | a |\n| B | b |",
        "- **标题**：第一起\n- **摘要**：甲\n- **严重度**：高\n- **受害组织**：甲\n"
        "- **标题**：第二起\n- **摘要**：乙\n- **关联团伙**：未知",
        "- **第一起**\n  - 摘要：甲\n  - 严重度：高\n\n- **第二起**\n  - 摘要：乙\n  - 受害组织：乙",
    ],
)
def test_recognized_formats_accept_correct_count_and_reject_mismatch(body):
    assert declared_group_counts("### Any group (2 events)\n\n" + body) == ([], [])
    issues, warnings = declared_group_counts("### Any group (3 events)\n\n" + body)
    assert len(issues) == 1 and issues[0]["actual"] == 2
    assert not warnings


@pytest.mark.parametrize(
    "body",
    [
        "文段描述两个事件，未列出可可靠识别的结构。",
        "- **第一起**\n- 非结构化的另一事件",
        "| Organization | Quantity |\n|---|---|\n| A | 1 |",
    ],
)
def test_unsupported_or_partial_structure_is_unknown_not_zero(body):
    issues, warnings = declared_group_counts("### 分组（2 起）\n\n" + body)
    assert not issues
    assert warnings[0]["actual"] is None


def test_code_and_nested_fields_are_not_records():
    report = "### 分组（1 起）\n\n- **真正事件**\n\n```md\n- **示例**\n### 伪分组（9 起）\n```"
    assert declared_group_counts(report) == ([], [])


def test_multiple_representations_are_not_assumed_additive():
    issues, warnings = declared_group_counts(
        "### 分组（2 起）\n\n- **事件甲**\n- **事件乙**\n\n| Title |\n|---|\n| 事件甲 |\n| 事件乙 |"
    )
    assert not issues and warnings


def test_empty_group_has_actual_zero():
    assert declared_group_counts("### 分组（0 起）\n")[0] == []
    assert declared_group_counts("### 分组（1 起）\n")[0][0]["actual"] == 0


def test_subgroup_headings_are_not_counted_as_events():
    report = (
        "### Group (3 events)\n\n#### Subgroup A (2 events)\n\n"
        "| Title |\n|---|\n| A |\n| B |\n\n"
        "#### Subgroup B (1 event)\n\n| Title |\n|---|\n| C |"
    )
    assert declared_group_counts(report) == ([], [])


def test_analysis_subheadings_are_not_assumed_to_be_event_records():
    issues, warnings = declared_group_counts(
        "### Group (2 events)\n\n#### Background\nA and B\n\n#### Impact\nAnalysis\n\n#### Response\nAdvice"
    )
    assert not issues and warnings[0]["actual"] is None
