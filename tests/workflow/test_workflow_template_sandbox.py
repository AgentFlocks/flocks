from __future__ import annotations

import pytest

from flocks.workflow.engine import WorkflowEngine
from flocks.workflow.errors import NodeExecutionError
from flocks.workflow.models import Node, Workflow


def _engine() -> WorkflowEngine:
    workflow = Workflow.from_dict(
        {
            "start": "start",
            "nodes": [{"id": "start", "type": "python", "code": "outputs['ok'] = True"}],
            "edges": [],
        }
    )
    return WorkflowEngine(workflow)


def test_llm_node_prompt_uses_jinja_sandbox() -> None:
    node = Node.model_validate(
        {
            "id": "llm",
            "type": "llm",
            "prompt": "{{ ''.__class__.__mro__ }}",
        }
    )

    with pytest.raises(NodeExecutionError, match="Prompt template render failed"):
        _engine()._execute_llm_node(node, {})


def test_http_request_node_url_uses_jinja_sandbox() -> None:
    node = Node.model_validate(
        {
            "id": "http",
            "type": "http_request",
            "method": "GET",
            "url": "{{ ''.__class__.__mro__ }}",
        }
    )

    with pytest.raises(NodeExecutionError, match="HTTP request template render failed"):
        _engine()._execute_http_request_node(node, {})
