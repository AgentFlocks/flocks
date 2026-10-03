"""Agent messages consumed between tool batches; no separate task scheduler."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field

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


class AgentInbox:
    _inboxes: dict[str, Inbox] = {}
    _wakes: dict[str, asyncio.Task] = {}
    # Includes submitted children that have not entered SessionLoop yet.
    _children: dict[str, tuple[str, asyncio.Task]] = {}

    @classmethod
    def get(cls, session_id: str) -> Inbox:
        return cls._inboxes.setdefault(session_id, Inbox())

    @classmethod
    def post(cls, session_id: str, message: AgentMessage, *, publisher=None, inbox=None) -> None:
        target = inbox or cls.get(session_id)
        if target.stopped:
            return  # A late child result cannot revive a stopped conversation.
        target.messages.append(message)
        target.ready.set()
        cls.wake(session_id, publisher)

    @classmethod
    def wake(cls, session_id: str, publisher=None) -> None:
        from flocks.session.session_loop import SessionLoop, LoopCallbacks
        previous = cls._wakes.get(session_id)
        if previous and previous.done():
            cls._wakes.pop(session_id)
        child = cls._children.get(session_id)
        if SessionLoop.is_running(session_id) or session_id in cls._wakes or (child and not child[1].done()):
            return
        inbox = cls.get(session_id)
        if not inbox.messages or inbox.stopped:
            return

        async def run():
            try:
                session = await Session.get_by_id(session_id)
                if not session or session.status != "active":
                    inbox.messages.clear()
                    return
                from flocks.project.instance import Instance
                from flocks.project.bootstrap import instance_bootstrap
                while inbox.messages and not inbox.stopped:
                    result = await Instance.provide(
                        directory=session.directory, init=instance_bootstrap,
                        fn=lambda: SessionLoop.run(session_id, callbacks=LoopCallbacks(event_publish_callback=publisher)),
                    )
                    if result.action != "stop" or result.error or result.metadata.get("aborted"):
                        break
            finally:
                if cls._wakes.get(session_id) is asyncio.current_task():
                    cls._wakes.pop(session_id, None)

        task = fire_and_forget(run(), label="agent.inbox", name=f"inbox:{session_id}")
        cls._wakes[session_id] = task

        def finished(done):
            # Cancellation before run() starts does not execute its finally.
            if cls._wakes.get(session_id) is done:
                cls._wakes.pop(session_id, None)

        task.add_done_callback(finished)

    @classmethod
    async def drain(cls, session_id: str, callbacks) -> bool:
        from flocks.session.session_loop import SessionLoop
        inbox = cls.get(session_id)
        # Messages arriving during projection belong to the next boundary.
        pending = list(inbox.messages)
        if not pending:
            return False
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
            if inbox.stopped:
                break
            if not await Message.get(session_id, incoming.id):
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
            message = await Message.get_with_parts_lazy(session_id, incoming.id)
            await SessionLoop._publish_runtime_event(callbacks, "message.updated", {
                "info": message.info.model_dump(mode="json", by_alias=True)})
            for part in message.parts:
                await SessionLoop._publish_runtime_event(callbacks, "message.part.updated", {
                    "part": part.model_dump(mode="json", by_alias=True)})
            if inbox.stopped:
                break
            inbox.messages.popleft()
        if not inbox.messages:
            inbox.ready.clear()
        return bool(pending)

    @classmethod
    def stop(cls, session_id: str) -> None:
        inbox = cls._inboxes.pop(session_id, None)
        if inbox:
            inbox.stopped = True
            inbox.messages.clear()
            inbox.ready.set()
        wake = cls._wakes.pop(session_id, None)
        if wake and wake is not asyncio.current_task():
            wake.cancel()
        own_job = cls._children.get(session_id)
        if own_job and own_job[1] is not asyncio.current_task():
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
