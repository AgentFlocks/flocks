"""Invocation-scoped controls shared by headless sessions and their tools.

No policy is persisted. A ContextVar keeps unrelated sessions unaffected.
"""
from contextvars import ContextVar
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any


READ_TOOLS = frozenset({"read", "glob", "grep", "ls", "todowrite", "todoread", "todo"})
EDIT_TOOLS = frozenset({"write", "edit", "apply_patch", "multiedit"})
# These entrypoints can escape invocation lifetime/context into other workers.
DETACHED_TOOLS = frozenset({"delegate_task", "run_workflow", "run_workflow_node", "task", "task_manage", "schedule_task"})
SANDBOX_TOOLS = frozenset({"bash", "read", "write", "edit"})


@dataclass
class RuntimeControls:
    permission_mode: str = "default"
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    sandbox_required: bool = False
    max_budget: float | None = None
    total_cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    costs: dict[str, float] = field(default_factory=dict)
    failure: str | None = None

    def denial(self, name: str) -> str | None:
        if name == "question":
            return "Questions require interactive input; headless execution cannot answer them"
        if any(fnmatchcase(name, p) for p in self.disallowed_tools):
            return f"Tool denied by --disallowed-tools: {name}"
        if self.sandbox_required and name not in SANDBOX_TOOLS:
            return f"Tool has no verified sandbox adapter: {name}"
        if name in DETACHED_TOOLS:
            return f"Detached orchestration is unavailable in headless execution: {name}"
        if self.allowed_tools and not any(fnmatchcase(name, p) for p in self.allowed_tools):
            return f"Tool is outside --allowed-tools: {name}"
        if self.permission_mode == "plan" and name not in READ_TOOLS:
            return f"Plan mode disallows tool: {name}"
        if self.allowed_tools or self.permission_mode == "bypassPermissions" or name in READ_TOOLS:
            return None
        if self.permission_mode == "acceptEdits" and name in EDIT_TOOLS:
            return None
        return f"Tool requires approval in {self.permission_mode} mode: {name}; use --allowed-tools"

    def check_budget(self, pricing: Any = None, *, require_pricing: bool = False) -> None:
        if self.failure:
            raise RuntimeError(self.failure)
        if self.max_budget is None:
            return
        if self.total_cost >= self.max_budget:
            self.failure = f"Maximum budget reached ({self.total_cost:.8f} USD >= {self.max_budget:g} USD)"
        elif require_pricing and (pricing is None or pricing.currency != "USD"):
            self.failure = "--max-budget requires known USD model pricing"
        if self.failure:
            raise RuntimeError(self.failure)

    def record_usage(self, usage: dict, pricing: Any, *, previous_usage: dict | None = None) -> None:
        """Record a request snapshot minus its previously accounted snapshot."""
        from flocks.provider.cost_calculator import CostCalculator

        if not previous_usage:
            self.requests += 1
        previous_usage = previous_usage or {}
        self.input_tokens += usage.get("prompt_tokens", 0) - previous_usage.get("prompt_tokens", 0)
        self.output_tokens += usage.get("completion_tokens", 0) - previous_usage.get("completion_tokens", 0)
        if pricing is not None:
            # Price complete snapshots: late cache details can reduce the cost.
            cost_delta = 0.0
            for snapshot, sign in ((usage, 1), (previous_usage, -1)):
                cost = CostCalculator.calculate(
                    input_tokens=snapshot.get("prompt_tokens", 0),
                    output_tokens=snapshot.get("completion_tokens", 0),
                    cached_tokens=snapshot.get("cache_read_input_tokens", 0),
                    cache_write_tokens=snapshot.get("cache_creation_input_tokens", 0),
                    pricing=pricing,
                )
                cost_delta += sign * cost.total_cost
            self.costs[pricing.currency] = self.costs.get(pricing.currency, 0.0) + cost_delta
            self.total_cost = self.costs.get("USD", 0.0)
        if self.max_budget is not None and self.total_cost >= self.max_budget:
            self.failure = f"Maximum budget reached ({self.total_cost:.8f} USD >= {self.max_budget:g} USD)"


runtime_controls: ContextVar[RuntimeControls | None] = ContextVar("runtime_controls", default=None)
