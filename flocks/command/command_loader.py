"""
Command discovery and loading.

Loads command templates from Flocks and Claude-compatible locations.
"""

import glob
import os
from typing import Dict, List, Optional

from flocks.command.command import CommandInfo
from flocks.project.instance import Instance
from flocks.utils.log import Log


log = Log.create(service="command.loader")


def _parse_command_md(filepath: str, source: Optional[str] = None) -> Optional[CommandInfo]:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        command_name = os.path.splitext(os.path.basename(filepath))[0]
        description = ""
        body = content

        # Frontmatter parsing (simple)
        lines = content.strip().split("\n")
        if lines and lines[0].strip() == "---":
            end_index = None
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    end_index = i
                    break
                if lines[i].strip().startswith("description:"):
                    description = lines[i].split(":", 1)[1].strip().strip('"\'')
            if end_index is not None:
                body = "\n".join(lines[end_index + 1 :]).strip()

        if not description:
            description = f"Command: {command_name}"

        template = body.strip()
        if not template:
            template = description

        return CommandInfo(
            name=command_name,
            description=description,
            template=template,
        )
    except Exception as e:
        log.warn("command.parse.error", {"filepath": filepath, "error": str(e), "source": source})
        return None


def _scan_dir(directory: str, pattern: str, source: str, result: Dict[str, CommandInfo]) -> None:
    if not os.path.exists(directory):
        return
    try:
        search_pattern = os.path.join(directory, pattern)
        for match in glob.glob(search_pattern, recursive=True):
            command = _parse_command_md(match, source=source)
            if command:
                if command.name in result:
                    log.warn("command.duplicate", {
                        "name": command.name,
                        "existing": result[command.name].template[:60],
                        "duplicate": match,
                        "source": source,
                    })
                result[command.name] = command
                log.debug("command.found", {"name": command.name, "location": match, "source": source})
    except Exception as e:
        log.error("command.scan.error", {"directory": directory, "error": str(e), "source": source})


def discover_commands() -> Dict[str, CommandInfo]:
    """
    Discover commands from supported locations with Flocks-compatible precedence.
    """
    result: Dict[str, CommandInfo] = {}

    home_dir = os.path.expanduser("~")
    current_dir = Instance.get_directory() or os.getcwd()

    # Walk up to find .claude directories
    project_claude_dirs: List[str] = []
    check_dir = current_dir
    while True:
        claude_dir = os.path.join(check_dir, ".claude")
        if os.path.exists(claude_dir) and claude_dir not in project_claude_dirs:
            project_claude_dirs.append(claude_dir)
        parent = os.path.dirname(check_dir)
        if parent == check_dir:
            break
        check_dir = parent

    # Lowest -> highest priority
    sources: List[tuple[str, str, str]] = []
    sources.append(("claude-user", os.path.join(home_dir, ".claude", "commands"), "**/*.md"))

    try:
        from flocks.utils.compat import get_flocks_config_dir
        flocks_global_dir = str(get_flocks_config_dir(binary="opencode") / "command")
        sources.append(("flocks-global", flocks_global_dir, "**/*.md"))
    except Exception as e:
        log.warn("command.flocks_dir.error", {"error": str(e)})

    for claude_dir in project_claude_dirs:
        sources.append(("claude-project", os.path.join(claude_dir, "commands"), "**/*.md"))

    flocks_project_dir = os.path.join(current_dir, ".flocks", "command")
    sources.append(("flocks-project", flocks_project_dir, "**/*.md"))

    for source_name, base_dir, pattern in sources:
        _scan_dir(base_dir, pattern, source_name, result)

    return result
