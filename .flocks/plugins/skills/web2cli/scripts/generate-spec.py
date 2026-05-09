#!/usr/bin/env python3
"""Generate a web2cli spec from captured API requests."""

from __future__ import annotations

import argparse
import json
import keyword
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse


PAGE_PARAM_NAMES = {"page", "pageNo", "pageNum", "current", "pageIndex", "curPage"}
LIMIT_PARAM_NAMES = {"limit", "size", "pageSize", "page_size", "page_limit", "rows"}


def sanitize_name(name: str) -> str:
    """Convert text to a valid Python/CLI-friendly identifier."""
    value = re.sub(r"\?.*$", "", name)
    value = re.sub(r"[^a-zA-Z0-9_]", "_", value)
    value = re.sub(r"_+", "_", value)
    value = value.strip("_")
    if value and value[0].isdigit():
        value = f"_{value}"
    value = value.lower() or "endpoint"
    if keyword.iskeyword(value):
        value = f"{value}_"
    return value


def load_requests(input_path: str) -> list[dict[str, Any]]:
    """Load captured request list from disk."""
    with open(input_path, encoding="utf-8") as f:
        payload = json.load(f)

    requests = payload if isinstance(payload, list) else payload.get("requests", [])
    return [item for item in requests if isinstance(item, dict)]


def parse_json_text(text: str) -> Any:
    """Parse a response/request body string when possible."""
    if not text:
        return {}

    value = text.strip()
    if value.endswith("...[truncated]"):
        value = value[: -len("...[truncated]")]

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"raw": text}


def infer_type(value: Any) -> str:
    """Return a compact type name for spec/verify output."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def normalize_url_info(request: dict[str, Any]) -> dict[str, Any]:
    """Return normalized URL parts from capture metadata or raw URL."""
    url = (
        request.get("normalizedUrl")
        or request.get("url")
        or ""
    )
    parsed = urlparse(url)
    query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return {
        "url": url,
        "origin": request.get("origin") or (f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""),
        "pathname": request.get("pathname") or (parsed.path or "/"),
        "query": request.get("query") or query_items,
        "queryKeys": request.get("queryKeys") or list(query_items.keys()),
        "host": parsed.netloc,
    }


def score_request(request: dict[str, Any], index: int) -> tuple[int, int]:
    """Score a captured request to decide which one should become the spec."""
    score = 0
    response = parse_json_text(str(request.get("response", "")))
    action = ((request.get("actionContext") or {}).get("lastAction") or {}).get("action")

    status = request.get("status")
    if isinstance(status, int) and 200 <= status < 300:
        score += 30
    elif status == "error":
        score -= 20

    if request.get("captureReason") in {"nonGet", "captureModeAll", "includePattern"}:
        score += 15
    if action:
        score += 12
    if isinstance(response, dict) and "raw" not in response:
        score += 20

    collection = find_best_collection(response)
    if collection is not None:
        score += 20 + min(collection["length"], 20)

    return score, index


def choose_primary_request(requests: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the best request candidate from the captured request list."""
    if not requests:
        raise ValueError("No captured requests available")
    ranked = sorted(
        ((score_request(req, index), req) for index, req in enumerate(requests)),
        key=lambda item: (item[0][0], item[0][1]),
        reverse=True,
    )
    return ranked[0][1]


def find_collections(value: Any, path: str = "$") -> list[dict[str, Any]]:
    """Find likely row collections inside a JSON response."""
    results: list[dict[str, Any]] = []

    if isinstance(value, list):
        item = value[0] if value else None
        score = 10
        if isinstance(item, dict):
            score += 25
        elif item is not None:
            score += 10
        results.append(
            {
                "collectionPath": path + "[]",
                "path": path,
                "length": len(value),
                "item": item,
                "score": score,
            }
        )
        if isinstance(item, dict):
            for key, child in item.items():
                results.extend(find_collections(child, path + "[]." + key))
        return results

    if isinstance(value, dict):
        for key, child in value.items():
            next_path = path + "." + key if path != "$" else "$." + key
            results.extend(find_collections(child, next_path))

    return results


def find_best_collection(value: Any) -> dict[str, Any] | None:
    """Return the highest scoring collection candidate from the response."""
    candidates = find_collections(value)
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            item["score"],
            item["length"],
            -len(item["path"]),
        ),
        reverse=True,
    )
    return candidates[0]


def collect_columns(item: Any) -> list[dict[str, Any]]:
    """Infer a compact column list from a sample row."""
    columns: list[dict[str, Any]] = []

    if isinstance(item, dict):
        for key, value in item.items():
            if isinstance(value, (dict, list)):
                if isinstance(value, dict):
                    for nested_key, nested_value in value.items():
                        if isinstance(nested_value, (dict, list)):
                            continue
                        columns.append(
                            {
                                "name": sanitize_name(f"{key}_{nested_key}"),
                                "path": "$." + key + "." + nested_key,
                                "relativePath": key + "." + nested_key,
                                "sourceField": nested_key,
                                "type": infer_type(nested_value),
                            }
                        )
                continue
            columns.append(
                {
                    "name": sanitize_name(key),
                    "path": "$." + key,
                    "relativePath": key,
                    "sourceField": key,
                    "type": infer_type(value),
                }
            )
            if len(columns) >= 8:
                break
    elif item is not None:
        columns.append(
            {
                "name": "value",
                "path": "$",
                "relativePath": "$",
                "sourceField": "value",
                "type": infer_type(item),
            }
        )

    if not columns:
        columns.append(
            {
                "name": "value",
                "path": "$",
                "relativePath": "$",
                "sourceField": "value",
                "type": "string",
            }
        )
    return columns


def build_templates(request: dict[str, Any], url_info: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Build query/body templates and CLI arg definitions."""
    args: list[dict[str, Any]] = []
    seen_args: set[str] = set()

    def add_arg(name: str, default: Any, help_text: str) -> None:
        if name in seen_args:
            return
        seen_args.add(name)
        arg_type = "int" if isinstance(default, int) else "string"
        args.append({"name": name, "type": arg_type, "default": default, "help": help_text})

    def transform_mapping(data: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in data.items():
            if key in PAGE_PARAM_NAMES:
                default = int(value) if str(value).isdigit() else 1
                result[key] = "${page}"
                add_arg("page", default, "Page number")
            elif key in LIMIT_PARAM_NAMES:
                default = int(value) if str(value).isdigit() else 20
                result[key] = "${limit}"
                add_arg("limit", default, "Page size")
            else:
                result[key] = value
        return result

    body = parse_json_text(str(request.get("requestBody", "")))
    if not isinstance(body, dict) or "raw" in body:
        body = {}

    query_template = transform_mapping(url_info["query"])
    body_template = transform_mapping(body)

    args.sort(key=lambda item: (0 if item["name"] == "page" else 1 if item["name"] == "limit" else 2, item["name"]))
    return query_template, body_template, args


def build_strategy(request: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Infer auth strategy and auth metadata from request headers."""
    headers = request.get("requestHeaders", {}) or request.get("request_headers", {})
    normalized = {str(key).lower(): value for key, value in headers.items()}
    strategy = "PUBLIC"
    required_headers: list[dict[str, Any]] = []

    if "authorization" in normalized:
        strategy = "HEADER"
        required_headers.append({"name": "Authorization", "source": "manual", "key": "authorization"})
    elif "cookie" in normalized:
        strategy = "COOKIE"

    for header_name in ("x-csrf-token", "x-xsrf-token", "x-auth-token"):
        if header_name in normalized:
            strategy = "HEADER"
            required_headers.append({"name": header_name, "source": "manual", "key": header_name})

    return strategy, {"stateFile": "auth-state.json", "requiredCookies": [], "requiredHeaders": required_headers}


def safe_headers(request: dict[str, Any]) -> dict[str, Any]:
    """Return non-sensitive request headers that can be replayed safely."""
    headers = request.get("requestHeaders", {}) or request.get("request_headers", {})
    result = {}
    for key, value in headers.items():
        if str(key).lower() in {"cookie", "authorization", "x-csrf-token", "x-xsrf-token", "x-auth-token"}:
            continue
        result[key] = value
    return result


def site_name_from_host(host: str) -> str:
    """Return a readable site name from a host."""
    cleaned = host.split(":")[0]
    parts = [part for part in cleaned.split(".") if part not in {"www", "api", "m"}]
    if len(parts) >= 2:
        return sanitize_name(parts[-2])
    if parts:
        return sanitize_name(parts[0])
    return "captured_site"


def command_name_from_path(pathname: str) -> str:
    """Return a command name from an API pathname."""
    parts = [part for part in pathname.split("/") if part]
    return sanitize_name(parts[-1] if parts else "command")


def generate_spec_from_requests(requests: list[dict[str, Any]], *, base_url: str | None = None) -> dict[str, Any]:
    """Build a web2cli spec object from captured request data."""
    request = choose_primary_request(requests)
    url_info = normalize_url_info(request)
    response = parse_json_text(str(request.get("response", "")))
    collection = find_best_collection(response)
    row_item = collection["item"] if collection is not None else response
    query_template, body_template, args = build_templates(request, url_info)
    strategy, auth = build_strategy(request)
    columns = collect_columns(row_item)

    defaults = {item["name"]: item["default"] for item in args}
    verify_types = {column["name"]: column["type"] for column in columns}
    verify_not_empty = [column["name"] for column in columns[: min(3, len(columns))]]
    row_count = {"min": 1}
    if collection and collection["length"]:
        row_count["max"] = collection["length"]

    purpose = request.get("apiPurpose", {}) if isinstance(request.get("apiPurpose"), dict) else {}
    host_origin = base_url or url_info["origin"] or "https://example.com"
    pathname = url_info["pathname"] or "/"
    site = site_name_from_host(urlparse(host_origin).netloc or url_info["host"])
    command = purpose.get("name") or command_name_from_path(pathname)

    return {
        "schemaVersion": "1.0",
        "site": site,
        "command": sanitize_name(command),
        "description": purpose.get("desc") or f"Generated from {request.get('method', 'GET')} {pathname}",
        "baseUrl": host_origin,
        "strategy": strategy,
        "auth": auth,
        "operation": {
            "method": request.get("method", "GET"),
            "endpoint": pathname,
            "queryTemplate": query_template,
            "bodyTemplate": body_template,
            "headers": safe_headers(request),
            "captureSource": request.get("captureSource", "pageHook"),
            "captureReason": request.get("captureReason", ""),
            "sourceRequestId": request.get("timestamp", ""),
        },
        "rowSource": {
            "path": collection["collectionPath"] if collection else "$",
            "collectionPath": collection["collectionPath"] if collection else "$",
        },
        "args": args,
        "columns": columns,
        "verify": {
            "args": defaults,
            "rowCount": row_count,
            "columns": [column["name"] for column in columns],
            "types": verify_types,
            "notEmpty": verify_not_empty,
            "patterns": {},
        },
    }


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Generate a web2cli spec from captured APIs")
    parser.add_argument("input", help="Input JSON file with captured requests")
    parser.add_argument("--output", "-o", help="Output spec path")
    parser.add_argument("--base-url", help="Optional base URL override")
    args = parser.parse_args()

    requests = load_requests(args.input)
    if not requests:
        print("No requests found in input file", file=sys.stderr)
        sys.exit(1)

    spec = generate_spec_from_requests(requests, base_url=args.base_url)
    rendered = json.dumps(spec, indent=2, ensure_ascii=False)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(rendered, encoding="utf-8")
        print(f"Written to {output_path}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
