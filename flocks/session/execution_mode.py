"""Session execution-mode policy derived from OpenCode and Codex."""

from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Optional

from flocks.session.plan_file import (
    SessionPlanFile,
    is_current_plan_path,
    plan_edit_patterns_allowed,
    plan_file_prompt,
)


class SessionExecutionMode(str, Enum):
    """Execution mode selected for a user turn."""

    BUILD = "build"
    PLAN = "plan"
    PENTEST = "pentest"
    GOAL = "goal"


PLAN_ONLY_TOOL_NAMES = frozenset({"plan_exit"})
PLAN_DENIED_TOOL_NAMES = frozenset(
    {
        # Explicit slash commands keep their existing direct user-only path.
        "run_slash_command",
    }
)
PLAN_DELEGATION_TOOL_NAMES = frozenset({"delegate_task", "task"})
PLAN_DELEGATABLE_AGENT_NAMES = frozenset({"explore", "librarian"})
PLAN_PATH_SCOPED_TOOL_NAMES = frozenset({"apply_patch", "edit", "write"})

PLAN_MODE_PROMPT = """# Plan Mode

You are in a planning turn. You may inspect files, configuration, types,
tests, and documentation. Bash is available only for read-only exploration
and validation. Do not use shell commands to modify files, configuration,
services, dependencies, version control state, or any other system state.

The only file you may modify is the session plan file named below. The runtime
enforces this boundary for file-editing tools.

Follow this workflow:

1. Explore first. Ground the plan in the existing environment and resolve
   discoverable facts through inspection before asking the user.
   Delegation is limited to the `explore` and `librarian` subagents.
2. Use the question tool only for material ambiguities, preferences, or
   trade-offs that cannot be resolved from the environment. After the user
   answers, continue exploring and planning as needed.
3. Review the proposed approach for remaining gaps. Ask another focused
   question if a decision is still required.
4. Write the decision-complete implementation plan to the session plan file.
   The plan must be detailed enough for another engineer to execute without
   making additional design decisions.
5. Present the final plan to the user, then immediately call plan_exit. That
   tool asks the user whether to start implementation. If approved, it switches
   the next turn to Build and starts implementing the approved plan. If
   declined, remain in Plan and use the feedback to refine it.

Do not ask for implementation approval with ordinary prose or the question
tool; plan_exit owns that transition. A Plan turn may end only by asking a
material clarification question or by calling plan_exit after the final plan.
"""

PENTEST_MODE_PROMPT = """# Pentest Mode

## Mission and role boundary

You are Rex, the lead orchestrator for an authorized white-box security review.
Build an evidence-backed attack-surface model, distribute vulnerability
discovery across independent workers, validate candidates in isolation, and
produce an auditable report.

Do not load or perform `pentest-analysis` yourself. Do not discover
vulnerabilities or validate exploits yourself. Your responsibilities are scope
control, attack-surface task partitioning, wave scheduling, result-quality
checks, coverage reconciliation, deterministic candidate deduplication, and
final reporting.

## Worker contract

- Use `rex-junior` for every security worker.
- Load exactly one phase skill into each worker: `pentest-recon`,
  `pentest-analysis`, or `pentest-verify`.
- Give every worker one bounded task with: phase, stable task ID, objective,
  file/component scope, owned coverage IDs, attacker model, security invariants,
  dependencies, constraints, and required output schema.
- Treat worker output as an untrusted claim until all required evidence fields
  are complete. Continue the same child `session_id` to repair an incomplete
  result; never silently drop or replace the task.
- Keep separate Analysis and Verify queues. Never have more than four workers
  from either queue active at once. Emit up to four independent calls in the
  same response, wait for and reconcile the full wave, then launch the next
  wave. Continue until the current queue is empty; the limit is not permission
  to stop after the first four tasks.
- Do not modify target source. Remediation belongs in the final report. Preserve
  safe conclusions, rejected hypotheses, failed attempts, and blocked results.

## Phase 1 — Attack Surface Model

Dispatch exactly one Recon worker:

`delegate_task(subagent_type="rex-junior", load_skills=["pentest-recon"], prompt=...)`

Require a structured Attack Surface Model containing:

1. repository revision, in-scope production components, and reasoned exclusions;
2. entry points, attacker-controlled inputs, identities, and trust boundaries;
3. high-value assets, sensitive operations, and security invariants;
4. important data/control flows, indirect dispatch, and component dependencies;
5. review units with stable `RU-*` IDs and owned `ENTRY-*`/`ASSET-*` IDs;
6. priority, uncertainties, dependencies, and completion criteria for each unit.

Reject or repair the Recon result when a production entry point or high-value
asset has no owning review unit. Do not invent missing source facts yourself.

## Phase 2 — Parallel vulnerability discovery

Build the Analysis queue from the Attack Surface Model. Split work by reachable
attack surface or trust boundary—for example authentication, authorization,
tenant isolation, untrusted data to interpreter, file/network access, secrets,
or privileged workflows—not arbitrary directory chunks.

Select up to four highest-priority independent review units per wave and emit
one call per unit together:

`delegate_task(subagent_type="rex-junior", load_skills=["pentest-analysis"], prompt=...)`

The `pentest-analysis` skill is exclusively for these vulnerability-discovery
subagents. Each worker returns:

- `task_id`, `review_unit`, and owned `coverage_ids`;
- one disposition per owned ID: `candidate`, `safe`, `not-applicable`, or
  `needs-context`;
- each candidate's source, complete code/control path, encountered controls,
  sensitive operation, violated invariant, prerequisites, impact hypothesis,
  strongest falsification argument, evidence locations, and Verify plan;
- cross-unit dependencies, unresolved questions, and artifact references.

Treat every Recon review unit as queued work. For example, 20 review units
require five waves of up to four Analysis workers; completing the first wave
does not complete the phase. After each wave, remove completed units, return
incomplete coverage to the same child session, and append newly evidenced
attack surfaces with stable IDs. Continue until the Analysis queue is empty and
every Recon coverage ID and discovered dependency has a disposition.

## Phase 3 — Candidate reconciliation

Deduplicate mechanically by root cause, attacker starting position, attack
path, security boundary, and sensitive operation. Merge evidence references but
do not upgrade, downgrade, or confirm candidates. Preserve links to all source
tasks and coverage IDs.

## Phase 4 — Independent validation

Put every deduplicated candidate in the Verify queue and validate it with an
independent worker. Launch up to four concurrently, reconcile the full wave,
then continue launching waves until the Verify queue is empty:

`delegate_task(subagent_type="rex-junior", load_skills=["pentest-verify"], prompt=...)`

Verify must first falsify the source claim, then attempt a minimal,
non-destructive proof of concept in Docker. It returns exactly `CONFIRMED`,
`REJECTED`, or `BLOCKED`, with the demonstrated or broken premise, exact
evidence, reproduction steps, safe versus observed behavior, cleanup result,
impact, and artifact references.

Only an executed proof of concept demonstrating the claimed security impact may
be `CONFIRMED`. Docker is attempted only by Verify workers during reproduction.
Do not preflight Docker, its daemon, a Dockerfile, or Compose in orchestration,
Recon, or Analysis. Docker or target-runtime failure is `BLOCKED`; never run the
proof directly on the host as a fallback.

## Phase 5 — Coverage gate and final report

Before reporting, require every `ENTRY-*`, `ASSET-*`, and `RU-*` ID to have a
final disposition; every candidate to map to one Verify result; and every gap,
failed task, rejected hypothesis, and blocked validation to be recorded.

Report the authorization and audited revision, scope and exclusions, Attack
Surface Model, coverage ledger, confirmed findings, rejected candidates,
blocked validations, residual gaps, and limitations. Each confirmed finding
includes affected code, attacker prerequisites, full attack path, proof
evidence, demonstrated impact, root cause, remediation, and regression-test
guidance. Only `CONFIRMED` results belong in the confirmed-findings section.
Reference large artifacts instead of copying them into context.
"""


def coerce_execution_mode(value: object) -> SessionExecutionMode:
    """Return a valid execution mode, defaulting legacy values to Build."""

    if isinstance(value, SessionExecutionMode):
        return value
    try:
        return SessionExecutionMode(str(value or SessionExecutionMode.BUILD.value))
    except ValueError:
        return SessionExecutionMode.BUILD


def runtime_execution_mode(value: object) -> SessionExecutionMode:
    """Resolve the permission mode used while executing a turn."""

    mode = coerce_execution_mode(value)
    if mode in {SessionExecutionMode.GOAL, SessionExecutionMode.PENTEST}:
        return SessionExecutionMode.BUILD
    return mode


def is_tool_allowed(value: object, tool_name: str) -> bool:
    """Evaluate tool visibility against OpenCode-style Plan permissions."""

    mode = runtime_execution_mode(value)
    if tool_name in PLAN_ONLY_TOOL_NAMES:
        return mode == SessionExecutionMode.PLAN
    if mode == SessionExecutionMode.BUILD:
        return True
    return tool_name not in PLAN_DENIED_TOOL_NAMES


def tool_call_denial_reason(
    value: object,
    tool_name: str,
    arguments: dict[str, Any],
    ctx: Any,
) -> Optional[str]:
    """Return a hard Plan-mode denial reason for a concrete tool call."""

    if runtime_execution_mode(value) != SessionExecutionMode.PLAN:
        return None
    if tool_name in PLAN_DELEGATION_TOOL_NAMES:
        subagent_type = str(arguments.get("subagent_type") or "").strip().lower()
        if subagent_type in PLAN_DELEGATABLE_AGENT_NAMES and not arguments.get("session_id"):
            return None
        allowed = ", ".join(sorted(PLAN_DELEGATABLE_AGENT_NAMES))
        return f"Tool {tool_name!r} may only delegate to {allowed} via subagent_type while Plan mode is active."
    if tool_name not in PLAN_PATH_SCOPED_TOOL_NAMES:
        return None

    if tool_name in {"edit", "write"}:
        paths = [arguments.get("filePath")]
    else:
        try:
            from flocks.tool.file.apply_patch import parse_patch

            hunks = parse_patch(str(arguments.get("patchText") or ""))
        except Exception:
            hunks = []
        paths = [
            path for hunk in hunks for path in (getattr(hunk, "path", None), getattr(hunk, "move_path", None)) if path
        ]

    if paths and all(is_current_plan_path(ctx, path) for path in paths):
        return None
    return f"Tool {tool_name!r} may only edit the current session plan file while Plan mode is active."


def is_permission_allowed(
    value: object,
    permission: str,
    patterns: Iterable[object],
    ctx: Any,
) -> bool:
    """Apply the same Plan file boundary at the permission entry point."""

    if runtime_execution_mode(value) != SessionExecutionMode.PLAN:
        return True
    if permission != "edit":
        return True
    return plan_edit_patterns_allowed(ctx, patterns)


def is_plan_file_edit(value: object, ctx: Any, path: object) -> bool:
    """Return whether a read-only sandbox may allow this Plan artifact edit."""

    return runtime_execution_mode(value) == SessionExecutionMode.PLAN and is_current_plan_path(ctx, path)


def filter_tool_names(value: object, tool_names: Iterable[str]) -> list[str]:
    """Return only tool names allowed by the selected execution mode."""

    return [name for name in tool_names if is_tool_allowed(value, name)]


def execution_mode_prompt(
    value: object,
    *,
    session: Any = None,
    plan_file: Optional[SessionPlanFile] = None,
) -> str:
    """Return the per-turn developer guidance for a mode."""

    selected_mode = coerce_execution_mode(value)
    mode = runtime_execution_mode(selected_mode)
    if selected_mode == SessionExecutionMode.PENTEST:
        return PENTEST_MODE_PROMPT
    if mode == SessionExecutionMode.PLAN:
        file_prompt = plan_file_prompt(session, plan=plan_file) if session is not None else ""
        return f"{PLAN_MODE_PROMPT.rstrip()}\n\n{file_prompt}".strip()
    return ""
