"""Restricted tools for the phase-one production situation-report Agent."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from flocks.situation_report.product import tool_display
from flocks.situation_report.product.workspace import (
    read_generation_context,
    read_material_detail,
    read_material_page,
    validate_candidate_report,
    write_candidate_report,
)
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)


async def _run(
    ctx: ToolContext, step: str, operation: Callable[..., Awaitable[dict[str, Any]]], **kwargs: Any
) -> ToolResult:
    config = await tool_display.settings(ctx.session_id, kwargs["generation_id"])
    language = config["language"]
    if step == "write" and config["revision"]:
        step = "revision"

    async def progress(current_step: str) -> None:
        if ctx.aborted:
            raise asyncio.CancelledError()
        value = tool_display.metadata(current_step, "running", language)
        if current_step == "materials":
            start = kwargs.get("offset", 0) + 1
            value["display"]["detail"] = (
                f"Reading from material {start}." if language == "en-US" else f"从第{start}条素材开始读取。"
            )
        ctx.metadata({"title": value["display"]["title"], "metadata": value})
        await asyncio.sleep(0)  # Let the existing SSE/persistence callback run.
        if ctx.aborted:
            raise asyncio.CancelledError()

    try:
        await progress(step)
        if step in {"write", "revision"}:
            kwargs["on_validation"] = lambda: progress("validate")
        output = await operation(**kwargs)
        if ctx.aborted:
            raise asyncio.CancelledError()
        display = tool_display.completed_metadata(step, output, language)
        return ToolResult(
            success=True,
            output=output,
            title=display["display"]["title"],
            metadata=display,
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            metadata=tool_display.metadata(step, "failed", language),
        )


@ToolRegistry.register_function(
    name="situation_product_context_read",
    description=(
        "Read the verified phase-one report context for this Session and generation. "
        "Returns the immutable template, operation, language, report title, material count, "
        "and the current base report only for modify."
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="generation_id",
            type=ParameterType.STRING,
            description="Exact generationID supplied in the product task instruction.",
            required=True,
        )
    ],
)
async def situation_product_context_read(ctx: ToolContext, generation_id: str) -> ToolResult:
    return await _run(
        ctx,
        "context",
        read_generation_context,
        session_id=ctx.session_id,
        generation_id=generation_id,
    )


@ToolRegistry.register_function(
    name="situation_product_material_read",
    description=(
        "Read one verified page of the immutable material snapshot for this report generation. "
        "Pages are bounded by serialized size as well as record count. Continue with the top-level "
        "nextOffset/nextContentOffset as offset/content_offset until hasMore=false before drafting. "
        "Oversized records return materialFragment.text JSON slices; read every slice. "
        "Numeric backend timestamps also include "
        "normalized *_iso_utc fields; use those normalized values in the report."
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="generation_id",
            type=ParameterType.STRING,
            description="Exact generationID supplied in the product task instruction.",
            required=True,
        ),
        ToolParameter(
            name="offset",
            type=ParameterType.INTEGER,
            description="Zero-based material offset.",
            required=False,
            default=0,
        ),
        ToolParameter(
            name="limit",
            type=ParameterType.INTEGER,
            description="Maximum records from 1 through 50; size limits can return fewer.",
            required=False,
            default=20,
        ),
        ToolParameter(
            name="content_offset",
            type=ParameterType.INTEGER,
            required=False,
            default=0,
            description="Character cursor inside a large material; copy top-level nextContentOffset with nextOffset.",
        ),
    ],
)
async def situation_product_material_read(
    ctx: ToolContext,
    generation_id: str,
    offset: int = 0,
    limit: int = 20,
    content_offset: int = 0,
) -> ToolResult:
    return await _run(
        ctx,
        "materials",
        read_material_page,
        session_id=ctx.session_id,
        generation_id=generation_id,
        offset=offset,
        limit=limit,
        content_offset=content_offset,
    )


@ToolRegistry.register_function(
    name="situation_product_source_read",
    description=(
        "Query the business backend for the full detail of one material selected in this report. "
        "Use only to resolve a specific ambiguity or factual conflict; ordinary authoring must use "
        "the immutable material snapshot. The first result is cached for this generation. "
        "Large details return bounded detailText with hasMore/nextOffset; continue with this "
        "same tool, or locate a fact with query. Never use filesystem tools to read details."
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="generation_id",
            type=ParameterType.STRING,
            description="Exact generationID supplied in the product task instruction.",
            required=True,
        ),
        ToolParameter(
            name="material_id",
            type=ParameterType.STRING,
            description="Exact source_type:source_id identity returned for one declared material.",
            required=True,
        ),
        ToolParameter(
            name="reason",
            type=ParameterType.STRING,
            description="Specific factual ambiguity or conflict being resolved.",
            required=True,
        ),
        ToolParameter(
            name="offset",
            type=ParameterType.INTEGER,
            required=False,
            default=0,
            description="Zero-based character offset in cached detail JSON; use nextOffset to continue.",
        ),
        ToolParameter(
            name="limit",
            type=ParameterType.INTEGER,
            required=False,
            default=6_000,
            description="Maximum detail characters per response, from 1 through 8000.",
        ),
        ToolParameter(
            name="query",
            type=ParameterType.STRING,
            required=False,
            default="",
            description="Optional case-sensitive literal text to find at or after offset. Not a regex or path.",
        ),
    ],
)
async def situation_product_source_read(
    ctx: ToolContext,
    generation_id: str,
    material_id: str,
    reason: str,
    offset: int = 0,
    limit: int = 6_000,
    query: str = "",
) -> ToolResult:
    return await _run(
        ctx,
        "source",
        read_material_detail,
        session_id=ctx.session_id,
        generation_id=generation_id,
        material_id=material_id,
        reason=reason,
        offset=offset,
        limit=limit,
        query=query,
    )


@ToolRegistry.register_function(
    name="situation_product_report_write",
    description=(
        "Write the complete candidate Markdown for this generation into the restricted work area. "
        "Store report-to-material traceability in evidence_map, never in the report body. It cannot "
        "update current output. The write automatically validates and returns validation.status, "
        "issues, warnings and attempt; no separate validate call is needed after a successful write. "
        "A repair must supply the SHA-256 returned by the prior write."
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="generation_id",
            type=ParameterType.STRING,
            description="Exact generationID supplied in the product task instruction.",
            required=True,
        ),
        ToolParameter(
            name="content",
            type=ParameterType.STRING,
            description="Complete candidate report Markdown, without a wrapping code fence.",
            required=True,
        ),
        ToolParameter(
            name="evidence_map",
            type=ParameterType.OBJECT,
            description=(
                "Internal mapping from every exact material_id to one or more exact report H2 "
                "headings where that material informed facts, statistics, or analysis. These IDs "
                "are stored outside the Markdown report."
            ),
            required=True,
            json_schema={
                "type": "object",
                "additionalProperties": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1, "maxLength": 200},
                },
            },
        ),
        ToolParameter(
            name="expected_sha256",
            type=ParameterType.STRING,
            description="Prior candidate SHA-256 when repairing an existing candidate.",
            required=False,
            default="",
        ),
    ],
)
async def situation_product_report_write(
    ctx: ToolContext,
    generation_id: str,
    content: str,
    evidence_map: dict[str, list[str]],
    expected_sha256: str = "",
) -> ToolResult:
    return await _run(
        ctx,
        "write",
        write_candidate_report,
        session_id=ctx.session_id,
        generation_id=generation_id,
        content=content,
        evidence_map=evidence_map,
        expected_sha256=expected_sha256,
    )


@ToolRegistry.register_function(
    name="situation_product_report_validate",
    description=(
        "Validate the current candidate against the immutable template heading contract, internal "
        "evidence map, Markdown structure, and internal identifier/path leakage policy. At most "
        "three distinct candidate/evidence validation attempts. Writes already return validation; "
        "use this compatibility tool only if that result is missing. Unchanged checks reuse the result."
    ),
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="generation_id",
            type=ParameterType.STRING,
            description="Exact generationID supplied in the product task instruction.",
            required=True,
        )
    ],
)
async def situation_product_report_validate(ctx: ToolContext, generation_id: str) -> ToolResult:
    return await _run(
        ctx,
        "validate",
        validate_candidate_report,
        session_id=ctx.session_id,
        generation_id=generation_id,
    )
