"""
YAML-based tool loader.

Converts a YAML config dict into a :class:`Tool` instance, supporting:
- ``inputSchema``: MCP-compatible JSON Schema parameter definition
- ``parameters``: simplified parameter list (auto-converted to JSON Schema)
- ``handler.type=http``: declarative HTTP request handler
- ``handler.type=script``: external Python script handler

Used as the ``yaml_item_factory`` for the ``TOOLS`` extension point
in :mod:`flocks.plugin.loader`.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from flocks.plugin.loader import DEFAULT_PLUGIN_ROOT
from flocks.tool.registry import (
    ParameterType,
    Tool,
    ToolCategory,
    ToolContext,
    ToolHandler,
    ToolInfo,
    ToolParameter,
    ToolResult,
)
from flocks.utils.log import Log

log = Log.create(service="tool.loader")

_TOOLS_SUBDIR = DEFAULT_PLUGIN_ROOT / "tools"
_PROVIDER_FILENAME = "_provider.yaml"
_SECRET_PATTERN = re.compile(r"\{secret:([^}]+)\}")
_PARAM_PATTERN = re.compile(r"\{([^}]+)\}")

# ---------------------------------------------------------------------------
# Tool type constants — each type maps to a subdirectory under _TOOLS_SUBDIR
# ---------------------------------------------------------------------------

TOOL_TYPE_MCP = "mcp"
"""MCP server configurations (type: local/remote). Managed by MCP subsystem."""

TOOL_TYPE_API = "api"
"""YAML-based HTTP/script tools (handler.type: http|script)."""

TOOL_TYPE_PYTHON = "python"
"""Python code tools using @ToolRegistry.register_function."""

TOOL_TYPE_GENERATED = "generated"
"""Auto-generated tools from API specs. Supports hot-reload."""

ALL_TOOL_TYPES = (TOOL_TYPE_MCP, TOOL_TYPE_API, TOOL_TYPE_PYTHON, TOOL_TYPE_GENERATED)


# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------

def _load_provider_config(yaml_path: Path) -> Optional[Dict[str, Any]]:
    """Load ``_provider.yaml`` from the same directory if it exists."""
    provider_file = yaml_path.parent / _PROVIDER_FILENAME
    if not provider_file.is_file():
        return None
    try:
        data = yaml.safe_load(provider_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:
        log.warn("tool.provider.load_failed", {
            "path": str(provider_file), "error": str(e),
        })
        return None


def extract_provider_version(provider_cfg: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract a provider/service version string from a ``_provider.yaml`` dict.

    Lookup order (first non-null wins):
    1. top-level ``version``
    2. ``defaults.product_version``
    3. ``defaults.version``

    Always coerces the result to ``str`` so YAML-decoded floats like ``9.2``
    (which would otherwise round-trip lossy) are stable.
    Returns ``None`` when no version is declared anywhere.
    """
    if not isinstance(provider_cfg, dict):
        return None
    raw = provider_cfg.get("version")
    if raw is None:
        defaults = provider_cfg.get("defaults") or {}
        if isinstance(defaults, dict):
            raw = defaults.get("product_version") or defaults.get("version")
    if raw is None:
        return None
    return str(raw)


def _merge_provider_defaults(raw: dict, provider: Optional[Dict[str, Any]]) -> dict:
    """Apply provider defaults (base_url, timeout, category, auth) to a tool config."""
    if provider is None:
        return raw

    defaults = provider.get("defaults", {})

    if "category" not in raw and "category" in defaults:
        raw["category"] = defaults["category"]

    handler = raw.get("handler")
    if not isinstance(handler, dict):
        return raw

    if handler.get("type") == "http":
        if "timeout" not in handler and "timeout" in defaults:
            handler["timeout"] = defaults["timeout"]

        base_url = defaults.get("base_url", "")
        url = handler.get("url", "")
        if base_url and "{base_url}" in url:
            handler["url"] = url.replace("{base_url}", base_url.rstrip("/"))

        auth = provider.get("auth")
        if auth:
            _inject_provider_auth(handler, auth)

    raw["handler"] = handler
    return raw


def _inject_provider_auth(handler: dict, auth: Dict[str, Any]) -> None:
    """Inject provider-level auth into handler headers or query params."""
    secret_ref = auth.get("secret")
    if not secret_ref:
        return

    inject_as = auth.get("inject_as", "header")
    secret_placeholder = f"{{secret:{secret_ref}}}"

    if inject_as == "header":
        header_name = auth.get("header_name", "Authorization")
        prefix = auth.get("header_prefix", "Bearer ")
        headers = handler.setdefault("headers", {})
        if header_name not in headers:
            headers[header_name] = f"{prefix}{secret_placeholder}"
    elif inject_as == "query_param":
        param_name = auth.get("param_name", "api_key")
        query_params = handler.setdefault("query_params", {})
        if param_name not in query_params:
            query_params[param_name] = secret_placeholder


# ---------------------------------------------------------------------------
# Secret resolution
# ---------------------------------------------------------------------------

def _resolve_secrets(value: str) -> str:
    """Replace ``{secret:key}`` placeholders with actual secret values."""
    def _replacer(match: re.Match) -> str:
        secret_id = match.group(1)
        try:
            from flocks.security import get_secret_manager, resolve_secret_value
            secret_value = resolve_secret_value(secret_id, get_secret_manager())
            if secret_value:
                return secret_value
        except Exception:
            pass
        log.warn("tool.secret.not_found", {"secret_id": secret_id})
        return match.group(0)

    return _SECRET_PATTERN.sub(_replacer, value)


def _substitute_params(template: str, params: Dict[str, Any], url_encode: bool = False) -> str:
    """Replace ``{param_name}`` placeholders with actual parameter values.

    Secrets are resolved first, then parameter placeholders.
    """
    result = _resolve_secrets(template)

    def _replacer(match: re.Match) -> str:
        key = match.group(1)
        if key.startswith("secret:"):
            return match.group(0)
        value = params.get(key)
        if value is None:
            return ""
        text = str(value)
        return urllib.parse.quote(text, safe="") if url_encode else text

    return _PARAM_PATTERN.sub(_replacer, result)


# ---------------------------------------------------------------------------
# inputSchema normalization
# ---------------------------------------------------------------------------

def _normalize_input_schema(raw: dict) -> List[ToolParameter]:
    """Convert ``inputSchema`` (JSON Schema) or ``parameters`` list into ToolParameters.

    Supports two formats:
    1. MCP-compatible ``inputSchema`` (preferred)::

        inputSchema:
          type: object
          properties:
            ip: {type: string, description: "IP address"}
          required: [ip]

    2. Simplified ``parameters`` list (sugar)::

        parameters:
          - name: ip
            type: string
            description: IP address
            required: true
    """
    input_schema = raw.get("inputSchema")
    if isinstance(input_schema, dict):
        return _json_schema_to_params(input_schema)

    params_list = raw.get("parameters")
    if isinstance(params_list, list):
        return _params_list_to_params(params_list)

    return []


_TYPE_MAP = {
    "string": ParameterType.STRING,
    "integer": ParameterType.INTEGER,
    "number": ParameterType.NUMBER,
    "boolean": ParameterType.BOOLEAN,
    "array": ParameterType.ARRAY,
    "object": ParameterType.OBJECT,
}


def _json_schema_to_params(schema: dict) -> List[ToolParameter]:
    """Convert a JSON Schema ``properties`` dict to ToolParameter list."""
    properties = schema.get("properties", {})
    required_set = set(schema.get("required", []))
    result = []
    for name, prop in properties.items():
        json_type = prop.get("type", "string")
        json_schema = dict(prop) if json_type in {"object", "array"} else None
        result.append(ToolParameter(
            name=name,
            type=_TYPE_MAP.get(json_type, ParameterType.STRING),
            description=prop.get("description", ""),
            required=name in required_set,
            default=prop.get("default"),
            enum=prop.get("enum"),
            json_schema=json_schema,
        ))
    return result


def _params_list_to_params(params_list: list) -> List[ToolParameter]:
    """Convert a simplified parameter list to ToolParameter list."""
    result = []
    for p in params_list:
        if not isinstance(p, dict) or "name" not in p:
            continue
        json_type = p.get("type", "string")
        result.append(ToolParameter(
            name=p["name"],
            type=_TYPE_MAP.get(json_type, ParameterType.STRING),
            description=p.get("description", ""),
            required=p.get("required", True),
            default=p.get("default"),
            enum=p.get("enum"),
        ))
    return result


# ---------------------------------------------------------------------------
# Handler builders
# ---------------------------------------------------------------------------

def _build_handler(raw_handler: dict, yaml_path: Path) -> ToolHandler:
    """Build a ToolHandler from the ``handler`` section of a YAML config."""
    handler_type = raw_handler.get("type", "http")

    if handler_type == "http":
        return _build_http_handler(raw_handler)
    elif handler_type == "script":
        return _build_script_handler(raw_handler, yaml_path)
    else:
        raise ValueError(f"Unknown handler type: {handler_type}")


def _build_http_handler(cfg: dict) -> ToolHandler:
    """Build an async HTTP request handler from declarative config."""
    method = cfg.get("method", "GET").upper()
    url_template = cfg.get("url", "")
    headers_template = cfg.get("headers", {})
    query_params_template = cfg.get("query_params", {})
    body_template = cfg.get("body")
    timeout = cfg.get("timeout", 30)
    response_cfg = cfg.get("response", {})
    if not response_cfg:
        extract_path = cfg.get("response_path")
        error_mapping: Dict[int, str] = {}
    else:
        extract_path = response_cfg.get("extract") or cfg.get("response_path")
        error_mapping = {int(k): v for k, v in response_cfg.get("error_mapping", {}).items()}

    async def handler(ctx: ToolContext, **kwargs: Any) -> ToolResult:
        import aiohttp

        url = _substitute_params(url_template, kwargs, url_encode=False)
        headers = {
            k: _substitute_params(v, kwargs)
            for k, v in headers_template.items()
        }
        query_params = {
            k: _substitute_params(v, kwargs)
            for k, v in query_params_template.items()
        }
        query_params = {k: v for k, v in query_params.items() if v}

        body = None
        if body_template and isinstance(body_template, dict):
            import json as _json
            body = _json.dumps({
                k: _substitute_params(v, kwargs) if isinstance(v, str) else v
                for k, v in body_template.items()
            })
            headers.setdefault("Content-Type", "application/json")

        try:
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                req_kwargs: Dict[str, Any] = {"headers": headers}
                if query_params:
                    req_kwargs["params"] = query_params
                if body and method in ("POST", "PUT", "PATCH"):
                    req_kwargs["data"] = body

                async with session.request(method, url, **req_kwargs) as resp:
                    if resp.status >= 400:
                        friendly = error_mapping.get(resp.status)
                        if friendly:
                            return ToolResult(success=False, error=friendly)
                        text = await resp.text()
                        return ToolResult(
                            success=False,
                            error=f"HTTP {resp.status}: {text[:500]}",
                        )

                    data = await resp.json(content_type=None)
                    output = _extract_response(data, extract_path)
                    return ToolResult(success=True, output=output)

        except aiohttp.ClientError as e:
            return ToolResult(success=False, error=f"HTTP request failed: {e}")
        except Exception as e:
            return ToolResult(success=False, error=f"Tool execution error: {e}")

    return handler


def _extract_response(data: Any, path: Optional[str]) -> Any:
    """Extract a nested value from response data using a dot-separated path.

    Supports simple dot-notation like ``"data.results"`` (not full jmespath).
    """
    if not path or data is None:
        return data
    for key in path.split("."):
        if isinstance(data, dict):
            data = data.get(key)
        else:
            return data
    return data


def _build_script_handler(cfg: dict, yaml_path: Path) -> ToolHandler:
    """Build a handler that delegates to an external Python script."""
    script_file = cfg.get("script_file", "")
    function_name = cfg.get("function", "handle")

    script_path = (yaml_path.parent / script_file).resolve()

    user_plugins_root = DEFAULT_PLUGIN_ROOT.resolve()
    project_plugins_root = (Path.cwd() / ".flocks" / "plugins").resolve()
    script_str = str(script_path)
    if not script_str.startswith(str(user_plugins_root)) and not script_str.startswith(str(project_plugins_root)):
        raise ValueError(
            f"Script path {script_path} is outside the allowed plugins directories. "
            f"For security, scripts must be under {user_plugins_root} or {project_plugins_root}."
        )

    if not script_path.is_file():
        raise FileNotFoundError(f"Handler script not found: {script_path}")

    spec = importlib.util.spec_from_file_location(
        f"_flocks_tool_handler_{script_path.stem}",
        str(script_path),
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create import spec for {script_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    fn = getattr(module, function_name, None)
    if fn is None:
        raise AttributeError(
            f"Function '{function_name}' not found in {script_path}"
        )

    if not callable(fn):
        raise TypeError(f"'{function_name}' in {script_path} is not callable")

    # Inspect the target function signature once so the wrapper can adapt the
    # invocation to legacy handlers that either:
    #   * read parameters from ``ctx.params`` (signature: ``(ctx) -> ...``); or
    #   * take parameters as explicit keyword arguments / ``**kwargs``.
    #
    # Without this adaptation, callers like the test-credentials flow that
    # invoke ``ToolRegistry.execute(tool_name, **params)`` would either raise
    # ``TypeError: got an unexpected keyword argument`` or
    # ``AttributeError: 'ToolContext' object has no attribute 'params'``.
    fn_sig = inspect.signature(fn)
    fn_params = fn_sig.parameters
    fn_has_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in fn_params.values()
    )
    # Skip the first positional argument (always the ``ctx`` we supply
    # ourselves) so a user-provided kwarg named ``ctx`` cannot trigger
    # ``TypeError: got multiple values for argument 'ctx'``.
    _param_items = list(fn_params.items())
    if _param_items and _param_items[0][1].kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_ONLY,
    ):
        _param_items = _param_items[1:]
    fn_param_names = {
        name
        for name, p in _param_items
        if p.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }

    async def handler(ctx: ToolContext, **kwargs: Any) -> ToolResult:
        # Always expose the raw kwargs on the context so handlers that read
        # from ``ctx.params`` (e.g. ``params = dict(ctx.params)``) keep working
        # regardless of whether the caller created a fresh ``ToolContext``.
        try:
            ctx.params = kwargs  # type: ignore[attr-defined]
        except AttributeError:
            pass

        if fn_has_var_kw:
            call_kwargs = kwargs
        else:
            call_kwargs = {k: v for k, v in kwargs.items() if k in fn_param_names}

        result = await fn(ctx, **call_kwargs)
        if isinstance(result, ToolResult):
            return result
        return ToolResult(success=True, output=result)

    return handler


def _build_execution_handler(cfg: dict, yaml_path: Path) -> ToolHandler:
    """Build a handler from an ``execution`` section with inline Python code.

    Supports YAML configs that use the alternative format::

        execution:
          type: python
          code: |
            import os
            os.remove(file_path)
            return {"success": True}

    Parameter values are injected as local variables into the code scope.
    The code can ``return`` a value which becomes the tool output.
    """
    exec_type = cfg.get("type", "python")
    if exec_type != "python":
        raise ValueError(f"Unsupported execution type: {exec_type}")

    code = cfg.get("code", "")
    if not code or not code.strip():
        raise ValueError(f"Empty execution code in {yaml_path}")

    code_body = code.rstrip()
    wrapper_lines = ["async def _tool_exec(**_kw_):", "    import asyncio"]
    for line in code_body.splitlines():
        wrapper_lines.append(f"    {line}")
    wrapper_source = "\n".join(wrapper_lines)

    compiled = compile(wrapper_source, str(yaml_path), "exec")

    async def handler(ctx: ToolContext, **kwargs: Any) -> ToolResult:
        ns: Dict[str, Any] = {}
        exec(compiled, ns)
        _tool_exec = ns["_tool_exec"]
        try:
            result = await _tool_exec(**kwargs)
            if isinstance(result, ToolResult):
                return result
            if isinstance(result, dict):
                success = result.pop("success", True)
                error = result.pop("error", None)
                return ToolResult(success=success, output=result, error=error)
            return ToolResult(success=True, output=result)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    return handler


# ---------------------------------------------------------------------------
# yaml_item_factory for the TOOLS extension point
# ---------------------------------------------------------------------------

def yaml_to_tool(raw: dict, yaml_path: Path) -> Tool:
    """Convert a parsed YAML dict into a :class:`Tool`.

    This is the ``yaml_item_factory`` wired into the TOOLS extension point.

    Parameters
    ----------
    raw:
        The parsed YAML document (a dict).
    yaml_path:
        Absolute path to the source ``.yaml`` file.

    Raises
    ------
    ValueError
        If ``name`` or ``handler`` is missing.
    """
    name = raw.get("name")
    if not name:
        raise ValueError(f"Tool YAML config missing required 'name' field: {yaml_path}")

    handler_raw = raw.get("handler")
    execution_raw = raw.get("execution")
    if (not handler_raw or not isinstance(handler_raw, dict)) and (
        not execution_raw or not isinstance(execution_raw, dict)
    ):
        raise ValueError(
            f"Tool YAML config missing required 'handler' or 'execution' section: {yaml_path}"
        )

    provider_cfg = _load_provider_config(yaml_path)
    raw = _merge_provider_defaults(raw, provider_cfg)

    provider_name = raw.get("provider")
    if not provider_name and provider_cfg:
        provider_name = provider_cfg.get("name")

    provider_version = extract_provider_version(provider_cfg)

    cat_str = raw.get("category", "custom")
    try:
        category = ToolCategory(cat_str)
    except ValueError:
        category = ToolCategory.CUSTOM

    parameters = _normalize_input_schema(raw)

    if handler_raw and isinstance(handler_raw, dict):
        handler = _build_handler(raw["handler"], yaml_path)
        handler_type = raw["handler"].get("type", "http")
    else:
        handler = _build_execution_handler(execution_raw, yaml_path)
        handler_type = f"execution/{execution_raw.get('type', 'python')}"

    requires_confirm = raw.get("requires_confirmation", False)
    if not requires_confirm:
        safety_checks = raw.get("safety_checks")
        if isinstance(safety_checks, list) and any(
            isinstance(c, dict) and c.get("enabled", True) for c in safety_checks
        ):
            requires_confirm = True

    tool_type = _infer_tool_type(yaml_path)
    source = "api" if tool_type == TOOL_TYPE_API else None

    info = ToolInfo(
        name=name,
        description=raw.get("description", ""),
        category=category,
        parameters=parameters,
        enabled=raw.get("enabled", True),
        requires_confirmation=requires_confirm,
        provider=provider_name,
        provider_version=provider_version,
        source=source,
    )

    tool = Tool(info=info, handler=handler)
    tool._yaml_path = yaml_path  # type: ignore[attr-defined]
    tool._provider = provider_name  # type: ignore[attr-defined]
    tool._provider_version = provider_version  # type: ignore[attr-defined]
    tool._source = source or "yaml_plugin"  # type: ignore[attr-defined]

    log.info("tool.yaml.loaded", {
        "name": name,
        "provider": provider_name,
        "provider_version": provider_version,
        "handler_type": handler_type,
        "path": str(yaml_path),
    })

    return tool


# ---------------------------------------------------------------------------
# YAML Tool file CRUD helpers
# ---------------------------------------------------------------------------

def _yaml_tool_search_roots() -> List[Path]:
    """Return YAML tool roots for both user-level and project-level plugins."""
    roots = [_TOOLS_SUBDIR, Path.cwd() / ".flocks" / "plugins" / "tools"]
    result: List[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        result.append(root)
    return result


def _find_yaml_file(name: str) -> Optional[Path]:
    """Find the YAML source file for a plugin tool by name.

    Search order (first match wins):

    1. New type-based paths: ``tools/{type}/{name}.yaml``
       and ``tools/{type}/{provider}/{name}.yaml``
    2. Legacy flat path: ``tools/{name}.yaml``
    3. Legacy provider path: ``tools/{provider}/{name}.yaml``

    The ``mcp/`` subdirectory is skipped — MCP configs have a different
    format and are managed via :func:`find_mcp_config`.
    """
    for tools_root in _yaml_tool_search_roots():
        if not tools_root.is_dir():
            continue

        # 1. New type-based directories (api/, python/)
        for type_dir in (TOOL_TYPE_API, TOOL_TYPE_PYTHON):
            type_path = tools_root / type_dir
            if not type_path.is_dir():
                continue
            for suffix in (".yaml", ".yml"):
                candidate = type_path / f"{name}{suffix}"
                if candidate.is_file():
                    return candidate
            # Provider sub-subdirectories within the type dir
            for subdir in type_path.iterdir():
                if not subdir.is_dir() or subdir.name.startswith("_"):
                    continue
                for suffix in (".yaml", ".yml"):
                    candidate = subdir / f"{name}{suffix}"
                    if candidate.is_file():
                        return candidate

        # 2. Legacy flat path (backward compat)
        for suffix in (".yaml", ".yml"):
            candidate = tools_root / f"{name}{suffix}"
            if candidate.is_file():
                return candidate

        # 3. Legacy provider subdirectories (backward compat)
        _type_dirs = set(ALL_TOOL_TYPES)
        for subdir in tools_root.iterdir():
            if not subdir.is_dir() or subdir.name.startswith("_"):
                continue
            if subdir.name in _type_dirs:
                continue  # already searched above
            for suffix in (".yaml", ".yml"):
                candidate = subdir / f"{name}{suffix}"
                if candidate.is_file():
                    return candidate

    return None


def _read_yaml_raw(yaml_path: Path) -> Dict[str, Any]:
    """Read and parse a YAML file, returning the raw dict."""
    return yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}


def _write_yaml(yaml_path: Path, data: Dict[str, Any]) -> None:
    """Atomic-ish write of a dict back to a YAML file."""
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.dump(
        data, default_flow_style=False, allow_unicode=True, sort_keys=False,
    )
    yaml_path.write_text(content, encoding="utf-8")


def find_yaml_tool(name: str) -> Optional[Path]:
    """Public API: return the YAML path for a plugin tool, or None."""
    return _find_yaml_file(name)


def read_yaml_tool(name: str) -> Optional[Dict[str, Any]]:
    """Read the raw YAML dict for a plugin tool. Returns None if not found."""
    path = _find_yaml_file(name)
    if path is None:
        return None
    try:
        return _read_yaml_raw(path)
    except Exception as e:
        log.error("tool.yaml.read_failed", {"name": name, "error": str(e)})
        return None


def create_yaml_tool(
    data: Dict[str, Any],
    provider: Optional[str] = None,
    tool_type: str = TOOL_TYPE_API,
) -> Path:
    """Create a new YAML tool plugin file.

    Parameters
    ----------
    data:
        Tool definition dict (must include ``name``).
    provider:
        Optional provider name.  When given, the file is placed under
        a provider subdirectory.
    tool_type:
        Tool type determines the base subdirectory
        (``api``, ``python``, etc.).  Defaults to ``api``.

    The resulting path is::

        ~/.flocks/plugins/tools/{tool_type}/{provider?}/{name}.yaml

    Returns
    -------
    Path to the created YAML file.

    Raises
    ------
    ValueError
        If ``name`` is missing or the tool already exists.
    """
    name = data.get("name")
    if not name:
        raise ValueError("Tool data missing required 'name' field")

    if _find_yaml_file(name):
        raise ValueError(f"Tool '{name}' already exists")

    base_dir = _TOOLS_SUBDIR / tool_type
    if provider:
        target_dir = base_dir / provider
    else:
        target_dir = base_dir

    target_path = target_dir / f"{name}.yaml"
    _write_yaml(target_path, data)
    log.info("tool.yaml.created", {"name": name, "tool_type": tool_type, "path": str(target_path)})
    return target_path


def update_yaml_tool(name: str, updates: Dict[str, Any]) -> bool:
    """Apply partial updates to a YAML plugin tool file.

    Returns True on success, False if the YAML file was not found.
    """
    path = _find_yaml_file(name)
    if path is None:
        return False

    try:
        data = _read_yaml_raw(path)

        for key, value in updates.items():
            if value is not None:
                data[key] = value
            else:
                data.pop(key, None)

        _write_yaml(path, data)
        log.info("tool.yaml.updated", {"name": name, "path": str(path)})
        return True
    except Exception as e:
        log.error("tool.yaml.update_failed", {"name": name, "error": str(e)})
        return False


def delete_yaml_tool(name: str) -> bool:
    """Delete a YAML plugin tool file and its handler script (if any).

    Returns True on success, False if the YAML file was not found.
    """
    path = _find_yaml_file(name)
    if path is None:
        return False

    try:
        data = _read_yaml_raw(path)
        handler = data.get("handler", {})
        if isinstance(handler, dict) and handler.get("type") == "script":
            script_file = handler.get("script_file")
            if script_file:
                script_path = path.parent / script_file
                if script_path.is_file():
                    script_path.unlink()

        path.unlink()
        log.info("tool.yaml.deleted", {"name": name, "path": str(path)})
        return True
    except Exception as e:
        log.error("tool.yaml.delete_failed", {"name": name, "error": str(e)})
        return False


def _python_tool_dirs() -> List[Path]:
    """Return all plugin python tool directories to search."""
    dirs = [_TOOLS_SUBDIR / TOOL_TYPE_PYTHON, Path.cwd() / ".flocks" / "plugins" / "tools" / TOOL_TYPE_PYTHON]
    result: List[Path] = []
    seen: set[str] = set()
    for directory in dirs:
        key = str(directory.resolve()) if directory.exists() else str(directory)
        if key in seen:
            continue
        seen.add(key)
        result.append(directory)
    return result


def _iter_python_tool_files() -> List[Path]:
    """List all candidate plugin python files."""
    files: List[Path] = []
    for directory in _python_tool_dirs():
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_") or path.name == "__init__.py":
                continue
            files.append(path)
    return files


def _register_function_name(call: ast.Call) -> Optional[str]:
    """Extract tool name from a ToolRegistry.register_function decorator call."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "register_function":
        return None
    for kw in call.keywords:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _find_python_tool_block(tool_name: str) -> tuple[Optional[Path], Optional[int], Optional[int]]:
    """Locate the decorated function block that registers *tool_name*."""
    for path in _iter_python_tool_files():
        try:
            source = path.read_text(encoding="utf-8")
            module = ast.parse(source)
        except Exception as e:
            log.warn("tool.python.parse_failed", {"path": str(path), "error": str(e)})
            continue

        for node in ast.walk(module):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                registered_name = _register_function_name(decorator)
                if registered_name != tool_name:
                    continue
                start_line = min(
                    [getattr(dec, "lineno", node.lineno) for dec in node.decorator_list] + [node.lineno]
                )
                end_line = getattr(node, "end_lineno", node.lineno)
                return path, start_line, end_line
    return None, None, None


def delete_python_tool(name: str) -> bool:
    """Delete a Python plugin tool definition by tool name.

    Removes only the decorated function that registers the requested tool.
    If the file becomes empty after removal, the file itself is deleted.
    """
    path, start_line, end_line = _find_python_tool_block(name)
    if path is None or start_line is None or end_line is None:
        return False

    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        start_idx = max(start_line - 1, 0)
        end_idx = min(end_line, len(lines))

        while start_idx > 0 and lines[start_idx - 1].strip() == "":
            start_idx -= 1
        while end_idx < len(lines) and lines[end_idx].strip() == "":
            end_idx += 1

        remaining = lines[:start_idx] + lines[end_idx:]
        if "".join(remaining).strip():
            path.write_text("".join(remaining), encoding="utf-8")
        else:
            path.unlink()
        log.info("tool.python.deleted", {"name": name, "path": str(path)})
        return True
    except Exception as e:
        log.error("tool.python.delete_failed", {"name": name, "path": str(path), "error": str(e)})
        return False


def _infer_tool_type(yaml_path: Path) -> str:
    """Infer the tool type from a YAML file's location in the directory tree.

    Checks both the user-level tools dir (~/.flocks/plugins/tools/) and
    the project-level tools dir (<cwd>/.flocks/plugins/tools/).
    """
    candidates = [_TOOLS_SUBDIR, Path.cwd() / ".flocks" / "plugins" / "tools"]
    for base in candidates:
        try:
            rel = yaml_path.relative_to(base)
            first_part = rel.parts[0] if rel.parts else ""
            if first_part in ALL_TOOL_TYPES:
                return first_part
        except ValueError:
            continue
    return "legacy"


def list_yaml_tools() -> List[Dict[str, Any]]:
    """List all YAML plugin tools with basic metadata.

    Returns a list of dicts with ``name``, ``description``, ``provider``,
    ``handler_type``, ``tool_type``, and ``path``.

    Searches both user-level (~/.flocks/plugins/tools/) and project-level
    (<cwd>/.flocks/plugins/tools/) directories, as well as legacy flat/provider paths.
    The ``mcp/`` subdirectory is excluded (MCP configs are a different format).
    """
    results: List[Dict[str, Any]] = []

    _skip_dirs = {TOOL_TYPE_MCP, TOOL_TYPE_GENERATED}
    yaml_files: List[Path] = []
    seen_names: set = set()

    def _collect(directory: Path, depth: int = 0, max_depth: int = 2) -> None:
        if not directory.is_dir():
            return
        for item in directory.iterdir():
            if item.is_file() and item.suffix in (".yaml", ".yml") and not item.name.startswith("_"):
                yaml_files.append(item)
            elif (
                item.is_dir()
                and not item.name.startswith("_")
                and depth < max_depth
                and not (depth == 0 and item.name in _skip_dirs)
            ):
                _collect(item, depth + 1, max_depth)

    search_roots = [_TOOLS_SUBDIR, Path.cwd() / ".flocks" / "plugins" / "tools"]
    for root in search_roots:
        _collect(root)

    for yf in sorted(yaml_files):
        try:
            data = _read_yaml_raw(yf)
            name = data.get("name")
            if not name or name in seen_names:
                continue
            seen_names.add(name)

            provider_cfg = _load_provider_config(yf)
            provider_name = data.get("provider")
            if not provider_name and provider_cfg:
                provider_name = provider_cfg.get("name")

            handler = data.get("handler", {})
            results.append({
                "name": name,
                "description": data.get("description", ""),
                "provider": provider_name,
                "handler_type": handler.get("type", "unknown") if isinstance(handler, dict) else "unknown",
                "tool_type": _infer_tool_type(yf),
                "path": str(yf),
                "enabled": data.get("enabled", True),
            })
        except Exception as e:
            log.warn("tool.yaml.list.error", {"path": str(yf), "error": str(e)})

    return results


# ---------------------------------------------------------------------------
# MCP config CRUD helpers
# ---------------------------------------------------------------------------

_MCP_SUBDIR = _TOOLS_SUBDIR / TOOL_TYPE_MCP


def _mcp_filename(name: str) -> str:
    """Normalise an MCP server name to a safe filename stem."""
    return name.replace("-", "_")


def save_mcp_config(name: str, config: Dict[str, Any]) -> Path:
    """Save an MCP server config to ``~/.flocks/plugins/tools/mcp/{name}.yaml``.

    Parameters
    ----------
    name:
        MCP server name (e.g. ``"brave-search"``).
    config:
        Server configuration dict (type, command/url, environment, etc.).

    Returns
    -------
    Path to the created/updated YAML file.
    """
    filename = _mcp_filename(name)
    target = _MCP_SUBDIR / f"{filename}.yaml"
    data: Dict[str, Any] = {"name": name}
    data.update(config)
    _write_yaml(target, data)
    log.info("tool.mcp_config.saved", {"name": name, "path": str(target)})
    return target


def find_mcp_config(name: str) -> Optional[Path]:
    """Find an MCP config YAML under ``~/.flocks/plugins/tools/mcp/``."""
    if not _MCP_SUBDIR.is_dir():
        return None
    filename = _mcp_filename(name)
    for variant in (filename, name):
        for suffix in (".yaml", ".yml"):
            candidate = _MCP_SUBDIR / f"{variant}{suffix}"
            if candidate.is_file():
                return candidate
    return None


def delete_mcp_config(name: str) -> bool:
    """Delete an MCP config YAML.  Returns True if a file was removed."""
    path = find_mcp_config(name)
    if path is None:
        return False
    try:
        path.unlink()
        log.info("tool.mcp_config.deleted", {"name": name, "path": str(path)})
        return True
    except Exception as e:
        log.error("tool.mcp_config.delete_failed", {"name": name, "error": str(e)})
        return False


def list_mcp_configs() -> List[Dict[str, Any]]:
    """List all MCP server configs under ``~/.flocks/plugins/tools/mcp/``."""
    results: List[Dict[str, Any]] = []
    if not _MCP_SUBDIR.is_dir():
        return results
    for item in sorted(_MCP_SUBDIR.iterdir()):
        if not item.is_file() or item.suffix not in (".yaml", ".yml"):
            continue
        if item.name.startswith("_"):
            continue
        try:
            data = _read_yaml_raw(item)
            results.append({
                "name": data.get("name", item.stem),
                "type": data.get("type", "unknown"),
                "path": str(item),
                **{k: v for k, v in data.items() if k not in ("name", "type")},
            })
        except Exception as e:
            log.warn("tool.mcp_config.list_error", {"path": str(item), "error": str(e)})
    return results
