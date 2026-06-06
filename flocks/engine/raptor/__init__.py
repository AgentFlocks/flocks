"""
RaptorEngine — Pluggable adapter for hermes-agent via tui_gateway stdio JSON-RPC.

Architecture overview
---------------------
Raptor reuses hermes-agent's existing ``tui_gateway`` subprocess (entry.py)
as its execution backend.  One daemon process is kept alive per Flocks session
(per-session daemon model) so hermes's prompt cache, checkpoints and compression
work across turns.

Package contents
----------------
protocol.py       — RPC message builders, event-type constants, history helpers
daemon.py         — Per-session daemon lifecycle (spawn / reuse / teardown)
agent_bridge.py   — Inject Flocks agent system_message into hermes session
message_bridge.py — Convert Flocks MessageInfo/Parts ↔ OpenAI messages
stream_bridge.py  — Map tui_gateway events → Flocks publish_event SSE + turn_state
engine.py         — RaptorEngine Protocol implementation; registered at startup
"""

from .engine import RaptorEngine

__all__ = ["RaptorEngine"]
