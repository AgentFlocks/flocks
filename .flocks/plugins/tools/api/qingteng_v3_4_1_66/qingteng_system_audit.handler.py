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


async def system_audit(
    ctx: ToolContext,
    eventName: str | None = None,
    userName: str | None = None,
    page: int = 0,
    size: int = 20,
    sorts: str | None = None,
    **kwargs,
) -> ToolResult:
    core = _load_core_module()
    return await core.system_audit(
        ctx,
        eventName=eventName,
        userName=userName,
        page=page,
        size=size,
        sorts=sorts,
        **kwargs,
    )
