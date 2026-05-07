import importlib.util
from pathlib import Path

from flocks.tool.registry import ToolContext, ToolResult


def _load_core_module():
    script_path = Path(__file__).with_name("qingteng.handler.py")
    spec = importlib.util.spec_from_file_location("_flocks_qingteng_core", str(script_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create import spec for {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def host_list(ctx: ToolContext, os_type: str = "linux", page: int = 0, size: int = 50, **kwargs) -> ToolResult:
    core = _load_core_module()
    return await core.assets(
        ctx,
        action="list",
        resource="host",
        os_type=os_type,
        page=page,
        size=size,
        **kwargs,
    )
