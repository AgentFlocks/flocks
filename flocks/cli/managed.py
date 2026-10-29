"""
CLI commands that would run a runtime of their own refuse in a managed install.

A managed install runs Flocks under its service manager; that process is the
one the deployment verified and configured, with everything the deployment
registered -- policy, redaction, audit -- attached to it.  A runtime started
from a shell (an interactive or headless agent turn, a workflow run, an MCP or
ACP server) would carry none of that, so those commands stop and point at the
service instead.
"""

from __future__ import annotations

import typer

#: Exit status of a refused command (usage error, as for a bad argument).
MANAGED_REFUSAL_EXIT_CODE = 2


def managed_refusal_message(command: str) -> str:
    return (
        f"{command} is not available in a managed install: Flocks runs under its service "
        "manager here. Use the WebUI, or `flocks tui --host <host> --port <port>` to attach "
        "to the running service."
    )


def local_runtime_refused() -> bool:
    """Whether this install is managed, so no local runtime may start."""
    from flocks.updater.deploy import declared_managed

    return declared_managed()


def refuse_local_runtime_when_managed(command: str) -> None:
    """Exit with :data:`MANAGED_REFUSAL_EXIT_CODE` when *command* would start a runtime here."""
    if not local_runtime_refused():
        return
    typer.echo(managed_refusal_message(command), err=True)
    raise typer.Exit(MANAGED_REFUSAL_EXIT_CODE)


__all__ = [
    "MANAGED_REFUSAL_EXIT_CODE",
    "local_runtime_refused",
    "managed_refusal_message",
    "refuse_local_runtime_when_managed",
]
