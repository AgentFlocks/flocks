---
name: tool-builder
category: system
description: "Creates reusable Flocks tools and API integrations. It supports YAML-HTTP for REST APIs and Python for local utilities, with mandatory verification and smoke testing. All output goes under ~/.flocks/plugins/tools/. Use it when creating or adding a new Flocks tool, building local utilities such as base64 encode/decode, URL encode/decode, JSON formatting, parsing, hashing, text or file transformation, or integrating an external REST API as a reusable tool."
description_cn: "用于创建可复用的 Flocks 工具和 API 集成。支持用 YAML-HTTP 封装 REST API，也支持用 Python 构建本地实用工具，并且要求必须完成校验和冒烟测试。所有产物都输出到 ~/.flocks/plugins/tools/。适用于创建或新增 Flocks 工具、构建 base64 编解码、URL 编解码、JSON 格式化/解析、哈希、文本或文件转换等本地工具，或将外部 REST API 集成为可复用工具。"
---

# Tool Builder

## Quick Start

When the user asks to create a new tool or integrate an API:
1. Choose the mode
2. Follow the workflow
3. **Run the mandatory verification protocol** (every mode)
4. **Enable the tool immediately after creation** so it is available right away without asking the user to enable it manually

## Activation Requirement

Every tool created with this skill must be usable immediately after the task finishes.

- **YAML tools**: always set `enabled: true`
- **Python tools**: place the file in the correct plugin directory so the file watcher can auto-load it immediately
- Never leave a newly created tool disabled unless the user explicitly requests that behavior
- Do not stop after writing files; finish only when the tool is created, enabled, and ready for use

## CRITICAL: Output Location

**ALL generated artifacts MUST be placed under `~/.flocks/plugins/tools/`.**

```
~/.flocks/plugins/tools/
├── api/                          # YAML HTTP/Script tools
│   ├── standalone_tool.yaml
│   └── threatbook/               # Provider group
│       ├── _provider.yaml
│       ├── ip_query.yaml
│       └── ip_query.handler.py   # Script handler (if needed)
├── python/                       # Python code tools
│   └── my_tool.py
└── generated/                    # Auto-generated tools (hot-reloadable)
    └── virustotal.py
```

| Mode | Output Path |
|------|------------|
| YAML-HTTP tool | `~/.flocks/plugins/tools/api/{name}.yaml` |
| YAML-HTTP tool (Provider) | `~/.flocks/plugins/tools/api/{provider}/{name}.yaml` |
| YAML script handler | `~/.flocks/plugins/tools/api/{provider}/{name}.handler.py` |
| Provider config | `~/.flocks/plugins/tools/api/{provider}/_provider.yaml` |
| Python tool | `~/.flocks/plugins/tools/python/{name}.py` |

**NEVER** write to `flocks/tool/generated/`, `flocks/tool/`, or any other project source path.

## Mode Selection

| Criteria | A: YAML-HTTP/Script | B: Python |
|----------|---------------------|-----------|
| Simple REST API calls | **Yes** | **NO** |
| API with pre/post-processing | **Yes** (script handler) | **NO** |
| Tools sharing auth/base URL | **Yes** (Provider) | **NO** |
| Local utility (file ops, text processing) | No | **Yes** |
| Complex data transformation (no API) | No | **Yes** |
| Multi-step orchestration (no API) | No | **Yes** |

**Rule of thumb**: single HTTP endpoint → A; anything with logic and no external API → B.

**⚠️ CRITICAL: All external API integrations MUST use Mode A (YAML-HTTP or YAML-Script), NOT Mode B (Python).** This includes FOFA, VirusTotal, ThreatBook, Shodan, or any tool that calls a remote HTTP API. Only Mode A places files under `api/` which is required for the tool to appear as an API service card in the Web UI (Tools > API Services tab). If the API needs complex pre/post-processing, use `handler.type: script` (still Mode A). Mode B (Python) is reserved for tools that do NOT call external APIs (local utilities, data processing, etc.).

---

## Tool Description — "Outcomes over Operations"

**This applies to ALL modes.** The `description` field is how the LLM decides when to use the tool.

```yaml
# BAD: describes the API endpoint
description: "Call ThreatBook /v3/ip/query endpoint with an IP parameter"

# GOOD: describes when to use and what the agent gets
description: >
  Query IP threat intelligence including reputation score, geolocation,
  and associated threats. Use when analyzing suspicious IPs during
  security incident investigation.
```

### Bilingual descriptions for API services

For **API service integrations (Mode A under `api/`)**, always provide:

- `description`: English description
- `description_cn`: Chinese description

This applies to service-level metadata such as `_provider.yaml`, and also to metadata JSON files when the service uses that format. The Web UI uses `description_cn` for Chinese locales and falls back to `description` otherwise.

Recommended rule:

- `description` should be concise, natural English, focused on the service capability and use case
- `description_cn` should be a natural Simplified Chinese translation, not machine-like literal wording

Example:

```yaml
description: Threat intelligence service for IP, domain, URL, and file hash lookups
description_cn: 威胁情报服务，提供 IP、域名、URL 和文件哈希查询能力
```

---

## Adding Secrets

**This applies to ALL modes** that need API keys or credentials.

Add the secret to `~/.flocks/config/.secret.json`:
```python
from flocks.security import get_secret_manager
sm = get_secret_manager()
sm.set("threatbook_api_key", "your-api-key-here")
```

Then reference it:
- YAML handler: `{secret:threatbook_api_key}` in headers/params
- Python code: `get_secret_manager().get("threatbook_api_key")`

---

## Mode A: YAML-HTTP Tool

For declarative REST API integrations. One YAML file per endpoint, no Python code needed.

### Workflow

1. **Clarify requirements** — Ask only when key parameters are missing.
2. **Inventory the API surface first** — Read the official API reference / OpenAPI spec / sidebar nav and make a complete list of in-scope endpoints before writing files.
3. **Check if a Provider exists** — Look for `~/.flocks/plugins/tools/api/{provider}/_provider.yaml`.
4. **Create Provider (if needed)** — Write `_provider.yaml` with shared auth and base URL.
5. **Add secret (if needed)** — Add API key to `.secret.json` (see above).
6. **Write tool YAMLs for all in-scope endpoints** — Create `~/.flocks/plugins/tools/api/{name}.yaml` (or `api/{provider}/{name}.yaml`) until the inventory is exhausted, not just the first few endpoints.
7. **Run Verification Protocol** (see below) — MANDATORY.

### Endpoint Coverage Rule (CRITICAL)

When the user asks to "integrate an API", "build tools for X service", or similar, the default goal is **broad coverage**, not a minimal demo.

1. **Default scope**: if the user names a provider/product rather than a single endpoint, assume they want the API integrated as comprehensively as practical.
2. **Build an endpoint inventory first**: collect endpoints from the official docs, OpenAPI schema, endpoint tables, and doc navigation pages. Do not stop after the first matching page.
3. **Track every discovered endpoint**: each endpoint must end in exactly one state:
   - implemented as a tool
   - intentionally skipped with a concrete reason
4. **Allowed skip reasons**:
   - deprecated/legacy endpoint
   - duplicate alias of an already implemented endpoint
   - unsupported protocol (for example WebSocket/streaming-only)
   - dangerously destructive admin action not requested by the user
   - documentation too incomplete to build a reliable tool
   - clearly out of the user's requested scope
5. **Keep going until closure**: do not stop after "core" endpoints if the docs show more pages or endpoint groups. Continue traversing the API reference until all discovered groups are covered or explicitly skipped.
6. **Bias toward inclusion**: when in doubt between "install now" and "maybe later", prefer implementing the endpoint if the docs are clear and it fits YAML-HTTP/YAML-script mode.
7. **Respect explicit narrowing**: if the user explicitly asks for only one endpoint or one capability, follow that narrower scope instead of broad coverage.

### Endpoint Inventory Output

Before finishing the task, summarize the API surface briefly:

- Implemented endpoints/groups
- Skipped endpoints/groups with reasons
- Any doc sections that could not be accessed or were ambiguous

### Naming Consistency Rule (CRITICAL)

When creating API tools, keep the **tool name**, **YAML filename**, and
**script handler function name** aligned with the real API path semantics.

Rules:

- Preserve the endpoint wording from the path whenever practical. If the path
  says `report`, `reputation`, `sandbox`, `submit`, or `query`, keep that word
  in the tool/function/file naming instead of silently replacing it with a
  different synonym.
- You may add a small amount of extra wording for readability, such as a
  provider prefix or a clarifying suffix like `file_sandbox_submit`, but do not
  change the core path vocabulary.
- Prefer consistency across all three layers:
  - YAML `name`
  - YAML filename
  - script `function`

Examples:

```yaml
# GOOD: preserves path wording
name: threatbook_file_report
# file: threatbook_file_report.yaml
# function: file_report
# path: /v3/file/report

# GOOD: adds readability without changing endpoint vocabulary
name: threatbook_url_sandbox_submit
# file: threatbook_url_sandbox_submit.yaml
# function: url_sandbox_submit
# path: /v3/url/sandbox

# BAD: hides path semantics behind a different word
name: threatbook_file_query
# path is actually /v3/file/report

# BAD: tool says query but path is reputation
name: threatbook_url_query
# path is actually /v3/scene/url_reputation
```

### YAML-HTTP Format

```yaml
name: threatbook_ip_query       # snake_case, globally unique
description: >
  Query IP threat intelligence including reputation score, geolocation,
  and associated threats. Use when analyzing suspicious IP addresses.
category: custom
enabled: true
requires_confirmation: false
provider: threatbook

# Parameters — MCP-compatible JSON Schema (preferred)
inputSchema:
  type: object
  properties:
    ip:
      type: string
      description: IPv4 or IPv6 address to query
    fields:
      type: string
      description: Comma-separated fields to return
      default: "reputation,location,tags"
  required: [ip]

# Handler — MUST be type: http or type: script
handler:
  type: http
  method: GET
  url: "{base_url}/v3/ip/query"
  query_params:
    resource: "{ip}"
    lang: "en"
    fields: "{fields}"
  timeout: 30

# Response processing
response:
  extract: "data"
  error_mapping:
    401: "API key invalid or expired"
    429: "Rate limit exceeded, try again later"
    404: "No data found for this IP"
```

A simplified `parameters` list is also supported as sugar syntax:
```yaml
parameters:
  - name: ip
    type: string
    description: IPv4 or IPv6 address
    required: true
```

### Provider YAML Format

`_provider.yaml` is **required** for grouped tools. It serves two purposes: shared auth/base_url injection, and **triggering the API service card** in the Tools > API Services tab (for API key configuration).

```yaml
# ~/.flocks/plugins/tools/api/threatbook-cn/_provider.yaml
name: threatbook-cn
description: ThreatBook Threat Intelligence Platform
description_cn: ThreatBook 威胁情报平台，提供 IOC 查询与安全分析能力
auth:
  secret: threatbook_api_key
  inject_as: query_param         # header | query_param
  param_name: apikey
defaults:
  base_url: "https://api.threatbook.cn"
  timeout: 30
  category: custom
```

`_provider.yaml` description rules:

- `description` is required for English display
- `description_cn` should be added for Chinese display
- Both descriptions should explain the service capability and typical use case, not just repeat the vendor name

### Script Handler

For API calls requiring pre/post-processing that still benefits from YAML metadata:

```yaml
# ~/.flocks/plugins/tools/api/threatbook-cn/threatbook_cn_file_report.yaml
name: threatbook_cn_file_report
description: Query file hash threat intelligence from ThreatBook API
inputSchema:
  type: object
  properties:
    file_hash: { type: string }
    lang:
      type: string
      enum: [zh, en]
      default: en
  required: [file_hash]
handler:
  type: script
  script_file: threatbook_cn.handler.py
  function: file_report
```

Script (`~/.flocks/plugins/tools/api/threatbook-cn/threatbook_cn.handler.py`):
```python
from flocks.tool.registry import ToolContext, ToolResult

async def file_report(ctx: ToolContext, file_hash: str, lang: str = "en") -> ToolResult:
    data = await fetch_upstream_payload(file_hash=file_hash, lang=lang)
    return ToolResult(success=True, output=data)
```

### Response Fidelity Rule (CRITICAL)

For API tools, default to returning the upstream response data as faithfully as
possible.

Rules:

- Do **not** delete, collapse, or selectively keep only a few fields unless the
  user explicitly requests a reduced response.
- Prefer returning the raw `data` object, or the raw resource-specific object
  such as `data[ip]`, `data[url]`, or `data[domain]`.
- If you add convenience fields for compatibility or readability, they must be
  additive only. Do not remove or rename upstream fields in the process.
- If the API already returns structured JSON, keep that structure intact rather
  than flattening it into a hand-curated summary.

Examples:

```python
# GOOD
return ToolResult(success=True, output=data)

# GOOD
result = data.get(ip, {})
return ToolResult(success=True, output=result)

# BAD: lossy transformation
return ToolResult(success=True, output={
    "severity": result.get("severity"),
    "judgments": result.get("judgments"),
})
```

### IMPORTANT: Do NOT use YAML for non-HTTP tools

YAML-HTTP mode is **only** for REST API integrations. For local utilities, file operations, data processing, or anything that runs Python logic — use **Mode B (Python)** instead.

---

## Mode B: Python Code Tool

For tools that do NOT call external APIs: local utilities, data processing, multi-step orchestration, non-HTTP integrations, etc.

**⚠️ NEVER use Mode B for external API integrations (REST, HTTP).** Tools in `python/` do NOT appear in the API Services tab. Use Mode A with `handler.type: script` instead — it provides the same Python flexibility while keeping the tool under `api/` for proper API service card display.

### Workflow

1. **Clarify requirements** — Ask only when key parameters are missing.
2. **Add secret (if needed)** — Add API key to `.secret.json` (see above).
3. **Generate tool code** — Create `~/.flocks/plugins/tools/python/{name}.py` with `@ToolRegistry.register_function`.
4. **Run Verification Protocol** (see below) — MANDATORY.

### Python Tool Format

File: `~/.flocks/plugins/tools/python/{name}.py`
```python
from flocks.tool.registry import (
    ToolRegistry, ToolContext, ToolResult,
    ToolParameter, ParameterType, ToolCategory,
)

@ToolRegistry.register_function(
    name="my_tool",
    description="Example tool that does X. Use when the user needs Y.",
    category=ToolCategory.CUSTOM,
    parameters=[
        ToolParameter(name="query", type=ParameterType.STRING, description="Search query"),
        ToolParameter(name="limit", type=ParameterType.INTEGER, description="Max results", required=False, default=10),
    ]
)
async def my_tool(ctx: ToolContext, query: str, limit: int = 10) -> ToolResult:
    # ... implementation ...
    return ToolResult(success=True, output={"result": "..."})
```

### Tool Output Format
- **String**: `ToolResult(success=True, output="Done")`
- **Dict**: `ToolResult(success=True, output={"key": "value"})` — auto-serialized to JSON
- **List**: `ToolResult(success=True, output=[item1, item2])`

---

## Naming

- Tool names: `snake_case` (e.g., `threatbook_ip_query`)
- Provider names: `snake_case` (e.g., `threatbook`)
- File names: always `snake_case` matching the tool name
- Script handler files: `{name}.handler.py`

---

## Verification Protocol (MANDATORY)

**You MUST run these steps after creating any tool. Do NOT skip or defer.**

### Step 1: Static Validation

Run via bash to confirm file syntax:

**YAML-HTTP tools (Mode A):**
```bash
uv run python -c "
import yaml, sys
from pathlib import Path
path = Path('$TOOL_PATH').expanduser()
data = yaml.safe_load(path.read_text(encoding='utf-8'))
assert 'name' in data, 'Missing name field'
assert 'handler' in data, 'Missing handler section'
handler = data['handler']
assert handler.get('type') in ('http', 'script'), 'handler.type must be http or script'
print(f'PASS: YAML valid, name={data[\"name\"]}, handler.type={handler[\"type\"]}')
"
```

**Python tools (Mode B):**
```bash
uv run python -c "
import py_compile
py_compile.compile('$TOOL_PATH', doraise=True)
print('PASS: Python syntax valid')
"
```

If validation fails: **fix the file immediately** and re-validate.

### Step 2: Load Test

Attempt to load the tool into the registry to catch schema/handler errors:

**YAML-HTTP tools (Mode A):**
```bash
uv run python -c "
from pathlib import Path
from flocks.tool.tool_loader import yaml_to_tool, _read_yaml_raw

yaml_path = Path('$TOOL_PATH').expanduser()
raw = _read_yaml_raw(yaml_path)
tool = yaml_to_tool(raw, yaml_path)
print(f'PASS: Tool loaded successfully')
print(f'  Name:     {tool.info.name}')
print(f'  Category: {tool.info.category.value}')
print(f'  Source:   {tool.info.source}')
print(f'  Provider: {tool.info.provider}')
print(f'  Parameters: {[p.name for p in tool.info.parameters]}')
print(f'  Enabled:  {tool.info.enabled}')
if yaml_path.parent.parent.name == 'api' or (yaml_path.parent.name == 'api'):
    assert tool.info.source == 'api', f'FAIL: source should be api, got {tool.info.source}'
    assert tool.info.provider, 'FAIL: provider must be set for API tools (check _provider.yaml exists)'
    print(f'  API card: WILL appear in Tools > API Services tab')
"
```

**Python tools (Mode B):**
```bash
uv run python -c "
from flocks.tool.registry import ToolRegistry
import importlib.util, sys
from pathlib import Path

path = Path('$TOOL_PATH').expanduser()
spec = importlib.util.spec_from_file_location(f'_test_{path.stem}', str(path))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

tool = ToolRegistry.get('$TOOL_NAME')
if tool:
    print(f'PASS: Tool registered successfully')
    print(f'  Name: {tool.info.name}')
    print(f'  Parameters: {[p.name for p in tool.info.parameters]}')
else:
    print('FAIL: Tool not found in registry after import')
    sys.exit(1)
"
```

If load fails: **read the error, fix the root cause**, and re-run.

### Step 3: Smoke Test

Execute the tool with **safe, minimal test parameters** to confirm end-to-end functionality:

**YAML-HTTP tools (Mode A):**
```bash
uv run python -c "
import asyncio
from pathlib import Path
from flocks.tool.tool_loader import yaml_to_tool, _read_yaml_raw
from flocks.tool.registry import ToolContext

yaml_path = Path('$TOOL_PATH').expanduser()
raw = _read_yaml_raw(yaml_path)
tool = yaml_to_tool(raw, yaml_path)
ctx = ToolContext(session_id='test', message_id='test')

# Replace with actual safe test parameters
test_params = {$TEST_PARAMS}

async def run():
    result = await tool.execute(ctx, **test_params)
    print(f'Success: {result.success}')
    if result.error:
        print(f'Error: {result.error}')
    if result.output:
        output_str = str(result.output)
        print(f'Output: {output_str[:500]}')
    return result.success

ok = asyncio.run(run())
if not ok:
    print('WARN: Smoke test returned success=False (may be expected for auth errors with unconfigured API keys)')
"
```

**Python tools (Mode B):**
```bash
uv run python -c "
import asyncio
from flocks.tool.registry import ToolRegistry, ToolContext
import importlib.util
from pathlib import Path

path = Path('$TOOL_PATH').expanduser()
spec = importlib.util.spec_from_file_location(f'_test_{path.stem}', str(path))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

ctx = ToolContext(session_id='test', message_id='test')
test_params = {$TEST_PARAMS}

async def run():
    result = await ToolRegistry.execute('$TOOL_NAME', ctx, **test_params)
    print(f'Success: {result.success}')
    if result.error:
        print(f'Error: {result.error}')
    if result.output:
        output_str = str(result.output)
        print(f'Output: {output_str[:500]}')
    return result.success

asyncio.run(run())
"
```

### Choosing test parameters

- **HTTP tools**: use a real but harmless query (e.g., IP `8.8.8.8`, domain `example.com`).
- **Python tools**: use the simplest valid input that exercises the happy path. For destructive tools (delete, write), create a temp file first.
- If the tool requires an API key that hasn't been configured yet, the smoke test may return an auth error — that's **acceptable**. Report it to the user and note the tool structure is correct.

### Step 4: Report Results

After all steps, summarize to the user:

```
Tool created: {name}
  Mode: {A/B}
  Path: {file_path}
  Static validation: PASS
  Load test: PASS
  Smoke test: {PASS/WARN/FAIL} — {details}
  Hot-reload: automatic (file watcher active — no restart or manual refresh needed)

{If WARN/FAIL, explain what the user needs to do (e.g., configure API key)}
```

---

## Failure Handling

- **YAML parse errors**: fix syntax before proceeding
- **HTTP handler errors**: check URL, auth config, parameter placeholders
- **Python import errors**: check imports, fix missing dependencies
- **Smoke test auth error**: expected if API key not configured — report to user
- **Wrong output path**: STOP immediately, move files to `~/.flocks/plugins/tools/{type}/`

## Pre-flight Checklist (mental, before writing any file)

1. Output path is under `~/.flocks/plugins/tools/{type}/`
2. **If the tool calls an external HTTP API → MUST be under `api/` (Mode A), NEVER `python/` (Mode B)**
3. Tool name is `snake_case` and unique (no collision with builtins: read, write, edit, bash, grep, glob, todo, question, plan, task, websearch, webfetch, codesearch, skill, etc.)
4. Description follows "outcomes over operations" style
5. Category is one of: `file`, `terminal`, `browser`, `code`, `search`, `system`, `custom`
6. Parameters have clear descriptions
7. For YAML-HTTP: `handler` section present with `type: http` or `type: script`, URL uses `{param}` placeholders
8. For Python: function signature matches parameter definitions exactly
9. Test parameters prepared for the smoke test
10. For API integrations: endpoint inventory completed; every discovered endpoint is implemented or explicitly skipped
11. Tool name / filename / function name preserve endpoint vocabulary
12. API tool output does not drop upstream fields unless the user explicitly asked for a reduced result
