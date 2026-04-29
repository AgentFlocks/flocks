"""
Tool Registry - Tool registration and management system

Provides a framework for registering, discovering, and executing tools.
Compatible with Flocks's TypeScript Tool system.
"""

import asyncio
import importlib
import json
import os
import sys
import threading
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from pydantic import BaseModel, Field

from flocks.utils.log import Log

log = Log.create(service="tool-registry")


class ToolCategory(str, Enum):
    """Tool categories"""
    FILE = "file"
    TERMINAL = "terminal"
    BROWSER = "browser"
    CODE = "code"
    SEARCH = "search"
    SYSTEM = "system"
    CUSTOM = "custom"


class ParameterType(str, Enum):
    """Parameter types for tool schema"""
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class ToolParameter(BaseModel):
    """Tool parameter definition"""
    name: str = Field(..., description="Parameter name")
    type: ParameterType = Field(..., description="Parameter type")
    description: str = Field("", description="Parameter description")
    required: bool = Field(True, description="Is parameter required")
    default: Optional[Any] = Field(None, description="Default value")
    enum: Optional[List[Any]] = Field(None, description="Allowed values")
    json_schema: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Optional JSON Schema override for this parameter. "
            "When provided, this schema will be used as-is (merged with description/default/enum) "
            "instead of the simplified schema derived from `type`."
        ),
    )


class ToolSchema(BaseModel):
    """Tool JSON Schema"""
    type: str = Field("object", description="Schema type")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Parameter properties")
    required: List[str] = Field(default_factory=list, description="Required parameters")

    def to_json_schema(self) -> Dict[str, Any]:
        """Convert to JSON Schema format."""
        schema = {
            "type": self.type,
            "properties": self.properties or {},
        }
        # Only include required if non-empty
        if self.required:
            schema["required"] = self.required
        return schema


class ToolInfo(BaseModel):
    """Tool information"""
    name: str = Field(..., description="Tool name (unique identifier)")
    description: str = Field(..., description="Tool description")
    description_cn: Optional[str] = Field(None, description="Chinese UI description")
    category: ToolCategory = Field(ToolCategory.CUSTOM, description="Tool category")
    parameters: List[ToolParameter] = Field(default_factory=list, description="Tool parameters")
    enabled: bool = Field(True, description="Is tool enabled")
    requires_confirmation: bool = Field(False, description="Requires user confirmation")
    provider: Optional[str] = Field(None, description="Tool provider name (for grouped plugin tools)")
    provider_version: Optional[str] = Field(
        None,
        description=(
            "Provider/service version string (e.g. '9.2'). Sourced from the "
            "`version` field in `_provider.yaml`. Surfaced to the LLM via the "
            "tool description so the model can pick version-appropriate behavior."
        ),
    )
    source: Optional[str] = Field(None, description="Tool source: builtin, dynamic, plugin_py, plugin_yaml, mcp")
    native: bool = Field(False, description=(
        "True for built-in tools (registered via @register_function) and project-level "
        "plugin tools (<cwd>/.flocks/plugins/tools/). False for user-level plugin tools "
        "(~/.flocks/plugins/tools/). Determined by loading context, not declared in YAML."
    ))
    always_load: Optional[bool] = Field(
        None,
        description="Whether the tool should always be exposed in each request",
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Lightweight retrieval tags used by tool catalog search",
    )

    def get_schema(self) -> ToolSchema:
        """Generate JSON Schema for this tool."""
        properties = {}
        required = []

        for param in self.parameters:
            if param.json_schema:
                # Prefer user-provided JSON Schema for complex parameters.
                prop: Dict[str, Any] = dict(param.json_schema)
                # Ensure description/default/enum are present unless explicitly overridden.
                if param.description and "description" not in prop:
                    prop["description"] = param.description
                if param.default is not None and "default" not in prop:
                    prop["default"] = param.default
                if param.enum and "enum" not in prop:
                    prop["enum"] = param.enum
            else:
                prop = {
                    "type": param.type.value,
                    "description": param.description,
                }

                # Array type requires items property
                if param.type == ParameterType.ARRAY:
                    prop["items"] = {"type": "string"}

                # Object type requires properties
                if param.type == ParameterType.OBJECT:
                    prop["properties"] = {}

                if param.default is not None:
                    prop["default"] = param.default
                if param.enum:
                    prop["enum"] = param.enum

            properties[param.name] = prop

            if param.required:
                required.append(param.name)

        return ToolSchema(properties=properties, required=required)


class ToolResult(BaseModel):
    """Tool execution result"""
    success: bool = Field(..., description="Execution successful")
    output: Any = Field(None, description="Output data")
    error: Optional[str] = Field(None, description="Error message if failed")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    title: Optional[str] = Field(None, description="Result title for display")
    truncated: bool = Field(False, description="Whether output was truncated")
    attachments: Optional[List[Dict[str, Any]]] = Field(None, description="File attachments (images, PDFs)")


@dataclass
class PermissionRequest:
    """Permission request for tool execution"""
    permission: str  # Type: read, edit, bash, grep, glob, list, external_directory
    patterns: List[str]  # Patterns to match
    always: List[str] = dataclass_field(default_factory=list)  # Always allow patterns
    metadata: Dict[str, Any] = dataclass_field(default_factory=dict)


class ToolContext:
    """
    Context for tool execution

    Provides session info, abort signal, and permission handling.
    Compatible with Flocks's Tool.Context interface.
    """

    def __init__(
        self,
        session_id: str,
        message_id: str,
        agent: str = "rex",
        call_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
        abort_event: Optional[asyncio.Event] = None,
        permission_callback: Optional[Callable[[PermissionRequest], Awaitable[None]]] = None,
        metadata_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        event_publish_callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.session_id = session_id
        self.message_id = message_id
        self.agent = agent
        self.call_id = call_id
        self.extra = extra or {}
        self._abort_event = abort_event or asyncio.Event()
        self._permission_callback = permission_callback
        self._metadata_callback = metadata_callback
        self._metadata: Dict[str, Any] = {}
        self.event_publish_callback = event_publish_callback

    @property
    def abort(self) -> asyncio.Event:
        """Get abort event (for checking if execution should be cancelled)"""
        return self._abort_event

    @property
    def aborted(self) -> bool:
        """Check if execution was aborted"""
        return self._abort_event.is_set()

    async def ask(
        self,
        permission: str,
        patterns: List[str],
        always: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Request permission for an operation

        Args:
            permission: Permission type (read, edit, bash, etc.)
            patterns: Patterns to match
            always: Always-allow patterns
            metadata: Additional metadata
        """
        request = PermissionRequest(
            permission=permission,
            patterns=patterns,
            always=always or ["*"],
            metadata=metadata or {}
        )

        if self._permission_callback:
            await self._permission_callback(request)
        else:
            # Default: auto-approve (for testing/non-interactive mode)
            log.debug("permission.auto_approved", {
                "permission": permission,
                "patterns": patterns
            })

    def metadata(self, input_data: Dict[str, Any]) -> None:
        """
        Update tool metadata (for live progress updates)

        Args:
            input_data: Metadata to update (may contain 'title' and 'metadata' keys)
        """
        if "title" in input_data:
            self._metadata["title"] = input_data["title"]
        if "metadata" in input_data:
            self._metadata.update(input_data["metadata"])

        if self._metadata_callback:
            self._metadata_callback(self._metadata)


# Type for tool handler function
ToolHandler = Callable[..., Awaitable[ToolResult]]


def _coerce_params(
    kwargs: Dict[str, Any],
    parameters: List[ToolParameter],
    tool_name: str = "",
) -> Dict[str, Any]:
    """Coerce parameter types based on declared schema to handle LLM type mismatches.

    For example, an LLM may pass a list/dict where a string is expected.
    Returns a new dict with coerced values.
    """
    param_type_map = {p.name: p.type for p in parameters}
    coerced: Dict[str, Any] = {}

    def _coerce_json_string(value: Any, expected_type: type[Any]) -> Any:
        if not isinstance(value, str):
            return value
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return value
        if isinstance(parsed, expected_type):
            return parsed
        return value

    for k, v in kwargs.items():
        declared = param_type_map.get(k)
        if declared == ParameterType.STRING and not isinstance(v, str):
            original_type = type(v).__name__
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            else:
                v = str(v)
            log.debug("tool.execute.coerce_param", {
                "tool": tool_name, "param": k,
                "original_type": original_type, "coerced_to": "str",
            })
        elif declared == ParameterType.BOOLEAN and not isinstance(v, bool):
            v = str(v).lower() in ("true", "1", "yes")
        elif declared == ParameterType.INTEGER and not isinstance(v, int):
            try:
                v = int(v)
            except (TypeError, ValueError):
                pass
        elif declared == ParameterType.NUMBER and not isinstance(v, (int, float)):
            try:
                v = float(v)
            except (TypeError, ValueError):
                pass
        elif declared == ParameterType.OBJECT and isinstance(v, str):
            coerced_value = _coerce_json_string(v, dict)
            if coerced_value is not v:
                v = coerced_value
                log.debug("tool.execute.coerce_param", {
                    "tool": tool_name, "param": k,
                    "original_type": "str", "coerced_to": "dict",
                })
        elif declared == ParameterType.ARRAY and isinstance(v, str):
            coerced_value = _coerce_json_string(v, list)
            if coerced_value is not v:
                v = coerced_value
                log.debug("tool.execute.coerce_param", {
                    "tool": tool_name, "param": k,
                    "original_type": "str", "coerced_to": "list",
                })
        coerced[k] = v
    return coerced


def _normalize_param_key(name: str) -> str:
    """Normalize parameter keys for conservative alias matching."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _remap_schema_kwargs(
    kwargs: Dict[str, Any],
    declared_param_names: List[str],
) -> tuple[Dict[str, Any], Dict[str, str]]:
    """Remap guessed argument keys to declared schema keys when unambiguous.

    Only applies conservative normalization (case / separators), and only when
    a normalized key maps to exactly one declared parameter name.
    """
    declared_lookup: Dict[str, List[str]] = {}
    for name in declared_param_names:
        declared_lookup.setdefault(_normalize_param_key(name), []).append(name)

    remapped: Dict[str, Any] = {}
    aliases: Dict[str, str] = {}

    for key, value in kwargs.items():
        if key in declared_param_names:
            remapped[key] = value
            continue
        normalized = _normalize_param_key(key)
        candidates = declared_lookup.get(normalized, [])
        if len(candidates) == 1:
            target = candidates[0]
            if target not in remapped:
                remapped[target] = value
                aliases[key] = target
                continue
        remapped[key] = value

    return remapped, aliases


def _schema_hint_from_properties(
    declared_param_names: List[str],
    required: List[str],
    *,
    max_items: int = 20,
) -> str:
    """Build a compact schema hint string for argument-validation errors."""
    allowed_preview = ", ".join(declared_param_names[:max_items]) or "(none)"
    if len(declared_param_names) > max_items:
        allowed_preview += ", ..."
    required_preview = ", ".join(required[:max_items]) or "(none)"
    if len(required) > max_items:
        required_preview += ", ..."
    return f"Allowed parameters: {allowed_preview}. Required: {required_preview}."


class Tool:
    """Tool wrapper class"""

    def __init__(
        self,
        info: ToolInfo,
        handler: ToolHandler,
    ):
        self.info = info
        self.handler = handler

    async def execute(self, ctx: ToolContext, **kwargs) -> ToolResult:
        """Execute the tool with given parameters and context"""
        try:
            # Log tool execution start
            log.info("tool.execute.start", {
                "tool": self.info.name,
                "agent": ctx.agent,
                "session": ctx.session_id,
                "params": list(kwargs.keys()),
            })

            schema = self.info.get_schema()
            schema_properties = schema.properties if isinstance(schema.properties, dict) else {}
            declared_param_names = list(schema_properties.keys())

            # Strict schema precheck: remap obvious aliases first, then validate.
            # This reduces first-call failures caused by case/separator drift
            # (e.g. file_path -> filePath) without allowing speculative params.
            remap_aliases: Dict[str, str] = {}
            effective_kwargs = dict(kwargs)
            if declared_param_names:
                effective_kwargs, remap_aliases = _remap_schema_kwargs(
                    effective_kwargs,
                    declared_param_names,
                )
                if remap_aliases:
                    log.info("tool.execute.param_remapped", {
                        "tool": self.info.name,
                        "aliases": remap_aliases,
                    })

                unknown = sorted(
                    key for key in effective_kwargs.keys() if key not in schema_properties
                )
                if unknown:
                    schema_hint = _schema_hint_from_properties(
                        declared_param_names,
                        list(schema.required),
                    )
                    return ToolResult(
                        success=False,
                        error=(
                            f"Invalid arguments for {self.info.name}: unknown parameters: "
                            f"{', '.join(unknown)}. {schema_hint}"
                        ),
                        metadata={
                            "schema_precheck": {
                                "tool": self.info.name,
                                "source": self.info.source,
                                "unknown": unknown,
                                "allowed": declared_param_names,
                                "required": list(schema.required),
                                "aliases": remap_aliases,
                            }
                        },
                    )

            # Validate required parameters
            for required_param in schema.required:
                if required_param not in effective_kwargs:
                    log.error("tool.execute.missing_param", {
                        "tool": self.info.name,
                        "missing": required_param,
                        "provided": list(effective_kwargs.keys()),
                    })
                    schema_hint = _schema_hint_from_properties(
                        declared_param_names,
                        list(schema.required),
                    )
                    return ToolResult(
                        success=False,
                        error=(
                            f"Missing required parameter: {required_param}. "
                            f"{schema_hint}"
                        ),
                        metadata={
                            "schema_precheck": {
                                "tool": self.info.name,
                                "source": self.info.source,
                                "provided": sorted(effective_kwargs.keys()),
                                "allowed": declared_param_names,
                                "required": list(schema.required),
                                "aliases": remap_aliases,
                            }
                        },
                    )

            coerced_kwargs = _coerce_params(effective_kwargs, self.info.parameters, self.info.name)

            # Execute handler
            result = await self.handler(ctx, **coerced_kwargs)

            # Auto-truncate output unless the tool already handled it
            if result.success and not result.truncated:
                from flocks.tool.truncation import truncate_output
                output_text = result.output
                if output_text is not None and not isinstance(output_text, str):
                    import json as _json
                    try:
                        output_text = _json.dumps(output_text, ensure_ascii=False, indent=2)
                        result.output = output_text
                    except (TypeError, ValueError):
                        output_text = str(output_text)
                        result.output = output_text
                if isinstance(output_text, str):
                    agent_name = ctx.agent if isinstance(ctx.agent, str) else ""
                    tr = truncate_output(output_text, has_task_tool="task" in agent_name.lower())
                    if tr.truncated:
                        result.output = tr.content
                        result.truncated = True
                        result.metadata = {
                            **(result.metadata or {}),
                            "truncated": True,
                            "output_path": tr.output_path,
                        }

            log.info("tool.execute.complete", {
                "tool": self.info.name,
                "success": result.success,
                "has_output": bool(result.output),
                "truncated": result.truncated,
            })

            return result

        except Exception as e:
            if isinstance(e, FuturesTimeoutError):
                raise
            log.error("tool.execute.error", {
                "tool": self.info.name,
                "error": str(e),
                "error_type": type(e).__name__,
            })
            return ToolResult(
                success=False,
                error=str(e)
            )


class ToolRegistry:
    """
    Tool Registry - manages tool registration and execution

    Compatible with Flocks's Tool Registry pattern.
    """

    _tools: Dict[str, Tool] = {}
    _initialized: bool = False
    _dynamic_modules: Dict[str, str] = {}
    _dynamic_tools_by_module: Dict[str, List[str]] = {}
    _plugin_tool_names: List[str] = []
    _revision: int = 0
    _failure_state: Dict[str, Dict[str, Any]] = {}
    _failure_disable_threshold: int = 3

    # Snapshot of every tool's factory-default ``enabled`` flag — captured
    # in :meth:`register` at the moment the tool object is handed to the
    # registry (i.e. right after construction from YAML / decorator /
    # ``TOOLS`` attribute + :func:`apply_tool_catalog_defaults`, but
    # BEFORE any of the overlay / service-sync machinery gets to mutate
    # ``tool.info.enabled``).  This is the source of truth for the
    # "factory default" surfaced in the HTTP API and used by
    # :func:`reset_tool_setting` to recover the original value for tools
    # that don't have a YAML file (built-in / plugin_py).
    #
    # Lifecycle is kept in lock-step with ``_tools``:
    #   • :meth:`register`                 → write (always overwrites,
    #     so a YAML upgrade that flips the default is picked up on the
    #     next reload/`POST /reload`).
    #   • :meth:`_unregister_plugin_tools` → pop  (avoids stale defaults
    #     leaking across ``refresh_plugin_tools`` cycles).
    _enabled_defaults: Dict[str, bool] = {}

    @classmethod
    def register(cls, tool: Tool) -> None:
        """Register a tool"""
        try:
            from flocks.tool.catalog import apply_tool_catalog_defaults

            tool.info = apply_tool_catalog_defaults(tool.info)
        except Exception as e:
            log.debug("tool.policy_defaults.apply_failed", {
                "name": tool.info.name,
                "error": str(e),
            })
        # Capture the factory default BEFORE anything else can touch
        # ``info.enabled``.  ``apply_tool_catalog_defaults`` above only
        # fills metadata like ``always_load`` and never flips
        # ``enabled``, so whatever we read here is the value written in
        # the YAML / decorator at source-tree time.
        #
        # Direct assignment (not setdefault) is deliberate: every
        # ``register`` — including re-registering the same name after a
        # YAML edit via ``POST /api/tools/{name}/reload`` or a
        # ``refresh_plugin_tools`` cycle — must refresh the snapshot so
        # ``enabled_default`` / ``reset`` reflect the current source of
        # truth instead of the first value ever observed.
        cls._enabled_defaults[tool.info.name] = bool(tool.info.enabled)
        cls._tools[tool.info.name] = tool
        log.debug("tool.registered", {
            "name": tool.info.name,
            "category": tool.info.category.value,
        })

    @classmethod
    def revision(cls) -> int:
        """Return the current registry revision.

        The revision is bumped when plugin or dynamic tools are reloaded so
        long-lived session caches can detect toolset changes.
        """
        return cls._revision

    @classmethod
    def _bump_revision(cls, reason: str) -> None:
        """Advance the registry revision and invalidate agent prompt caches."""
        cls._revision += 1
        try:
            from flocks.agent.registry import Agent
            Agent.invalidate_cache()
        except Exception as e:
            log.debug("tool.revision.agent_invalidate_failed", {"error": str(e)})
        log.info("tool.registry.revision.bumped", {"revision": cls._revision, "reason": reason})

    @classmethod
    def register_function(
        cls,
        name: str,
        description: str,
        description_cn: Optional[str] = None,
        category: ToolCategory = ToolCategory.CUSTOM,
        parameters: Optional[List[ToolParameter]] = None,
        requires_confirmation: bool = False,
        native: bool = False,
        always_load: Optional[bool] = None,
        tags: Optional[List[str]] = None,
    ) -> Callable[[ToolHandler], ToolHandler]:
        """
        Decorator to register a function as a tool.

        ``native`` defaults to False (safe default).  Built-in tools get
        ``native=True`` in bulk by ``_register_builtin_tools()`` after all
        built-in modules are imported, so callers don't need to pass it
        explicitly.  User plugin Python files that use this decorator will
        correctly stay ``native=False``.

        Usage:
            @ToolRegistry.register_function(
                name="read",
                description="Read file contents",
                parameters=[ToolParameter(name="filePath", type=ParameterType.STRING)]
            )
            async def read_tool(ctx: ToolContext, filePath: str) -> ToolResult:
                ...
        """
        def decorator(func: ToolHandler) -> ToolHandler:
            info = ToolInfo(
                name=name,
                description=description,
                description_cn=description_cn,
                category=category,
                parameters=parameters or [],
                requires_confirmation=requires_confirmation,
                native=native,
                always_load=always_load,
                tags=list(tags or []),
            )
            tool = Tool(info=info, handler=func)
            cls.register(tool)
            return func
        return decorator

    @classmethod
    def unregister(cls, name: str) -> bool:
        """Unregister a tool by name. Returns True if the tool was found and removed."""
        removed = cls._tools.pop(name, None)
        if removed:
            log.debug("tool.unregistered", {"name": name})
        return removed is not None

    @classmethod
    def _ensure_initialized(cls) -> None:
        """Initialize the registry on first public access."""
        if not cls._initialized:
            cls.init()

    @classmethod
    def get(cls, name: str) -> Optional[Tool]:
        """Get a tool by name"""
        cls._ensure_initialized()
        return cls._tools.get(name)

    @classmethod
    def list_tools(cls, category: Optional[ToolCategory] = None) -> List[ToolInfo]:
        """List all registered tools, optionally filtered by category"""
        cls._ensure_initialized()
        tools = list(cls._tools.values())

        if category:
            tools = [t for t in tools if t.info.category == category]

        return [t.info for t in tools]

    @classmethod
    def get_schema(cls, name: str) -> Optional[ToolSchema]:
        """Get tool schema by name"""
        tool = cls.get(name)
        if tool:
            return tool.info.get_schema()
        return None

    @classmethod
    async def execute(
        cls,
        tool_name: str,
        ctx: Optional[ToolContext] = None,
        **kwargs
    ) -> ToolResult:
        """Execute a tool by name"""
        tool = cls.get(tool_name)
        if not tool:
            return ToolResult(
                success=False,
                error=f"Tool not found: {tool_name}"
            )

        if not tool.info.enabled:
            return ToolResult(
                success=False,
                error=f"Tool is disabled: {tool_name}"
            )

        # Create default context if not provided
        if ctx is None:
            ctx = ToolContext(
                session_id="default",
                message_id="default"
            )

        log.info("tool.execute", {
            "name": tool_name,
            "params": list(kwargs.keys()),
        })

        result = await tool.execute(ctx, **kwargs)
        if result.success:
            cls._reset_failure_state(tool_name)
        else:
            disabled = cls._record_failure(tool, kwargs, result.error)
            if disabled:
                result.metadata = {**(result.metadata or {}), "disabled": True, "disabled_reason": "repeated_error"}
                suffix = f"tool disabled after {cls._failure_disable_threshold} identical errors"
                if result.error:
                    result.error = f"{result.error} ({suffix})"
                else:
                    result.error = suffix
        return result

    @classmethod
    async def execute_batch(
        cls,
        calls: List[Dict[str, Any]],
        ctx: Optional[ToolContext] = None,
        parallel: bool = True
    ) -> List[ToolResult]:
        """
        Execute multiple tools

        Args:
            calls: List of {"name": str, "params": dict}
            ctx: Tool context
            parallel: Execute in parallel if True

        Returns:
            List of results in same order
        """
        if parallel:
            tasks = [
                cls.execute(tool_name=call["name"], ctx=ctx, **call.get("params", {}))
                for call in calls
            ]
            return await asyncio.gather(*tasks)
        else:
            results = []
            for call in calls:
                result = await cls.execute(tool_name=call["name"], ctx=ctx, **call.get("params", {}))
                results.append(result)
            return results

    @classmethod
    def all_tool_ids(cls) -> List[str]:
        """Get all registered tool IDs"""
        cls._ensure_initialized()
        return list(cls._tools.keys())

    @classmethod
    def get_dynamic_tools_by_module(cls) -> Dict[str, List[str]]:
        """Return a copy of the dynamic-module → tool-names mapping."""
        cls._ensure_initialized()
        return dict(cls._dynamic_tools_by_module)

    @classmethod
    def get_api_service_ids(cls) -> set:
        """Return all known API service IDs (provider names).

        API service discovery must only come from tools loaded through the
        ``tools/api`` path, i.e. tools whose ``ToolInfo`` is explicitly marked
        with ``source='api'`` and has a provider name.

        This intentionally ignores dynamic module names and other registration
        side effects so non-API helper/security modules cannot appear in the
        API Services UI by mistake.
        """
        ids: set = set()
        for tool_info in cls.list_tools():
            if tool_info.source == "api" and tool_info.provider:
                ids.add(tool_info.provider)
        return ids

    @classmethod
    def init(cls) -> None:
        """Initialize registry with built-in tools"""
        if cls._initialized:
            return

        # Import and register built-in tools
        cls._register_builtin_tools()
        cls._register_dynamic_tools()
        cls._register_plugin_extension_point()
        cls._load_plugin_tools()
        cls._initialized = True
        log.debug("tool_registry.initialized", {"count": len(cls._tools)})

    @classmethod
    def _load_plugin_tools(cls) -> None:
        """Load plugin tools from both user-level and project-level plugin dirs on init.

        Without this, YAML/Python plugin tools only appear after
        ``PluginLoader.load_all()`` is triggered by Agent initialization
        or an explicit ``POST /api/tools/refresh``.

        Scans both:
        - ``~/.flocks/plugins/tools/`` (user-level)
        - ``<cwd>/.flocks/plugins/tools/`` (project-level)

        Tracks which tool names were added so that
        ``refresh_plugin_tools`` can accurately unregister stale entries
        (regardless of the ``ToolInfo.source`` value).
        """
        before = set(cls._tools.keys())
        try:
            from flocks.plugin import PluginLoader
            PluginLoader.load_all()
        except Exception as e:
            log.warn("tool_registry.plugin_load_failed", {"error": str(e)})
        after = set(cls._tools.keys())
        new_plugin_tools = sorted(after - before)
        for name in new_plugin_tools:
            tool = cls._tools.get(name)
            if tool is None:
                continue
            # Python plugin files that register via @ToolRegistry.register_function
            # are imported for side effects and therefore bypass the extension-point
            # consumer that would normally stamp source="plugin_py".
            if tool.info.source is None:
                tool.info.source = "plugin_py"
        cls._plugin_tool_names = new_plugin_tools
        cls._bootstrap_user_api_services()
        # Defence-in-depth: ``register()`` is the canonical writer for
        # ``_enabled_defaults`` but this catches any tool that landed in
        # ``_tools`` via an unorthodox path.  Must run BEFORE
        # ``_sync_api_service_states`` / ``_apply_tool_settings`` so the
        # snapshot still reflects factory values, not overlay results.
        cls._snapshot_enabled_defaults()
        cls._sync_api_service_states()
        cls._apply_tool_settings()

    @classmethod
    def _bootstrap_user_api_services(cls) -> None:
        """Auto-enable newly loaded user-level API tools in flocks.json.

        For each tool that satisfies ALL of:
          - source == "api"
          - provider is set
          - native is False (user-level plugin)
          - enabled is True (tool-level default)

        Ensures the corresponding api_services entry in flocks.json has
        ``enabled: true`` so that ``_sync_api_service_states()`` won't
        immediately disable it.  Existing explicit user choices
        (``enabled: false``) are never overwritten.
        """
        try:
            from flocks.config.config_writer import ConfigWriter
        except Exception:
            return

        seen_providers: set = set()
        for tool in cls._tools.values():
            info = tool.info
            if (
                info.source != "api"
                or not info.provider
                or info.native
                or not info.enabled
            ):
                continue
            provider = info.provider
            if provider in seen_providers:
                continue
            seen_providers.add(provider)

            existing = ConfigWriter.get_api_service_raw(provider)
            if existing is None:
                ConfigWriter.set_api_service(provider, {"enabled": True})
                log.info("tool_registry.bootstrap_api_service", {
                    "provider": provider, "action": "created_enabled",
                })
            elif "enabled" not in existing:
                existing["enabled"] = True
                ConfigWriter.set_api_service(provider, existing)
                log.info("tool_registry.bootstrap_api_service", {
                    "provider": provider, "action": "added_enabled",
                })

    @classmethod
    def _sync_api_service_states(cls) -> None:
        """Disable tools whose API service is disabled in flocks.json.

        YAML plugin tools default to ``enabled=True``, but the corresponding
        API service in ``api_services`` may be ``enabled: false``.  Without
        this sync the runner exposes disabled-service tools to the LLM.
        """
        try:
            from flocks.config.config_writer import ConfigWriter
            api_services = ConfigWriter.list_api_services_raw()
        except Exception:
            return

        disabled_count = 0
        for tool in cls._tools.values():
            provider = tool.info.provider
            if not provider:
                continue
            svc = api_services.get(provider, {})
            if not svc.get("enabled", False):
                tool.info.enabled = False
                disabled_count += 1

        if disabled_count:
            disabled_providers = [
                p for p, svc in api_services.items()
                if not svc.get("enabled", False)
            ]
            log.info("tool_registry.api_service_sync", {
                "disabled_tools": disabled_count,
                "disabled_providers": disabled_providers,
            })

    @classmethod
    def _snapshot_enabled_defaults(cls) -> None:
        """Idempotent safety-net for the ``_enabled_defaults`` snapshot.

        The *primary* write path is :meth:`register` (see comment on
        ``_enabled_defaults``); refresh paths keep the snapshot fresh via
        :meth:`_unregister_plugin_tools` / :meth:`_unregister_dynamic_tools`
        popping the entry and the subsequent ``register`` call overwriting
        it.  This method exists only as a defensive backstop for:

        - Tests that bypass :meth:`register` by poking at ``_tools``
          directly (``tests/tool/test_apply_tool_settings.py`` uses this
          to stage stub tools without invoking catalog-defaults logic).
        - Any future registration path that inserts into ``_tools``
          outside :meth:`register` — we'd rather have a best-effort
          factory value in the snapshot than hand out ``None`` via the
          HTTP API.

        ``setdefault`` is deliberate: this method is called at the end of
        :meth:`_load_plugin_tools`, which in turn runs on every
        :meth:`refresh_plugin_tools` cycle.  On the *second* and later
        cycles any built-in / previously-registered tool's
        ``info.enabled`` has already been mutated by the prior run's
        :meth:`_sync_api_service_states` / :meth:`_apply_tool_settings`,
        so a direct assignment here would overwrite the true factory
        default with the post-sync state.  Since :meth:`register` is the
        authoritative writer for anything that actually goes through it,
        a ``setdefault`` here only fills in genuinely missing entries and
        leaves correct snapshots untouched.
        """
        for name, t in cls._tools.items():
            cls._enabled_defaults.setdefault(name, bool(t.info.enabled))

    @classmethod
    def get_default_enabled(cls, name: str) -> Optional[bool]:
        """Return the YAML/registration default ``enabled`` for ``name``.

        Returns ``None`` if the tool was never seen during plugin loading
        (e.g. dynamic tools registered after init).  Callers should fall
        back to the live ``ToolInfo.enabled`` in that case.
        """
        return cls._enabled_defaults.get(name)

    @classmethod
    def _apply_tool_settings(cls) -> None:
        """Apply user-level ``tool_settings`` from flocks.json on top of YAML defaults.

        The YAML file is treated as the factory default; the overlay in
        ``flocks.json`` (``tool_settings[<tool_name>]``) is the user's
        current choice and is applied last.

        Service gate: an overlay can NEVER enable a tool whose API service
        is currently ``enabled: false`` in ``api_services``.  Without this
        gate ``ToolRegistry.execute`` (which only checks
        ``tool.info.enabled``) would happily run a tool whose service has
        no credentials configured, producing repeated auth failures and
        eventually triggering the auto-disable threshold in
        :meth:`_record_failure`.

        An overlay can always *disable* a tool, regardless of service
        state.
        """
        try:
            from flocks.config.config_writer import ConfigWriter
            settings = ConfigWriter.list_tool_settings()
            api_services = ConfigWriter.list_api_services_raw()
        except Exception:
            return

        applied = 0
        unknown: List[str] = []
        blocked: List[str] = []
        for name, entry in settings.items():
            if not isinstance(entry, dict):
                continue
            tool = cls._tools.get(name)
            if tool is None:
                unknown.append(name)
                continue
            if "enabled" not in entry:
                continue

            desired = bool(entry["enabled"])
            if desired and tool.info.provider:
                svc = api_services.get(tool.info.provider, {})
                if not svc.get("enabled", False):
                    # Service disabled — refuse to "open" the tool via overlay.
                    # The overlay entry stays in flocks.json so that re-enabling
                    # the service later restores the user's intent automatically.
                    blocked.append(name)
                    continue

            tool.info.enabled = desired
            applied += 1

        if applied or unknown or blocked:
            log.info("tool_registry.tool_settings_applied", {
                "applied": applied,
                "stale": unknown,
                "blocked_by_service": blocked,
            })

    @classmethod
    def _register_plugin_extension_point(cls) -> None:
        """Register the TOOLS extension point with the unified PluginLoader.

        Plugin tool modules can either:
        - Use ``@ToolRegistry.register_function`` decorators (auto-registered on import).
        - Expose a ``TOOLS: list[dict]`` attribute with declarative tool definitions.
          Each dict must have at minimum ``name``, ``description``, and ``handler``
          (a callable or a string referencing a module-level function).
        - Use YAML config files (processed via ``yaml_to_tool`` factory).
        """
        from flocks.plugin import ExtensionPoint, PluginLoader
        from flocks.tool.tool_loader import yaml_to_tool

        # User-level plugin root: ~/.flocks/plugins/
        _user_plugin_root = Path.home() / ".flocks" / "plugins"

        def _is_native_source(source: str) -> bool:
            """True if the source file is project-level (not user-global)."""
            try:
                Path(source).relative_to(_user_plugin_root)
                return False  # Under ~/.flocks/plugins/ → user-level → not native
            except ValueError:
                return True   # Under <cwd>/.flocks/plugins/ or elsewhere → project-level → native

        def _consume_tools(items: list, source: str) -> None:
            is_native = _is_native_source(source)
            for spec in items:
                # YAML factory produces Tool instances directly
                if isinstance(spec, Tool):
                    if spec.info.name in cls._tools:
                        log.warn("plugin.tool.duplicate", {"source": source, "name": spec.info.name})
                        continue
                    if spec.info.source is None:
                        spec.info.source = "plugin_yaml"
                    spec.info.native = is_native
                    cls.register(spec)
                    continue

                if not isinstance(spec, dict):
                    log.warn("plugin.tool.invalid_spec", {"source": source})
                    continue
                name = spec.get("name")
                handler = spec.get("handler")
                if not name or not handler:
                    log.warn("plugin.tool.missing_fields", {
                        "source": source,
                        "spec_keys": list(spec.keys()),
                    })
                    continue
                if name in cls._tools:
                    log.warn("plugin.tool.duplicate", {"source": source, "name": name})
                    continue

                if isinstance(handler, str):
                    import importlib as _imp
                    mod = _imp.import_module(source) if "." in source else None
                    handler = getattr(mod, handler, None) if mod else None
                    if handler is None:
                        log.warn("plugin.tool.handler_not_found", {
                            "source": source, "handler": spec.get("handler"),
                        })
                        continue

                params = [
                    ToolParameter(**p) if isinstance(p, dict) else p
                    for p in spec.get("parameters", [])
                ]
                cat_str = spec.get("category", "custom")
                category = ToolCategory(cat_str) if cat_str in ToolCategory.__members__.values() else ToolCategory.CUSTOM

                info = ToolInfo(
                    name=name,
                    description=spec.get("description", ""),
                    category=category,
                    parameters=params,
                    source="plugin_py",
                    native=is_native,
                )
                cls.register(Tool(info=info, handler=handler))

        def _dedup_key(item: Any) -> str:
            if isinstance(item, Tool):
                return item.info.name
            if isinstance(item, dict):
                return item.get("name", "")
            return ""

        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="TOOLS",
            subdir="tools",
            consumer=_consume_tools,
            item_type=None,
            dedup_key=_dedup_key,
            yaml_item_factory=yaml_to_tool,
            recursive=True,
            max_depth=2,
            exclude_subdirs=frozenset({"mcp", "generated"}),
        ))

    @classmethod
    def _register_builtin_tools(cls) -> None:
        """Register built-in tools by importing tool modules.

        All tools registered during these imports are marked ``native=True``
        after the fact.  Using a post-import bulk update means individual
        ``@register_function`` call sites don't need to pass ``native=True``
        explicitly, and user plugin Python files that also use
        ``@register_function`` won't accidentally inherit native status.
        """
        before = set(cls._tools.keys())

        _tool_groups = [
            # file/ — filesystem operations
            ("flocks.tool.file", ["read", "write", "edit", "multiedit", "apply_patch", "glob", "list_tool", "file_search", "doc_parser"]),
            # code/ — code analysis + terminal
            ("flocks.tool.code", ["bash", "grep", "codesearch", "lsp_tool"]),
            # web/ — internet access
            ("flocks.tool.web", ["webfetch", "websearch"]),
            # agent/ — agent delegation/coordination
            ("flocks.tool.agent", ["delegate_task", "call_omo_agent"]),
            # task/ — task/workflow
            ("flocks.tool.task", ["task", "task_center", "todo", "plan", "run_workflow", "run_workflow_node"]),
            # security/ — SSH forensics + threat intelligence (optional: asyncssh)
            ("flocks.tool.security", ["ssh_host_cmd", "ssh_run_script"]),
            # system/ — background tasks, questions, model config, memory, skill, batch, session management, slash commands
            ("flocks.tool.system", ["background_output", "background_cancel", "question", "model_config", "memory", "skill", "batch", "session_manage", "slash_command", "tool_search"]),
            # skill/ — skill management (search, install, status, deps, remove + L2 author)
            ("flocks.tool.skill", ["flocks_skills", "skill_manage"]),
            # channel/ — IM platform messaging
            ("flocks.tool.channel", ["channel_message"]),
            # wecom/ — 企业微信 MCP（文档、智能表格）
            ("flocks.tool.wecom", ["wecom_mcp"]),
        ]
        for package, modules in _tool_groups:
            for mod_name in modules:
                try:
                    importlib.import_module(f"{package}.{mod_name}")
                except ImportError as e:
                    log.warn("builtin_tools.import_failed", {"module": f"{package}.{mod_name}", "error": str(e)})

        # Mark every tool registered during this call as native=True.
        # This is done in bulk here so individual @register_function call
        # sites don't need to pass native=True, and user plugin files using
        # the same decorator won't be misclassified.
        for name in set(cls._tools.keys()) - before:
            cls._tools[name].info.native = True

        # Sample tools for testing (only register if not already registered)
        if "echo" not in cls._tools:
            @cls.register_function(
                name="echo",
                description="Echo back the input message",
                category=ToolCategory.SYSTEM,
                native=True,
                parameters=[
                    ToolParameter(
                        name="message",
                        type=ParameterType.STRING,
                        description="Message to echo",
                        required=True,
                    )
                ]
            )
            async def echo(ctx: ToolContext, message: str) -> ToolResult:
                return ToolResult(success=True, output=message)

        if "get_time" not in cls._tools:
            @cls.register_function(
                name="get_time",
                description="Get current date and time",
                category=ToolCategory.SYSTEM,
                native=True,
                parameters=[]
            )
            async def get_time(ctx: ToolContext) -> ToolResult:
                from datetime import datetime
                return ToolResult(
                    success=True,
                    output=datetime.now().isoformat()
                )

    @classmethod
    def _unregister_plugin_tools(cls) -> List[str]:
        """Remove all previously tracked plugin tools from the registry.

        Uses ``_plugin_tool_names`` (populated by ``_load_plugin_tools``)
        so that tools registered via any mechanism (YAML, ``TOOLS``
        attribute, or ``@register_function`` decorator) are correctly
        cleaned up.

        Also drops the matching entries from ``_enabled_defaults`` so a
        subsequent ``_load_plugin_tools`` call picks up the current YAML
        factory default rather than the one observed on a previous
        cycle.  Without this, editing a YAML file to flip ``enabled:``
        and hitting ``refresh_plugin_tools`` would leave the snapshot —
        and therefore ``reset_tool_setting`` — stuck on the old value.
        """
        removed: List[str] = []
        for name in cls._plugin_tool_names:
            if cls._tools.pop(name, None) is not None:
                removed.append(name)
            cls._enabled_defaults.pop(name, None)
        if removed:
            log.info("tool.plugin.unregistered", {"tools": removed})
        cls._plugin_tool_names = []
        return removed

    # ── File watcher ────────────────────────────────────────────────────────

    _watcher: Optional["ToolFileWatcher"] = None

    @classmethod
    def start_watcher(cls) -> None:
        """Start the file watcher that auto-reloads plugin tools on file changes."""
        if cls._watcher is None:
            cls._watcher = ToolFileWatcher()
        cls._watcher.start()

    @classmethod
    def refresh_plugin_tools(cls) -> List[str]:
        """Reload plugin tools (YAML + Python) from disk.

        Unregisters stale plugin tools first so that deleted files are
        correctly removed from the registry.
        """
        cls._ensure_initialized()
        cls._unregister_plugin_tools()
        cls._load_plugin_tools()
        cls._bump_revision("plugin_refresh")
        return cls.all_tool_ids()

    @classmethod
    def refresh_dynamic_tools(cls) -> List[str]:
        """Reload dynamically generated tools and return tool ids."""
        cls._ensure_initialized()
        cls._register_dynamic_tools()
        cls._bump_revision("dynamic_refresh")
        return cls.all_tool_ids()

    @classmethod
    def _reset_failure_state(cls, tool_name: str) -> None:
        """Reset failure tracking for a tool after success."""
        if tool_name in cls._failure_state:
            cls._failure_state.pop(tool_name, None)

    @classmethod
    def _should_track_failure(cls, tool: Tool) -> bool:
        """Track failures only for custom tools to avoid disabling core tools."""
        return tool.info.category == ToolCategory.CUSTOM and tool.info.name != "invalid"

    @classmethod
    def _is_countable_error(cls, error: Optional[str]) -> bool:
        """Skip non-actionable or validation errors."""
        if not error or not isinstance(error, str):
            return False
        error_lower = error.lower()
        skip_phrases = [
            "missing required parameter",
            "invalid arguments",
            "tool not found",
            "tool is disabled",
        ]
        return not any(phrase in error_lower for phrase in skip_phrases)

    @classmethod
    def _failure_key(cls, tool_name: str, params: Dict[str, Any], error: Optional[str]) -> str:
        """Build a stable signature for repeated failure detection."""
        try:
            params_json = json.dumps(params, sort_keys=True, default=str)
        except (TypeError, ValueError):
            params_json = repr(params)
        return f"{tool_name}|{params_json}|{error or ''}"

    @classmethod
    def _record_failure(cls, tool: Tool, params: Dict[str, Any], error: Optional[str]) -> bool:
        """
        Track repeated identical errors and disable tool if threshold reached.

        Returns True if the tool was disabled.
        """
        if not cls._should_track_failure(tool) or not cls._is_countable_error(error):
            return False

        tool_name = tool.info.name
        key = cls._failure_key(tool_name, params, error)
        state = cls._failure_state.get(tool_name, {"key": None, "count": 0})

        if state.get("key") == key:
            state["count"] = state.get("count", 0) + 1
        else:
            state = {"key": key, "count": 1}

        cls._failure_state[tool_name] = state

        if state["count"] >= cls._failure_disable_threshold:
            tool.info.enabled = False
            log.warn("tool.disabled.repeated_error", {
                "tool": tool_name,
                "count": state["count"],
                "error": error,
            })
            return True

        return False

    @classmethod
    def _generated_tools_dirs(cls) -> List[Path]:
        """Return directories containing dynamically generated tools.

        Checks (in order):
        1. ``~/.flocks/plugins/tools/generated/``  (external plugin path)
        2. ``flocks/tool/security/``  (built-in threat intelligence tools)
        """
        from flocks.plugin.loader import DEFAULT_PLUGIN_ROOT

        dirs: List[Path] = []
        plugins_dir = DEFAULT_PLUGIN_ROOT / "tools" / "generated"
        if plugins_dir.is_dir():
            dirs.append(plugins_dir)
        security_dir = Path(__file__).resolve().parent / "security"
        if security_dir.is_dir():
            dirs.append(security_dir)
        return dirs

    @classmethod
    def _discover_dynamic_modules(cls) -> Dict[str, Path]:
        modules: Dict[str, Path] = {}
        seen_stems: set = set()
        for gen_dir in cls._generated_tools_dirs():
            for path in gen_dir.glob("*.py"):
                if path.name == "__init__.py" or path.name.startswith("_"):
                    continue
                if path.stem in seen_stems:
                    continue
                seen_stems.add(path.stem)
                module_name = f"_flocks_gen_{gen_dir.parent.name}_{path.stem}"
                if "security" in str(gen_dir) and "plugins" not in str(gen_dir):
                    module_name = f"flocks.tool.security.{path.stem}"
                modules[module_name] = path
        return modules

    @classmethod
    def _unregister_dynamic_tools(cls, module_name: str) -> None:
        old_tools = cls._dynamic_tools_by_module.get(module_name, [])
        if not old_tools:
            return
        for tool_name in old_tools:
            cls._tools.pop(tool_name, None)
            # Mirror ``_unregister_plugin_tools``: the factory-default
            # snapshot lives in lock-step with ``_tools``.  Reload of the
            # same module re-registers via ``register()`` which overwrites
            # the entry, but when a dynamic module is deleted entirely
            # (the ``module_name not in modules`` branch of
            # ``_register_dynamic_tools``) nothing re-adds it, so without
            # this pop a stale factory default would linger forever.
            cls._enabled_defaults.pop(tool_name, None)
        log.info("tool.dynamic.unregistered", {
            "module": module_name,
            "tools": old_tools,
        })

    @classmethod
    def _register_dynamic_tools(cls) -> None:
        """Register dynamically generated tools by importing modules."""
        modules = cls._discover_dynamic_modules()

        # Remove tools for modules that no longer exist
        for module_name in list(cls._dynamic_tools_by_module.keys()):
            if module_name not in modules:
                cls._unregister_dynamic_tools(module_name)
                cls._dynamic_tools_by_module.pop(module_name, None)
                cls._dynamic_modules.pop(module_name, None)

        for module_name, path in modules.items():
            cls._unregister_dynamic_tools(module_name)
            before = set(cls._tools.keys())
            try:
                if module_name in sys.modules:
                    importlib.reload(sys.modules[module_name])
                    action = "reloaded"
                else:
                    importlib.import_module(module_name)
                    action = "imported"
            except Exception as e:
                log.warn("tool.dynamic.import_failed", {
                    "module": module_name,
                    "error": str(e),
                })
                continue

            after = set(cls._tools.keys())
            new_tools = sorted(after - before)
            cls._dynamic_modules[module_name] = str(path)
            cls._dynamic_tools_by_module[module_name] = new_tools
            log.debug("tool.dynamic.registered", {
                "module": module_name,
                "tools": new_tools,
                "action": action,
            })


# ---------------------------------------------------------------------------
# Tool File Watcher
# ---------------------------------------------------------------------------


class ToolFileWatcher:
    """Watch plugin tool directories and auto-reload plugin tools on change.

    Monitors the ``api/`` and ``python/`` subdirectories under:
    - ``~/.flocks/plugins/tools/``       (user-level)
    - ``<cwd>/.flocks/plugins/tools/``   (project-level)

    Triggers on ``*.yaml`` or ``*.py`` file changes with a 1.0 s debounce.
    Does **not** watch ``generated/`` or ``mcp/`` — those subdirectories have
    their own dedicated reload mechanisms.
    """

    _DEBOUNCE_SECONDS = 1.0
    _WATCH_SUBDIRS = ("api", "python")

    def __init__(self) -> None:
        self._observer: Optional[object] = None
        self._debounce_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ---- public ----

    def start(self) -> None:
        if self._observer is not None:
            return

        try:
            from watchdog.events import FileSystemEvent, FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            log.warn(
                "tool.watcher.watchdog_missing",
                {"msg": "watchdog not installed, tool file watcher disabled"},
            )
            return

        # Capture the running event loop so _do_refresh can safely schedule
        # back onto it from the watchdog daemon thread.
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = None

        watch_dirs = self._collect_watch_dirs()
        if not watch_dirs:
            log.info("tool.watcher.no_dirs", {"msg": "no tool plugin directories to watch"})
            return

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event: FileSystemEvent) -> None:
                if event.is_directory:
                    return
                src = getattr(event, "src_path", "") or ""
                if src.endswith(".yaml") or src.endswith(".py"):
                    watcher._schedule_refresh()

        handler = _Handler()
        observer = Observer()
        for d in watch_dirs:
            try:
                observer.schedule(handler, d, recursive=True)
                log.debug("tool.watcher.watching", {"directory": d})
            except Exception as e:
                log.warn("tool.watcher.schedule_error", {"directory": d, "error": str(e)})

        observer.daemon = True
        observer.start()
        self._observer = observer
        log.info("tool.watcher.started", {"directories": sorted(watch_dirs)})

    def stop(self) -> None:
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
        if self._observer is not None:
            try:
                self._observer.stop()  # type: ignore[union-attr]
                self._observer.join(timeout=2)  # type: ignore[union-attr]
            except Exception:
                pass
            self._observer = None
            log.info("tool.watcher.stopped")

    # ---- internal ----

    def _schedule_refresh(self) -> None:
        """Debounced plugin tool reload."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(
                self._DEBOUNCE_SECONDS, self._do_refresh
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _do_refresh(self) -> None:
        # Schedule onto the asyncio event loop thread to avoid concurrent
        # modification of _tools while request handlers may be reading it.
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._run_refresh)
        else:
            self._run_refresh()

    def _run_refresh(self) -> None:
        try:
            ToolRegistry.refresh_plugin_tools()
            log.info("tool.watcher.reloaded", {"reason": "plugin tool file changed on disk"})
        except Exception as e:
            log.warn("tool.watcher.reload_failed", {"error": str(e)})

    def _collect_watch_dirs(self) -> Set[str]:
        """Return the api/ and python/ subdirectories that exist and should be watched."""
        dirs: Set[str] = set()
        try:
            from flocks.plugin.loader import DEFAULT_PLUGIN_ROOT
            tools_root = DEFAULT_PLUGIN_ROOT / "tools"
        except Exception:
            tools_root = Path.home() / ".flocks" / "plugins" / "tools"

        for subdir in self._WATCH_SUBDIRS:
            d = str(tools_root / subdir)
            if os.path.isdir(d):
                dirs.add(d)

        try:
            project_tools_root = Path.cwd() / ".flocks" / "plugins" / "tools"
            for subdir in self._WATCH_SUBDIRS:
                d = str(project_tools_root / subdir)
                if d not in dirs and os.path.isdir(d):
                    dirs.add(d)
        except Exception:
            pass

        return dirs
