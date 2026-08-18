from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from rich.console import Console

from flocks.cli.session_runner import CLISessionRunner
import flocks.cli.session_runner as cli_runner_module
from flocks.agent.agent import AgentInfo
from flocks.agent.registry import Agent
from flocks.provider.provider import Provider
from flocks.project.project import Project
from flocks.tool.registry import ToolRegistry
from flocks.session.session import Session, SessionInfo, SessionTime


def _session(session_id: str, directory: Path, *, updated: int) -> SessionInfo:
    return SessionInfo(
        id=session_id,
        projectID="default",
        directory=str(directory),
        title=session_id,
        time=SessionTime(created=updated, updated=updated),
    )


@pytest.mark.asyncio
async def test_continue_uses_latest_session_from_current_worktree(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    session_a = _session("ses-a", project_a, updated=2_000)
    session_b = _session("ses-b", project_b, updated=1_000)

    async def fake_list(project_id: str):
        assert project_id == "default"
        return [session_a, session_b]

    monkeypatch.setattr(Session, "list", fake_list)
    runner = CLISessionRunner(
        console=Console(file=StringIO()),
        directory=project_b,
    )

    resumed = await runner._get_or_create_session(
        project_id="default",
        continue_session=True,
    )

    assert resumed.id == session_b.id


@pytest.mark.asyncio
async def test_dedicated_cli_isolates_before_env_provider_and_project_bootstrap(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "hostile-target"
    target.mkdir()
    (target / ".env").write_text("UNTRUSTED_VALUE=1\n", encoding="utf-8")
    runtime_directory = tmp_path / "private-runtime"
    agent = AgentInfo(
        name="isolated-security",
        mode="primary",
        tools=["audit_prepare"],
        session_directory=str(runtime_directory),
        memory_enabled=False,
        require_dedicated_session=True,
    )
    session = _session("ses-security", runtime_directory, updated=1)
    monkeypatch.setattr(ToolRegistry, "init", MagicMock())
    monkeypatch.setattr(Agent, "get", AsyncMock(return_value=agent))
    monkeypatch.setattr(Provider, "init", AsyncMock())
    project_from_directory = AsyncMock(
        return_value={"project": MagicMock(id="security-project")}
    )
    monkeypatch.setattr(Project, "from_directory", project_from_directory)
    load_dotenv = MagicMock()
    monkeypatch.setattr(cli_runner_module, "load_dotenv", load_dotenv)
    prepare = AsyncMock(return_value=session)
    monkeypatch.setattr(
        "flocks.session.agent_policy.prepare_session_for_agent",
        prepare,
    )
    runner = CLISessionRunner(
        console=Console(file=StringIO()),
        directory=target,
        agent=agent.name,
    )
    monkeypatch.setattr(runner, "_get_or_create_session", AsyncMock(return_value=session))
    monkeypatch.setattr(runner, "_interactive_loop", AsyncMock())

    await runner.start()

    load_dotenv.assert_not_called()
    project_from_directory.assert_awaited_once_with(str(runtime_directory.resolve()))
    prepare.assert_awaited_once_with(session, agent)
