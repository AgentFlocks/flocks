"""Agent invocation command and plain/JSON output protocol."""
import asyncio
from contextlib import chdir, redirect_stdout
from enum import Enum
import json
import math
from pathlib import Path
import sys
from typing import Optional

import typer

from flocks.cli.headless import ExecOptions, load_images, load_schema, run_headless
from flocks.session.runtime_controls import RuntimeControls


class OutputFormat(str, Enum):
    text = "text"
    json = "json"
    stream_json = "stream-json"


class PermissionMode(str, Enum):
    default = "default"
    accept_edits = "acceptEdits"
    bypass = "bypassPermissions"
    plan = "plan"
    dont_ask = "dontAsk"


class SandboxMode(str, Enum):
    off = "off"
    on = "on"
    read_only = "read-only"
    workspace_write = "workspace-write"


def split_tools(values: list[str]) -> tuple[str, ...]:
    return tuple(part.strip() for value in values for part in value.split(",") if part.strip())


def invoke_headless(options: ExecOptions, output_format: OutputFormat, output_file: Optional[Path] = None):
    output = sys.stdout
    def emit(event):
        if output_format == OutputFormat.stream_json:
            output.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            output.flush()
    async def execute():
        from flocks.cli.runtime import close_cli_resources
        try:
            return await run_headless(options, emit)
        finally:
            await close_cli_resources()
    with chdir(options.directory), redirect_stdout(sys.stderr):
        result = asyncio.run(execute())
        sys.stderr.flush()
    if output_file is not None and result["success"]:
        try:
            output_file.write_text(result["result"], encoding="utf-8")
        except OSError as exc:
            result.update(success=False, error=str(exc))
    if output_format == OutputFormat.text:
        if result["success"]:
            output.write((result["result"] or result["session_id"] or "") + "\n")
        else:
            typer.echo(result["error"], err=True)
    else:
        output.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
    output.flush()
    if not result["success"]:
        raise typer.Exit(1)
    return result


def exec_command(
    prompt: str = typer.Argument(..., help="Prompt, or - to read UTF-8 stdin"),
    directory: Path = typer.Option(Path("."), "-C", "--cd", exists=True, file_okay=False, resolve_path=True),
    model: Optional[str] = typer.Option(None, "-m", "--model", help="provider/model"),
    agent: Optional[str] = typer.Option(None, "--agent"),
    session_id: Optional[str] = typer.Option(None, "--session", help="Resume a session in this worktree"),
    last: bool = typer.Option(False, "--last", help="Resume most recently updated session in this worktree"),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--output-format"),
    json_schema: Optional[Path] = typer.Option(None, "--json-schema", exists=True, dir_okay=False),
    output_last_message: Optional[Path] = typer.Option(None, "-o", "--output-last-message"),
    permission_mode: PermissionMode = typer.Option(PermissionMode.default, "--permission-mode", help="default/dontAsk: reads only unless allowlisted; acceptEdits also allows edits; plan: reads only"),
    allowed_tools: list[str] = typer.Option([], "--allowed-tools", help="Tool-name globs, comma separated or repeated; narrows and approves tools"),
    disallowed_tools: list[str] = typer.Option([], "--disallowed-tools", help="Tool-name globs; denial takes precedence"),
    sandbox: Optional[SandboxMode] = typer.Option(None, "--sandbox", help="Docker isolation; on/read-only mounts workspace read-only; workspace-write allows writes"),
    config: list[str] = typer.Option([], "-c", "--config", help="Runtime dotted.key=value override (JSON value or string)"),
    strict_config: bool = typer.Option(False, "--strict-config", help="Reject unknown typed configuration fields"),
    images: list[Path] = typer.Option([], "-i", "--image", exists=True, dir_okay=False),
    max_budget: Optional[float] = typer.Option(None, "--max-budget", help="USD stop threshold checked between calls; one in-flight request can exceed it"),
):
    """Run an agent to completion without entering a TUI or requesting input."""
    if session_id and last:
        raise typer.BadParameter("--session and --last are mutually exclusive")
    if max_budget is not None and (not math.isfinite(max_budget) or max_budget < 0):
        raise typer.BadParameter("--max-budget must be finite and non-negative")
    if prompt == "-":
        prompt = sys.stdin.read()
    if not prompt.strip():
        raise typer.BadParameter("Prompt must not be empty")
    try:
        schema = load_schema(json_schema) if json_schema else None
        attachments = load_images(images)
    except Exception as exc:
        raise typer.BadParameter(str(exc)) from exc
    options = ExecOptions(
        directory=directory, prompt=prompt, model=model, agent=agent,
        session_id=session_id, last=last, schema=schema, images=attachments,
        config=config, strict_config=strict_config, sandbox=sandbox.value if sandbox else None,
        controls=RuntimeControls(permission_mode=permission_mode.value,
                                 allowed_tools=split_tools(allowed_tools), disallowed_tools=split_tools(disallowed_tools),
                                 max_budget=max_budget),
    )
    invoke_headless(options, output_format, output_last_message.resolve() if output_last_message else None)
