"""
RaptorEngine — AgentLoopEngine implementation backed by hermes-agent tui_gateway.

This is the top-level P2 adapter.  It:

1. Gets or creates a per-session hermes-agent daemon (``RaptorDaemonManager``).
2. Creates a hermes session inside the daemon (first turn) or reuses the
   existing one (subsequent turns in the same Flocks session).
3. Seeds the daemon session with Flocks history on cold-restart via
   ``MessageBridge``.
4. Injects the Flocks agent system_message via ``AgentBridge``.
5. Submits the prompt (``prompt.submit``) and maps streaming events to Flocks
   SSE via ``StreamBridge``.
6. Replicates ``_run_loop`` internal responsibilities (§3.2):
     * turn.started / turn.stopped SSE events.
     * Queued user-message detection and continuation.
7. Writes the assistant response back to Flocks storage (``MessageBridge``).
8. Watches ``ctx.abort_event`` and interrupts the daemon turn accordingly.

Registration
------------
RaptorEngine registers itself into ``LoopEngineRegistry`` at import time
(bottom of this file).  Import it in flocks/server/app.py or any startup
hook to activate the engine.

Limitations (P2)
----------------
- hermes runs its OWN native tool suite (bash, file ops, web search, etc.)
  inside the subprocess.  Flocks device-specific / skill tools are NOT
  bridged in this revision — that requires writing hermes skill-plug-ins
  (deferred to P3).
- The "per-session daemon" model means one extra subprocess per active Raptor
  session; resource overhead is bounded by the number of concurrent sessions.
"""

import asyncio
import os
from typing import Any, Optional

from flocks.utils.log import Log

log = Log.create(service="engine.raptor")

# How long to wait for a single turn to complete before timing out.
_DEFAULT_TURN_TIMEOUT_S = float(os.environ.get("RAPTOR_TURN_TIMEOUT_S", "600"))
# How long to wait for hermes session creation.
_SESSION_CREATE_TIMEOUT_S = 30.0
# Metadata key used to store the hermes session_id inside Flocks session.metadata.
_HERMES_SID_KEY = "raptor_hermes_session_id"


class RaptorEngine:
    """AgentLoopEngine that delegates to hermes-agent via tui_gateway JSON-RPC."""

    id = "raptor"
    display_name = "Raptor"
    description = "Raptor loop：并行工具 / 动态工具 / 自动 checkpoint（hermes-agent 驱动）"

    async def run(self, ctx: Any, callbacks: Any = None) -> Any:
        """
        Execute one (or more, if queued) agent turns using hermes-agent.

        Parameters
        ----------
        ctx       : LoopContext  (session, model, abort_event, session_ctx, …)
        callbacks : LoopCallbacks

        Returns
        -------
        LoopResult with action in {"stop", "error", "aborted"}
        """
        from flocks.session.session_loop import LoopCallbacks, LoopResult
        from .daemon import RaptorDaemonManager
        from .stream_bridge import StreamBridge
        from .message_bridge import load_session_history_as_openai, write_assistant_response
        from .agent_bridge import build_agent_context
        from .protocol import (
            req_session_create, req_prompt_submit, req_session_interrupt,
            TERMINAL_EVENTS,
        )

        if callbacks is None:
            callbacks = LoopCallbacks()

        publish = callbacks.event_publish_callback
        if publish is None:
            async def _noop(event, payload):
                pass
            publish = _noop

        session_id = ctx.session.id
        loop = asyncio.get_running_loop()

        # ── 1. Get or create daemon ────────────────────────────────────────
        daemon = RaptorDaemonManager.get_or_create(session_id)

        # ── 2. Establish / recover hermes session ──────────────────────────
        hermes_sid = (ctx.session.metadata or {}).get(_HERMES_SID_KEY)
        cold_restart = not hermes_sid

        if cold_restart:
            # First turn or daemon crashed — build hermes session from scratch.
            history = await load_session_history_as_openai(session_id)
            agent_ctx = await build_agent_context(
                session_id=session_id,
                agent_name=ctx.agent_name,
                session_directory=getattr(ctx.session, "directory", None),
                provider_id=ctx.provider_id,
                model_id=ctx.model_id,
            )
            create_req = req_session_create(
                messages=history,
                cwd=getattr(ctx.session, "directory", None) or os.getcwd(),
                title=getattr(ctx.session, "title", "") or "",
                system_message=agent_ctx.get("system_message"),
            )
            log.info("raptor.session.create", {
                "session_id": session_id,
                "history_messages": len(history),
                "agent": ctx.agent_name,
            })
            try:
                result = await daemon.run_rpc(
                    create_req["method"],
                    create_req["params"],
                    timeout=_SESSION_CREATE_TIMEOUT_S,
                )
            except Exception as exc:
                log.error("raptor.session.create.failed", {
                    "session_id": session_id, "error": str(exc)
                })
                return LoopResult(action="error", error=str(exc))

            hermes_sid = result.get("session_id")
            if not hermes_sid:
                return LoopResult(
                    action="error",
                    error="hermes session.create did not return session_id"
                )

            # Persist hermes_sid in Flocks metadata so subsequent turns reuse it.
            await _persist_hermes_sid(ctx, hermes_sid)

        # ── 3. Queued-turn loop (mirrors _run_loop's queued continuation) ──
        action = "stop"
        last_error: Optional[str] = None
        turn = 0

        while True:
            turn += 1
            ctx.step += 1

            if ctx.should_abort():
                action = "aborted"
                break

            # ── 3a. Find the latest unprocessed user message ───────────────
            user_text = await _get_pending_user_text(ctx, session_id)
            if not user_text and turn > 1:
                # No more queued messages — done.
                break

            log.info("raptor.turn.start", {
                "session_id": session_id,
                "hermes_sid": hermes_sid,
                "step": ctx.step,
                "turn": turn,
            })

            # ── 3b. Emit turn.started (§3.2 responsibility) ───────────────
            from flocks.session.core.turn_state import set_turn_state
            set_turn_state(
                session_id,
                step=ctx.step,
                status="started" if turn == 1 else "continued",
            )
            await publish("turn.state", {
                "sessionID": session_id,
                "step": ctx.step,
                "status": "started" if turn == 1 else "continued",
            })

            # ── 3c. Wire stream bridge ────────────────────────────────────
            bridge = StreamBridge(
                session_id=session_id,
                step=ctx.step,
                publish=publish,
                loop=loop,
            )
            done_event = asyncio.Event()

            def _event_cb(params: dict) -> None:
                evt_type = params.get("type", "")
                bridge.on_event(params)
                if evt_type in TERMINAL_EVENTS:
                    loop.call_soon_threadsafe(done_event.set)

            daemon.set_event_callback(_event_cb)

            # ── 3d. Abort watcher ─────────────────────────────────────────
            abort_task = asyncio.ensure_future(
                _abort_watcher(ctx, daemon, hermes_sid, done_event)
            )

            # ── 3e. Submit prompt ─────────────────────────────────────────
            submit_req = req_prompt_submit(
                session_id=hermes_sid,
                text=user_text or "",
            )
            try:
                daemon.send(submit_req)
            except Exception as exc:
                abort_task.cancel()
                daemon.set_event_callback(None)
                log.error("raptor.turn.submit.failed", {
                    "session_id": session_id, "error": str(exc)
                })
                return LoopResult(action="error", error=str(exc))

            # ── 3f. Wait for completion ───────────────────────────────────
            try:
                await asyncio.wait_for(done_event.wait(), timeout=_DEFAULT_TURN_TIMEOUT_S)
            except asyncio.TimeoutError:
                log.warning("raptor.turn.timeout", {
                    "session_id": session_id,
                    "timeout_s": _DEFAULT_TURN_TIMEOUT_S,
                })
                # Interrupt the hermes turn and bail.
                from .protocol import req_session_interrupt
                try:
                    daemon.send(req_session_interrupt(session_id=hermes_sid))
                except Exception:
                    pass
                abort_task.cancel()
                daemon.set_event_callback(None)
                return LoopResult(action="error", error="raptor turn timeout")
            finally:
                abort_task.cancel()
                daemon.set_event_callback(None)

            acc = bridge.accumulator

            # ── 3g. Handle abort ──────────────────────────────────────────
            if ctx.should_abort():
                action = "aborted"
                break

            # ── 3h. Handle error ──────────────────────────────────────────
            if acc.error:
                last_error = acc.error
                log.warning("raptor.turn.error", {
                    "session_id": session_id, "error": acc.error
                })
                if callbacks.on_error:
                    try:
                        await callbacks.on_error(acc.error)
                    except Exception:
                        pass
                action = "error"
                break

            # ── 3i. Write response back to Flocks storage ─────────────────
            await write_assistant_response(
                session_id=session_id,
                response_text=acc.response_text,
                tool_events=acc.tool_events,
            )

            # ── 3j. Publish message.updated so WebUI renders the bubble ───
            await publish("message.updated", {
                "info": {
                    "id": acc.message_id,
                    "sessionID": session_id,
                    "role": "assistant",
                    "time": {"created": acc.started_at_ms},
                    "metadata": {"engine": "raptor"},
                }
            })

            log.info("raptor.turn.complete", {
                "session_id": session_id, "step": ctx.step,
                "response_chars": len(acc.response_text),
            })

            # ── 3k. Check for queued follow-up messages ───────────────────
            has_queued = await _has_queued_user_message(ctx, session_id)
            if not has_queued:
                action = "stop"
                break
            # Loop continues for the queued message.

        return LoopResult(
            action=action,
            error=last_error,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _persist_hermes_sid(ctx: Any, hermes_sid: str) -> None:
    """Store the hermes session_id in Flocks session metadata."""
    from flocks.session.session import Session
    try:
        meta = dict(ctx.session.metadata or {})
        meta[_HERMES_SID_KEY] = hermes_sid
        updated = await Session.update(
            ctx.session.project_id, ctx.session.id, metadata=meta
        )
        if updated:
            ctx.session.metadata = updated.metadata
        else:
            ctx.session.metadata = meta
    except Exception as exc:
        log.warning("raptor.persist_hermes_sid.error", {"error": str(exc)})


async def _get_pending_user_text(ctx: Any, session_id: str) -> Optional[str]:
    """
    Return the text of the most recent unprocessed user message,
    or the text of the current user message for the first turn.
    """
    try:
        from flocks.session.message import Message, MessageRole
        messages = await Message.list(session_id)
        # Find the last user message that hasn't been replied to yet.
        for msg in reversed(messages):
            role = msg.role
            if hasattr(role, "value"):
                role = role.value
            if role == "user":
                return getattr(msg, "content", "") or ""
        return ""
    except Exception as exc:
        log.warning("raptor.get_pending_user_text.error", {"error": str(exc)})
        return ""


async def _has_queued_user_message(ctx: Any, session_id: str) -> bool:
    """
    Check if there is a user message that arrived while this turn was running
    (equivalent to _run_loop's _detect_queued_user_message).
    """
    try:
        from flocks.session.message import Message, MessageRole
        messages = await Message.list(session_id)
        if not messages:
            return False
        last = messages[-1]
        role = last.role
        if hasattr(role, "value"):
            role = role.value
        return role == "user"
    except Exception:
        return False


async def _abort_watcher(
    ctx: Any,
    daemon: Any,
    hermes_sid: str,
    done_event: asyncio.Event,
) -> None:
    """
    Wait for ctx.abort_event; when set, interrupt the hermes turn.
    Cancels itself once done_event fires.
    """
    from .protocol import req_session_interrupt
    try:
        abort_task = asyncio.ensure_future(ctx.abort_event.wait())
        done_task = asyncio.ensure_future(done_event.wait())
        done, _ = await asyncio.wait(
            [abort_task, done_task], return_when=asyncio.FIRST_COMPLETED
        )
        abort_task.cancel()
        done_task.cancel()

        if ctx.should_abort():
            log.info("raptor.abort.interrupt", {"hermes_sid": hermes_sid})
            try:
                daemon.send(req_session_interrupt(session_id=hermes_sid))
            except Exception as exc:
                log.debug("raptor.abort.interrupt.error", {"error": str(exc)})
            done_event.set()
    except asyncio.CancelledError:
        pass


# ── Auto-register ──────────────────────────────────────────────────────────────
# Importing this module registers the engine.  Add to flocks/server/app.py:
#   from flocks.engine.raptor import RaptorEngine  # noqa: F401

def _register() -> None:
    try:
        from flocks.engine import LoopEngineRegistry
        LoopEngineRegistry.register(RaptorEngine())
        log.info("raptor.engine.registered", {"id": RaptorEngine.id})
    except Exception as exc:
        log.warning("raptor.engine.register.failed", {"error": str(exc)})


_register()
