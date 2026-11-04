import json
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from flocks.workflow.errors import WorkflowValidationError
from flocks.workflow.models import Workflow
from flocks.workflow.runner import run_workflow
from flocks.workflow.validation import python_syntax_errors


def _workflow(code: str):
    return {
        "name": "syntax-check", "start": "prepare",
        "nodes": [
            {"id": "prepare", "type": "python", "code": "raise AssertionError('must not execute')"},
            {"id": "analyse", "type": "python", "code": code},
        ],
        "edges": [{"from": "prepare", "to": "analyse"}],
    }


def test_syntax_check_reports_node_and_line_without_execution():
    issues = python_syntax_errors(Workflow.from_dict(_workflow("if True:\n    x = 1\n        y = 2")))
    assert len(issues) == 1
    assert issues[0]["node_id"] == "analyse"
    assert issues[0]["line"] == 3


def test_invalid_later_node_is_rejected_before_any_side_effect():
    with pytest.raises(WorkflowValidationError, match="analyse.*line 1"):
        run_workflow(workflow=_workflow("return 1"), ensure_requirements=False)


def test_logic_node_with_existing_python_is_also_checked():
    workflow = _workflow("if True:\npass")
    workflow["nodes"][1].update(type="logic", description="Existing generated code")
    with pytest.raises(WorkflowValidationError, match="analyse.*line 2"):
        run_workflow(workflow=workflow, ensure_requirements=False, use_llm=False)


@pytest.mark.parametrize("node_type", ["branch", "loop"])
def test_non_executable_branch_code_is_not_rejected(node_type):
    workflow = _workflow("if True:\npass")
    workflow["nodes"][1]["type"] = node_type
    assert python_syntax_errors(Workflow.from_dict(workflow)) == []


def test_file_syntax_is_checked_before_logic_codegen_or_cache_write(tmp_path, monkeypatch):
    import flocks.workflow.runner as runner

    workflow = _workflow("if True:\npass")
    workflow["nodes"].append({"id": "generate", "type": "logic", "description": "Generate some Python"})
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(workflow), encoding="utf-8")
    compile_workflow = Mock()
    dump_workflow = Mock()
    monkeypatch.setattr(runner, "compile_workflow", compile_workflow)
    monkeypatch.setattr(runner, "dump_workflow", dump_workflow)
    with pytest.raises(WorkflowValidationError, match="analyse"):
        run_workflow(workflow=path, ensure_requirements=False)
    compile_workflow.assert_not_called()
    dump_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_save_rejects_invalid_python_before_writing(monkeypatch):
    from flocks.server.routes import workflow as routes

    writes = []
    monkeypatch.setattr(routes, "_write_workflow_to_fs", lambda *args, **kwargs: writes.append(args))
    req = routes.WorkflowCreateRequest(name="invalid", workflowJson=_workflow("if True:\npass"))
    with pytest.raises(HTTPException) as error:
        await routes.create_workflow(req)
    assert error.value.status_code == 400
    assert "analyse" in error.value.detail
    assert writes == []


@pytest.mark.asyncio
async def test_run_rejects_invalid_python_before_creating_execution(monkeypatch):
    from flocks.server.routes import workflow as routes

    monkeypatch.setattr(routes, "_read_workflow_from_fs", lambda _id: {"workflowJson": _workflow("if True:\npass")})
    create = AsyncMock()
    monkeypatch.setattr(routes, "create_execution_record", create)
    with pytest.raises(HTTPException) as error:
        await routes.run_workflow_endpoint("wf", routes.WorkflowRunRequest(inputs={}))
    assert error.value.status_code == 400
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabling_trigger_rejects_invalid_workflow_before_write(monkeypatch):
    from flocks.server.routes import workflow as routes
    from flocks.workflow.triggers.models import TriggerDefinition

    writes = []
    monkeypatch.setattr(routes, "_write_workflow_to_fs", lambda *args, **kwargs: writes.append(args))
    trigger = TriggerDefinition(id="schedule", type="schedule", enabled=True, source={"intervalSeconds": 30})
    with pytest.raises(HTTPException) as error:
        await routes._persist_workflow_triggers("wf", {"workflowJson": _workflow("return 1")}, [trigger])
    assert error.value.status_code == 400
    assert writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["kafka", "poller", "syslog"])
async def test_legacy_enable_rejects_syntax_before_persisting_config(kind, monkeypatch):
    from flocks.server.routes import workflow as routes

    monkeypatch.setattr(routes, "_read_workflow_from_fs", lambda _id: {"workflowJson": _workflow("if True:\npass")})
    put_config = AsyncMock()
    monkeypatch.setattr(routes.WorkflowStore, "put_config", put_config)
    function, request = {
        "kafka": (routes.save_kafka_config, routes.KafkaConfigRequest(enabled=True)),
        "poller": (routes.save_workflow_poller_config, routes.WorkflowPollerConfigRequest(enabled=True)),
        "syslog": (routes.save_syslog_config, routes.SyslogConfigRequest(enabled=True)),
    }[kind]
    with pytest.raises(HTTPException) as error:
        await function("wf", request)
    assert error.value.status_code == 400
    assert "analyse" in error.value.detail
    put_config.assert_not_awaited()
