"""A suite child the deployment serves is not installed locally, and counts
as present only while the deployment actually serves it."""

from __future__ import annotations

import json
from typing import Dict, Optional, Sequence, Tuple

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flocks.content import ContentBody, ContentEntry, ContentRef, register_source, reset_sources
from flocks.content.managed import ManagedContent
from flocks.hub import catalog, installer, local
from flocks.server.auth import require_admin, require_user
from flocks.server.routes.hub import router

CHILD = "stream_alert_triage"
WORKFLOW_JSON = {"start": "p", "nodes": [{"id": "p", "type": "python", "code": "outputs['x'] = 1"}], "edges": []}


class Deployment:
    def __init__(self) -> None:
        self.latest: Dict[Tuple[str, str], str] = {("workflow", CHILD): "1"}

    @property
    def name(self) -> str:
        return "deployment"

    def supports(self, kind: str) -> bool:
        return kind in {"agent", "skill", "workflow"}

    async def list_entries(self, kind: str) -> Sequence[ContentEntry]:
        return [
            ContentEntry(ref=ContentRef(kind=k, content_id=cid, version=v), name=cid)
            for (k, cid), v in self.latest.items() if k == kind
        ]

    async def fetch(self, ref: ContentRef) -> Optional[ContentBody]:
        version = self.latest.get((ref.kind, ref.content_id))
        if version is None:
            return None
        return ContentBody(ref=ContentRef(kind=ref.kind, content_id=ref.content_id, version=version),
                           text=json.dumps(WORKFLOW_JSON))


@pytest.fixture
async def deployment():
    reset_sources()
    ManagedContent.reset()
    source = Deployment()
    register_source(source, priority=0, exclusive_kinds=("agent", "skill", "workflow"))
    await ManagedContent.refresh()
    yield source
    reset_sources()
    ManagedContent.reset()


@pytest.fixture
async def suite_client(isolated_hub_env, monkeypatch):
    async def refresh(*args):
        pass

    monkeypatch.setattr(installer, "_refresh_runtime", refresh)
    monkeypatch.setattr(installer, "_build_webui_pages", lambda *args: None)
    catalog.clear_catalog_caches()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: object()
    app.dependency_overrides[require_user] = lambda: object()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    catalog.clear_catalog_caches()


async def _suite_state(client) -> str:
    response = await client.get("/hub/scene-suites")
    assert response.status_code == 200
    return next(item["state"] for item in response.json() if item["id"] == "soc-workspace")


async def test_a_served_child_is_skipped_and_counts_as_present(suite_client, deployment):
    from flocks.hub.update_protection import build_plan

    plan = build_plan("component", "soc-workspace")
    assert ("workflow", CHILD) not in {(item["type"], item["id"]) for item in plan["items"]}

    await installer.install_plugin("component", "soc-workspace")

    assert local.get_record("workflow", CHILD) is None
    assert await _suite_state(suite_client) == "installed"


async def test_a_withdrawn_child_is_neither_installed_nor_counted(suite_client, deployment):
    del deployment.latest[("workflow", CHILD)]
    await ManagedContent.refresh()

    with pytest.raises((PermissionError, RuntimeError), match="not available"):
        await installer.install_plugin("component", "soc-workspace")
    assert local.get_record("workflow", CHILD) is None


async def test_an_agent_package_naming_a_managed_agent_is_refused(tmp_path, deployment):
    deployment.latest[("agent", "sealed-analyst")] = "1"

    async def _fetch(ref):
        return ContentBody(ref=ContentRef(kind=ref.kind, content_id=ref.content_id, version="1"),
                           text="---\nname: sealed-analyst\n---\nprompt")

    deployment.fetch = _fetch
    await ManagedContent.refresh()
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "agent.yaml").write_text("name: sealed-analyst\nprompt: mine\n", encoding="utf-8")

    with pytest.raises(PermissionError, match="sealed-analyst"):
        installer._refuse_managed_definition_name("agent", "innocent-alias", package)


# Review round 3 (batch 2): the suite state follows what the deployment serves,
# and a local copy under a managed id plays no part in it.

def _stale_local_copy():
    stale = local.install_dir("workflow", CHILD)
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "workflow.json").write_text(json.dumps(WORKFLOW_JSON), encoding="utf-8")
    return stale


async def test_the_suite_state_follows_the_deployment_without_a_cache_reset(suite_client, deployment):
    await installer.install_plugin("component", "soc-workspace")
    assert await _suite_state(suite_client) == "installed"

    del deployment.latest[("workflow", CHILD)]
    await ManagedContent.refresh()
    assert await _suite_state(suite_client) == "partial"

    deployment.latest[("workflow", CHILD)] = "2"
    await ManagedContent.refresh()
    assert await _suite_state(suite_client) == "installed"


async def test_a_local_copy_of_a_withdrawn_child_does_not_complete_the_suite(suite_client, deployment):
    await installer.install_plugin("component", "soc-workspace")
    _stale_local_copy()
    del deployment.latest[("workflow", CHILD)]
    await ManagedContent.refresh()

    assert await _suite_state(suite_client) == "partial"


async def test_a_stale_local_copy_of_a_served_child_does_not_make_the_suite_outdated(suite_client, deployment):
    await installer.install_plugin("component", "soc-workspace")
    _stale_local_copy()

    assert await _suite_state(suite_client) == "installed"
