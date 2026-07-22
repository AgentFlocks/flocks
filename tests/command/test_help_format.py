from __future__ import annotations

from flocks.command.command import Command
from flocks.command.direct import list_agent_safe_direct_commands
from flocks.command.help import format_help, list_help_commands


class TestHelpFormatting:
    def test_list_help_commands_filters_by_surface(self):
        commands = list_help_commands("webui")
        names = {command.name for command in commands}

        assert "help" in names
        assert "goal" in names
        assert "mcp" in names
        assert "model" not in names
        assert "status" not in names
        assert "new" not in names

    def test_format_help_uses_registry_descriptions(self):
        output = format_help(surface="webui")
        compact = Command.get("compact")

        assert compact is not None
        assert f"/compact: {compact.description}" in output

    def test_format_help_groups_direct_and_non_direct_commands(self):
        output = format_help(surface="webui")

        assert "Direct commands:" in output
        assert "Other commands (handled through the normal assistant/session flow):" in output
        assert output.index("Direct commands:") < output.index("Other commands")

    def test_format_help_does_not_emit_raw_angle_placeholders(self):
        output = format_help(surface="webui")

        assert "<focus>" not in output
        assert "<name>" not in output
        assert "<server>" not in output

    def test_agent_safe_help_only_lists_agent_safe_direct_commands(self):
        commands = list_agent_safe_direct_commands()
        output = format_help(commands=commands, agent_safe=True)

        assert "Direct commands:" in output
        assert "Other commands (handled through the normal assistant/session flow):" not in output
        assert "/help" in output
        assert "/tools" in output
        assert "/skills" in output
        assert "/agents" in output
        assert "/workflows" in output
        assert "/mcp" in output
        assert "/goal" not in output
        assert "/clear" not in output
        assert "/plan" not in output
        assert "/compact" not in output
        assert "refresh `server`" not in output

    def test_format_help_tips_only_show_extra_usage(self):
        output = format_help(surface="webui")

        assert "Tips:" in output
        assert "/tools [list|refresh|info `name`|create `requirement`]" in output
        assert "/skills [list|refresh]" in output
        assert "/mcp [list|status|tools|refresh `server`]" in output
        assert "/clear clears the screen" not in output
        assert "/workflows - list all available workflows" not in output
