"""Workflow discovery and foreground execution."""
import asyncio
from contextlib import chdir
from dataclasses import asdict
import json
from pathlib import Path

import typer
from flocks.cli.commands.platform_output import run_and_render

workflow_app = typer.Typer(help="Discover and run workflows", no_args_is_help=True)


@workflow_app.command("repair")
def workflow_repair(
    workflow: str = typer.Argument("all", help="Known GT workflow ID, workflow.json path, or all"),
    apply: bool = typer.Option(False, "--apply", help="Back up and apply repairs; restart workflow triggers afterwards"),
    expected_sha256: str | None = typer.Option(None, "--expected-sha256", help="Require the digest from a single-file preview"),
    backup_dir: Path | None = typer.Option(None, "--backup-dir", file_okay=False),
    directory: Path = typer.Option(Path("."), "-C", "--cd", exists=True, file_okay=False, resolve_path=True),
):
    """Preview targeted fixes without replacing custom workflow settings."""
    from flocks.workflow.fs_store import workflow_scan_dirs
    from flocks.workflow.repairs import repair_workflow_file

    known_ids = ("backlog_monitor_gt", "stream_alert_triage_gt", "stream_alert_dispose_gt")
    with chdir(directory):
        explicit = Path(workflow).expanduser()
        if explicit.is_file():
            paths = [explicit.absolute()]
        elif explicit.is_dir() and (explicit / "workflow.json").is_file():
            paths = [(explicit / "workflow.json").absolute()]
        else:
            if workflow != "all" and workflow not in known_ids:
                raise typer.BadParameter("Expected an existing workflow.json path or a known GT workflow ID")
            ids = known_ids if workflow == "all" else (workflow,)
            paths = sorted({
                path.absolute()
                for root, _source in workflow_scan_dirs()
                for workflow_id in ids
                if (path := root / workflow_id / "workflow.json").is_file()
            })
        if not paths:
            raise typer.BadParameter("No matching GT workflow definitions found")
        if expected_sha256 and len(paths) != 1:
            raise typer.BadParameter("--expected-sha256 requires exactly one workflow file")
        reports = []
        errors = 0
        for path in paths:
            try:
                reports.append(repair_workflow_file(
                    path, apply=apply, expected_sha256=expected_sha256, backup_dir=backup_dir,
                ))
            except (OSError, ValueError) as exc:
                errors += 1
                reports.append({"path": str(path), "applied": False, "error": str(exc)})
    typer.echo(json.dumps({"apply": apply, "workflows": reports, "errors": errors}, ensure_ascii=False, default=str))
    if errors:
        raise typer.Exit(1)


@workflow_app.command("list")
def workflow_list(
    directory: Path = typer.Option(Path("."), "-C", "--cd", exists=True, file_okay=False, resolve_path=True),
    format: str = typer.Option("table", "--format"),
):
    async def collect():
        from flocks.workflow.center import scan_skill_workflows
        from flocks.workflow.store import WorkflowStore
        try:
            return await scan_skill_workflows(directory)
        finally:
            await WorkflowStore.close()
    run_and_render(collect(), format, ("logicalWorkflowId", "name", "workflowPath"))


@workflow_app.command("run")
def workflow_run(
    workflow: str = typer.Argument(..., help="workflow.json path or discovered workflow ID"),
    inputs: str = typer.Option("{}", "--inputs", help="JSON object of workflow inputs"),
    directory: Path = typer.Option(Path("."), "-C", "--cd", exists=True, file_okay=False, resolve_path=True),
    timeout: float = typer.Option(300.0, "--timeout", min=0.001),
):
    try:
        values = json.loads(inputs)
        if not isinstance(values, dict):
            raise ValueError("--inputs must be a JSON object")
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    async def execute():
        from flocks.workflow.center import scan_skill_workflows
        from flocks.workflow.runner import run_workflow
        from flocks.workflow.store import WorkflowStore
        from flocks.workflow.tool_context import build_workflow_tool_context, cleanup_workflow_tool_context
        context = None
        try:
            path = Path(workflow).expanduser()
            if not path.is_file():
                entries = await scan_skill_workflows(directory)
                match = next((e for e in entries if workflow in {e["workflowId"], e["logicalWorkflowId"]}), None)
                if match is None:
                    raise ValueError(f"Workflow not found: {workflow}")
                path = Path(match["workflowPath"])
            context = await build_workflow_tool_context(workflow_id=path.parent.name, action_name="cli")
            result = await asyncio.to_thread(run_workflow, workflow=path, inputs=values, timeout_s=timeout, tool_context=context)
            return asdict(result)
        finally:
            if context is not None:
                await cleanup_workflow_tool_context(context)
            await WorkflowStore.close()
    with chdir(directory):
        result = run_and_render(execute())
    if result["status"] != "SUCCEEDED":
        raise typer.Exit(1)
