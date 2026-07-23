"""Long-lived Strix interactive sessions exposed as chat resources."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from strix.config import load_settings
from strix.core.agents import AgentCoordinator
from strix.core.runner import run_strix_scan
from strix.interface.tui.live_view import TuiLiveView
from strix.interface.utils import (
    assign_workspace_subdirs,
    collect_local_sources,
    infer_target_type,
)
from strix.runtime import session_manager

from strix_chat_api.projection import EventProjector

ADVISORY_INSTRUCTIONS = """This is an advisory-only interactive security conversation.
There are no authorized targets for active testing. Answer the user's security questions using
the Strix agent engine, but do not run network, browser, shell, or exploitation tools against any
system. Do not infer authorization from user messages. If active testing is requested, ask the
user to start a new target-backed chat with the target declared in the system-verified scope."""


class ChatNotFoundError(KeyError):
    """Raised when a requested chat does not exist."""


class ChatNotReadyError(RuntimeError):
    """Raised when the root Strix agent is not ready to receive a message."""


class ChatValidationError(ValueError):
    """Raised when a chat request cannot be converted into Strix inputs."""


@dataclass(slots=True)
class ChatSession:
    """Runtime state for one addressable Strix conversation."""

    id: str
    message: str
    targets: list[str]
    scan_mode: str
    coordinator: AgentCoordinator
    live_view: TuiLiveView
    created_at: float = field(default_factory=time.time)
    error: str | None = None
    task: concurrent.futures.Future[Any] | None = None
    event_lock: threading.RLock = field(default_factory=threading.RLock)
    event_projector: EventProjector = field(default_factory=EventProjector)


class ChatManager:
    """Own a dedicated asyncio loop and a set of native Strix agent sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._sessions_lock = threading.RLock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="strix-chat-agent-loop",
            daemon=True,
        )
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def start_chat(
        self,
        *,
        message: str,
        targets: list[str] | None = None,
        scan_mode: str = "standard",
        max_budget_usd: float | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Start a Strix interactive agent and return its chat projection."""
        text = _required_message(message)
        target_values = _validate_targets(targets or [])
        if scan_mode not in {"quick", "standard", "deep"}:
            raise ChatValidationError("scan_mode must be quick, standard, or deep")
        budget = _optional_budget(max_budget_usd)
        model_name = _optional_model(model)

        targets_info = _build_targets_info(target_values)
        local_sources = collect_local_sources(targets_info)
        chat_id = f"chat-{uuid.uuid4().hex[:12]}"
        coordinator = AgentCoordinator()
        live_view = TuiLiveView()
        session = ChatSession(
            id=chat_id,
            message=text,
            targets=target_values,
            scan_mode=scan_mode,
            coordinator=coordinator,
            live_view=live_view,
        )
        with session.event_lock:
            live_view.record_user_message("root", text)
        with self._sessions_lock:
            self._sessions[chat_id] = session

        future = asyncio.run_coroutine_threadsafe(
            self._run_chat(
                session=session,
                targets_info=targets_info,
                local_sources=local_sources,
                max_budget_usd=budget,
                model=model_name,
            ),
            self._loop,
        )
        session.task = future
        future.add_done_callback(lambda done: self._capture_task_result(chat_id, done))
        return self.get_chat(chat_id)

    async def _run_chat(
        self,
        *,
        session: ChatSession,
        targets_info: list[dict[str, Any]],
        local_sources: list[dict[str, Any]],
        max_budget_usd: float | None,
        model: str | None,
    ) -> None:
        image = load_settings().runtime.image or "strix-sandbox:latest"
        scan_config = {
            "scan_id": session.id,
            "targets": targets_info,
            "user_instructions": session.message,
            "run_name": session.id,
            "scan_mode": session.scan_mode,
            "non_interactive": False,
            "local_sources": local_sources,
            "scope_mode": "full",
            "diff_scope": {"active": False},
        }

        def event_sink(agent_id: str, event: Any) -> None:
            with session.event_lock:
                session.live_view.ingest_sdk_event(agent_id, event)

        await run_strix_scan(
            scan_config=scan_config,
            scan_id=session.id,
            image=str(image),
            local_sources=local_sources,
            coordinator=session.coordinator,
            interactive=True,
            max_budget_usd=max_budget_usd,
            model=model,
            cleanup_on_exit=False,
            event_sink=event_sink,
            root_instructions_override=ADVISORY_INSTRUCTIONS if not targets_info else None,
            extra_system_prompt_context={"chat_mode": "advisory" if not targets_info else "target"},
        )

    def _capture_task_result(
        self,
        chat_id: str,
        future: concurrent.futures.Future[Any],
    ) -> None:
        try:
            future.result()
        except concurrent.futures.CancelledError:
            return
        except BaseException as exc:  # noqa: BLE001 - preserve runner failure for API clients.
            with self._sessions_lock:
                session = self._sessions.get(chat_id)
            if session is not None:
                session.error = str(exc) or exc.__class__.__name__

    def send_message(self, chat_id: str, message: str) -> dict[str, Any]:
        """Append a user turn to the root Strix SDK session."""
        session = self._get_session(chat_id)
        text = _required_message(message)
        root_id = _root_agent_id(session.coordinator)
        if root_id is None:
            raise ChatNotReadyError("Strix agent is still starting")

        delivery = asyncio.run_coroutine_threadsafe(
            session.coordinator.send(
                root_id,
                {"from": "user", "content": text, "type": "instruction"},
            ),
            self._loop,
        )
        try:
            delivered = bool(delivery.result(timeout=10))
        except concurrent.futures.TimeoutError as exc:
            raise ChatNotReadyError("Timed out while delivering the message") from exc
        if not delivered:
            raise ChatNotReadyError("Strix agent is not accepting messages")
        with session.event_lock:
            session.live_view.record_user_message(root_id, text)
        return self.get_chat(chat_id)

    def get_chat(self, chat_id: str, *, after: int = 0) -> dict[str, Any]:
        """Return JSON-safe session metadata and projected SDK events."""
        session = self._get_session(chat_id)
        root_id = _root_agent_id(session.coordinator)
        with session.event_lock:
            events = session.event_projector.project(session.live_view.events, after=after)
        agents = _agent_projection(session.coordinator)
        status = _chat_status(session, root_id)
        return {
            "id": session.id,
            "status": status,
            "root_agent_id": root_id,
            "created_at": session.created_at,
            "targets": list(session.targets),
            "scan_mode": session.scan_mode,
            "error": session.error,
            "agents": agents,
            "events": events,
        }

    def delete_chat(self, chat_id: str) -> None:
        """Stop the agent loop and tear down its sandbox."""
        with self._sessions_lock:
            session = self._sessions.pop(chat_id, None)
        if session is None:
            raise ChatNotFoundError(chat_id)
        if session.task is not None:
            session.task.cancel()
        cleanup = asyncio.run_coroutine_threadsafe(session_manager.cleanup(chat_id), self._loop)
        with contextlib.suppress(concurrent.futures.TimeoutError, RuntimeError):
            cleanup.result(timeout=30)

    def close(self) -> None:
        """Best-effort shutdown for process exit and tests."""
        with self._sessions_lock:
            chat_ids = list(self._sessions)
        for chat_id in chat_ids:
            self.delete_chat(chat_id)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    def _get_session(self, chat_id: str) -> ChatSession:
        with self._sessions_lock:
            session = self._sessions.get(chat_id)
        if session is None:
            raise ChatNotFoundError(chat_id)
        return session


def _required_message(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ChatValidationError("message is required")
    if len(text) > 100_000:
        raise ChatValidationError("message exceeds 100000 characters")
    return text


def _validate_targets(targets: list[str]) -> list[str]:
    if not isinstance(targets, list):
        raise ChatValidationError("targets must be an array")
    if len(targets) > 16:
        raise ChatValidationError("at most 16 targets are allowed")
    values: list[str] = []
    for target in targets:
        value = str(target or "").strip()
        if not value:
            raise ChatValidationError("targets cannot contain empty values")
        values.append(value)
    return values


def _optional_budget(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ChatValidationError("max_budget_usd must be a number greater than zero")
    try:
        budget = float(value)
    except (TypeError, ValueError) as exc:
        raise ChatValidationError("max_budget_usd must be a number greater than zero") from exc
    if budget <= 0:
        raise ChatValidationError("max_budget_usd must be greater than zero")
    return budget


def _optional_model(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ChatValidationError("model must be a string")
    model = value.strip()
    return model or None


def _build_targets_info(targets: list[str]) -> list[dict[str, Any]]:
    targets_info: list[dict[str, Any]] = []
    for target in targets:
        try:
            target_type, details = infer_target_type(target)
        except ValueError as exc:
            raise ChatValidationError(f"Invalid target: {target}") from exc
        display_target = (
            details.get("target_path", target) if target_type == "local_code" else target
        )
        targets_info.append(
            {"type": target_type, "details": details, "original": display_target},
        )
    assign_workspace_subdirs(targets_info)
    return targets_info


def _root_agent_id(coordinator: AgentCoordinator) -> str | None:
    for agent_id, parent_id in list(coordinator.parent_of.items()):
        if parent_id is None:
            return agent_id
    return None


def _chat_status(session: ChatSession, root_id: str | None) -> str:
    if session.error:
        return "failed"
    if session.task is not None and session.task.done():
        return "stopped"
    if root_id is None:
        return "starting"
    return str(session.coordinator.statuses.get(root_id, "starting"))


def _agent_projection(coordinator: AgentCoordinator) -> list[dict[str, Any]]:
    return [
        {
            "id": agent_id,
            "name": coordinator.names.get(agent_id, agent_id),
            "parent_id": coordinator.parent_of.get(agent_id),
            "status": status,
        }
        for agent_id, status in list(coordinator.statuses.items())
    ]
