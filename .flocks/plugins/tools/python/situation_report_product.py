"""Restricted tools for the phase-one production situation-report Agent."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

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


async def _run(operation: Callable[..., Awaitable[dict[str, Any]]], **kwargs: Any) -> ToolResult:
    try:
        output = await operation(**kwargs)
        return ToolResult(
            success=True,
            output=output,
            metadata={"runtime": "situation-report-product-v1"},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            metadata={"runtime": "situation-report-product-v1"},
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
        read_generation_context,
        session_id=ctx.session_id,
        generation_id=generation_id,
    )


@ToolRegistry.register_function(
    name="situation_product_material_read",
    description=(
        "Read one verified page of the immutable material snapshot for this report generation. "
        "Continue until hasMore=false before drafting. Numeric backend timestamps also include "
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
            description="Page size from 1 through 50.",
            required=False,
            default=20,
        ),
    ],
)
async def situation_product_material_read(
    ctx: ToolContext,
    generation_id: str,
    offset: int = 0,
    limit: int = 20,
) -> ToolResult:
    return await _run(
        read_material_page,
        session_id=ctx.session_id,
        generation_id=generation_id,
        offset=offset,
        limit=limit,
    )


@ToolRegistry.register_function(
    name="situation_product_source_read",
    description=(
        "Query the business backend for the full detail of one material selected in this report. "
        "Use only to resolve a specific ambiguity or factual conflict; ordinary authoring must use "
        "the immutable material snapshot. The first result is cached for this generation."
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
    ],
)
async def situation_product_source_read(
    ctx: ToolContext,
    generation_id: str,
    material_id: str,
    reason: str,
) -> ToolResult:
    return await _run(
        read_material_detail,
        session_id=ctx.session_id,
        generation_id=generation_id,
        material_id=material_id,
        reason=reason,
    )


@ToolRegistry.register_function(
    name="situation_product_report_write",
    description=(
        "Write the complete candidate Markdown for this generation into the restricted work area. "
        "Store report-to-material traceability in evidence_map, never in the report body. It cannot "
        "update current output. A repair must supply the SHA-256 returned by the prior write."
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
        "three validation attempts."
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
        validate_candidate_report,
        session_id=ctx.session_id,
        generation_id=generation_id,
    )
