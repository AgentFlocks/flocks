#!/usr/bin/env python3
"""
Generate CLI/Documentation from captured API requests (v3.0 format)

Usage:
    python generate-cli.py captured_api.json --output mycli.py --base-url https://api.example.com
    python generate-cli.py captured_api.json --docs --output api_docs.md
"""

import json
import argparse
import keyword
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from typing import List, Dict, Any, Optional


def sanitize_name(name: str) -> str:
    """Convert URL/path to valid Python identifier"""
    name = re.sub(r'\?.*$', '', name)
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip('_')
    if name and name[0].isdigit():
        name = '_' + name
    name = name.lower() or 'endpoint'
    if keyword.iskeyword(name):
        name = f'{name}_'
    return name


def sanitize_python_output_path(output_path: str) -> str:
    """Return a Python output path whose filename is importable as a module."""
    path = Path(output_path)
    suffix = path.suffix or '.py'
    stem = path.stem if path.suffix else path.name
    safe_stem = sanitize_name(stem)
    return str(path.with_name(f'{safe_stem}{suffix}'))


def parse_endpoint(url: str) -> Dict[str, Any]:
    """Parse URL into components"""
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split('/') if p]

    return {
        'url': url,
        'scheme': parsed.scheme,
        'netloc': parsed.netloc,
        'path': parsed.path,
        'path_parts': path_parts,
        'query': parse_qs(parsed.query),
        'resource': path_parts[-1] if path_parts else 'root'
    }


def normalize_endpoint(url: str) -> str:
    """Return a request URL as an API endpoint path."""
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return parsed.path or '/'
    return url.split('?')[0] or '/'


def group_endpoints(requests: List[Dict]) -> Dict[str, List[Dict]]:
    """Group endpoints by unique URL (deduped)"""
    groups = {}

    for req in requests:
        url = req.get('url', '')
        if not url:
            continue

        path = normalize_endpoint(url)
        if path not in groups:
            groups[path] = []
        groups[path].append(req)

    return groups


def extract_common_headers(requests: List[Dict]) -> Dict[str, str]:
    """Extract common headers from requests"""
    all_headers = {}

    for req in requests:
        headers = req.get('requestHeaders', {}) or req.get('request_headers', {})
        for k, v in headers.items():
            if k.lower() not in ['cookie', 'authorization']:
                all_headers[k] = v

    return all_headers


def parse_request_body(body_str: str) -> Any:
    """Parse request body from string"""
    if not body_str or body_str == '{}':
        return {}

    try:
        # Handle JSON string
        return json.loads(body_str)
    except json.JSONDecodeError:
        # Return as raw if not JSON
        return {"raw": body_str}


def parse_response_body(resp_str: str) -> Any:
    """Parse response body, handle truncation"""
    if not resp_str:
        return {}

    if resp_str.endswith('[truncated]'):
        # Try to parse what's there
        # Find the last complete JSON object
        for i in range(len(resp_str) - 20, -1, -1):
            if resp_str[i] == '{' or resp_str[i] == '[':
                try:
                    return json.loads(resp_str[i:])
                except json.JSONDecodeError:
                    continue

    try:
        return json.loads(resp_str)
    except json.JSONDecodeError:
        return {"raw": resp_str[:500], "note": "not valid JSON"}


def generate_python_client(requests: List[Dict], base_url: str) -> str:
    """Generate Python client code"""
    groups = group_endpoints(requests)

    code = '''#!/usr/bin/env python3
"""
Auto-generated API Client
Generated from captured API requests

Usage:
    client = APIClient(cookie_file="auth-state.json")
    result = client.get_alarms()
"""

import json
import requests
from typing import Dict, Any, Optional, List
from urllib.parse import urljoin, urlparse


class APIClient:
    """Auto-generated API Client"""

    @staticmethod
    def _load_cookie_items(cookie_file: str) -> List[Dict[str, Any]]:
        """Load cookies from either a raw cookie list or a storageState object."""
        try:
            with open(cookie_file, encoding="utf-8") as f:
                payload = json.load(f)
        except FileNotFoundError:
            print(f"Warning: Cookie file {cookie_file} not found")
            return []
        except json.JSONDecodeError as error:
            print(f"Warning: Failed to parse cookie file {cookie_file}: {error}")
            return []

        if isinstance(payload, list):
            cookies = payload
        elif isinstance(payload, dict):
            cookies = payload.get("cookies", [])
        else:
            print(f"Warning: Unsupported cookie file format in {cookie_file}")
            return []

        return [cookie for cookie in cookies if isinstance(cookie, dict)]

    @staticmethod
    def _domain_match(host: str, cookie_domain: str) -> bool:
        pure_domain = str(cookie_domain or "").lstrip(".")
        return bool(pure_domain) and (host == pure_domain or host.endswith(f".{pure_domain}"))

    @staticmethod
    def _path_match(request_path: str, cookie_path: str) -> bool:
        normalized_cookie_path = str(cookie_path or "/")
        normalized_request_path = request_path or "/"
        if normalized_cookie_path == "/":
            return True
        prefix = normalized_cookie_path.rstrip("/") or "/"
        return normalized_request_path == prefix or normalized_request_path.startswith(prefix + "/")

    def _build_cookie_header(self, url: str) -> str:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        request_path = parsed.path or "/"
        is_https = parsed.scheme == "https"
        selected = {}

        for index, cookie in enumerate(self.cookie_items):
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value in (None, ""):
                continue
            domain = str(cookie.get("domain", ""))
            if domain and not self._domain_match(host, domain):
                continue
            cookie_path = str(cookie.get("path", "/") or "/")
            if not self._path_match(request_path, cookie_path):
                continue
            if cookie.get("secure") and not is_https:
                continue

            score = (len(cookie_path), len(domain.lstrip(".")), index)
            current = selected.get(name)
            if current is None or score > current[0]:
                selected[name] = (score, f"{name}={value}")

        return "; ".join(
            header for _, header in sorted(selected.values(), key=lambda item: item[0][2])
        )

    def __init__(self, base_url: str = __BASE_URL__, cookie_file: str = "auth-state.json"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.cookie_items = self._load_cookie_items(cookie_file)

        # Common headers
        self.session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json; charset=UTF-8",
        })

    def _request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict:
        url = f"{self.base_url}{endpoint}"
        headers = dict(self.session.headers)
        cookie_header = self._build_cookie_header(url)
        if cookie_header:
            headers["Cookie"] = cookie_header
        resp = self.session.request(method, url, json=data, headers=headers)
        resp.raise_for_status()
        return resp.json()


'''.replace("__BASE_URL__", json.dumps(base_url))

    for path, apis in groups.items():
        sample = apis[0]
        method = sample.get('method', 'POST').lower()
        func_name = sanitize_name(path.split('/')[-1])

        # Get purpose if available
        purpose = sample.get('apiPurpose', {})
        desc = purpose.get('desc', '') or 'API endpoint'
        name = purpose.get('name', func_name)

        # Parse request body schema
        req_body = parse_request_body(sample.get('requestBody', '{}'))

        code += f'''
    def {func_name}(self{", data" if req_body else ""}: Optional[Dict] = None) -> Dict:
        """{desc}

        Endpoint: {path}
        Page: {purpose.get("page", "N/A")}
        """
        return self._request("{method.upper()}", "{path}"{", data" if req_body else ""})
'''

    return code


def generate_markdown_docs(requests: List[Dict], title: str = "API Documentation") -> str:
    """Generate Markdown API documentation"""
    groups = group_endpoints(requests)

    md = f'''# {title}

> Auto-generated API Documentation
> Total endpoints: {len(groups)}
> Generated by web2cli tool

---

'''

    # Sort by path
    for path in sorted(groups.keys()):
        apis = groups[path]
        sample = apis[0]

        # Get purpose info
        purpose = sample.get('apiPurpose', {})
        api_name = purpose.get('name', sanitize_name(path.split('/')[-1]))
        api_desc = purpose.get('desc', 'Unknown API')
        page = purpose.get('page', 'N/A')

        # Request info
        method = sample.get('method', 'POST')
        req_body = parse_request_body(sample.get('requestBody', '{}'))

        # Response info
        resp = parse_response_body(sample.get('response', '{}'))

        # Headers (exclude sensitive)
        headers = sample.get('requestHeaders', {}) or sample.get('request_headers', {})
        safe_headers = {k: v for k, v in headers.items()
                       if k.lower() not in ['cookie', 'authorization', 'tdp-authentication']}

        md += f'''## {api_name}

**用途**: {api_desc}
**页面**: {page}

###基本信息

| 属性 | 值 |
|------|-----|
| Method | `{method}` |
| Endpoint | `{path}` |
| Status | {sample.get('status', 'N/A')} |
| Duration | {sample.get('duration', 'N/A')}ms |

'''

        if safe_headers:
            md += '''### 请求头

```http
'''
            for k, v in safe_headers.items():
                md += f'{k}: {v}\n'
            md += '```\n\n'

        if req_body:
            md += '''### 请求体

```json
'''
            md += json.dumps(req_body, indent=2, ensure_ascii=False) + '\n'
            md += '```\n\n'

        # Response preview
        if resp and resp != {}:
            # Show structure, not full data
            resp_preview = json.dumps(resp, indent=2, ensure_ascii=False)
            if len(resp_preview) > 1500:
                resp_preview = resp_preview[:1500] + '\n... // truncated'

            md += '''### 响应示例

```json
'''
            md += resp_preview + '\n'
            md += '```\n\n'

        # Add response code check
        if resp and 'response_code' in str(resp):
            md += '''### 响应码

| response_code | 含义 |
|---------------|------|
| 0 | 成功 |
| -1 | 失败/参数错误 |
| -2 | 未授权 |

'''

        md += '---\n\n'

    # Add usage section
    md += '''## 使用示例

### Python

```python
from generated_client import APIClient

client = APIClient(
    base_url="https://api.example.com",
    cookie_file="auth-state.json"
)

# 调用 API
result = client.tag_list()
print(result)
```

### cURL

```bash
# 示例
curl -X POST "https://api.example.com/api/web/tag/list" \
  -H "Content-Type: application/json" \
  -d '{"type":"asset"}' \
  -b "COOKIE_STRING"
```

'''

    return md


def generate_postman_collection(requests: List[Dict], base_url: str) -> Dict:
    """Generate Postman collection"""
    groups = group_endpoints(requests)

    collection = {
        "info": {
            "name": "Captured API Collection",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
        },
        "item": []
    }

    for path in sorted(groups.keys()):
        apis = groups[path]
        sample = apis[0]

        purpose = sample.get('apiPurpose', {})

        item = {
            "name": purpose.get('name', path),
            "request": {
                "method": sample.get('method', 'POST'),
                "url": {
                    "raw": f"{{{{base_url}}}}{path}",
                    "host": ["{{base_url}}"],
                    "path": path.lstrip('/').split('/')
                },
                "header": [
                    {"key": k, "value": v}
                    for k, v in (sample.get('requestHeaders') or {}).items()
                    if k.lower() not in ['cookie', 'authorization']
                ]
            }
        }

        # Add body if present
        req_body = parse_request_body(sample.get('requestBody', '{}'))
        if req_body:
            item["request"]["body"] = {
                "mode": "raw",
                "raw": json.dumps(req_body, ensure_ascii=False),
                "options": {"raw": {"language": "json"}}
            }

        collection["item"].append(item)

    return collection


def load_spec(spec_path: str) -> Dict[str, Any]:
    """Load a web2cli spec from disk."""
    with open(spec_path, encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("Spec file must contain a JSON object")
    return payload


def generate_verify_materials_from_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Generate verify metadata from a web2cli spec."""
    def build_verify(command: str, args: Dict[str, Any], columns: List[Dict[str, Any]], verify: Dict[str, Any]) -> Dict[str, Any]:
        column_names = [column.get("name") for column in columns if isinstance(column, dict) and column.get("name")]
        return {
            "command": command,
            "args": args,
            "expect": {
                "rowCount": verify.get("rowCount", {"min": 1}),
                "columns": verify.get("columns", column_names),
                "types": verify.get(
                    "types",
                    {
                        column.get("name"): column.get("type", "string")
                        for column in columns
                        if isinstance(column, dict) and column.get("name")
                    },
                ),
                "notEmpty": verify.get("notEmpty", column_names[: min(3, len(column_names))]),
                "patterns": verify.get("patterns", {}),
            },
        }

    entries = spec_operation_entries(spec)
    if len(entries) > 1:
        return {
            "site": spec.get("site", ""),
            "command": spec.get("command", ""),
            "operations": [
                build_verify(
                    entry["command"],
                    entry.get("verify", {}).get(
                        "args",
                        {
                            arg.get("name"): arg.get("default")
                            for arg in entry.get("args", [])
                            if isinstance(arg, dict) and arg.get("name")
                        },
                    ),
                    entry.get("columns", []),
                    entry.get("verify", {}) if isinstance(entry.get("verify"), dict) else {},
                )
                for entry in entries
            ],
        }

    verify = spec.get("verify", {}) if isinstance(spec.get("verify"), dict) else {}
    columns = spec.get("columns", [])
    column_names = [column.get("name") for column in columns if isinstance(column, dict) and column.get("name")]

    return {
        "site": spec.get("site", ""),
        "command": spec.get("command", ""),
        "args": verify.get("args", {}),
        "expect": {
            "rowCount": verify.get("rowCount", {"min": 1}),
            "columns": verify.get("columns", column_names),
            "types": verify.get(
                "types",
                {
                    column.get("name"): column.get("type", "string")
                    for column in columns
                    if isinstance(column, dict) and column.get("name")
                },
            ),
            "notEmpty": verify.get("notEmpty", column_names[: min(3, len(column_names))]),
            "patterns": verify.get("patterns", {}),
        },
    }


def cli_command_name(value: Any, fallback: str = "command") -> str:
    """Return an argparse-friendly subcommand name."""
    name = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or fallback))
    name = re.sub(r"[-_]+", "-", name).strip("-").lower()
    return name or fallback


def spec_operation_entries(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return normalized operation entries from either old or multi-operation specs."""
    raw_operations = spec.get("operations")
    entries: List[Dict[str, Any]] = []

    if isinstance(raw_operations, list):
        for index, item in enumerate(raw_operations, start=1):
            if not isinstance(item, dict):
                continue
            operation = item.get("operation") if isinstance(item.get("operation"), dict) else item
            fallback = f"{spec.get('command', 'command')}-{index}"
            command = cli_command_name(
                item.get("command") or item.get("name") or operation.get("command") or operation.get("name"),
                fallback,
            )
            entries.append(
                {
                    "command": command,
                    "description": item.get("description") or operation.get("description") or spec.get("description", ""),
                    "operation": operation,
                    "rowSource": item.get("rowSource", spec.get("rowSource", {})),
                    "args": item.get("args", spec.get("args", [])),
                    "columns": item.get("columns", spec.get("columns", [])),
                    "verify": item.get("verify", spec.get("verify", {})),
                }
            )

    if entries:
        return entries

    operation = spec.get("operation", {}) if isinstance(spec.get("operation"), dict) else {}
    return [
        {
            "command": cli_command_name(spec.get("command", "command")),
            "description": spec.get("description", ""),
            "operation": operation,
            "rowSource": spec.get("rowSource", {}),
            "args": spec.get("args", []),
            "columns": spec.get("columns", []),
            "verify": spec.get("verify", {}),
        }
    ]


def generate_markdown_docs_from_spec(spec: Dict[str, Any], title: str = "API Documentation") -> str:
    """Generate Markdown documentation from a web2cli spec."""
    entries = spec_operation_entries(spec)
    primary = entries[0]
    operation = primary["operation"]
    args = primary["args"]
    columns = primary["columns"]
    verify = primary.get("verify", {}) if isinstance(primary.get("verify"), dict) else {}
    verify_args = verify.get(
        "args",
        {arg.get("name"): arg.get("default") for arg in args if isinstance(arg, dict) and arg.get("name")},
    )
    verify_not_empty = verify.get(
        "notEmpty",
        [column.get("name") for column in columns[: min(3, len(columns))] if isinstance(column, dict)],
    )

    md = f"""# {title}

> Auto-generated Web2CLI Specification
> Site: `{spec.get("site", "")}`
> Command: `{spec.get("command", "")}`

## 概览

- **描述**: {spec.get("description", "N/A")}
- **策略**: `{spec.get("strategy", "PUBLIC")}`
- **Base URL**: `{spec.get("baseUrl", "")}`
- **Method**: `{operation.get("method", "GET")}`
- **Endpoint**: `{operation.get("endpoint", "/")}`
- **Payload Mode**: `{operation.get("payloadMode", "none")}`

"""

    if len(entries) > 1:
        md += "## 子命令\n\n"
        md += "| 子命令 | Method | Endpoint | 说明 |\n"
        md += "|--------|--------|----------|------|\n"
        for entry in entries:
            op = entry["operation"]
            md += (
                f"| `{entry['command']}` | `{op.get('method', 'GET')}` | "
                f"`{op.get('endpoint', '/')}` | {entry.get('description', '')} |\n"
            )
        md += "\n"

    md += """
## 参数

"""

    if args:
        md += "| 参数 | 类型 | 默认值 | 说明 |\n"
        md += "|------|------|--------|------|\n"
        for arg in args:
            md += f"| `{arg.get('name', '')}` | `{arg.get('type', 'string')}` | `{arg.get('default', '')}` | {arg.get('help', '')} |\n"
        md += "\n"
    else:
        md += "无参数。\n\n"

    md += "## 输出列\n\n"
    md += "| 列名 | 类型 | 路径 |\n"
    md += "|------|------|------|\n"
    for column in columns:
        md += f"| `{column.get('name', '')}` | `{column.get('type', 'string')}` | `{column.get('path', '')}` |\n"

    md += "\n## 验证建议\n\n"
    md += f"- 默认参数: `{json.dumps(verify_args, ensure_ascii=False)}`\n"
    md += f"- 最少行数: `{verify.get('rowCount', {}).get('min', 0)}`\n"
    md += f"- 必填列: `{', '.join(verify_not_empty)}`\n"

    return md


def generate_postman_collection_from_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a minimal Postman collection from a web2cli spec."""
    items = []
    for entry in spec_operation_entries(spec):
        operation = entry["operation"]
        headers = operation.get("headers", {}) if isinstance(operation.get("headers"), dict) else {}
        body_template = operation.get("bodyTemplate", {}) if isinstance(operation.get("bodyTemplate"), dict) else {}
        payload_mode = operation.get("payloadMode") or ("json" if body_template else "none")
        raw_body_template = operation.get("rawBodyTemplate", "")
        endpoint = operation.get("endpoint", "/")
        path_parts = endpoint.lstrip("/").split("/") if endpoint.lstrip("/") else []

        request = {
            "method": operation.get("method", "GET"),
            "url": {
                "raw": f"{{{{base_url}}}}{endpoint}",
                "host": ["{{base_url}}"],
                "path": path_parts,
            },
            "header": [{"key": key, "value": value} for key, value in headers.items()],
        }
        if payload_mode == "json" and body_template:
            request["body"] = {
                "mode": "raw",
                "raw": json.dumps(body_template, ensure_ascii=False),
                "options": {"raw": {"language": "json"}},
            }
        elif payload_mode == "form" and body_template:
            request["body"] = {
                "mode": "urlencoded",
                "urlencoded": [{"key": key, "value": str(value)} for key, value in body_template.items()],
            }
        elif payload_mode == "raw" and raw_body_template:
            request["body"] = {
                "mode": "raw",
                "raw": str(raw_body_template),
                "options": {"raw": {"language": "text"}},
            }
        items.append(
            {
                "name": entry["command"],
                "request": request,
            }
        )

    return {
        "info": {
            "name": f"{spec.get('site', 'captured')} {spec.get('command', 'command')}",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": items,
        "variable": [{"key": "base_url", "value": spec.get("baseUrl", "")}],
    }


def generate_python_cli_from_spec(spec: Dict[str, Any]) -> str:
    """Generate a fixed command CLI script from a web2cli spec."""
    spec_json = json.dumps(spec, indent=2, ensure_ascii=False)
    return '''#!/usr/bin/env python3
"""
Auto-generated Web2CLI command script.
Generated from web2cli-spec.json
"""

import argparse
import csv
import json
import re
import sys
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests


SPEC = ''' + spec_json + '''


def _load_json(path: str) -> Any:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    return payload


def _resolve_string_template(value: str, args: Dict[str, Any]) -> Any:
    exact_match = re.fullmatch(r"\\$\\{([A-Za-z0-9_]+)\\}", value)
    if exact_match:
        return args.get(exact_match.group(1), value)
    return re.sub(
        r"\\$\\{([A-Za-z0-9_]+)\\}",
        lambda match: str(args.get(match.group(1), match.group(0))),
        value,
    )


def _coerce_bool(value: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value}")


def _type_name(value: Any) -> str:
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


def _cli_command_name(value: Any, fallback: str = "command") -> str:
    name = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or fallback))
    name = re.sub(r"[-_]+", "-", name).strip("-").lower()
    return name or fallback


def _operation_entries() -> List[Dict[str, Any]]:
    raw_operations = SPEC.get("operations")
    entries: List[Dict[str, Any]] = []
    if isinstance(raw_operations, list):
        for index, item in enumerate(raw_operations, start=1):
            if not isinstance(item, dict):
                continue
            operation = item.get("operation") if isinstance(item.get("operation"), dict) else item
            fallback = f"{SPEC.get('command', 'command')}-{index}"
            command = _cli_command_name(
                item.get("command") or item.get("name") or operation.get("command") or operation.get("name"),
                fallback,
            )
            entries.append(
                {
                    "command": command,
                    "description": item.get("description") or operation.get("description") or SPEC.get("description", ""),
                    "operation": operation,
                    "rowSource": item.get("rowSource", SPEC.get("rowSource", {})),
                    "args": item.get("args", SPEC.get("args", [])),
                    "columns": item.get("columns", SPEC.get("columns", [])),
                    "verify": item.get("verify", SPEC.get("verify", {})),
                }
            )
    if entries:
        return entries
    operation = SPEC.get("operation", {}) if isinstance(SPEC.get("operation"), dict) else {}
    return [
        {
            "command": _cli_command_name(SPEC.get("command", "command")),
            "description": SPEC.get("description", ""),
            "operation": operation,
            "rowSource": SPEC.get("rowSource", {}),
            "args": SPEC.get("args", []),
            "columns": SPEC.get("columns", []),
            "verify": SPEC.get("verify", {}),
        }
    ]


def _uses_subcommands() -> bool:
    return isinstance(SPEC.get("operations"), list) and bool(SPEC.get("operations"))


def _operation_by_command(command: str | None) -> Dict[str, Any]:
    entries = _operation_entries()
    if command is None:
        return entries[0]
    for entry in entries:
        if entry["command"] == command:
            return entry
    raise SystemExit(f"unknown command: {command}")


class APIClient:
    """Fixed command client generated from a web2cli spec."""

    @staticmethod
    def _load_cookie_items(auth_state_path: str) -> List[Dict[str, Any]]:
        payload = _load_json(auth_state_path)
        if isinstance(payload, list):
            cookies = payload
        elif isinstance(payload, dict):
            cookies = payload.get("cookies", [])
        else:
            cookies = []
        if isinstance(cookies, list):
            return [cookie for cookie in cookies if isinstance(cookie, dict)]
        return []

    @staticmethod
    def _load_storage_map(payload: Dict[str, Any]) -> Dict[str, str]:
        if not isinstance(payload, dict):
            return {}
        values = {}
        for origin_entry in payload.get("origins", []):
            if not isinstance(origin_entry, dict):
                continue
            for item in origin_entry.get("localStorage", []):
                if isinstance(item, dict) and item.get("name"):
                    values[item["name"]] = item.get("value", "")
        return values

    @staticmethod
    def _domain_match(host: str, cookie_domain: str) -> bool:
        pure_domain = str(cookie_domain or "").lstrip(".")
        return bool(pure_domain) and (host == pure_domain or host.endswith(f".{pure_domain}"))

    @staticmethod
    def _path_match(request_path: str, cookie_path: str) -> bool:
        normalized_cookie_path = str(cookie_path or "/")
        normalized_request_path = request_path or "/"
        if normalized_cookie_path == "/":
            return True
        prefix = normalized_cookie_path.rstrip("/") or "/"
        return normalized_request_path == prefix or normalized_request_path.startswith(prefix + "/")

    def _select_cookie_header(self, url: str) -> str:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        request_path = parsed.path or "/"
        is_https = parsed.scheme == "https"
        selected = {}

        for index, cookie in enumerate(self._load_cookie_items(self.auth_state_path)):
            name = cookie.get("name")
            value = cookie.get("value")
            if not name or value in (None, ""):
                continue
            domain = str(cookie.get("domain", ""))
            if domain and not self._domain_match(host, domain):
                continue
            cookie_path = str(cookie.get("path", "/") or "/")
            if not self._path_match(request_path, cookie_path):
                continue
            if cookie.get("secure") and not is_https:
                continue

            score = (len(cookie_path), len(domain.lstrip(".")), index)
            current = selected.get(name)
            if current is None or score > current[0]:
                selected[name] = (score, f"{name}={value}")

        return "; ".join(
            header for _, header in sorted(selected.values(), key=lambda item: item[0][2])
        )

    def _resolve_cookie_value(self, key: Any) -> str | None:
        target_name = str(key or "")
        if not target_name:
            return None
        parsed = urlparse(self.base_url or "")
        host = parsed.hostname or ""
        request_path = parsed.path or "/"
        is_https = parsed.scheme == "https"
        selected = None
        for index, cookie in enumerate(self._load_cookie_items(self.auth_state_path)):
            if not isinstance(cookie, dict) or cookie.get("name") != target_name:
                continue
            domain = str(cookie.get("domain", ""))
            if domain and not self._domain_match(host, domain):
                continue
            cookie_path = str(cookie.get("path", "/") or "/")
            if not self._path_match(request_path, cookie_path):
                continue
            if cookie.get("secure") and not is_https:
                continue
            score = (len(cookie_path), len(domain.lstrip(".")), index)
            if selected is None or score > selected[0]:
                selected = (score, str(cookie.get("value", "")))
        return selected[1] if selected else None

    @staticmethod
    def _resolve_header_value(payload: Dict[str, Any], rule: Dict[str, Any]) -> str | None:
        source = rule.get("source")
        key = rule.get("key")
        if source == "localStorage":
            return APIClient._load_storage_map(payload).get(str(key))
        return None

    @staticmethod
    def _resolve_template(value: Any, args: Dict[str, Any]) -> Any:
        if isinstance(value, str):
            return _resolve_string_template(value, args)
        if isinstance(value, dict):
            return {key: APIClient._resolve_template(item, args) for key, item in value.items()}
        if isinstance(value, list):
            return [APIClient._resolve_template(item, args) for item in value]
        return value

    @staticmethod
    def _tokenize_path(path: str) -> List[str]:
        if not path or path == "$":
            return []
        normalized = path
        if normalized.startswith("$."):
            normalized = normalized[2:]
        elif normalized.startswith("$"):
            normalized = normalized[1:]
        normalized = normalized.replace("[]", ".[]")
        return [token for token in normalized.split(".") if token]

    @classmethod
    def _extract_many(cls, value: Any, path: str) -> List[Any]:
        tokens = cls._tokenize_path(path)
        current = [value]
        for token in tokens:
            next_values = []
            if token == "[]":
                for item in current:
                    if isinstance(item, list):
                        next_values.extend(item)
            else:
                for item in current:
                    if isinstance(item, dict) and token in item:
                        next_values.append(item[token])
            current = next_values
            if not current:
                break
        return current

    @classmethod
    def _extract_first(cls, value: Any, path: str) -> Any:
        if not path or path == "$":
            return value
        values = cls._extract_many(value, path)
        return values[0] if values else None

    def __init__(self, base_url: str = SPEC.get("baseUrl", ""), auth_state: str = "auth-state.json"):
        self.base_url = (base_url or SPEC.get("baseUrl", "")).rstrip("/")
        self.auth_state_path = auth_state
        self.auth_state = _load_json(auth_state) if auth_state else {}
        self.session = requests.Session()
        self._apply_auth_state()

    def _apply_auth_state(self) -> None:
        strategy = SPEC.get("strategy", "PUBLIC")
        auth = SPEC.get("auth", {})
        headers = SPEC.get("headers", {})
        if isinstance(headers, dict) and headers:
            self.session.headers.update(headers)

        if strategy == "HEADER":
            for rule in auth.get("requiredHeaders", []):
                if not isinstance(rule, dict) or not rule.get("name"):
                    continue
                source = rule.get("source")
                if source == "cookie":
                    value = self._resolve_cookie_value(rule.get("key"))
                else:
                    value = self._resolve_header_value(self.auth_state, rule)
                if value:
                    self.session.headers[str(rule["name"])] = value

    def build_request(self, args: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
        operation = entry.get("operation", {})
        endpoint = operation.get("endpoint", "/")
        query = self._resolve_template(operation.get("queryTemplate", {}), args)
        body = self._resolve_template(operation.get("bodyTemplate", {}), args)
        payload_mode = str(operation.get("payloadMode") or ("json" if body else "none")).lower()
        raw_body = self._resolve_template(operation.get("rawBodyTemplate", ""), args)
        headers = dict(operation.get("headers", {}) or {})
        cookie_strategy = str(SPEC.get("strategy", "PUBLIC") or "PUBLIC").upper()
        request_options = {
            "method": operation.get("method", "GET"),
            "url": f"{self.base_url}{endpoint}",
            "params": query or None,
            "json": None,
            "data": None,
            "headers": headers or None,
        }
        if payload_mode == "json":
            request_options["json"] = body or None
        elif payload_mode == "form":
            request_options["data"] = body or None
        elif payload_mode == "raw":
            request_options["data"] = raw_body or None
        if cookie_strategy in {"COOKIE", "HEADER"}:
            cookie_header = self._select_cookie_header(request_options["url"])
            if cookie_header:
                headers["Cookie"] = cookie_header
        return {
            "method": request_options["method"],
            "url": request_options["url"],
            "params": request_options["params"],
            "json": request_options["json"],
            "data": request_options["data"],
            "headers": request_options["headers"],
        }

    def _project_rows(self, payload: Any, entry: Dict[str, Any]) -> List[Dict[str, Any]]:
        row_source = entry.get("rowSource", {})
        collection_path = row_source.get("collectionPath") or row_source.get("path") or "$"
        collection = self._extract_many(payload, collection_path) if collection_path != "$" else [payload]
        if not collection:
            return []

        rows = []
        columns = entry.get("columns", [])
        for index, row in enumerate(collection, start=1):
            projected = {}
            for column in columns:
                if not isinstance(column, dict) or not column.get("name"):
                    continue
                rel_path = column.get("relativePath") or column.get("path") or "$"
                if rel_path == "__index__":
                    value = index
                elif rel_path.startswith("$."):
                    value = self._extract_first(payload, rel_path)
                else:
                    value = self._extract_first(row, rel_path)
                projected[column["name"]] = value
            rows.append(projected)
        return rows

    def run(self, args: Dict[str, Any], entry: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        operation_entry = entry or _operation_entries()[0]
        request_options = self.build_request(args, operation_entry)
        response = self.session.request(
            request_options["method"],
            request_options["url"],
            params=request_options["params"],
            json=request_options["json"],
            data=request_options["data"],
            headers=request_options["headers"],
        )
        response.raise_for_status()
        return self._project_rows(response.json(), operation_entry)


def verify_rows(rows: List[Dict[str, Any]], verify_spec: Dict[str, Any]) -> List[str]:
    errors = []
    expect = verify_spec.get("expect", verify_spec)
    row_count = expect.get("rowCount", {})
    min_rows = row_count.get("min")
    max_rows = row_count.get("max")

    if min_rows is not None and len(rows) < min_rows:
        errors.append(f"rowCount too small: expected >= {min_rows}, got {len(rows)}")
    if max_rows is not None and len(rows) > max_rows:
        errors.append(f"rowCount too large: expected <= {max_rows}, got {len(rows)}")

    columns = expect.get("columns", [])
    types = expect.get("types", {})
    not_empty = expect.get("notEmpty", [])
    patterns = expect.get("patterns", {})

    for row in rows:
        for column in columns:
            if column not in row:
                errors.append(f"missing column: {column}")
        for column in not_empty:
            if row.get(column) in (None, "", [], {}):
                errors.append(f"empty required column: {column}")
        for column, expected_type in types.items():
            if column in row and row[column] is not None and _type_name(row[column]) != expected_type:
                errors.append(
                    f"type mismatch for {column}: expected {expected_type}, got {_type_name(row[column])}"
                )
        for column, pattern in patterns.items():
            if column in row and row[column] is not None:
                import re
                if not re.search(pattern, str(row[column])):
                    errors.append(f"pattern mismatch for {column}: {pattern}")

    return errors


def _print_rows(rows: List[Dict[str, Any]], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        return
    columns = list(rows[0].keys())
    if output_format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        return
    print("\\t".join(columns))
    for row in rows:
        print("\\t".join("" if row.get(column) is None else str(row.get(column)) for column in columns))


def _add_output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=["json", "csv", "table"], default="json", help="Output format")
    parser.add_argument("--verify", action="store_true", help="Validate rows against embedded or external verify spec")
    parser.add_argument("--verify-spec", help="Optional verify JSON path")


def _add_operation_arguments(parser: argparse.ArgumentParser, entry: Dict[str, Any]) -> None:
    for arg in entry.get("args", []):
        if not isinstance(arg, dict) or not arg.get("name"):
            continue
        option = "--" + str(arg["name"]).replace("_", "-")
        arg_type = arg.get("type", "string")
        kwargs = {
            "dest": arg["name"],
            "default": arg.get("default"),
            "help": arg.get("help", ""),
        }
        if arg_type == "int":
            kwargs["type"] = int
        elif arg_type == "float":
            kwargs["type"] = float
        elif arg_type == "bool":
            kwargs["type"] = _coerce_bool
        else:
            kwargs["type"] = str
        parser.add_argument(option, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=SPEC.get("description", "Generated Web2CLI command"))
    parser.add_argument("--base-url", default=SPEC.get("baseUrl", ""), help="Override base URL")
    parser.add_argument(
        "--auth-state",
        default=(SPEC.get("auth", {}) or {}).get("stateFile", "auth-state.json"),
        help="Path to auth state JSON",
    )
    entries = _operation_entries()
    if _uses_subcommands():
        subparsers = parser.add_subparsers(dest="command", required=True)
        for entry in entries:
            subparser = subparsers.add_parser(
                entry["command"],
                description=entry.get("description") or entry["command"],
                help=entry.get("description") or entry["command"],
            )
            _add_output_arguments(subparser)
            _add_operation_arguments(subparser, entry)
    else:
        _add_output_arguments(parser)
        _add_operation_arguments(parser, entries[0])
    return parser


def main() -> None:
    parser = build_parser()
    parsed = parser.parse_args()
    entry = _operation_by_command(getattr(parsed, "command", None) if _uses_subcommands() else None)
    runtime_args = {
        item["name"]: getattr(parsed, item["name"])
        for item in entry.get("args", [])
        if isinstance(item, dict) and item.get("name")
    }
    client = APIClient(base_url=parsed.base_url, auth_state=parsed.auth_state)
    rows = client.run(runtime_args, entry)

    if parsed.verify:
        verify_spec = _load_json(parsed.verify_spec) if parsed.verify_spec else entry.get("verify", {})
        errors = verify_rows(rows, verify_spec)
        if errors:
            raise SystemExit("\\n".join(errors))

    _print_rows(rows, parsed.format)


if __name__ == "__main__":
    main()
'''


def main():
    parser = argparse.ArgumentParser(description='Generate CLI/docs from captured APIs or a web2cli spec')
    parser.add_argument('input', nargs='?', help='Input JSON file with captured requests')
    parser.add_argument('--spec', help='Input web2cli-spec.json file')
    parser.add_argument('--output', '-o', help='Output file')
    parser.add_argument('--base-url', '-u', default='https://example.com', help='Base URL')
    parser.add_argument('--format', '-f', choices=['python', 'markdown', 'postman', 'verify'],
                       default='markdown', help='Output format')
    parser.add_argument('--title', '-t', default='API Documentation', help='Document title')

    args = parser.parse_args()

    if not args.input and not args.spec:
        parser.error('either input or --spec is required')

    if args.spec:
        spec = load_spec(args.spec)
        if args.format == 'python':
            output = generate_python_cli_from_spec(spec)
        elif args.format == 'verify':
            output = json.dumps(generate_verify_materials_from_spec(spec), indent=2, ensure_ascii=False)
        elif args.format == 'postman':
            output = json.dumps(generate_postman_collection_from_spec(spec), indent=2, ensure_ascii=False)
        else:
            output = generate_markdown_docs_from_spec(spec, args.title)
    else:
        # Load input
        with open(args.input, encoding='utf-8') as f:
            data = json.load(f)

        # Handle both array and object formats
        requests = data if isinstance(data, list) else data.get('requests', [])

        if not requests:
            print("No requests found in input file", file=sys.stderr)
            sys.exit(1)

        print(f"Processing {len(requests)} requests, {len(group_endpoints(requests))} unique endpoints...")

        # Generate output
        if args.format == 'python':
            output = generate_python_client(requests, args.base_url)
        elif args.format == 'postman':
            output = json.dumps(generate_postman_collection(requests, args.base_url), indent=2, ensure_ascii=False)
        elif args.format == 'verify':
            print("verify output requires --spec", file=sys.stderr)
            sys.exit(1)
        else:
            output = generate_markdown_docs(requests, args.title)

    # Write output
    output_path = args.output
    if output_path and args.format == 'python':
        safe_output_path = sanitize_python_output_path(output_path)
        if safe_output_path != output_path:
            print(f"Python output filename normalized: {output_path} -> {safe_output_path}", file=sys.stderr)
        output_path = safe_output_path

    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f"Written to {output_path}")
    else:
        print(output)


if __name__ == '__main__':
    main()
