"""Before a call to REA's tools, follow what another Flocks process did to the service.

An uninstall (or a new registration) writes flocks.json for the whole host but
removes or reconnects the ``rea`` service only in the process that ran it. A
conversation in another process would otherwise keep analysing with the old
service; this brings that process in line first, so after an uninstall the
call fails instead. A call is also stopped while an uninstall runs, or when
the state cannot be read.
"""

from __future__ import annotations

from flocks.hooks.pipeline import HookBase, HookContext
from flocks.rea import runtime
from flocks.utils.log import Log


log = Log.create(service="rea.service_guard")

HOOK_NAME = "rea.service_guard"
_PLUGIN_TOOL = "rea_analysis"  # Flocks' own REA tool, not one of the service's
UNREADABLE_DETAIL = (
    "Stopped: whether REA is still installed could not be checked ({error}). Nothing was called; "
    "try again shortly."
)
UNINSTALLING_DETAIL = "Stopped: REA is being uninstalled. Tell the user to use it again once that finished."


def _tool_name(payload: object) -> object:
    execution = payload.get("tool_execution") if isinstance(payload, dict) else None
    tool = execution.get("tool") if isinstance(execution, dict) else None
    return tool.get("name") if isinstance(tool, dict) else None


class ReaServiceGuard(HookBase):
    name = HOOK_NAME

    async def tool_before(self, ctx: HookContext) -> None:
        name = _tool_name(ctx.input)
        if not isinstance(name, str) or name == _PLUGIN_TOOL or not name.startswith(f"{runtime.MCP_SERVER_NAME}_"):
            return
        from flocks.rea import service
        from flocks.rea.uninstaller import ReaUninstaller

        try:
            # The jobs first: a cached "still running" would skip following the service.
            await service.sync_with_host(strict=True)
        except Exception as exc:
            log.warning("rea.service_guard.sync_failed", {"error": str(exc)})
            ctx.output["execution"] = {"stop": True, "detail": UNREADABLE_DETAIL.format(error=type(exc).__name__)}
            return
        if ReaUninstaller.is_running():
            ctx.output["execution"] = {"stop": True, "detail": UNINSTALLING_DETAIL}


def register() -> None:
    from flocks.hooks.pipeline import HookPipeline

    HookPipeline.register(HOOK_NAME, ReaServiceGuard())
