"""Read-only retrieval from the current Session's bound Datasets."""

from __future__ import annotations

from flocks.agent.toolset import resolve_agent_initial_tools
from flocks.auth.context import get_current_auth_user
from flocks.knowledgebase.errors import KnowledgebaseError
from flocks.knowledgebase.retrieval import retrieve_for_session
from flocks.knowledgebase.runtime import get_client
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)


@ToolRegistry.register_function(
    name="rag_retrieve",
    group="知识检索",
    description=(
        "Read-only retrieval from this Session's selected Datasets. "
        "Omit dataset or pass an empty list to search that whole selection. "
        "A non-empty dataset list may only narrow it."
    ),
    category=ToolCategory.SEARCH,
    native=True,
    always_load=False,
    requires_confirmation=False,
    tags=["knowledgebase", "retrieval", "read-only"],
    parameters=[
        ToolParameter(name="keywords", type=ParameterType.STRING, description="Retrieval question or keywords."),
        ToolParameter(
            name="dataset",
            type=ParameterType.ARRAY,
            required=False,
            description="Optional subset of this Session's Dataset IDs. Leave empty to search every selected Dataset.",
        ),
        ToolParameter(name="top_k", type=ParameterType.INTEGER, required=False, default=5, description="Maximum chunks."),
        ToolParameter(
            name="similarity_threshold",
            type=ParameterType.NUMBER,
            required=False,
            default=0.2,
            description="Minimum similarity from 0 to 1.",
        ),
        ToolParameter(
            name="vector_similarity_weight",
            type=ParameterType.NUMBER,
            required=False,
            default=0.3,
            description="Vector similarity weight from 0 to 1.",
        ),
    ],
)
async def rag_retrieve_tool(
    ctx: ToolContext,
    keywords: str,
    dataset: list[str] | None = None,
    top_k: int = 5,
    similarity_threshold: float = 0.2,
    vector_similarity_weight: float = 0.3,
) -> ToolResult:
    try:
        from flocks.agent.registry import Agent

        agent = await Agent.get(ctx.agent)
        names, _rules = resolve_agent_initial_tools(
            getattr(agent, "tools", None),
            getattr(agent, "permission", None),
            agent_name=ctx.agent,
        )
        if "rag_retrieve" not in names:
            raise KnowledgebaseError(403, "tool_not_allowed_for_agent", "Knowledge retrieval is not available to this agent.")
        client = get_client()
        if client is None:
            raise KnowledgebaseError(503, "knowledgebase_not_configured", "Knowledge retrieval is not configured.")
        await ctx.ask(permission="rag_retrieve", patterns=["*"])
        result = await retrieve_for_session(
            ctx.session_id,
            get_current_auth_user(),
            keywords,
            client=client,
            dataset=dataset,
            top_k=top_k,
            similarity_threshold=similarity_threshold,
            vector_similarity_weight=vector_similarity_weight,
        )
        return ToolResult(success=True, output=result)
    except KnowledgebaseError as error:
        return ToolResult(success=False, error=error.message, metadata={"code": error.code})
