"""Workflow execution state containers."""

from __future__ import annotations

from dataclasses import dataclass, field
import uuid
from typing import Any, Dict, Literal, Optional, Set

from pydantic import BaseModel, Field


_VERTEX_OUTPUT_KEY_LIMIT = 50


class StepResult(BaseModel):
    node_id: str
    inputs: Dict[str, Any] = Field(default_factory=dict)
    outputs: Dict[str, Any] = Field(default_factory=dict)
    stdout: str = ""
    error: Optional[str] = None
    traceback: Optional[str] = None
    duration_ms: Optional[float] = None


class ExecutionResult(BaseModel):
    steps: int
    history: list[StepResult] = Field(default_factory=list)
    last_node_id: Optional[str] = None
    outputs: Dict[str, Any] = Field(default_factory=dict)
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class WorkflowExecutionState:
    """Mutable state for one workflow engine run."""

    run_id: str
    history_mode: Literal["full", "summary"]
    retain_history: bool = False
    steps: int = 0
    last_node_id: Optional[str] = None
    last_outputs: Dict[str, Any] = field(default_factory=dict)
    history: list[StepResult] = field(default_factory=list)
    join_inputs: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)
    join_seen_sources: Dict[str, Set[str]] = field(default_factory=dict)
    vertex_outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def retain_step(self, step: StepResult) -> None:
        if self.retain_history:
            self.history.append(step)

    def record_vertex_output(self, node_id: str, outputs: Dict[str, Any]) -> None:
        self.vertex_outputs[node_id] = outputs

    def build_context(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "steps": self.steps,
            "last_node_id": self.last_node_id,
            "outputs": self.last_outputs,
            "history": self.history,
            "vertex_output_keys": {
                node_id: list(outputs.keys())[:_VERTEX_OUTPUT_KEY_LIMIT]
                for node_id, outputs in self.vertex_outputs.items()
            },
        }

    def to_result(self) -> ExecutionResult:
        return ExecutionResult(
            steps=self.steps,
            history=self.history,
            last_node_id=self.last_node_id,
            outputs=self.last_outputs,
            run_id=self.run_id,
        )
