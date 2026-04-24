"""
tests/command/test_workflows_command.py

单元测试：
1. Command 注册表 — /workflows 命令存在且属性正确
2. handler.handle_slash_command — /workflows 处理分支
3. handler.handle_slash_command — /help 输出包含 /workflows 条目
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from flocks.command.command import Command, CommandInfo
from flocks.command.handler import handle_slash_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_workflow_entry(
    name: str,
    description: str = "",
    path: str = "",
    source: str = "project",
    status: str = "unpublished",
) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "workflowPath": path,
        "sourceType": source,
        "publishStatus": status,
    }


async def _collect_text(content: str) -> tuple[list[str], bool]:
    """Run handle_slash_command, collect output strings, return (texts, handled)."""
    texts: list[str] = []

    async def send_text(t: str) -> None:
        texts.append(t)

    async def send_prompt(t: str) -> None:
        pass

    handled = await handle_slash_command(content, send_text=send_text, send_prompt=send_prompt)
    return texts, handled


# ===========================================================================
# Command registration
# ===========================================================================

class TestWorkflowsCommandRegistration:

    def setup_method(self):
        # Reset class state to force re-initialization
        Command._commands = {}

    def test_workflows_command_registered(self):
        cmd = Command.get("workflows")
        assert cmd is not None

    def test_workflows_command_name(self):
        cmd = Command.get("workflows")
        assert cmd.name == "workflows"

    def test_workflows_description_not_empty(self):
        cmd = Command.get("workflows")
        assert cmd.description

    def test_workflows_template_not_empty(self):
        cmd = Command.get("workflows")
        assert cmd.template

    def test_workflows_agent_is_rex(self):
        cmd = Command.get("workflows")
        assert cmd.agent == "rex"

    def test_workflows_not_hidden(self):
        cmd = Command.get("workflows")
        assert not cmd.hidden

    def test_workflows_in_list(self):
        commands = Command.list()
        names = [c.name for c in commands]
        assert "workflows" in names

    def test_tools_skills_workflows_all_registered(self):
        """Verify the three capability-discovery commands exist together."""
        for name in ("tools", "skills", "workflows"):
            assert Command.get(name) is not None, f"Command '/{name}' not registered"

    def test_restart_command_removed(self):
        assert Command.get("restart") is None


# ===========================================================================
# /workflows handler — happy path
# ===========================================================================

class TestWorkflowsHandler:
    # handler.py does a lazy `from flocks.workflow.center import scan_skill_workflows`,
    # so we patch at the source module to intercept the import correctly.
    _SCAN_WF_CENTER = "flocks.workflow.center.scan_skill_workflows"

    async def test_returns_true_handled(self):
        entries = [_make_workflow_entry("ndr_triage")]
        with patch(self._SCAN_WF_CENTER, new_callable=AsyncMock, return_value=entries):
            texts, handled = await _collect_text("/workflows")
        assert handled

    async def test_removed_restart_command_not_handled(self):
        texts, handled = await _collect_text("/restart")
        assert handled is False
        assert texts == []

    async def test_workflow_names_in_output(self):
        entries = [
            _make_workflow_entry("ndr_triage"),
            _make_workflow_entry("global_scan"),
        ]
        with patch(self._SCAN_WF_CENTER, new_callable=AsyncMock, return_value=entries):
            texts, _ = await _collect_text("/workflows")
        output = "\n".join(texts)
        assert "ndr_triage" in output
        assert "global_scan" in output

    async def test_descriptions_in_output(self):
        entries = [_make_workflow_entry("wf", description="NDR 告警研判")]
        with patch(self._SCAN_WF_CENTER, new_callable=AsyncMock, return_value=entries):
            texts, _ = await _collect_text("/workflows")
        assert "NDR 告警研判" in "\n".join(texts)

    async def test_path_in_output(self):
        entries = [_make_workflow_entry("wf", path="/home/user/.flocks/workflow/wf/workflow.json")]
        with patch(self._SCAN_WF_CENTER, new_callable=AsyncMock, return_value=entries):
            texts, _ = await _collect_text("/workflows")
        assert "/home/user/.flocks/workflow/wf/workflow.json" in "\n".join(texts)

    async def test_source_type_in_output(self):
        entries = [
            _make_workflow_entry("proj_wf", source="project"),
            _make_workflow_entry("glob_wf", source="global"),
        ]
        with patch(self._SCAN_WF_CENTER, new_callable=AsyncMock, return_value=entries):
            texts, _ = await _collect_text("/workflows")
        output = "\n".join(texts)
        assert "project" in output
        assert "global" in output

    async def test_publish_status_in_output(self):
        entries = [_make_workflow_entry("wf", status="published")]
        with patch(self._SCAN_WF_CENTER, new_callable=AsyncMock, return_value=entries):
            texts, _ = await _collect_text("/workflows")
        assert "published" in "\n".join(texts)

    async def test_run_workflow_tip_in_output(self):
        entries = [_make_workflow_entry("wf")]
        with patch(self._SCAN_WF_CENTER, new_callable=AsyncMock, return_value=entries):
            texts, _ = await _collect_text("/workflows")
        assert "run_workflow" in "\n".join(texts)


# ===========================================================================
# /workflows handler — edge cases
# ===========================================================================

class TestWorkflowsHandlerEdgeCases:
    _SCAN_WF_CENTER = "flocks.workflow.center.scan_skill_workflows"  # patch at source

    async def test_no_workflows_returns_helpful_message(self):
        with patch(self._SCAN_WF_CENTER, new_callable=AsyncMock, return_value=[]):
            texts, handled = await _collect_text("/workflows")
        assert handled
        output = "\n".join(texts)
        assert "No workflows" in output

    async def test_no_workflows_suggests_creation(self):
        with patch(self._SCAN_WF_CENTER, new_callable=AsyncMock, return_value=[]):
            texts, _ = await _collect_text("/workflows")
        assert "workflow.json" in "\n".join(texts)

    async def test_scan_exception_handled_gracefully(self):
        with patch(self._SCAN_WF_CENTER, new_callable=AsyncMock, side_effect=Exception("scan failed")):
            texts, handled = await _collect_text("/workflows")
        assert handled
        assert "scan failed" in "\n".join(texts)

    async def test_unnamed_workflow_shown_as_placeholder(self):
        entries = [{"name": None, "description": "", "workflowPath": "", "sourceType": "project", "publishStatus": "unpublished"}]
        with patch(self._SCAN_WF_CENTER, new_callable=AsyncMock, return_value=entries):
            texts, _ = await _collect_text("/workflows")
        output = "\n".join(texts)
        assert "(unnamed)" in output or "None" not in output


# ===========================================================================
# /help handler
# ===========================================================================

class TestHelpHandler:

    async def test_help_mentions_workflows(self):
        texts, handled = await _collect_text("/help")
        assert handled
        output = "\n".join(texts)
        assert "/workflows" in output

    async def test_help_lists_core_discovery_commands(self):
        texts, _ = await _collect_text("/help")
        output = "\n".join(texts)
        assert "/tools" in output
        assert "/skills" in output
        assert "/workflows" in output

    async def test_help_returns_true(self):
        _, handled = await _collect_text("/help")
        assert handled


# ===========================================================================
# Unrecognized commands pass through
# ===========================================================================

class TestUnhandledCommands:

    async def test_non_slash_content_not_handled(self):
        _, handled = await _collect_text("not a slash command")
        assert not handled

    async def test_empty_slash_not_handled(self):
        _, handled = await _collect_text("/")
        assert not handled
