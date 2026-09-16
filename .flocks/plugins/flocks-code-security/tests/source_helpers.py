"""Seed domain-test evidence using native tools and the production receipt parser.

These are fixture helpers, not replacement audit tools. Native session lifecycle
integration is exercised separately in test_builtin_tools.
"""

import hashlib
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from flocks.tool.file.read import read_tool
from flocks.tool.file.glob import glob_tool
from flocks.tool.code.grep import grep_tool
from flocks.tool.registry import ToolContext, ToolResult
from flocks_code_security.tools import _require_agent_execution, _error, STORE_ERRORS
from flocks_code_security.runtime import get_runtime
from flocks_code_security.source import SOURCE_ROLES
from flocks_code_security.source_receipts import _record_transcript_receipts


async def _source_call(ctx, name, handler, arguments):
    _require_agent_execution(ctx, SOURCE_ROLES)
    runtime = get_runtime()
    binding = runtime.source.binding(ctx.session_id)
    result = await handler(ctx, **arguments)
    if result.success:
        part = SimpleNamespace(id=uuid4().hex, tool=name, state=SimpleNamespace(input=arguments, output=result.output))
        _record_transcript_receipts(runtime, binding, [part], str(Path.cwd()))
    return result


async def inventory_source(ctx: ToolContext) -> ToolResult:
    try:
        runtime = get_runtime()
        binding = runtime.source.binding(ctx.session_id)
        root = runtime.store.get_snapshot(binding.snapshot_id).root_path
        result = await _source_call(ctx, "glob", glob_tool, {"pattern": "**/*", "path": root})
        if result.success:
            found = set(result.output.splitlines())
            result.output = {"files": [
                {"path": item.relative_path, "is_binary": item.is_binary, "line_count": item.line_count}
                for item in runtime.store.list_snapshot_files(binding.snapshot_id)
                if str(Path(root) / item.relative_path) in found
            ]}
        return result
    except STORE_ERRORS as exc:
        return _error(exc, title="Source fixture failed")


async def read_source(ctx: ToolContext, relative_path: str, start_line: int = 1, end_line: int | None = None) -> ToolResult:
    try:
        runtime = get_runtime()
        binding = runtime.source.binding(ctx.session_id)
        root = runtime.store.get_snapshot(binding.snapshot_id).root_path
        result = await _source_call(ctx, "read", read_tool, {
            "filePath": str(Path(root) / relative_path), "offset": start_line - 1,
            "limit": end_line - start_line + 1 if end_line is not None else 200,
        })
        if result.success:
            record = runtime.store.get_snapshot_file(binding.snapshot_id, relative_path)
            lines = [match[1] for line in result.output.split("\n") if (match := re.fullmatch(r"\d+\| (.*)", line))]
            text = "\n".join(lines)
            result.output = {
                "blob_digest": record.blob_digest, "text": text,
                "end_line": min(start_line + len(lines) - 1, record.line_count),
                "excerpt_hash": hashlib.sha256(text.encode()).hexdigest(),
            }
        return result
    except STORE_ERRORS as exc:
        return _error(exc, title="Source fixture failed")


async def search_source(ctx: ToolContext, query: str, path_glob: str | None = None) -> ToolResult:
    try:
        runtime = get_runtime()
        binding = runtime.source.binding(ctx.session_id)
        root = runtime.store.get_snapshot(binding.snapshot_id).root_path
        return await _source_call(ctx, "grep", grep_tool, {"pattern": re.escape(query), "path": root, "include": path_glob})
    except STORE_ERRORS as exc:
        return _error(exc, title="Source fixture failed")
