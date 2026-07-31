from __future__ import annotations

from types import SimpleNamespace

from flocks.session.execution_profile import profile_from_session
from flocks.session.tool_execution import _filesystem_action_payload


def test_execution_profile_carries_trusted_session_project_directory() -> None:
    session = SimpleNamespace(
        id="session-1",
        project_id="project-1",
        directory="/projects/current",
        agent="rex",
        owner_username="alice",
        metadata={},
    )

    profile = profile_from_session(session)

    assert profile["workspace_dir"] == "/projects/current"
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
            "workspace_dir": "/projects/current",
            "project_root": "/projects/current",
            "project_id": "project-1",
            "permission_mode": "require-confirm",
            "runtime_mode": "dev-mode",
        },
        tool_context_extra={"agent_execution_session": True},
    )

    assert action is not None
    assert action["cwd"] == "/projects/current"
    assert action["project"]["root"] == "/projects/current"
    assert action["project"]["id"] == "project-1"
    assert action["agent_execution_session"] is True


def test_apply_patch_does_not_expand_batch_actions() -> None:
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
                "*** Update File: old.txt -> moved.txt\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** Delete File: deleted.txt\n"
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
    assert "batch_actions" not in action
    assert action["target_path"] is None
