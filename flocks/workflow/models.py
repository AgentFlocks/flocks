"""Core workflow schema models."""

from collections.abc import Iterable
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import WorkflowValidationError
from .triggers.models import TriggerDefinition, normalize_trigger_definitions


class Node(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1)
    type: Literal[
        "python", "logic", "branch", "loop",
        "tool", "llm", "http_request", "subworkflow",
    ] = "python"
    code: Optional[str] = None
    description: Optional[str] = None
    select_key: Optional[str] = None
    join: bool = False
    join_mode: Literal["flat", "namespace"] = "flat"
    join_conflict: Literal["overwrite", "error"] = "overwrite"
    join_namespace_key: str = "__by_source__"
    input_schema: Optional[Dict[str, Any]] = Field(None, alias="inputSchema")
    output_schema: Optional[Dict[str, Any]] = Field(None, alias="outputSchema")

    # tool 节点
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None

    # llm 节点
    prompt: Optional[str] = None
    model: Optional[str] = None

    # llm / subworkflow 共用
    output_key: Optional[str] = None

    # http_request 节点
    method: Optional[str] = None
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    body: Optional[Any] = None
    response_key: Optional[str] = None

    # subworkflow 节点
    workflow_id: Optional[str] = None
    inputs_mapping: Optional[Dict[str, str]] = None
    inputs_const: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _validate_code(self) -> "Node":
        if self.type == "python":
            if self.code is None or not str(self.code).strip():
                raise ValueError("python node requires non-empty code")
        elif self.type == "logic":
            if self.description is None or not str(self.description).strip():
                raise ValueError("logic node requires non-empty description")
        elif self.type == "tool":
            if not self.tool_name or not str(self.tool_name).strip():
                raise ValueError("tool node requires non-empty tool_name")
        elif self.type == "llm":
            if not self.prompt or not str(self.prompt).strip():
                raise ValueError("llm node requires non-empty prompt")
        elif self.type == "http_request":
            if not self.url or not str(self.url).strip():
                raise ValueError("http_request node requires non-empty url")
            if not self.method or not str(self.method).strip():
                raise ValueError("http_request node requires non-empty method")
        elif self.type == "subworkflow":
            if not self.workflow_id or not str(self.workflow_id).strip():
                raise ValueError("subworkflow node requires non-empty workflow_id")
        else:
            if self.code is not None and not str(self.code).strip():
                self.code = None
        return self


class Edge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    order: int = 0
    label: Optional[str] = None
    mapping: Optional[Dict[str, str]] = None
    const: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _validate_order(self) -> "Edge":
        if self.order < 0:
            raise ValueError("edge.order must be >= 0")
        return self


class Workflow(BaseModel):
    version: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    start: str = Field(min_length=1)
    nodes: List[Node] = Field(default_factory=list)
    edges: List[Edge] = Field(default_factory=list)
    triggers: List[TriggerDefinition] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _validate_graph(self) -> "Workflow":
        if self.version is not None:
            self.version = None
        if not self.triggers and isinstance(self.metadata, dict) and isinstance(self.metadata.get("triggers"), list):
            self.triggers = normalize_trigger_definitions(self.metadata.get("triggers"))
        node_ids = [n.id for n in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            dupes = sorted({x for x in node_ids if node_ids.count(x) > 1})
            raise WorkflowValidationError(f"Duplicate node ids: {dupes}")
        nodes_set = set(node_ids)
        if self.start not in nodes_set:
            raise WorkflowValidationError(f"start node id '{self.start}' not found in nodes")
        for e in self.edges:
            if e.from_ not in nodes_set:
                raise WorkflowValidationError(f"edge.from '{e.from_}' not found in nodes")
            if e.to not in nodes_set:
                raise WorkflowValidationError(f"edge.to '{e.to}' not found in nodes")
        return self

    def nodes_by_id(self) -> Dict[str, Node]:
        return {n.id: n for n in self.nodes}

    def outgoing_edges(self, node_id: str) -> List[Edge]:
        return [e for e in self.edges if e.from_ == node_id]

    def adjacency(self) -> Dict[str, List[Edge]]:
        adj: Dict[str, List[Edge]] = {n.id: [] for n in self.nodes}
        for e in self.edges:
            adj.setdefault(e.from_, []).append(e)
        for k in adj:
            adj[k].sort(key=lambda x: (x.order, x.to))
        return adj

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Workflow":
        try:
            return cls.model_validate(data)
        except Exception as e:
            if isinstance(e, WorkflowValidationError):
                raise
            raise WorkflowValidationError(str(e)) from e

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


def ensure_nodes(nodes: Iterable[Node]) -> List[Node]:
    return list(nodes)
