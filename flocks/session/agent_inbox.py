"""Agent messages consumed between tool batches; no separate task scheduler."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from flocks.session.background_tasks import track_background_task
from flocks.session.core.task_utils import fire_and_forget
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session
from flocks.utils.id import Identifier


GUIDANCE = (
    "This is a message from another agent, not a replacement for your current task. "
    "Use relevant information in your next actions. Add unrelated requests to your TODO, "
    "preserving existing unfinished items, and handle them after your current work. "
    "Use todo(action=\"read\") before todo(action=\"write\"); load todo with tool_search if needed. "
    "Progress reports do not mean completion and need no acknowledgement unless requested."
)


@dataclass
class AgentMessage:
    source_session_id: str
    source_agent: str
    content: str
    id: str = field(default_factory=lambda: Identifier.create("message"))


@dataclass
class Inbox:
    parent_id: str | None = None
    messages: deque[AgentMessage] = field(default_factory=deque)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stopped: bool = False
    owner_loop: asyncio.AbstractEventLoop | None = field(default=None, repr=False)
    on_result: Callable[[Any], Awaitable[None]] | None = field(default=None, repr=False)
    auto_failover_allowed: bool = False
    prepared_user_id: str | None = None


class AgentInbox:
    _inboxes: dict[str, Inbox] = {}
    _wakes: dict[str, asyncio.Task] = {}
    # Includes submitted children that have not entered SessionLoop yet.
    _children: dict[str, tuple[str, asyncio.Task]] = {}
    _finalizers: set[asyncio.Task] = set()

    @classmethod
    def track_finalizer(cls, task: asyncio.Task) -> None:
        # Ordinary Stop preserves terminal cleanup; lifecycle cancellation still
        # owns these tasks through the shared background-task tracker.
        cls._finalizers.add(task)
        task.add_done_callback(cls._finalizers.discard)

    @classmethod
    def get(cls, session_id: str) -> Inbox:
        inbox = cls._inboxes.setdefault(session_id, Inbox())
        if inbox.stopped:
            # Keep ancestry while cancelled wakes hand off to their finalizers.
            inbox = Inbox(parent_id=inbox.parent_id, owner_loop=inbox.owner_loop)
            cls._inboxes[session_id] = inbox
        return inbox

    @classmethod
    def bind(cls, session_id: str) -> Inbox:
        """Bind notification and wake scheduling to the consuming loop."""
        inbox = cls.get(session_id)
        loop = asyncio.get_running_loop()
        if inbox.owner_loop is not None and inbox.owner_loop is not loop:
            inbox.ready = asyncio.Event()
            if inbox.messages:
                inbox.ready.set()
        inbox.owner_loop = loop
        return inbox

    @staticmethod
    def dispatch(inbox: Inbox, callback: Callable[[], None]) -> bool:
        loop = inbox.owner_loop
        if loop is None or loop.is_closed():
            return False
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if loop is current:
            return False
        loop.call_soon_threadsafe(callback)
        return True

    @classmethod
    def post(cls, session_id: str, message: AgentMessage, *, publisher=None, inbox=None) -> None:
        target = inbox or cls.get(session_id)
        if target.stopped or cls._inboxes.get(session_id) is not target:
            return  # A late child result cannot revive a stopped conversation.
        if cls.dispatch(target, lambda: cls.post(session_id, message, publisher=publisher, inbox=target)):
            return
        target.messages.append(message)
        target.ready.set()
        cls.wake(session_id, publisher)

    @classmethod
    def wake(cls, session_id: str, publisher=None) -> None:
        from flocks.session.session import is_model_auto_session_category
        from flocks.session.session_loop import SessionLoop, LoopCallbacks, LoopResult

        inbox = cls.get(session_id)
        if cls.dispatch(inbox, lambda: cls.wake(session_id, publisher)):
            return
        # A completed execution remains registered until its result is delivered.
        if SessionLoop.is_running(session_id) or session_id in cls._wakes or session_id in cls._children:
            return
        if not inbox.messages or inbox.stopped:
            return
        cls.bind(session_id)

        async def run():
            session = await Session.get_by_id(session_id)
            if not session or session.status != "active" or inbox.stopped:
                inbox.messages.clear()
                return None
            from flocks.project.instance import Instance
            from flocks.project.bootstrap import instance_bootstrap

            auto_failover = bool(inbox.auto_failover_allowed and session.model_auto
                                 and is_model_auto_session_category(session.category))
            return await Instance.provide(
                directory=session.directory, init=instance_bootstrap,
                fn=lambda: SessionLoop.run(session_id,
                    callbacks=LoopCallbacks(event_publish_callback=publisher),
                    auto_failover=auto_failover),
            )

        async def finish(done):
            try:
                result = done.result()
            except asyncio.CancelledError:
                result = LoopResult(action="stop", metadata={"aborted": True})
            except Exception as exc:
                result = LoopResult(action="error", error=str(exc))
            if result is not None and inbox.stopped:
                result = LoopResult(action="stop", metadata={"aborted": True})
            if result is not None and result.action in {"stop", "error"} and inbox.on_result:
                await inbox.on_result(result)
            return result

        def completed(done):
            if cls._wakes.get(session_id) is not done:
                return
            finalizer = fire_and_forget(finish(done), label="agent.inbox_result")
            cls._wakes[session_id] = finalizer
            cls.track_finalizer(finalizer)
            track_background_task(finalizer, session_id=session_id)

            def finished(delivered):
                if cls._wakes.get(session_id) is delivered:
                    cls._wakes.pop(session_id, None)
                if not delivered.cancelled() and delivered.exception() is None:
                    result = delivered.result()
                    if result is not None and result.action == "stop" and not result.error:
                        cls.wake(session_id, publisher)

            finalizer.add_done_callback(finished)

        task = fire_and_forget(run(), label="agent.inbox", name=f"inbox:{session_id}")
        cls._wakes[session_id] = task
        track_background_task(task, session_id=session_id)
        task.add_done_callback(completed)

    @classmethod
    async def drain(cls, session_id: str, callbacks) -> bool:
        from flocks.session.session_loop import SessionLoop
        inbox = cls.bind(session_id)
        # Messages arriving during projection belong to the next boundary.
        pending = list(inbox.messages)
        if not pending:
            return False

        def stopped():
            return inbox.stopped or cls._inboxes.get(session_id) is not inbox

        history = await Message.list(session_id)
        last_user = next((m for m in reversed(history) if m.role == MessageRole.USER), None)
        session = await Session.get_by_id(session_id)
        if not session or session.status != "active":
            cls.stop(session_id)
            return False
        config = {key: getattr(last_user, key) for key in
                  ("agent", "model", "system", "tools", "variant", "executionMode")} if last_user else {
                      "agent": session.agent or "rex"}
        for incoming in pending:
            created = False
            try:
                if stopped():
                    break
                existing = await Message.get(session_id, incoming.id)
                if stopped():
                    break
                if not existing:
                    # create() can persist/cache the input before its await returns.
                    created = True
                    await Message.create(
                        session_id, MessageRole.USER,
                        f"[Message from {incoming.source_agent}, session {incoming.source_session_id}]\n"
                        f"{incoming.content}\n\n<system-reminder>{GUIDANCE}</system-reminder>",
                        id=incoming.id, synthetic=True, **config,
                        part_metadata={"displayText": incoming.content, "communication": {
                            "source_type": "agent", "source_session_id": incoming.source_session_id,
                            "source_agent": incoming.source_agent,
                        }},
                    )
                if stopped():
                    break
                message = await Message.get_with_parts_lazy(session_id, incoming.id)
                if stopped():
                    break
                await SessionLoop._publish_runtime_event(callbacks, "message.updated", {
                    "info": message.info.model_dump(mode="json", by_alias=True)})
                for part in message.parts:
                    if stopped():
                        break
                    await SessionLoop._publish_runtime_event(callbacks, "message.part.updated", {
                        "part": part.model_dump(mode="json", by_alias=True)})
                if stopped():
                    break
                inbox.messages.popleft()
            except BaseException:
                if created:
                    await Message.delete(session_id, incoming.id)
                    created = False
                raise
            finally:
                if created and stopped():
                    await Message.delete(session_id, incoming.id)
        if not inbox.messages:
            inbox.ready.clear()
        return bool(pending)

    @classmethod
    def snapshot_tree(cls, session_id: str) -> tuple[set[str], set[asyncio.Task]]:
        session_ids = {session_id}
        while True:
            children = {sid for sid, box in cls._inboxes.items() if box.parent_id in session_ids}
            children.update(sid for sid, (parent, _) in cls._children.items() if parent in session_ids)
            if children <= session_ids:
                break
            session_ids.update(children)
        tasks = {task for sid, task in cls._wakes.items() if sid in session_ids}
        tasks.update(task for sid, (_, task) in cls._children.items() if sid in session_ids)
        return session_ids, tasks

    @classmethod
    async def wait_for_tree(cls, session_ids: set[str], tasks: set[asyncio.Task], *, timeout_s: float = 5) -> bool:
        """Join stopped executions, including their completion-callback handoffs."""
        from flocks.session.session_loop import SessionLoop

        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            registered = any(sid in cls._wakes or sid in cls._children for sid in session_ids)
            running = any(SessionLoop.is_running(sid) for sid in session_ids)
            if not registered and not running and all(task.done() for task in tasks):
                return True
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.01, remaining))

    @classmethod
    def stop(cls, session_id: str) -> None:
        inbox = cls._inboxes.get(session_id)
        if inbox and cls.dispatch(inbox, lambda: cls.stop(session_id)):
            return
        if inbox:
            inbox.stopped = True
            inbox.messages.clear()
            inbox.ready.set()
        wake = cls._wakes.get(session_id)
        if wake and wake is not asyncio.current_task() and wake not in cls._finalizers:
            wake.cancel()
        own_job = cls._children.get(session_id)
        if own_job and own_job[1] is not asyncio.current_task() and own_job[1] not in cls._finalizers:
            own_job[1].cancel()
        from flocks.session.session_loop import SessionLoop
        children = {sid for sid, box in cls._inboxes.items() if box.parent_id == session_id}
        children.update(sid for sid, (parent, _) in cls._children.items() if parent == session_id)
        for child_id in children:
            SessionLoop.abort(child_id)


async def authorize_message(source_id: str, target_id: str) -> Session:
    """Permit collaboration within a delegation tree, otherwise use session ACL."""
    from flocks.session.policy import SessionPolicy
    source, target = await Session.get_by_id(source_id), await Session.get_by_id(target_id)
    if not source or not target or target.status != "active" or Session.is_lifecycle_transitioning(target_id):
        raise ValueError("Session unavailable")

    async def root(session):
        visited = set()
        while session.parent_id and session.id not in visited:
            visited.add(session.id)
            parent = await Session.get_by_id(session.parent_id)
            if not parent:
                break
            session = parent
        return session.id

    if await root(source) != await root(target):
        user = SessionPolicy._resolve_user(None)
        if user is None or not SessionPolicy.can_write(target, user):
            raise ValueError("Session unavailable")
    return target
