"""Stage budgets for one batch attempt; shared by worker and supervisor."""

from __future__ import annotations

import math
from pathlib import Path
import time

PHASE_ALIASES = {"probing": "dynamic_validation", "cybergym_solving": "dynamic_validation"}
# Cumulative seconds per phase, copied into each new batch's saved config.
DEFAULT_PHASE_TIMEOUTS = {
    "source_extraction": 3600,
    "snapshot": 3600,
    "threat_modeling": 7200,
    "baseline": 7200,
    "investigation": 3600,
    "verification": 7200,
    "adjudication": 3600,
    "targeted_rescan": 3600,
    "poc_generation": 7200,
    "dynamic_validation": 3600,
    "finalization": 1200,
    "cleanup": 1200,
}
PHASES = frozenset(DEFAULT_PHASE_TIMEOUTS)


class ExecutionControlError(RuntimeError):
    code = "execution_control_error"


class PhaseTimeout(TimeoutError):
    code = "phase_timeout"

    def __init__(self, details: dict):
        self.details = details
        super().__init__(f"Phase {details['timeout_phase']} exceeded {details['phase_budget_seconds']} seconds")


def validate_budgets(value: dict, *, dynamic: bool) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("phase_timeouts must be an object of phase names and positive seconds")
    unknown = value.keys() - PHASES
    required = set(PHASES)
    if not dynamic:
        required.remove("dynamic_validation")
    missing = required - value.keys()
    if unknown or missing:
        raise ValueError(f"Invalid phase_timeouts: unknown={sorted(unknown)}, missing={sorted(missing)}")
    if any(type(seconds) is not int or seconds <= 0 for seconds in value.values()):
        raise ValueError("Every phase timeout must be a positive integer in seconds")
    return dict(value)


def timeout_details(current: dict, *, now: float | None = None) -> dict | None:
    state = current.get("phase_timeout")
    if not isinstance(state, dict) or state.get("phase") not in PHASES:
        raise ExecutionControlError("Missing or invalid phase timeout state")
    for key in ("entered_at", "deadline", "budget_seconds"):
        number = state.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ExecutionControlError(f"Invalid phase timeout {key}")
    now = time.monotonic() if now is None else now
    if now < state["entered_at"] or state["budget_seconds"] <= 0:
        raise ExecutionControlError("Phase timeout clock or budget is invalid")
    if now < state["deadline"]:
        return None
    return {
        "status": "timed_out", "failure_code": PhaseTimeout.code,
        "timeout_phase": state["phase"], "phase_budget_seconds": state["budget_seconds"],
        "phase_elapsed_seconds": state["budget_seconds"] + now - state["deadline"],
    }


def enter_phase(current: dict, budgets: dict, phase: str, *, after_exit: bool = False, now: float | None = None) -> None:
    """Advance a detached task record. The caller must atomically persist it."""
    phase = PHASE_ALIASES.get(phase, phase)
    if phase not in budgets or phase not in PHASES:
        raise ExecutionControlError(f"No budget configured for phase {phase}")
    if after_exit and phase != "cleanup":
        raise ExecutionControlError("Only cleanup may start after worker exit")
    now = time.monotonic() if now is None else now
    previous = current.get("phase_timeout")
    spent = {}
    if previous is not None:
        expired = timeout_details(current, now=now)
        if previous["phase"] == phase:
            if expired:
                raise PhaseTimeout(expired)
            return  # Repeated notifications never renew a deadline.
        if expired and not after_exit:
            raise PhaseTimeout(expired)
        spent = dict(previous.get("spent_seconds", {}))
        spent[previous["phase"]] = spent.get(previous["phase"], 0) + now - previous["entered_at"]
    remaining = budgets[phase] - spent.get(phase, 0)
    current["phase_timeout"] = {
        "phase": phase, "entered_at": now, "deadline": now + remaining,
        "budget_seconds": budgets[phase], "spent_seconds": spent,
    }
    if remaining <= 0:
        raise PhaseTimeout(timeout_details(current, now=now))


def read_control(task_dir: Path, attempt: str, previous: dict | None = None) -> dict:
    """A transient read failure never grants more time to a running attempt."""
    from flocks.security.batch import read_json

    try:
        current = read_json(task_dir / "current.json")
    except (OSError, ValueError) as exc:
        if previous is None:
            raise ExecutionControlError("Unable to read phase deadline") from exc
        current = previous
    if current.get("attempt") != attempt:
        raise ExecutionControlError("Phase deadline belongs to another attempt")
    timeout_details(current)  # Validate before using it as the next fallback.
    return current
