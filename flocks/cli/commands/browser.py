"""Browser CLI passthrough command."""

import typer

from flocks.browser import run as browser_run


BROWSER_CONTEXT_SETTINGS = {"allow_extra_args": True, "ignore_unknown_options": True}


def browser_command(ctx: typer.Context) -> None:
    """Forward raw browser arguments to the runtime entrypoint."""
    browser_run.main(list(ctx.args))
