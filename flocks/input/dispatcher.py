"""Unified command and prompt dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from flocks.command.command import Command
from flocks.command.handler import handle_slash_command
from flocks.input.events import ParsedCommand, UserInputEvent
from flocks.input.output import OutputSink


@dataclass
class DispatchResult:
    """Result of dispatching a user input event."""

    action: str
    command_name: Optional[str] = None
    handled: bool = True


def parse_slash_command(text: str) -> Optional[ParsedCommand]:
    """Parse slash text into a command and registry metadata."""
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return None

    without_slash = stripped[1:]
    if not without_slash:
        return None

    command_name, _, raw_args = without_slash.partition(" ")
    command_def = Command.resolve(command_name)
    canonical_name = command_def.canonical_name if command_def else command_name.lower()
    return ParsedCommand(
        raw_text=stripped,
        command_name=command_name.lower(),
        canonical_name=canonical_name,
        args=raw_args.strip(),
        command_def=command_def,
    )


def _has_non_text_parts(event: UserInputEvent) -> bool:
    return any(part.get("type") != "text" for part in (event.parts or []))


async def dispatch_user_input(event: UserInputEvent, sink: OutputSink) -> DispatchResult:
    """Route a normalized user input through direct / llm / session-control paths."""
    parsed = parse_slash_command(event.text)
    if parsed is None:
        await sink.run_llm(event, event.text, event.display_text)
        return DispatchResult(action="llm", handled=False)

    command_def = parsed.command_def
    if command_def is None:
        await sink.run_llm(event, parsed.raw_text, event.display_text or parsed.raw_text)
        return DispatchResult(action="llm", command_name=parsed.command_name, handled=False)

    if not command_def.is_visible_on(sink.surface):
        await sink.publish_direct_response(
            event,
            f"命令 `/{command_def.name}` 在当前入口不可用。",
        )
        return DispatchResult(action="rejected", command_name=command_def.name)

    if sink.surface == "channel" and not command_def.channel_safe:
        await sink.publish_direct_response(
            event,
            f"命令 `/{command_def.name}` 不支持在渠道会话中执行。",
        )
        return DispatchResult(action="rejected", command_name=command_def.name)

    if command_def.requires_existing_session and not event.session_id:
        await sink.publish_direct_response(
            event,
            f"命令 `/{command_def.name}` 需要先有一个会话。",
        )
        return DispatchResult(action="rejected", command_name=command_def.name)

    if _has_non_text_parts(event) and not command_def.allow_attachments:
        await sink.publish_direct_response(
            event,
            f"命令 `/{command_def.name}` 不支持附件。",
        )
        return DispatchResult(action="rejected", command_name=command_def.name)

    if command_def.execution_kind == "session_control":
        handled = await sink.execute_session_control(event, parsed)
        if not handled:
            await sink.publish_direct_response(
                event,
                f"命令 `/{command_def.name}` 在当前环境不可用。",
            )
            return DispatchResult(action="rejected", command_name=command_def.name)
        return DispatchResult(action="session_control", command_name=command_def.name)

    if command_def.execution_kind == "direct":
        direct_texts: list[str] = []
        llm_prompts: list[str] = []

        async def _collect_text(text: str) -> None:
            direct_texts.append(text)

        async def _collect_prompt(prompt: str) -> None:
            llm_prompts.append(prompt)

        handled = await handle_slash_command(
            parsed.raw_text,
            send_text=_collect_text,
            send_prompt=_collect_prompt,
            clear_screen=sink.clear_screen,
            restart_session=sink.restart_session,
            surface=sink.surface,
        )
        if handled:
            if llm_prompts:
                await sink.run_llm(
                    event,
                    llm_prompts[0],
                    event.display_text or parsed.raw_text,
                )
                return DispatchResult(action="llm", command_name=command_def.name)
            await sink.publish_direct_response(event, "\n".join(direct_texts))
            return DispatchResult(action="direct", command_name=command_def.name)

    await sink.run_llm(event, parsed.raw_text, event.display_text or parsed.raw_text)
    return DispatchResult(action="llm", command_name=command_def.name)
