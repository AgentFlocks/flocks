"""Synthetic contract checks for write/validate; no paid model or backend calls."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from flocks.situation_report.product import workspace as ws
from flocks.situation_report.product.files import atomic_write_bytes, read_json
from flocks.situation_report.product.output import ReportPublicationError, publish_validated_candidate


@pytest.fixture
def run(tmp_path, monkeypatch):
    context = {}
    for key, name, content in [
        ("template", "template.md", "## Custom chapter\n"),
        ("materials", "materials.jsonl", '{"source_type":"REPORT","source_id":"synthetic-id"}\n'),
    ]:
        path = tmp_path / name
        atomic_write_bytes(path, content.encode())
        context[key] = {"path": name, "sha256": ws.file_sha256(path), "sizeBytes": path.stat().st_size}
    monkeypatch.setattr(ws, "_resolve_run", AsyncMock(return_value=(tmp_path, {}, {})))
    monkeypatch.setattr(ws, "_load_generation_context", lambda *args: context)
    return tmp_path, {"session_id": "ses_unit", "generation_id": "gen_unit"}, context


def _validation_path(root: Path) -> Path:
    return root / "runs/gen_unit/validation.json"


@pytest.mark.asyncio
async def test_write_validates_immediately_and_duplicate_calls_are_idempotent(run):
    root, params, _ = run
    result = await ws.write_candidate_report(
        **params,
        content="## Custom chapter\n事实",
        evidence_map={"REPORT:synthetic-id": ["Custom chapter"]},
    )
    assert result["validation"]["status"] == "passed"
    assert result["validation"]["attempt"] == 1
    assert result["validation"]["candidateSHA256"] == result["sha256"]
    assert read_json(_validation_path(root)) == result["validation"]
    initial_mtime = _validation_path(root).stat().st_mtime_ns
    for _ in range(4):
        assert await ws.validate_candidate_report(**params) == result["validation"]
    assert _validation_path(root).stat().st_mtime_ns == initial_mtime


@pytest.mark.asyncio
async def test_three_distinct_candidates_limit_cannot_be_spent_by_duplicates_or_exceeded(run):
    root, params, _ = run
    result = None
    for attempt in range(1, 4):
        result = await ws.write_candidate_report(
            **params,
            content=f"# Bad title {attempt}\n\n## Custom chapter\n事实",
            evidence_map={"REPORT:synthetic-id": ["Custom chapter"]},
            expected_sha256=result["sha256"] if result else "",
        )
        assert result["validation"]["status"] == "needs_revision"
        assert result["validation"]["attempt"] == attempt
        assert await ws.validate_candidate_report(**params) == result["validation"]
    report_path = root / "work/gen_unit/report.md"
    previous_bytes = report_path.read_bytes()
    with pytest.raises(ws.ProductWorkspaceError, match="budget"):
        await ws.write_candidate_report(
            **params,
            content="## Custom chapter\n修正版",
            expected_sha256=result["sha256"],
            evidence_map={"REPORT:synthetic-id": ["Custom chapter"]},
        )
    assert report_path.read_bytes() == previous_bytes
    assert read_json(_validation_path(root)) == result["validation"]
    same = await ws.write_candidate_report(
        **params,
        content=previous_bytes.decode(),
        expected_sha256=result["sha256"],
        evidence_map={"REPORT:synthetic-id": ["Custom chapter"]},
    )
    assert same["validation"] == result["validation"]


@pytest.mark.asyncio
async def test_evidence_only_repair_revalidates_and_stale_write_is_rejected(run):
    _, params, _ = run
    content = "## Custom chapter\n事实"
    result = await ws.write_candidate_report(**params, content=content, evidence_map={})
    assert result["validation"]["issues"][0]["code"] == "evidence_map"
    corrected = await ws.write_candidate_report(
        **params,
        content=content,
        evidence_map={"REPORT:synthetic-id": ["Custom chapter"]},
        expected_sha256=result["sha256"],
    )
    assert corrected["sha256"] == result["sha256"]
    assert corrected["evidenceSHA256"] != result["evidenceSHA256"]
    assert corrected["validation"]["status"] == "passed"
    assert corrected["validation"]["attempt"] == 2
    with pytest.raises(ws.ProductWorkspaceError, match="changed"):
        await ws.write_candidate_report(**params, content=content, evidence_map={})


@pytest.mark.asyncio
async def test_concurrent_validate_does_not_spend_multiple_attempts(run):
    root, params, _ = run
    await ws.write_candidate_report(**params, content="## Custom chapter\n事实", evidence_map={})
    _validation_path(root).unlink()  # Simulate interruption before validation persistence.
    results = await asyncio.gather(*(ws.validate_candidate_report(**params) for _ in range(4)))
    assert all(value == results[0] for value in results)
    assert results[0]["attempt"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["report.md", "evidence.json"])
async def test_publication_rejects_pair_changed_after_auto_validation(run, monkeypatch, changed):
    root, params, _ = run
    result = await ws.write_candidate_report(
        **params,
        content="## Custom chapter\n事实",
        evidence_map={"REPORT:synthetic-id": ["Custom chapter"]},
    )
    assert result["validation"]["status"] == "passed"
    path = root / "work/gen_unit" / changed
    atomic_write_bytes(path, path.read_bytes() + b"\n")
    monkeypatch.setattr("flocks.situation_report.product.output.session_root", lambda _: root)
    with pytest.raises(ReportPublicationError, match="changed after validation"):
        await publish_validated_candidate(**params)
    assert not (root / "output").exists()


@pytest.mark.asyncio
async def test_changed_template_cannot_reuse_passed_result(run):
    root, params, context = run
    await ws.write_candidate_report(
        **params,
        content="## Custom chapter\n事实",
        evidence_map={"REPORT:synthetic-id": ["Custom chapter"]},
    )
    template = root / "template.md"
    atomic_write_bytes(template, b"## Another chapter\n")
    context["template"].update(sha256=ws.file_sha256(template), sizeBytes=template.stat().st_size)
    result = await ws.validate_candidate_report(**params)
    assert result["attempt"] == 2 and result["status"] == "needs_revision"
    assert result["issues"][0]["code"] == "template_headings"


@pytest.mark.asyncio
async def test_heading_and_evidence_equivalent_markdown_pass_without_repair(run):
    root, params, context = run
    template = root / 'template.md'
    atomic_write_bytes(template, b'## ATT\\&CK MAP\n')
    context['template'].update(sha256=ws.file_sha256(template), sizeBytes=template.stat().st_size)
    result = await ws.write_candidate_report(**params, content='## **ATT&CK MAP**\nFacts',
        evidence_map={'REPORT:synthetic-id':[r'ATT\&CK MAP']})
    assert result['validation']['status'] == 'passed'
    assert result['validation']['attempt'] == 1


@pytest.mark.asyncio
async def test_ambiguous_template_warns_but_does_not_invent_report_chapters(run):
    root, params, context = run
    template = root / 'template.md'
    atomic_write_bytes(template, '## 使用目标\n说明\n## 输入与证据边界\n说明'.encode())
    context['template'].update(sha256=ws.file_sha256(template), sizeBytes=template.stat().st_size)
    result = await ws.write_candidate_report(**params, content='## Custom chapter\nFacts',
        evidence_map={'REPORT:synthetic-id':['Custom chapter']})
    assert result['validation']['status'] == 'passed'
    assert result['validation']['warnings'][0]['code'] == 'template_structure_unresolved'


@pytest.mark.asyncio
async def test_validator_upgrade_rechecks_same_candidate_without_spending_revision(run):
    root, params, _ = run
    result = await ws.write_candidate_report(**params, content='## Custom chapter\nFacts',
        evidence_map={'REPORT:synthetic-id':['Custom chapter']})
    old = result['validation'] | {'validatorVersion':2, 'attempt':3, 'status':'needs_revision'}
    ws.atomic_write_json(_validation_path(root), old)
    checked = await ws.validate_candidate_report(**params)
    assert checked['validatorVersion'] == 3 and checked['attempt'] == 3 and checked['status'] == 'passed'
