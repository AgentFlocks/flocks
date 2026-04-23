"""Output sink abstractions for unified input dispatch."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Optional

from flocks.input.events import ParsedCommand, UserInputEvent

DirectResponseCallback = Callable[[UserInputEvent, str], Awaitable[None]]
RunLlmCallback = Callable[[UserInputEvent, str, Optional[str]], Awaitable[None]]
SessionControlCallback = Callable[[UserInputEvent, ParsedCommand], Awaitable[bool]]
SideEffectCallback = Callable[[], Awaitable[None]]


class OutputSink(ABC):
    """Surface-specific bridge used by the dispatcher."""

    def __init__(self, surface: str) -> None:
        self.surface = surface

    @abstractmethod
    async def publish_direct_response(self, event: UserInputEvent, text: str) -> None:
        """Emit a direct assistant response for a handled command."""

    @abstractmethod
    async def run_llm(
        self,
        event: UserInputEvent,
        prompt_text: str,
        display_text: Optional[str] = None,
    ) -> None:
        """Route a prompt through the normal session/LLM pipeline."""

    async def execute_session_control(
        self,
        event: UserInputEvent,
        parsed: ParsedCommand,
    ) -> bool:
        return False

    async def clear_screen(self) -> None:
        return None

    async def restart_session(self) -> None:
        return None


class CallbackOutputSink(OutputSink):
    """Simple sink backed by async callbacks from each surface adapter."""

    def __init__(
        self,
        surface: str,
        *,
        direct_response: DirectResponseCallback,
        run_llm: RunLlmCallback,
        session_control: Optional[SessionControlCallback] = None,
        clear_screen: Optional[SideEffectCallback] = None,
        restart_session: Optional[SideEffectCallback] = None,
    ) -> None:
        super().__init__(surface)
        self._direct_response = direct_response
        self._run_llm = run_llm
        self._session_control = session_control
        self._clear_screen = clear_screen
        self._restart_session = restart_session

    async def publish_direct_response(self, event: UserInputEvent, text: str) -> None:
        await self._direct_response(event, text)

    async def run_llm(
        self,
        event: UserInputEvent,
        prompt_text: str,
        display_text: Optional[str] = None,
    ) -> None:
        await self._run_llm(event, prompt_text, display_text)

    async def execute_session_control(
        self,
        event: UserInputEvent,
        parsed: ParsedCommand,
    ) -> bool:
        if self._session_control is None:
            return False
        return await self._session_control(event, parsed)

    async def clear_screen(self) -> None:
        if self._clear_screen is not None:
            await self._clear_screen()

    async def restart_session(self) -> None:
        if self._restart_session is not None:
            await self._restart_session()


class SSEOutputSink(CallbackOutputSink):
    """Output sink for SSE-backed surfaces such as WebUI/TUI/ACP."""


class ChannelOutputSink(CallbackOutputSink):
    """Output sink for IM/channel adapters."""


class CliOutputSink(CallbackOutputSink):
    """Output sink for the local Python CLI."""
