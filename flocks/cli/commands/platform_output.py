"""Shared output/async boundary for platform command groups."""
import asyncio
from contextlib import redirect_stdout
import json
import sys

import typer
from rich.console import Console
from rich.table import Table


def run_and_render(operation, output_format: str = "json", columns: tuple[str, ...] = ()):
    if output_format not in {"json", "table"}:
        operation.close()
        raise typer.BadParameter("--format must be json or table")
    async def execute():
        from flocks.cli.runtime import close_cli_resources
        try:
            return await operation
        finally:
            await close_cli_resources()
    try:
        with redirect_stdout(sys.stderr):
            data = asyncio.run(execute())
            sys.stderr.flush()
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if output_format == "json":
        typer.echo(json.dumps(data, ensure_ascii=False, default=str))
    else:
        table = Table(*columns)
        for row in data:
            table.add_row(*(str(row.get(column, "")) for column in columns))
        Console().print(table)
    return data
