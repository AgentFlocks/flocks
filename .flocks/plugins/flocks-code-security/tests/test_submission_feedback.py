from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from flocks.tool.registry import ToolRegistry
from flocks_code_security.coverage import normalize_dispositions, normalize_open_questions
from flocks_code_security.store import ScanStore
from flocks_code_security.tools import register_tools


@pytest.mark.parametrize(
    "decision,marker",
    [
        ({"action": "finalize", "unknown": True}, "static decision example: "),
        ({"action": "targeted_rescan", "unknown": True}, "decision example: "),
        ({"action": "targeted_rescan", "rescan": {}}, "decision example: "),
    ],
)
def test_adjudication_error_example_passes_structure_validation(tmp_path: Path, decision, marker):
    store = ScanStore(tmp_path / "audit.db")
    store.initialize()
    with pytest.raises(ValueError) as rejected:
        store.save_adjudication("missing-scan", decision)
    message = str(rejected.value)
    example, _ = json.JSONDecoder().raw_decode(message.split(marker, 1)[1])

    register_tools()
    schema = next(
        parameter.json_schema
        for parameter in ToolRegistry.get("audit_submit_adjudication").info.parameters
        if parameter.name == "decision"
    )
    jsonschema.validate(example, schema)
    # The actual store now passes field validation and reaches scan lookup.
    with pytest.raises(ValueError, match="Scan not found"):
        store.save_adjudication("missing-scan", example)
    if example["action"] == "finalize":
        assessments, _ = json.JSONDecoder().raw_decode(
            message.split("one per confirmed candidate: ", 1)[1]
        )
        example["dynamic_assessments"] = assessments
        jsonschema.validate(example, schema)
        with pytest.raises(ValueError, match="Scan not found"):
            store.save_adjudication("missing-scan", example)


@pytest.mark.parametrize("normalize", [normalize_dispositions, normalize_open_questions])
def test_coverage_error_example_can_be_resubmitted(normalize):
    with pytest.raises(ValueError) as rejected:
        normalize([{"unknown": True}])
    example = json.loads(str(rejected.value).split("Example: ", 1)[1])
    assert normalize([example])[0].items() >= example.items()


@pytest.mark.parametrize(
    "normalize,payload,field",
    [
        (normalize_dispositions, {"path": "src/parser.c", "claim": "unknown"}, "claim"),
        (normalize_open_questions, {"question": "Which path?", "category": "unknown", "blocking": False}, "category"),
    ],
)
def test_coverage_error_allowed_values_are_accepted(normalize, payload, field):
    with pytest.raises(ValueError) as rejected:
        normalize([payload])
    for value in str(rejected.value).split("allowed: ", 1)[1].split(", "):
        repaired = {**payload, field: value}
        if field == "claim" and value != "analyzed":
            repaired["reason"] = "Source unavailable or outside the analysis scope."
        if field == "category":
            repaired["blocking"] = value == "coverage_blocking"
        assert normalize([repaired])[0][field] == value
