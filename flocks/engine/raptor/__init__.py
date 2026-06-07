"""
Raptor loop engine — parallel tool execution engine built into Flocks.

Module layout:
  engine.py    — RaptorEngine (AgentLoopEngine protocol, auto-registered)
  loop.py      — RaptorSessionLoop (SessionLoop subclass; replaces only the runner factory)
  runner.py    — RaptorSessionRunner (SessionRunner subclass; uses the deferred processor)
  processor.py — RaptorStreamProcessor (StreamProcessor subclass; deferred + parallel execution)

Key capabilities (implemented natively inside Flocks):
  - Parallel execution of multiple tool calls from a single LLM response (asyncio.gather, up to 8)
  - Path-conflict detection: tools that write to the same file are automatically serialised
  - Full turn_state SSE semantics and queued-message continuation (design §3.2)
  - Flocks agent identity preserved (system prompt / tool whitelist / skill constraints all active)
"""

from .engine import RaptorEngine

__all__ = ["RaptorEngine"]
