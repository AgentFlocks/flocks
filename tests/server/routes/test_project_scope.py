from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from flocks.server.routes import project


@pytest.mark.asyncio
async def test_workbench_hides_audit_projects_but_audit_can_access_existing_projects(monkeypatch):
    entries = [SimpleNamespace(id='normal', scope='workbench'), SimpleNamespace(id='audit', scope='code-security')]
    monkeypatch.setattr(project, 'require_user', lambda request: SimpleNamespace(id='owner'))
    monkeypatch.setattr(project, '_list_project_summaries', AsyncMock(return_value=entries))
    assert [p.id for p in await project.list_projects(None, search=None, scope='workbench')] == ['normal']
    assert [p.id for p in await project.list_projects(None, search=None, scope='code-security')] == ['normal', 'audit']
