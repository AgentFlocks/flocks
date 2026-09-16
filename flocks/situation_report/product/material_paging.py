"""Lossless, size-bounded views of already verified material snapshots."""

from __future__ import annotations

import json
from typing import Any


def _fits(page: dict[str, Any]) -> bool:
    # Match the tool registry's serialization, leaving headroom below its hard limits.
    serialized = json.dumps(page, ensure_ascii=False, indent=2)
    return len(serialized) <= 60_000 and len(serialized.encode("utf-8")) <= 80_000 and serialized.count("\n") < 800


def bounded_material_page(
    rows: list[dict[str, Any]],
    *,
    offset: int,
    limit: int,
    content_offset: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    page = {
        **metadata,
        "offset": offset,
        "limit": limit,
        "total": len(rows),
        "hasMore": offset < len(rows),
        "nextOffset": offset,
        "nextContentOffset": 0,
        "materials": [],
    }
    selected: list[dict[str, Any]] = page["materials"]
    if content_offset and offset >= len(rows):
        raise ValueError("content_offset requires an existing material offset")
    for index in range(offset, min(offset + limit, len(rows))):
        row = rows[index]
        if not content_offset:
            selected.append(row)
            page["nextOffset"] = index + 1
            page["hasMore"] = index + 1 < len(rows)
            if _fits(page):
                continue
            selected.pop()
            page["nextOffset"] = index
            page["hasMore"] = True
            if selected:
                break
        # A single large record must not be silently truncated or fetched through a different API.
        text = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if content_offset >= len(text):
            raise ValueError("content_offset is outside this material; use the returned cursor")
        page["continuation"] = (
            "materialFragment.text is a slice of ONE material JSON, not a complete object. "
            "Read every slice, then continue with situation_product_material_read using "
            "offset=nextOffset and content_offset=nextContentOffset from the TOP LEVEL. "
            "Keep generation_id unchanged. Do not skip fragments, infer omitted facts, "
            "restart completed pages, or use filesystem/source-detail tools for continuation."
        )
        end = min(content_offset + 24_000, len(text))
        while True:
            more = end < len(text)
            page["materialFragment"] = {
                "material_id": row["material_id"],
                "text": text[content_offset:end],
                "offset": content_offset,
                "nextOffset": end,
                "totalCharacters": len(text),
                "hasMore": more,
            }
            page["nextOffset"] = index if more else index + 1
            page["nextContentOffset"] = end if more else 0
            page["hasMore"] = page["nextOffset"] < len(rows)
            if _fits(page):
                break
            if end - content_offset <= 1:
                raise ValueError("Material identity or page metadata exceeds the response budget")
            end = content_offset + (end - content_offset) // 2
        break
    page["hasMore"] = page["nextOffset"] < len(rows)
    return page
