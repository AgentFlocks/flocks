"""
Configuration management

Provides complete configuration system with TypeScript implementation compatibility.
Ensures compatibility between Python and TypeScript services.
"""

from __future__ import annotations

import os
import re
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, Literal
from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings

# ==================== Permission System ====================

class PermissionAction(str, Enum):
    """Permission action types"""
    ASK = "ask"
    ALLOW = "allow"
    DENY = "deny"


# Permission can be either a simple action or a nested dict of rules
PermissionRule = Union[PermissionAction, Dict[str, Union[PermissionAction, Dict[str, PermissionAction]]]]


class PermissionConfig(BaseModel):
    """Permission configuration (simplified for Phase 1-3)"""
    model_config = {"extra": "allow"}  # Allow additional fields
    
    read: Optional[PermissionRule] = None
    edit: Optional[PermissionRule] = None
    glob: Optional[PermissionRule] = None
    grep: Optional[PermissionRule] = None
    list: Optional[PermissionRule] = None
    bash: Optional[PermissionRule] = None
    task: Optional[PermissionRule] = None
    external_directory: Optional[PermissionRule] = None
    todowrite: Optional[PermissionAction] = None
    todoread: Optional[PermissionAction] = None
    question: Optional[PermissionAction] = None
    webfetch: Optional[PermissionAction] = None
    websearch: Optional[PermissionAction] = None
    codesearch: Optional[PermissionAction] = None
    lsp: Optional[PermissionRule] = None
    doom_loop: Optional[PermissionAction] = None
    delegate_task: Optional[PermissionRule] = None
    call_omo_agent: Optional[PermissionRule] = None
    background_output: Optional[PermissionRule] = None
    background_cancel: Optional[PermissionRule] = None


# ==================== Agent Configuration ====================

class AgentConfig(BaseModel):
    """
    Agent configuration
    
    Matches TypeScript Agent schema exactly.
    """
    model_config = {"extra": "allow", "populate_by_name": True}  # Allow unknown fields and populate by alias
    
    name: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    prompt: Optional[str] = None
    prompt_append: Optional[str] = Field(None, alias="promptAppend")
    description: Optional[str] = Field(None, description="Description of when to use the agent")
    description_cn: Optional[str] = Field(
        None,
        description="Chinese UI description; English description is used for delegation",
    )
    mode: Optional[Literal["subagent", "primary", "all"]] = None
    hidden: Optional[bool] = Field(None, description="Hide from autocomplete (subagent only)")
    color: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    steps: Optional[int] = Field(None, gt=0, description="Max agentic iterations")
    max_steps: Optional[int] = Field(None, gt=0, description="@deprecated Use 'steps'", alias="maxSteps")
    options: Dict[str, Any] = Field(default_factory=dict)
    permission: Optional[Union[PermissionConfig, Dict[str, Any]]] = None
    disable: Optional[bool] = None
    delegatable: Optional[bool] = Field(None, description="Whether this agent can be called via delegate_task")
    strategy: Optional[Literal["react", "plan_and_execute", "read_only", "explore"]] = None
    tools: Optional[Dict[str, bool]] = Field(None, description="@deprecated Use 'permission'")
    
    @model_validator(mode='after')
    def process_agent(self):
        """Post-processing like TypeScript transform"""
        # Convert legacy maxSteps to steps
        if self.steps is None and self.max_steps is not None:
            self.steps = self.max_steps
        
        # Convert legacy tools to permission
        if self.tools:
            if self.permission is None:
                self.permission = {}
            
            permission_dict = self.permission if isinstance(self.permission, dict) else {}
            
            for tool, enabled in self.tools.items():
                action = PermissionAction.ALLOW if enabled else PermissionAction.DENY
                # Map write/edit/patch/multiedit to edit
                if tool in ["write", "edit", "patch", "multiedit"]:
                    permission_dict["edit"] = action
                else:
                    permission_dict[tool] = action
            
            self.permission = permission_dict
        
        return self


# ==================== Category Configuration ====================

class CategoryConfig(BaseModel):
    """Delegate-task category configuration"""
    model_config = {"extra": "allow", "populate_by_name": True}

    model: Optional[str] = None
    variant: Optional[str] = None
    prompt_append: Optional[str] = Field(None, alias="promptAppend")
    description: Optional[str] = None
    is_unstable_agent: Optional[bool] = Field(None, alias="isUnstableAgent")


# ==================== Command Configuration ====================

class CommandConfig(BaseModel):
    """Command configuration"""
    name: Optional[str] = None
    template: str
    description: Optional[str] = None
    agent: Optional[str] = None
    model: Optional[str] = None
    subtask: Optional[bool] = None


# ==================== Provider Configuration ====================

class ProviderOptionsConfig(BaseModel):
    """Provider options"""
    model_config = {"extra": "allow", "populate_by_name": True}
    
    api_key: Optional[str] = Field(None, alias="apiKey")
    base_url: Optional[str] = Field(None, alias="baseURL")
    enterprise_url: Optional[str] = Field(None, alias="enterpriseUrl")
    set_cache_key: Optional[bool] = Field(None, alias="setCacheKey")
    timeout: Optional[Union[int, Literal[False]]] = None


class ModelVariantConfig(BaseModel):
    """Model variant configuration"""
    model_config = {"extra": "allow"}
    
    disabled: Optional[bool] = None


class ModelConfig(BaseModel):
    """Model configuration"""
    model_config = {"extra": "allow"}
    
    variants: Optional[Dict[str, ModelVariantConfig]] = None


class ProviderConfig(BaseModel):
    """Provider configuration"""
    model_config = {"extra": "allow"}
    
    whitelist: Optional[List[str]] = None
    blacklist: Optional[List[str]] = None
    models: Optional[Dict[str, ModelConfig]] = None
    options: Optional[ProviderOptionsConfig] = None


# ==================== MCP Configuration ====================

class McpOAuthConfig(BaseModel):
    """MCP OAuth configuration"""
    model_config = {"populate_by_name": True}
    
    client_id: Optional[str] = Field(None, alias="clientId")
    client_secret: Optional[str] = Field(None, alias="clientSecret")
    scope: Optional[str] = None


class McpLocalConfig(BaseModel):
    """MCP local server configuration"""
    model_config = {"extra": "allow"}
    
    type: Literal["local"]
    command: List[str]
    environment: Optional[Dict[str, str]] = None
    enabled: Optional[bool] = None
    timeout: Optional[int] = Field(None, gt=0)


class McpRemoteConfig(BaseModel):
    """MCP remote server configuration"""
    model_config = {"extra": "allow"}
    
    type: Literal["remote", "sse"]
    url: str
    enabled: Optional[bool] = None
    headers: Optional[Dict[str, str]] = None
    oauth: Optional[Union[McpOAuthConfig, Literal[False]]] = None
    timeout: Optional[int] = Field(None, gt=0)


McpConfig = Union[McpLocalConfig, McpRemoteConfig]


# ==================== Keybinds Configuration ====================

class KeybindsConfig(BaseModel):
    """Keybinds configuration"""
    model_config = {"extra": "allow"}
    
    leader: str = "ctrl+x"
    app_exit: str = "ctrl+c,ctrl+d,<leader>q"
    editor_open: str = "<leader>e"
    theme_list: str = "<leader>t"
    sidebar_toggle: str = "<leader>b"
    scrollbar_toggle: str = "none"
    username_toggle: str = "none"
    status_view: str = "<leader>s"
    session_export: str = "<leader>x"
    session_new: str = "<leader>n"
    session_list: str = "<leader>l"
    session_timeline: str = "<leader>g"
    session_fork: str = "none"
    session_rename: str = "ctrl+r"
    session_delete: str = "ctrl+d"
    # ... Add more as needed


# ==================== Other Configuration ====================

class TuiConfig(BaseModel):
    """TUI configuration"""
    scroll_speed: Optional[float] = Field(None, ge=0.001)
    scroll_acceleration: Optional[Dict[str, Any]] = None
    diff_style: Optional[Literal["auto", "stacked"]] = None


class ServerConfig(BaseModel):
    """Server configuration"""
    port: Optional[int] = Field(None, gt=0)
    hostname: Optional[str] = None
    mdns: Optional[bool] = None
    cors: Optional[List[str]] = None


class WatcherConfig(BaseModel):
    """File watcher configuration"""
    ignore: Optional[List[str]] = None


class CompactionConfig(BaseModel):
    """Compaction configuration"""
    auto: Optional[bool] = Field(None, description="Enable auto compaction")
    prune: Optional[bool] = Field(None, description="Enable pruning")


class EnterpriseConfig(BaseModel):
    """Enterprise configuration"""
    url: Optional[str] = None


class ExperimentalConfig(BaseModel):
    """Experimental features configuration"""
    model_config = {"extra": "allow", "populate_by_name": True}
    
    chat_max_retries: Optional[int] = Field(None, alias="chatMaxRetries")
    disable_paste_summary: Optional[bool] = None
    batch_tool: Optional[bool] = None
    open_telemetry: Optional[bool] = Field(None, alias="openTelemetry")
    primary_tools: Optional[List[str]] = None
    continue_loop_on_deny: Optional[bool] = None
    mcp_timeout: Optional[int] = Field(None, gt=0)


# ==================== Updater Configuration ====================

class UpdaterConfig(BaseModel):
    """Self-update configuration"""
    model_config = {"extra": "allow"}

    enabled: bool = Field(
        True,
        description="Set to false to disable update checks entirely.",
    )
    repo: str = Field(
        "AgentFlocks/Flocks",
        description="Repository path in 'owner/repo' format (used for GitHub / GitLab).",
    )
    gitee_repo: Optional[str] = Field(
        "flocks/flocks",
        description="Repository path on Gitee if different from 'repo'. Falls back to 'repo' if unset.",
    )
    sources: List[str] = Field(
        ["github", "gitee"],
        description=(
            "Ordered list of download sources to try. "
            "Supported values: 'github', 'gitee', 'gitlab'. "
            "The updater tries each in order; on failure it falls back to the next."
        ),
    )
    token: Optional[str] = Field(
        None,
        description=(
            "Personal access token for GitHub / GitLab. "
            "Supports {secret:name} syntax, e.g. '{secret:github_token}'."
        ),
    )
    gitee_token: Optional[str] = Field(
        None,
        description=(
            "Personal access token for Gitee (if the repo is private on Gitee). "
            "Supports {secret:name} syntax."
        ),
    )
    archive_format: Literal["auto", "zip", "tar.gz"] = Field(
        "auto",
        description="Source archive format to download. 'auto' picks zip on Windows, tar.gz elsewhere.",
    )
    backup_retain_count: int = Field(
        1,
        description="Number of old version backups to retain under ~/.flocks/version/. Oldest are purged first.",
    )
    provider: Literal["github", "gitlab"] = Field(
        "github",
        description="(Deprecated) Use 'sources' instead. Kept for backward compatibility.",
    )
    base_url: Optional[str] = Field(
        None,
        description=(
            "Base URL override for self-hosted instances (e.g. GitLab Enterprise). "
            "Leave unset for github.com / gitee.com."
        ),
    )
    remote: str = Field(
        "origin",
        description="Git remote name (used only as last-resort fallback).",
    )


# ==================== Channel Configuration ====================

class ChannelAccountConfig(BaseModel):
    """Configuration for a single channel account (multi-account support)."""
    model_config = {"extra": "allow"}

    enabled: bool = True
    name: Optional[str] = None


class FeishuGroupConfig(BaseModel):
    """Per-group configuration for Feishu channel.

    All fields are Optional; missing fields inherit from top-level channel config.

    Example flocks.json::

        "channels": {
          "feishu": {
            "groups": {
              "oc_security_team": {
                "requireMention": false,
                "systemPrompt": "你是安全团队专属助手。",
                "groupSessionScope": "group_sender"
              },
              "oc_readonly": { "enabled": false },
              "*": { "requireMention": true }
            }
          }
        }
    """
    model_config = {"extra": "allow", "populate_by_name": True}

    enabled: Optional[bool] = None
    require_mention: Optional[bool] = Field(None, alias="requireMention")
    allow_from: Optional[List[str]] = Field(None, alias="allowFrom")
    system_prompt: Optional[str] = Field(None, alias="systemPrompt")
    default_agent: Optional[str] = Field(None, alias="defaultAgent")
    group_session_scope: Optional[Literal[
        "group",
        "group_sender",
        "group_topic",
    ]] = Field(None, alias="groupSessionScope")
    mention_context_messages: Optional[int] = Field(None, alias="mentionContextMessages")


class ChannelConfig(BaseModel):
    """Per-channel configuration block in flocks.json.

    Platform-specific fields (appId, appSecret, …) are captured via
    ``extra = "allow"`` and can be accessed with ``get_extra()``.

    Feishu-specific fields (新增)：
    - ``inboundDebounceMs``: 消息防抖窗口（毫秒），0=禁用，默认 800
    - ``dedupTtlSeconds``: 去重 TTL（秒），默认 86400（24h）
    - ``reactionNotifications``: Emoji 响应策略 off/own/all，默认 off
    - ``streaming``: 是否启用流式卡片输出，默认 False
    - ``streamingCoalesceMs``: 流式节流窗口（毫秒），默认 200
    - ``groups``: 按 chat_id 的细粒度群组配置
    - ``mentionContextMessages``: 群聊最近未@消息缓存条数，0=禁用
    """
    model_config = {"extra": "allow", "populate_by_name": True}

    enabled: bool = False
    default_agent: Optional[str] = Field(None, alias="defaultAgent")
    dm_policy: Optional[str] = Field(None, alias="dmPolicy")
    group_trigger: Optional[str] = Field("mention", alias="groupTrigger")
    allow_from: Optional[List[str]] = Field(None, alias="allowFrom")
    accounts: Optional[Dict[str, ChannelAccountConfig]] = None
    workspace_dir: Optional[str] = Field(
        None,
        alias="workspaceDir",
        description=(
            "Working directory used when this channel auto-creates a Flocks session. "
            "If unset, falls back to the active project Instance directory, then to "
            "the server process cwd. Aligns channel sessions with WebUI sessions so "
            "that <env>, AGENTS.md / CLAUDE.md / CONTEXT.md and sandbox prompts are "
            "the same on both entry points."
        ),
    )

    # ── Feishu 新增字段 ──────────────────────────────────────────────
    inbound_debounce_ms: Optional[int] = Field(
        800, alias="inboundDebounceMs", ge=0,
        description="消息防抖窗口（毫秒），0=禁用",
    )
    dedup_ttl_seconds: Optional[int] = Field(
        86400, alias="dedupTtlSeconds", ge=60,
        description="去重 TTL（秒），默认 86400（24h）",
    )
    reaction_notifications: Optional[Literal["off", "own", "all"]] = Field(
        "off", alias="reactionNotifications",
        description="Emoji Reaction 响应策略",
    )
    streaming: Optional[bool] = Field(
        False,
        description="是否启用流式卡片输出（需要 cardkit:card:write 权限）",
    )
    streaming_coalesce_ms: Optional[int] = Field(
        200, alias="streamingCoalesceMs", ge=0,
        description="流式卡片追加节流窗口（毫秒）",
    )
    groups: Optional[Dict[str, Optional[FeishuGroupConfig]]] = Field(
        None,
        description="按 chat_id 的细粒度群组配置，支持通配符 '*'",
    )
    mention_context_messages: Optional[int] = Field(
        0,
        alias="mentionContextMessages",
        ge=0,
        description="群聊最近未@消息缓存条数，0=禁用",
    )

    def get_extra(self, key: str, default: Any = None) -> Any:
        """Safely read a platform-specific extra field."""
        if hasattr(self, key):
            val = getattr(self, key)
            return val if val is not None else default
        return default


# ==================== Main Configuration ====================

class ConfigInfo(BaseModel):
    """
    Main configuration schema
    
    Matches TypeScript Config.Info exactly.
    """
    model_config = {"extra": "allow", "populate_by_name": True}  # Allow extra fields for flexibility
    
    schema_: Optional[str] = Field(None, alias="$schema")
    theme: Optional[str] = None
    keybinds: Optional[KeybindsConfig] = None
    log_level: Optional[str] = Field(None, alias="logLevel")
    tui: Optional[TuiConfig] = None
    server: Optional[ServerConfig] = None
    command: Optional[Dict[str, CommandConfig]] = None
    watcher: Optional[WatcherConfig] = None
    plugin: Optional[List[str]] = Field(
        None,
        description=(
            "Extra plugin module sources (file paths or package names). "
            "Loaded by PluginLoader and dispatched to all registered extension "
            "points (agents, tools, hooks, etc.)."
        ),
    )
    snapshot: Optional[bool] = None
    share: Optional[Literal["manual", "auto", "disabled"]] = None
    autoshare: Optional[bool] = Field(None, description="@deprecated Use 'share'")
    autoupdate: Optional[Union[bool, Literal["notify"]]] = None
    disabled_providers: Optional[List[str]] = None
    enabled_providers: Optional[List[str]] = None
    model: Optional[str] = None
    small_model: Optional[str] = Field(None, alias="smallModel")
    default_agent: Optional[str] = Field(None, alias="defaultAgent")
    username: Optional[str] = None
    mode: Optional[Dict[str, AgentConfig]] = Field(None, description="@deprecated Use 'agent'")
    agent: Optional[Dict[str, AgentConfig]] = None
    provider: Optional[Dict[str, ProviderConfig]] = None
    categories: Optional[Dict[str, CategoryConfig]] = None
    mcp: Optional[Dict[str, Union[McpConfig, Dict[str, Any]]]] = None
    formatter: Optional[Union[Literal[False], Dict[str, Any]]] = None
    lsp: Optional[Union[Literal[False], Dict[str, Any]]] = None
    instructions: Optional[List[str]] = None
    layout: Optional[Literal["auto", "stretch"]] = Field(None, description="@deprecated")
    permission: Optional[Union[PermissionConfig, Dict[str, Any]]] = None
    tools: Optional[Dict[str, bool]] = Field(None, description="@deprecated Use 'permission'")
    enabled_agents: Optional[List[str]] = Field(
        None, alias="enabledAgents",
        description=(
            "Whitelist of agent names to load. When set, only listed agents "
            "enter the registry. Unset means all built-in agents are active."
        ),
    )
    agent_logic: Optional[Literal["base", "rex"]] = Field(None, alias="agentLogic")
    enterprise: Optional[EnterpriseConfig] = None
    compaction: Optional[CompactionConfig] = None
    experimental: Optional[ExperimentalConfig] = None
    
    # Memory system configuration (added for memory system integration)
    memory: Optional[Any] = Field(
        None,
        description="Memory system configuration"
    )
    
    # Sandbox configuration (对齐 OpenClaw agents.defaults.sandbox)
    sandbox: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Sandbox configuration. Controls Docker container isolation for tool execution. "
            "Keys: mode (off/on), scope (session/agent/shared), "
            "workspace_access (none/ro/rw), workspace_root, docker, tools, prune."
        ),
    )
    allow_read_paths: Optional[List[str]] = Field(
        None,
        alias="allowReadPaths",
        description=(
            "Extra absolute paths (directories or prefixes) allowed for HTTP "
            "/api/file/content and /api/file/list. Does not replace project root, data, or "
            "workspace; the Flocks config directory and ~/.ssh are never allowed."
        ),
    )

    # Channel configuration (IM platform integrations)
    channels: Optional[Dict[str, ChannelConfig]] = Field(
        None,
        description=(
            "Channel configuration. key is channel id (feishu, wecom, discord, …). "
            "Each value is a ChannelConfig with enabled, defaultAgent, "
            "dmPolicy, groupTrigger, allowFrom, plus platform-specific fields."
        ),
    )

    # Updater configuration
    updater: Optional[UpdaterConfig] = Field(
        None,
        description="Self-update configuration (GitHub repo, git remote, etc.)",
    )

    @model_validator(mode='after')
    def post_process(self):
        """Post-processing like TypeScript"""
        # Initialize defaults
        if self.agent is None:
            self.agent = {}
        if self.plugin is None:
            self.plugin = []
        
        # Parse memory configuration
        if self.memory is not None and isinstance(self.memory, dict):
            try:
                from flocks.memory.config import MemoryConfig
                self.memory = MemoryConfig(**self.memory)
            except Exception as e:
                from flocks.utils.log import Log
                log = Log.create(service="config")
                log.error("config.memory.parse_failed", {"error": str(e)})
                self.memory = None
        
        # Pydantic auto-parses channels dicts → ChannelConfig; no manual step needed.
        
        # Migrate mode to agent (deprecated field)
        if self.mode:
            for name, mode_config in self.mode.items():
                if name not in self.agent:
                    mode_config.mode = "primary"
                    self.agent[name] = mode_config
        
        # Handle autoshare -> share migration
        if self.autoshare is True and self.share is None:
            self.share = "auto"
        
        # Convert legacy tools to permission
        if self.tools:
            if self.permission is None:
                self.permission = {}
            
            permission_dict = self.permission if isinstance(self.permission, dict) else {}
            
            for tool, enabled in self.tools.items():
                action = PermissionAction.ALLOW if enabled else PermissionAction.DENY
                if tool in ["write", "edit", "patch", "multiedit"]:
                    permission_dict["edit"] = action
                else:
                    permission_dict[tool] = action
            
            self.permission = permission_dict
        
        # Set default username
        if self.username is None:
            import getpass
            self.username = getpass.getuser()
        
        # Initialize keybinds with defaults
        if self.keybinds is None:
            self.keybinds = KeybindsConfig()
        
        return self

    def get_channel_configs(self) -> Dict[str, ChannelConfig]:
        """Return the channels dict, empty dict if unset."""
        return self.channels or {}

    def get_channel_config(self, channel_id: str) -> ChannelConfig:
        """Return the ChannelConfig for *channel_id*, or a default instance."""
        return (self.channels or {}).get(channel_id, ChannelConfig())


# ==================== Global Configuration ====================

def _get_flocks_root() -> Path:
    """
    Get flocks root directory
    
    Priority:
    1. FLOCKS_ROOT environment variable
    2. ~/.flocks

    Returns the root without /config suffix.
    """
    # Priority 1: Explicit override
    root = os.getenv("FLOCKS_ROOT")
    if root:
        return Path(root)
    
    # Default: ~/.flocks
    return Path.home() / ".flocks"


def _get_config_dir() -> Path:
    """
    Get config directory
    
    Priority:
    1. FLOCKS_CONFIG_DIR environment variable
    2. Use flocks root/config
    """
    config_dir = os.getenv("FLOCKS_CONFIG_DIR")
    if config_dir:
        return Path(config_dir)
    return _get_flocks_root() / "config"


def _get_data_dir() -> Path:
    """
    Get data directory (under flocks root)
    
    Priority:
    1. FLOCKS_DATA_DIR environment variable
    2. XDG_DATA_HOME/flocks (if XDG_DATA_HOME is set)
    3. flocks_root/data (default)
    
    Can be overridden by FLOCKS_DATA_DIR environment variable.
    """
    data_dir = os.getenv("FLOCKS_DATA_DIR")
    if data_dir:
        return Path(data_dir)
    
    # Check XDG_DATA_HOME for data directory
    xdg_data = os.getenv("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "flocks"
    
    return _get_flocks_root() / "data"


def _get_log_dir() -> Path:
    """
    Get log directory (under flocks root)
    
    Priority:
    1. FLOCKS_LOG_DIR environment variable
    2. XDG_STATE_HOME/flocks/logs (if XDG_STATE_HOME is set)
    3. flocks_root/logs (default)
    
    Can be overridden by FLOCKS_LOG_DIR environment variable.
    """
    log_dir = os.getenv("FLOCKS_LOG_DIR")
    if log_dir:
        return Path(log_dir)
    
    # Check XDG_STATE_HOME for log directory
    xdg_state = os.getenv("XDG_STATE_HOME")
    if xdg_state:
        return Path(xdg_state) / "flocks" / "logs"
    
    return _get_flocks_root() / "logs"


class GlobalConfig(BaseSettings):
    """
    Global configuration from environment variables
    
    Similar to TypeScript Flag namespace.
    All data unified under ~/.flocks directory.
    """
    model_config = {"env_prefix": "FLOCKS_", "case_sensitive": False}
    
    # Paths - now using ~/.flocks by default
    config_dir: Path = Field(default_factory=_get_config_dir)
    data_dir: Path = Field(default_factory=_get_data_dir)
    log_dir: Path = Field(default_factory=_get_log_dir)
    
    # Server settings
    server_host: str = "127.0.0.1"
    server_port: int = 8000
    server_username: Optional[str] = None
    server_password: Optional[str] = None
    
    # Config overrides
    config_path: Optional[str] = None  # FLOCKS_CONFIG
    config_content: Optional[str] = None  # FLOCKS_CONFIG_CONTENT
    config_dir_override: Optional[str] = None  # FLOCKS_CONFIG_DIR
    
    # Permission overrides
    permission: Optional[str] = None  # FLOCKS_PERMISSION (JSON)
    
    # Compaction overrides
    disable_autocompact: bool = False
    disable_prune: bool = False
    
    # Development
    dev_mode: bool = False
    log_level: str = "INFO"


# ==================== Configuration Manager ====================

class Config:
    """
    Configuration manager
    
    Provides static methods for loading and managing configuration,
    matching TypeScript's Config namespace.
    """
    
    _global_config: Optional[GlobalConfig] = None
    _cached_config: Optional[ConfigInfo] = None
    
    @classmethod
    def get_global(cls) -> GlobalConfig:
        """Get global configuration from environment"""
        if cls._global_config is None:
            cls._global_config = GlobalConfig()
            cls._ensure_dirs()
        return cls._global_config
    
    @classmethod
    def _ensure_dirs(cls) -> None:
        """Ensure required directories exist"""
        config = cls.get_global()
        config.config_dir.mkdir(parents=True, exist_ok=True)
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Create memory directory structure
        memory_dir = config.data_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        
        # Create daily memory directory
        daily_dir = memory_dir / "daily"
        daily_dir.mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def merge_deep(cls, target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
        """
        Deep merge two dictionaries
        
        Matches TypeScript's mergeDeep from remeda library.
        
        Args:
            target: Target dictionary
            source: Source dictionary to merge
            
        Returns:
            Merged dictionary
        """
        result = target.copy()
        
        for key, value in source.items():
            if key in result:
                # If both are dicts, merge recursively
                if isinstance(result[key], dict) and isinstance(value, dict):
                    result[key] = cls.merge_deep(result[key], value)
                else:
                    # Otherwise, source wins
                    result[key] = value
            else:
                result[key] = value
        
        return result
    
    @classmethod
    def merge_config_concat_arrays(cls, target: ConfigInfo, source: ConfigInfo) -> ConfigInfo:
        """
        Merge two configs with array concatenation
        
        Matches TypeScript's mergeConfigConcatArrays function.
        Arrays (plugin, instructions) are concatenated instead of replaced.
        
        Args:
            target: Target config
            source: Source config to merge
            
        Returns:
            Merged config
        """
        # Convert to dicts
        target_dict = target.model_dump(by_alias=True, exclude_none=True)
        source_dict = source.model_dump(by_alias=True, exclude_none=True)
        
        # Deep merge
        merged = cls.merge_deep(target_dict, source_dict)
        
        # Special handling for arrays - concatenate instead of replace
        if target.plugin and source.plugin:
            merged["plugin"] = list(set(target.plugin + source.plugin))
        
        if target.instructions and source.instructions:
            merged["instructions"] = list(set(target.instructions + source.instructions))
        
        # Convert back to ConfigInfo
        return ConfigInfo.model_validate(merged)
    
    @classmethod
    def replace_env_vars(cls, text: str) -> str:
        """
        Replace environment variable references {env:VAR}
        
        Matches TypeScript implementation.
        
        Args:
            text: Text with {env:VAR} placeholders
            
        Returns:
            Text with environment variables substituted
        """
        def replacer(match):
            var_name = match.group(1)
            return os.getenv(var_name, "")
        
        return re.sub(r'\{env:([^}]+)\}', replacer, text)
    
    @classmethod
    def replace_secret_refs(cls, text: str) -> str:
        """
        Replace secret references {secret:SECRET_ID} with values from .secret.json
        
        Works alongside replace_env_vars(). Uses the security module's SecretManager.
        
        Args:
            text: Text with {secret:SECRET_ID} placeholders
            
        Returns:
            Text with secrets substituted
        """
        # Only import when needed to avoid circular dependencies
        if '{secret:' not in text:
            return text
        
        try:
            from flocks.security import resolve_secret_refs
            return resolve_secret_refs(text)
        except Exception as e:
            from flocks.utils.log import Log
            log = Log.create(service="config")
            log.warning("config.replace_secret_refs_failed", {"error": str(e)})
            return text
    
    @classmethod
    async def replace_file_refs(cls, text: str, config_dir: Path) -> str:
        """
        Replace file references {file:path}
        
        Matches TypeScript implementation.
        
        Args:
            text: Text with {file:path} placeholders
            config_dir: Directory containing the config file
            
        Returns:
            Text with file contents substituted
        """
        import asyncio
        
        # Find all {file:...} references
        file_matches = re.findall(r'\{file:[^}]+\}', text)
        
        if not file_matches:
            return text
        
        # Check for commented lines
        lines = text.split('\n')
        
        for match in file_matches:
            # Skip if in a comment
            for line in lines:
                if match in line and line.strip().startswith(('//','#')):
                    continue
            
            # Extract path
            file_path_str = match.replace('{file:', '').replace('}', '')
            
            # Handle ~ expansion
            if file_path_str.startswith('~/'):
                file_path = Path.home() / file_path_str[2:]
            elif Path(file_path_str).is_absolute():
                file_path = Path(file_path_str)
            else:
                file_path = config_dir / file_path_str
            
            # Read file content
            try:
                file_content = file_path.read_text(encoding="utf-8").strip()
                # Escape for JSON (remove outer quotes from json.dumps)
                json_escaped = json.dumps(file_content)[1:-1]
                text = text.replace(match, json_escaped)
            except FileNotFoundError:
                raise ValueError(f"File reference {match} not found: {file_path}")
            except Exception as e:
                raise ValueError(f"Error reading file reference {match}: {e}")
        
        return text
    
    @classmethod
    async def load_file(cls, filepath: Path) -> ConfigInfo:
        """
        Load configuration from a file
        
        Supports JSON and JSONC (JSON with comments).
        Matches TypeScript's loadFile function.
        
        Args:
            filepath: Path to config file
            
        Returns:
            ConfigInfo instance
        """
        from flocks.utils.log import Log
        log = Log.create(service="config")
        
        log.info("loading", {"path": str(filepath)})
        
        try:
            text = filepath.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ConfigInfo()
        except Exception as e:
            raise ValueError(f"Failed to read config file {filepath}: {e}")
        
        return await cls.load_text(text, filepath)
    
    @classmethod
    async def load_text(cls, text: str, filepath: Path) -> ConfigInfo:
        """
        Load configuration from text
        
        Args:
            text: Config text (JSON/JSONC)
            filepath: Source file path (for error messages and relative paths)
            
        Returns:
            ConfigInfo instance
        """
        original = text
        
        # Replace environment variables
        text = cls.replace_env_vars(text)
        
        # Replace secret references {secret:SECRET_ID}
        text = cls.replace_secret_refs(text)
        
        # Replace file references
        text = await cls.replace_file_refs(text, filepath.parent)
        
        # Try to parse as JSONC (JSON with comments)
        try:
            # Remove comments properly
            # 1. Remove /* */ block comments first
            text_no_comments = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
            
            # 2. Remove // line comments, but NOT in strings!
            # We need to be careful not to remove // inside quoted strings (like URLs)
            # This regex matches // that are NOT inside quotes
            # Negative lookbehind to avoid matching inside strings
            lines = text_no_comments.split('\n')
            cleaned_lines = []
            for line in lines:
                # Find // but not inside strings
                # Simple approach: find first // that is not between quotes
                in_string = False
                escape_next = False
                comment_start = -1
                
                for i, char in enumerate(line):
                    if escape_next:
                        escape_next = False
                        continue
                    
                    if char == '\\':
                        escape_next = True
                        continue
                    
                    if char == '"' and not escape_next:
                        in_string = not in_string
                    
                    if not in_string and i < len(line) - 1 and line[i:i+2] == '//':
                        comment_start = i
                        break
                
                if comment_start >= 0:
                    line = line[:comment_start]
                
                cleaned_lines.append(line)
            
            text_no_comments = '\n'.join(cleaned_lines)
            
            # Parse JSON
            data = json.loads(text_no_comments)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {filepath}: {e}")
        
        # Validate and parse with Pydantic
        try:
            config = ConfigInfo.model_validate(data)
        except Exception as e:
            raise ValueError(f"Invalid configuration in {filepath}: {e}")
        
        # Schema field is optional and not required
        
        return config
    
    @classmethod
    async def load_global_config(cls) -> ConfigInfo:
        """
        Load global user configuration
        
        Load configuration from the unified user config directory.
        
        Returns:
            ConfigInfo with global settings
        """
        global_cfg = cls.get_global()
        result = ConfigInfo()
        
        # Try multiple file names in the unified config directory.
        for filename in ["flocks.json", "flocks.jsonc", "config.json"]:
            filepath = global_cfg.config_dir / filename
            if filepath.exists():
                loaded = await cls.load_file(filepath)
                result = cls.merge_config_concat_arrays(result, loaded)
        
        return result
    
    @classmethod
    async def get(cls) -> ConfigInfo:
        """
        Get complete configuration with all layers merged
        
        Matches TypeScript's Config.get() and state() functions.
        
        Merge order (low to high priority):
        1. User config (~/.flocks/config)
        2. Custom config path (FLOCKS_CONFIG env var)
        3. Inline config (FLOCKS_CONFIG_CONTENT env var)
        
        Returns:
            Complete merged configuration
        """
        if cls._cached_config is not None:
            return cls._cached_config
        
        global_cfg = cls.get_global()
        result = ConfigInfo()
        
        # 1. Global user config
        result = cls.merge_config_concat_arrays(result, await cls.load_global_config())
        
        # 2. Custom config path
        if global_cfg.config_path:
            custom_path = Path(global_cfg.config_path)
            if custom_path.exists():
                result = cls.merge_config_concat_arrays(
                    result,
                    await cls.load_file(custom_path)
                )
        
        # 3. Inline config content
        if global_cfg.config_content:
            try:
                inline_data = json.loads(global_cfg.config_content)
                inline_config = ConfigInfo.model_validate(inline_data)
                result = cls.merge_config_concat_arrays(result, inline_config)
            except Exception as e:
                from flocks.utils.log import Log
                log = Log.create(service="config")
                log.error("Failed to parse FLOCKS_CONFIG_CONTENT", {"error": str(e)})
        
        # Apply flag overrides
        if global_cfg.disable_autocompact:
            if result.compaction is None:
                result.compaction = CompactionConfig()
            result.compaction.auto = False
        
        if global_cfg.disable_prune:
            if result.compaction is None:
                result.compaction = CompactionConfig()
            result.compaction.prune = False
        
        if global_cfg.permission:
            try:
                permission_data = json.loads(global_cfg.permission)
                if result.permission is None:
                    result.permission = {}
                if isinstance(result.permission, dict):
                    result.permission = cls.merge_deep(result.permission, permission_data)
            except Exception:
                pass
        
        cls._cached_config = result
        return result
    
    @classmethod
    async def resolve_default_llm(cls) -> Optional[Dict[str, str]]:
        """
        Resolve the default LLM model from configuration.
        
        Priority:
        1. default_models.llm in flocks.json (structured, preferred)
        2. config.model in flocks.json (legacy "provider/model" string, fallback)
        
        Returns:
            Dict with "provider_id" and "model_id", or None if not configured.
        """
        # Priority 1: default_models.llm (structured config)
        try:
            from flocks.config.config_writer import ConfigWriter
            default_llm = ConfigWriter.get_default_model("llm")
            if default_llm and default_llm.get("provider_id") and default_llm.get("model_id"):
                return {
                    "provider_id": default_llm["provider_id"],
                    "model_id": default_llm["model_id"],
                }
        except Exception:
            pass
        
        # Priority 2: config.model (legacy string "provider/model")
        try:
            cfg = await cls.get()
            if cfg.model:
                model_str = cfg.model
                if "/" in model_str:
                    provider_id, model_id = model_str.split("/", 1)
                    provider_id = provider_id.strip()
                    model_id = model_id.strip()
                    if provider_id and model_id:
                        return {
                            "provider_id": provider_id,
                            "model_id": model_id,
                        }
        except Exception:
            pass
        
        return None

    @classmethod
    async def update(cls, config: ConfigInfo, project_dir: Optional[Path] = None) -> None:
        """
        Update configuration
        
        Args:
            config: New configuration
            project_dir: Deprecated and ignored. Config is always written to
                the unified user config directory.
        """
        _ = project_dir

        config_file = cls.get_config_file()
        
        # Ensure parent directory exists
        config_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing
        existing = await cls.load_file(config_file) if config_file.exists() else ConfigInfo()
        
        # Merge
        merged = cls.merge_config_concat_arrays(existing, config)
        
        # Write
        config_data = merged.model_dump(by_alias=True, exclude_none=True, mode="json")
        config_file.write_text(json.dumps(config_data, indent=2), encoding="utf-8")
        
        # Clear cache
        cls._cached_config = None
    
    @classmethod
    def clear_cache(cls) -> None:
        """Clear cached configuration"""
        cls._cached_config = None
    
    @classmethod
    def get_data_path(cls) -> Path:
        """
        Get data directory path
        
        Returns:
            Path to data directory
        """
        global_cfg = cls.get_global()
        return global_cfg.data_dir
    
    @classmethod
    def get_config_path(cls) -> Path:
        """
        Get config directory path
        
        Returns:
            Path to config directory
        """
        global_cfg = cls.get_global()
        return global_cfg.config_dir

    @classmethod
    def get_config_file(cls) -> Path:
        """Get the primary flocks.json file path."""
        return cls.get_config_path() / "flocks.json"

    @classmethod
    def get_secret_file(cls) -> Path:
        """Get the primary .secret.json file path."""
        return cls.get_config_path() / ".secret.json"

    @classmethod
    def get_mcp_catalog_file(cls) -> Path:
        """Get the primary mcp_list.json file path."""
        return cls.get_config_path() / "mcp_list.json"
    
    @classmethod
    def get_log_path(cls) -> Path:
        """
        Get log directory path
        
        Returns:
            Path to log directory
        """
        global_cfg = cls.get_global()
        return global_cfg.log_dir
    
    @classmethod
    def get_project_config_path(cls) -> Path:
        """
        Compatibility wrapper for callers that still expect a config file path.
        """
        return cls.get_config_file()
