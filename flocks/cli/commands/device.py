"""Device inventory, reusing credential-masked store projections."""
from typing import Optional
import typer
from flocks.cli.commands.platform_output import run_and_render

device_app = typer.Typer(help="Inspect devices", no_args_is_help=True)


@device_app.command("list")
def device_list(
    group: Optional[str] = typer.Option(None, "--group"),
    format: str = typer.Option("table", "--format"),
):
    async def collect():
        from flocks.tool.device.store import list_devices
        from flocks.storage.storage import Storage
        await Storage.init()
        return [d.model_dump(mode="json") for d in await list_devices(group)]
    run_and_render(collect(), format, ("id", "name", "service_id", "enabled", "status"))
