"""
Default constants for the session module.

Single source of truth for fallback provider/model values used across
runner.py, session_loop.py, and message.py.
"""

import os

_DEFAULT_PROVIDER = "anthropic"
_DEFAULT_MODEL = "claude-sonnet-4-20250514"


def fallback_provider_id() -> str:
    """Return the fallback provider, respecting runtime env-var overrides."""
    return os.environ.get("LLM_PROVIDER", _DEFAULT_PROVIDER)


def fallback_model_id() -> str:
    """Return the fallback model, respecting runtime env-var overrides."""
    return os.environ.get("LLM_MODEL", _DEFAULT_MODEL)


# Doom-loop detection: if the last N tool calls in a single assistant
# message are identical (same tool + same input), stop processing.
DOOM_LOOP_THRESHOLD = 3

# Cross-step loop guard thresholds. These complement the per-message doom-loop
# detection in ``stream_processor.py`` by stopping repeated tool-only turns.
REPEATED_EXACT_TOOL_CALL_HALT_THRESHOLD = 3

# Consecutive steps whose tool-call arguments were cut off mid-stream before
# the turn stops. Each truncated step is fed back to the model so it can split
# the call; this only bounds a model that keeps producing oversized calls.
MAX_CONSECUTIVE_TOOL_ARG_TRUNCATIONS = 3
