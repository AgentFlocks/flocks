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


PAGE_PARAM_NAMES = {"page", "pageNo", "pageNum", "current", "pageIndex", "curPage", "cur_page"}
LIMIT_PARAM_NAMES = {"limit", "size", "pageSize", "page_size", "page_limit", "rows"}
TIME_PARAM_NAMES = {
    "time_from", "time_to", "start_time", "end_time",
    "create_time_from", "create_time_to", "update_time_from", "update_time_to",
    "last_update_time_from", "last_update_time_to", "begin_time", "endtime",
    "timeRange", "startTime", "endTime",
}
MIN_OPERATION_SCORE = 40


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


def get_request_content_type(request: dict[str, Any]) -> str:
    """Return the normalized request content type."""
    direct = request.get("requestContentType")
    if direct:
        return str(direct).lower()

    headers = request.get("requestHeaders", {}) or request.get("request_headers", {})
    for key, value in headers.items():
        if str(key).lower() == "content-type" and value:
            return str(value).lower()
    return ""


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
    purpose = request.get("apiPurpose", {}) if isinstance(request.get("apiPurpose"), dict) else {}
    if purpose.get("name") or purpose.get("desc"):
        score += 15
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


def build_templates(
    request: dict[str, Any], url_info: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], str, str, list[str]]:
    """Build query/body templates, payload mode, and CLI arg definitions."""
    args: list[dict[str, Any]] = []
    seen_args: set[str] = set()
    multipart_file_fields: list[str] = []

    def add_arg(name: str, default: Any, help_text: str) -> None:
        if name in seen_args:
            return
        seen_args.add(name)
        arg_type = "int" if isinstance(default, int) else "string"
        args.append({"name": name, "type": arg_type, "default": default, "help": help_text})

    def _is_special_param_key(key: str) -> bool:
        return key in PAGE_PARAM_NAMES or key in LIMIT_PARAM_NAMES or key in TIME_PARAM_NAMES

    def _template_scalar(key: str, value: Any) -> tuple[str, Any] | None:
        if key in PAGE_PARAM_NAMES:
            default = int(value) if str(value).isdigit() else 1
            add_arg("page", default, "Page number")
            return "${page}", default
        if key in LIMIT_PARAM_NAMES:
            default = int(value) if str(value).isdigit() else 20
            add_arg("limit", default, "Page size")
            return "${limit}", default
        if key in TIME_PARAM_NAMES:
            default = int(value) if str(value).isdigit() else value
            add_arg(key, default, "Time parameter (Unix timestamp)")
            return "${" + key + "}", default
        return None

    def _multipart_file_template(path_tokens: tuple[str, ...], value: Any) -> str | None:
        if payload_mode != "multipart" or value != "[file]":
            return None
        field_name = ".".join(path_tokens)
        arg_name = sanitize_name("_".join(path_tokens) + "_file")
        add_arg(arg_name, "", f"File path for multipart field {field_name}")
        if field_name not in multipart_file_fields:
            multipart_file_fields.append(field_name)
        return "${" + arg_name + "}"

    def transform_mapping(data: dict[str, Any], path_tokens: tuple[str, ...] = ()) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in data.items():
            field_path = path_tokens + (str(key),)
            multipart_template = _multipart_file_template(field_path, value)
            if multipart_template is not None:
                result[key] = multipart_template
                continue
            if _is_special_param_key(key):
                if isinstance(value, dict):
                    result[key] = transform_mapping(value, field_path)
                elif isinstance(value, list):
                    result[key] = [
                        transform_mapping(item, field_path) if isinstance(item, dict) else item
                        for item in value
                    ]
                else:
                    template_result = _template_scalar(key, value)
                    result[key] = template_result[0] if template_result else value
            elif isinstance(value, dict):
                result[key] = transform_mapping(value, field_path)
            elif isinstance(value, list):
                result[key] = [
                    transform_mapping(item, field_path) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                result[key] = value
        return result

    body_text = str(request.get("requestBody", ""))
    body_kind = str(request.get("requestBodyKind", "")).lower()
    content_type = get_request_content_type(request)
    parsed_body = parse_json_text(body_text)
    body: dict[str, Any] = {}
    payload_mode = "none"
    raw_body_template = ""

    if isinstance(parsed_body, dict) and "raw" not in parsed_body:
        body = parsed_body
        if body:
            if body_kind == "formdata" or "multipart/form-data" in content_type:
                payload_mode = "multipart"
            elif body_kind == "urlencoded":
                payload_mode = "form"
            elif "application/x-www-form-urlencoded" in content_type:
                payload_mode = "form"
            else:
                payload_mode = "json"
    elif body_text:
        if "application/x-www-form-urlencoded" in content_type:
            body = dict(parse_qsl(body_text, keep_blank_values=True))
            payload_mode = "form" if body else "raw"
        else:
            payload_mode = "raw"
            raw_body_template = body_text

    query_template = transform_mapping(url_info["query"])
    body_template = transform_mapping(body)

    args.sort(key=lambda item: (
        0 if item["name"] == "page" else
        1 if item["name"] == "limit" else
        2 if item["name"] in TIME_PARAM_NAMES else 3,
        item["name"],
    ))
    return query_template, body_template, args, payload_mode, raw_body_template, multipart_file_fields


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


def safe_headers(request: dict[str, Any], payload_mode: str = "") -> dict[str, Any]:
    """Return non-sensitive request headers that can be replayed safely."""
    headers = request.get("requestHeaders", {}) or request.get("request_headers", {})
    result = {}
    for key, value in headers.items():
        key_name = str(key).lower()
        if key_name in {"cookie", "authorization", "x-csrf-token", "x-xsrf-token", "x-auth-token"}:
            continue
        if payload_mode == "multipart" and key_name == "content-type":
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


def cli_command_name(name: str) -> str:
    """Return a CLI subcommand name from captured metadata."""
    return sanitize_name(name).replace("_", "-") or "command"


def select_operation_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one representative request per method/path pair in capture order."""
    selected: dict[tuple[str, str], dict[str, Any]] = {}

    for index, request in enumerate(requests):
        url_info = normalize_url_info(request)
        pathname = url_info["pathname"] or "/"
        method = str(request.get("method", "GET")).upper()
        key = (method, pathname)
        score, _ = score_request(request, index)
        current = selected.get(key)
        if current is None or score > current["score"]:
            selected[key] = {"index": index, "score": score, "request": request}

    candidates = sorted(selected.values(), key=lambda item: item["index"])
    filtered = [item for item in candidates if item["score"] >= MIN_OPERATION_SCORE]
    return [item["request"] for item in (filtered or candidates[:1])]


def make_unique_commands(entries: list[dict[str, Any]]) -> None:
    """Mutate operation entries so every command is unique."""
    counts: dict[str, int] = {}
    for entry in entries:
        command = entry["command"]
        counts[command] = counts.get(command, 0) + 1
        if counts[command] > 1:
            entry["command"] = f"{command}-{counts[command]}"


def build_operation_entry(request: dict[str, Any]) -> dict[str, Any]:
    """Build one multi-operation spec entry from a captured request."""
    url_info = normalize_url_info(request)
    response = parse_json_text(str(request.get("response", "")))
    collection = find_best_collection(response)
    row_item = collection["item"] if collection is not None else response
    query_template, body_template, args, payload_mode, raw_body_template, multipart_file_fields = build_templates(
        request,
        url_info,
    )
    columns = collect_columns(row_item)

    defaults = {item["name"]: item["default"] for item in args}
    verify_types = {column["name"]: column["type"] for column in columns}
    verify_not_empty = [column["name"] for column in columns[: min(3, len(columns))]]
    row_count = {"min": 1}
    if collection and collection["length"]:
        row_count["max"] = collection["length"]

    purpose = request.get("apiPurpose", {}) if isinstance(request.get("apiPurpose"), dict) else {}
    pathname = url_info["pathname"] or "/"
    command = purpose.get("name") or command_name_from_path(pathname)

    return {
        "command": cli_command_name(command),
        "description": purpose.get("desc") or f"Generated from {request.get('method', 'GET')} {pathname}",
        "operation": {
            "method": request.get("method", "GET"),
            "endpoint": pathname,
            "queryTemplate": query_template,
            "bodyTemplate": body_template,
            "payloadMode": payload_mode,
            "rawBodyTemplate": raw_body_template,
            "multipartFileFields": multipart_file_fields,
            "headers": safe_headers(request, payload_mode),
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


def generate_spec_from_requests(requests: list[dict[str, Any]], *, base_url: str | None = None) -> dict[str, Any]:
    """Build a web2cli spec object from captured request data."""
    request = choose_primary_request(requests)
    url_info = normalize_url_info(request)
    strategy, auth = build_strategy(request)
    primary_entry = build_operation_entry(request)
    host_origin = base_url or url_info["origin"] or "https://example.com"
    site = site_name_from_host(urlparse(host_origin).netloc or url_info["host"])

    operation_entries = [build_operation_entry(item) for item in select_operation_requests(requests)]
    make_unique_commands(operation_entries)

    spec = {
        "schemaVersion": "1.0",
        "site": site,
        "command": sanitize_name(primary_entry["command"].replace("-", "_")),
        "description": primary_entry["description"],
        "baseUrl": host_origin,
        "strategy": strategy,
        "auth": auth,
        "operation": primary_entry["operation"],
        "rowSource": primary_entry["rowSource"],
        "args": primary_entry["args"],
        "columns": primary_entry["columns"],
        "verify": primary_entry["verify"],
    }
    if len(operation_entries) > 1:
        spec["operations"] = operation_entries
    return spec


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
