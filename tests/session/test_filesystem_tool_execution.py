from __future__ import annotations

from types import SimpleNamespace

import pytest

from flocks.session.execution_profile import profile_from_session
from flocks.session.tool_execution import _filesystem_action_payload
from flocks.workspace.manager import WorkspaceManager


@pytest.fixture(autouse=True)
def _isolated_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path):
    workspace_dir = tmp_path / "workspace"
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace_dir))
    WorkspaceManager._instance = None
    yield
    WorkspaceManager._instance = None


def test_execution_profile_separates_workspace_root_and_project_root() -> None:
    session = SimpleNamespace(
        id="session-1",
        project_id="project-1",
        directory="/projects/current",
        agent="rex",
        owner_username="alice",
        metadata={},
    )

    profile = profile_from_session(session)

    assert profile["workspace_dir"].endswith("/workspace")
    assert profile["project_root"] == "/projects/current"


def test_non_agent_tool_context_does_not_create_filesystem_action() -> None:
    action = _filesystem_action_payload(
        session_id="workflow",
        message_id="message-1",
        agent="",
        tool_name="write",
        tool_input={"filePath": "/tmp/a.txt", "content": "x"},
        profile={
            "workspace_dir": "/tmp",
            "project_root": "/tmp",
            "permission_mode": "auto-allow-all",
            "runtime_mode": "exe-mode",
        },
        tool_context_extra={},
    )

    assert action is None


def test_agent_tool_context_uses_trusted_project_root() -> None:
    action = _filesystem_action_payload(
        session_id="session-1",
        message_id="message-1",
        agent="rex",
        tool_name="write",
        tool_input={"filePath": "/projects/current/a.txt", "content": "x"},
        profile={
            "workspace_dir": "/tmp/workspace",
            "project_root": "/projects/current",
            "project_id": "project-1",
            "permission_mode": "require-confirm",
            "runtime_mode": "dev-mode",
        },
        tool_context_extra={"agent_execution_session": True},
    )

    assert action is not None
    assert action["cwd"] == "/projects/current"
    assert action["workspace_root"] == "/tmp/workspace"
    assert action["project"]["root"] == "/projects/current"
    assert action["project"]["id"] == "project-1"
    assert action["agent_execution_session"] is True


def test_apply_patch_extracts_single_action() -> None:
    action = _filesystem_action_payload(
        session_id="session-1",
        message_id="message-1",
        agent="rex",
        tool_name="apply_patch",
        tool_input={
            "patchText": (
                "*** Begin Patch\n"
                "*** Add File: added.txt\n"
                "+new\n"
                "*** End Patch\n"
            )
        },
        profile={
            "workspace_dir": "/projects/current",
            "project_root": "/projects/current",
            "project_id": "project-1",
            "permission_mode": "require-confirm",
            "runtime_mode": "dev-mode",
        },
        tool_context_extra={"agent_execution_session": True},
    )

    assert action is not None
    assert action["target_path"] is None
    apply_patch_action = action.get("apply_patch_action")
    assert isinstance(apply_patch_action, dict)
    assert apply_patch_action["operation"] == "write"
    assert apply_patch_action["target_path"] == "/projects/current/added.txt"


def test_apply_patch_multi_file_results_in_missing_action() -> None:
    action = _filesystem_action_payload(
        session_id="session-1",
        message_id="message-1",
        agent="rex",
        tool_name="apply_patch",
        tool_input={
            "patchText": (
                "*** Begin Patch\n"
                "*** Add File: one.txt\n"
                "+one\n"
                "*** Add File: two.txt\n"
                "+two\n"
                "*** End Patch\n"
            )
        },
        profile={
            "workspace_dir": "/projects/current",
            "project_root": "/projects/current",
            "project_id": "project-1",
            "permission_mode": "require-confirm",
            "runtime_mode": "dev-mode",
        },
        tool_context_extra={"agent_execution_session": True},
    )

    assert action is not None
    assert action.get("apply_patch_action") is None
