"""The REA analysis prompt sent with nothing to analyse.

The 「REA 逆向分析」 shortcut fills the composer with 「帮我使用 REA 逆向工具分析上传的附件文件」
(PRD 6.2). Sent without a file, a model goes looking for "the attachment" on
the host and in other conversations' uploads (BUG-006 of the V5 validation).
The PRD asks for the conversation to ask for the file instead, so such a turn
is kept to that:

- ``user.prompt.before`` recognises the prompt in a conversation without any
  file the user attached (archived messages included) and without a named
  link or path, and tells Rex to ask for the file;
- ``tool.execute.before`` stops every tool call of that conversation, so
  nothing is searched, read or analysed.

The block lasts until the user sends the next message, which decides again.
Whatever runs in the conversation meanwhile (the rest of the reply, a
compaction, an inbox message, a goal continuation, a reply stopped halfway)
stays blocked; nothing lifts it on a timer or for lack of room. That errs on
the side of running nothing: the conversation is waiting for a file.
``user.prompt.before`` blocks before it counts the files, so a count that
fails or is cut off keeps the block.
"""

from __future__ import annotations

import re

from flocks.hooks.pipeline import HookBase, HookContext
from flocks.utils.log import Log


log = Log.create(service="rea.attachment_guard")

HOOK_NAME = "rea.attachment_guard"
# What the shortcut and the plugin square's 「使用」 put in the composer (``rea.usePrompt``).
USE_PROMPTS: tuple[str, ...] = (
    "帮我使用 REA 逆向工具分析上传的附件文件",
    "Help me analyze the uploaded attachments with the REA reverse-engineering tools",
)

# A link or a host path the user named is a target of its own.
_NAMED_TARGET = re.compile(r"(?i)\b(?:https?|ftp)://|\bwww\.|(?:^|\s)(?:/|~/|[a-z]:\\)\S")

ASK_FOR_FILES_CONTEXT = (
    "REA: the user asks to analyze the uploaded attachments, but no file is attached to this conversation and "
    "no link or path is named. There is nothing to analyze yet: do not analyze, guess or call any tool (tool "
    "calls are stopped for this turn), and never look for files on the host, in other conversations' uploads or "
    "in the Flocks data directory. Reply in one or two short sentences in the user's language: ask them to attach "
    "the file(s) to analyze with the + button in the message box (or to give a download link or website URL) "
    "and to send the message again."
)
TOOL_STOP_DETAIL = (
    "Stopped: no file is attached to this conversation, so there is nothing to analyze. Do not look for files "
    "anywhere. Ask the user to attach the file(s) to analyze (the + button) or to give a download link or "
    "website URL, then end your reply."
)

# Conversations whose tool calls are stopped.
_blocked: set[str] = set()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


_NORMALIZED_PROMPTS = tuple(_normalize(prompt) for prompt in USE_PROMPTS)


def asks_to_analyze_attachments(text: str) -> bool:
    """The text asks to analyse the uploaded files with REA and names no other target."""

    normalized = _normalize(text)
    if not any(prompt in normalized for prompt in _NORMALIZED_PROMPTS):
        return False
    return not _NAMED_TARGET.search(text or "")


async def attached_file_count(session_id: str) -> int:
    """Files the user attached to this conversation (images included), also in messages a compaction archived."""

    from flocks.session.message import Message

    return sum(
        1
        for message in await Message.list_with_parts(session_id, include_archived=True)
        if getattr(message.info, "role", None) == "user"
        for part in message.parts or []
        if getattr(part, "type", None) == "file"
    )


def is_blocked(session_id: object) -> bool:
    return isinstance(session_id, str) and session_id in _blocked


def _tool_session_id(payload: object) -> object:
    execution = payload.get("tool_execution") if isinstance(payload, dict) else None
    session_info = execution.get("session") if isinstance(execution, dict) else None
    return session_info.get("id") if isinstance(session_info, dict) else None


class ReaAttachmentGuard(HookBase):
    name = HOOK_NAME

    async def user_prompt_before(self, ctx: HookContext) -> None:
        session_id = ctx.input.get("sessionID")
        if not isinstance(session_id, str) or not session_id:
            return
        # Every message the user sends decides again: a file sent since then lifts it.
        _blocked.discard(session_id)
        if not asks_to_analyze_attachments(str(ctx.input.get("prompt") or "")):
            return
        # Block first: if counting the files fails or is cut off by the hook's time limit, it stays blocked.
        _blocked.add(session_id)
        previous = ctx.output.get("additionalContext")
        ctx.output["additionalContext"] = "\n\n".join(
            text for text in (previous if isinstance(previous, str) else "", ASK_FOR_FILES_CONTEXT) if text
        )
        try:
            attached = await attached_file_count(session_id)
        except Exception as exc:
            log.warning("rea.attachment_guard.count_failed", {"session_id": session_id, "error": str(exc)})
            return
        if attached > 0:
            _blocked.discard(session_id)
            if previous is None:
                ctx.output.pop("additionalContext", None)
            else:
                ctx.output["additionalContext"] = previous
            return
        log.info("rea.attachment_guard.ask_for_files", {
            "session_id": session_id, "message_id": ctx.input.get("messageID"),
        })

    async def tool_before(self, ctx: HookContext) -> None:
        if is_blocked(_tool_session_id(ctx.input)):
            ctx.output["execution"] = {"stop": True, "detail": TOOL_STOP_DETAIL}


def register() -> None:
    from flocks.hooks.pipeline import HookPipeline

    # Late, so the instruction is appended after other hooks' context.
    HookPipeline.register(HOOK_NAME, ReaAttachmentGuard(), order=1000)


def _reset_for_testing() -> None:
    _blocked.clear()
