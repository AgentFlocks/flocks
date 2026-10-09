"""List models from the configured provider registry."""
from typing import Optional
import typer
from flocks.cli.commands.platform_output import run_and_render

model_app = typer.Typer(help="Inspect available models", no_args_is_help=True)


@model_app.command("list")
def model_list(
    provider: Optional[str] = typer.Option(None, "--provider"),
    format: str = typer.Option("table", "--format"),
):
    async def collect():
        from flocks.provider.provider import Provider
        from flocks.config.config import Config
        await Provider.init()
        await Provider.apply_config(await Config.get())
        return [m.model_dump(mode="json") for m in Provider.list_models(provider)]
    run_and_render(collect(), format, ("id", "name", "provider_id"))
