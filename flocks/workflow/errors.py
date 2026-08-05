"""Project error types."""

from typing import Optional


class FlocksWorkflowError(Exception):
    """Base exception for this project."""


class WorkflowValidationError(FlocksWorkflowError):
    """Raised when a workflow JSON or graph is invalid."""


class NodeExecutionError(FlocksWorkflowError):
    """Raised when a node execution fails."""

    def __init__(
        self,
        node_id: str,
        message: str,
        *,
        stdout: Optional[str] = None,
        traceback: Optional[str] = None,
        execution_context: Optional[dict] = None,
    ):
        super().__init__(message)
        self.node_id = node_id
        self.stdout = stdout
        self.traceback = traceback
        self.execution_context = execution_context or {}


class MaxStepsExceededError(FlocksWorkflowError):
    """Raised when workflow execution exceeds max_steps."""

    def __init__(self, max_steps: int):
        super().__init__(f"Exceeded max_steps={max_steps}. Possible infinite loop.")
        self.max_steps = max_steps


class RunCancelledError(FlocksWorkflowError):
    """Raised when a workflow run is cancelled."""

    def __init__(self, run_id: str):
        super().__init__(f"Run cancelled: run_id={run_id}")
        self.run_id = run_id


class RunTimeoutError(FlocksWorkflowError):
    """Raised when a workflow run exceeds a wall-clock timeout."""

    def __init__(self, run_id: str, timeout_s: float):
        super().__init__(f"Run timed out: run_id={run_id} timeout_s={timeout_s}")
        self.run_id = run_id
        self.timeout_s = timeout_s


class NodeTimeoutError(NodeExecutionError):
    """Raised when a node exceeds its time limit and aborts the workflow run."""

    def __init__(
        self,
        node_id: str,
        timeout_s: float,
        *,
        execution_context: Optional[dict] = None,
    ):
        super().__init__(
            node_id=node_id,
            message=f"节点执行超时 ({timeout_s}s): node_id={node_id}",
            execution_context=execution_context,
        )
        self.timeout_s = timeout_s
