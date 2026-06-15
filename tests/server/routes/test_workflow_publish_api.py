from __future__ import annotations

from typing import Any

import pytest

from flocks.server.routes import workflow as workflow_routes


@pytest.mark.asyncio
async def test_publish_workflow_as_api_reuses_key_for_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workflow_id = "wf-1"
    existing_key = "existing-api-key"
    publish_calls: list[dict[str, Any]] = []
    writes: dict[str, Any] = {}

    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda requested_id: {
            "id": requested_id,
            "name": "Demo Workflow",
            "workflowJson": {
                "id": requested_id,
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
                "edges": [],
            },
        } if requested_id == workflow_id else None,
    )
    monkeypatch.setattr(workflow_routes.Config, "get_data_path", lambda: tmp_path)

    async def fake_read(key: Any, *_args: Any, **_kwargs: Any) -> Any:
        if str(key) == workflow_routes._api_service_key(workflow_id):
            return {"apiKey": existing_key}
        return None

    async def fake_write(key: Any, value: Any) -> None:
        writes[str(key)] = value

    async def fake_publish_workflow(
        requested_id: str,
        image: str | None = None,
        driver: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        publish_calls.append({
            "workflow_id": requested_id,
            "image": image,
            "driver": driver,
            "api_key": api_key,
        })
        return {
            "serviceUrl": "http://127.0.0.1:19000",
            "containerName": "local-wf-1",
            "driver": driver or "local",
            "apiKey": api_key,
        }

    monkeypatch.setattr(workflow_routes.Storage, "read", fake_read)
    monkeypatch.setattr(workflow_routes.Storage, "write", fake_write)
    monkeypatch.setattr(workflow_routes, "publish_workflow", fake_publish_workflow)

    result = await workflow_routes.publish_workflow_as_api(
        workflow_id,
        workflow_routes.WorkflowCenterPublishRequest(driver="local"),
    )

    assert publish_calls == [{
        "workflow_id": workflow_id,
        "image": None,
        "driver": "local",
        "api_key": existing_key,
    }]
    assert result["apiKey"] == existing_key
    assert writes[workflow_routes._api_service_key(workflow_id)]["apiKey"] == existing_key
