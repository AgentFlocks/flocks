"""
Agent Registry — simplified 4-step loader.

Replaces the old flocks.agent.core.registry module (kept as a shim).

Loading order:
  ① Collect context: tools, skills, categories
  ② scan_and_load() — built-in YAML agents + plugin YAML agents
  ③ Python plugin agents via PluginLoader + cfg.agent user overrides
  ④ inject_dynamic_prompts() — phase-2 dynamic prompt injection

Merged from flocks.agent.metadata:
  is_delegatable(), get_agent_mode(), is_hidden(),
  list_delegatable_agents(), list_primary_agents(), list_subagents()
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from flocks.utils.log import Log
from flocks.config.config import Config, ConfigInfo
from flocks.provider.provider import Provider, ChatMessage
from flocks.project.instance import Instance
from flocks.permission import from_config, merge
from flocks.agent.agent import (
    AgentInfo,
    AgentModel,
    AgentPromptMetadata,
    AvailableAgent,
    AvailableCategory,
    AvailableSkill,
    AvailableWorkflow,
    DelegationTrigger,
)
from flocks.agent.toolset import agent_declares_tool
from flocks.agent.prompt_utils import categorize_tools
from flocks.agent.agent_factory import (
    scan_and_load,
    inject_dynamic_prompts,
    yaml_to_agent_info as _yaml_to_agent_info,
)
from flocks.plugin import PluginLoader, ExtensionPoint
from flocks.skill.skill import Skill

log = Log.create(service="agent.registry")

# Agent name aliases for backward compatibility
AGENT_ALIASES: Dict[str, str] = {
    "sisyphus": "rex",
    "sisyphus-junior": "rex-junior",
}

# ---------------------------------------------------------------------------
# Runtime metadata reference  (shared with backward-compat metadata.py)
# ---------------------------------------------------------------------------

_agents_ref: Optional[Dict[str, AgentInfo]] = None


def _set_agents_ref(agents: Dict[str, AgentInfo]) -> None:
    """Set the live agent dict reference.  Called once after all agents load."""
    global _agents_ref
    _agents_ref = agents


# ---------------------------------------------------------------------------
# Metadata query helpers  (merged from metadata.py)
# ---------------------------------------------------------------------------

def is_delegatable(agent_name: str) -> bool:
    if _agents_ref and agent_name in _agents_ref:
        return bool(_agents_ref[agent_name].delegatable)
    return True  # unknown → safe default


def get_agent_mode(agent_name: str) -> Optional[str]:
    if _agents_ref and agent_name in _agents_ref:
        return _agents_ref[agent_name].mode
    return None


def is_hidden(agent_name: str) -> bool:
    if _agents_ref and agent_name in _agents_ref:
        return _agents_ref[agent_name].hidden
    return False


def list_delegatable_agents() -> List[str]:
    if _agents_ref:
        return [n for n, a in _agents_ref.items() if a.delegatable]
    return []


def list_primary_agents() -> List[str]:
    if _agents_ref:
        return [n for n, a in _agents_ref.items() if a.mode == "primary"]
    return []


def list_subagents() -> List[str]:
    if _agents_ref:
        return [n for n, a in _agents_ref.items() if a.mode == "subagent"]
    return []


# ---------------------------------------------------------------------------
# Delegation candidate list builder
# ---------------------------------------------------------------------------

def _make_default_prompt_metadata(agent: AgentInfo) -> AgentPromptMetadata:
    return AgentPromptMetadata(
        category="plugin",
        cost="medium",
        triggers=[DelegationTrigger(
            domain=agent.name,
            trigger=agent.description or f"Tasks requiring {agent.name}",
        )],
    )


def _build_available_agents(agents: Dict[str, AgentInfo]) -> List[AvailableAgent]:
    available: List[AvailableAgent] = []
    for agent in agents.values():
        if not (agent.delegatable and agent.mode != "primary" and not agent.hidden):
            continue
        metadata = agent.prompt_metadata or _make_default_prompt_metadata(agent)
        available.append(AvailableAgent(
            name=agent.name,
            description=agent.description or "",
            metadata=metadata,
        ))
    return available


# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------

class Agent:
    """
    Agent management namespace.

    Provides async get/list/refresh methods backed by a cached
    asyncio.Task that runs the 4-step load flow exactly once per
    session startup.
    """

    # Process-level registry for agents registered at runtime via Agent.register().
    #
    # Scope: GLOBAL (shared across all sessions in the same process).
    # This is intentional — runtime-registered agents behave like dynamically
    # installed plugin agents: once registered, they are available to every
    # session that runs in the process.
    #
    # Consequence: do NOT use Agent.register() to inject session-scoped
    # temporary agents.  If session isolation is needed in the future, a
    # separate per-session registry keyed by session_id should be introduced.
    _custom_agents: Dict[str, AgentInfo] = {}

    # ── Loading ────────────────────────────────────────────────────────────

    @staticmethod
    async def _load_agents() -> Dict[str, AgentInfo]:
        """
        4-step agent loading:

        ① Context  — tools, skills, categories
        ② YAML     — scan_and_load() from built-in + plugin directories
        ③ Plugins  — PluginLoader Python modules + cfg.agent overrides
        ④ Prompts  — inject_dynamic_prompts() for phase-2 dynamic agents
        """
        # Lazy imports to avoid circular dependencies
        from flocks.tool.delegate_task_constants import CATEGORY_DESCRIPTIONS, DEFAULT_CATEGORIES
        from flocks.tool.registry import ToolRegistry

        cfg = await Config.get()
        ToolRegistry.init()

        # ── ① Collect context ─────────────────────────────────────────────
        available_tools = [t.name for t in ToolRegistry.list_tools() if t.enabled]
        categorized_tools = categorize_tools(available_tools)
        skills = await Skill.all()
        available_skills = [
            AvailableSkill(name=s.name, description=s.description, location=s.source or "project")
            for s in skills
        ]
        category_configs = {**DEFAULT_CATEGORIES, **(cfg.categories or {})}
        available_categories = [
            AvailableCategory(
                name=name,
                description=(
                    cfg.categories.get(name).description
                    if cfg.categories and cfg.categories.get(name)
                    else CATEGORY_DESCRIPTIONS.get(name, name)
                ),
            )
            for name in category_configs.keys()
        ]

        # Discover available workflows (best-effort; failure must not block agent load)
        available_workflows: List[AvailableWorkflow] = []
        try:
            from flocks.workflow.center import scan_skill_workflows
            workflow_entries = await scan_skill_workflows()
            for entry in workflow_entries:
                available_workflows.append(AvailableWorkflow(
                    name=entry.get("name") or "",
                    description=entry.get("description") or "",
                    path=entry.get("workflowPath") or "",
                    source=entry.get("sourceType") or "project",
                ))
        except Exception as _wf_err:
            log.debug("agent.registry.workflow_scan_skipped", {"error": str(_wf_err)})

        user_perms = from_config(cfg.permission or {})
        cli_run_mode = (
            os.environ.get("FLOCKS_CLI_RUN_MODE") == "true"
            or os.environ.get("FLOCKS_CLI_RUN_MODE") == "true"
        )
        cli_overrides = from_config({"question": "deny"}) if cli_run_mode else []

        if cfg.agent_logic and cfg.agent_logic != "rex":
            log.warn("agent.logic.deprecated", {"message": "non-rex agent logic is deprecated; using rex"})

        # ── ② YAML agents ─────────────────────────────────────────────────
        result = scan_and_load()

        # Apply global base permissions on top of per-agent YAML permissions.
        # For agents with explicit tool lists, their deny-all baseline is kept;
        # only user config overrides are merged on top.
        if user_perms or cli_overrides:
            for agent in result.values():
                agent.permission = merge(agent.permission, user_perms, cli_overrides)

        # ── ③ Python plugin agents + cfg.agent overrides ──────────────────

        def _consume_agents(agents: list, source: str) -> None:
            for agent in agents:
                if agent.name in result:
                    log.warn("plugin.name_conflict", {
                        "name": agent.name,
                        "source": source,
                        "hint": f"Plugin agent '{agent.name}' conflicts with existing agent, skipped",
                    })
                    continue
                result[agent.name] = agent

        PluginLoader.register_extension_point(ExtensionPoint(
            attr_name="AGENTS",
            subdir="agents",
            item_type=AgentInfo,
            dedup_key=lambda a: a.name,
            consumer=_consume_agents,
            yaml_item_factory=_yaml_to_agent_info,
        ))
        PluginLoader.load_all(
            extra_sources=cfg.plugin or [],
            project_dir=Path.cwd(),
        )

        # User overrides from cfg.agent
        default_llm = await Config.resolve_default_llm()
        default_model_id = default_llm["model_id"] if default_llm else None

        base_permissions = _build_base_permissions(user_perms, cli_overrides)

        def _permission_dict_to_tools(permission_cfg: Dict[str, Any]) -> List[str]:
            try:
                from flocks.permission.next import PermissionNext
                from flocks.tool.registry import ToolRegistry

                ToolRegistry.init()
                ruleset = from_config(permission_cfg)
                return [
                    tool.name
                    for tool in ToolRegistry.list_tools()
                    if getattr(tool, "enabled", True)
                    and tool.name not in {"invalid", "_noop"}
                    and PermissionNext.evaluate(tool.name, "*", ruleset) == "allow"
                ]
            except Exception as exc:
                log.warn("agent.registry.permission_to_tools.error", {"error": str(exc)})
                return []

        if cfg.agent:
            for key, value in cfg.agent.items():
                alias_key = AGENT_ALIASES.get(key, key)
                if alias_key != key:
                    log.warn("agent.alias.deprecated", {"message": f'"{key}" is deprecated, use "{alias_key}"'})
                key = alias_key
                if value.disable:
                    result.pop(key, None)
                    continue

                item = result.get(key)
                if not item:
                    item = AgentInfo(
                        name=key,
                        mode="all",
                        permission=base_permissions,
                        tools=[],
                        options={},
                        native=False,
                    )
                    result[key] = item

                if value.prompt:
                    item.prompt = value.prompt
                if value.prompt_append and item.prompt:
                    item.prompt = item.prompt + "\n\n" + value.prompt_append
                if value.description:
                    item.description = value.description
                if value.description_cn is not None:
                    item.description_cn = value.description_cn
                if value.temperature is not None:
                    item.temperature = value.temperature
                if value.top_p is not None:
                    item.top_p = value.top_p
                if value.mode:
                    item.mode = value.mode
                if value.color:
                    item.color = value.color
                if value.hidden is not None:
                    item.hidden = value.hidden
                if value.steps is not None:
                    item.steps = value.steps
                if value.delegatable is not None:
                    item.delegatable = value.delegatable
                item.options.update(value.options)
                if value.permission:
                    item.permission = merge(item.permission, from_config(value.permission), cli_overrides)
                    if isinstance(value.permission, dict):
                        item.tools = _permission_dict_to_tools(value.permission)

        # enabled_agents whitelist filter
        if cfg.enabled_agents is not None:
            allowed = set(cfg.enabled_agents)
            for n in [k for k in result if k not in allowed]:
                del result[n]

        # ── ④ Phase-2 dynamic prompts ──────────────────────────────────────
        available_agents = _build_available_agents(result)
        inject_dynamic_prompts(
            result,
            available_agents,
            categorized_tools,
            available_skills,
            available_categories,
            available_workflows,
        )

        # Merge runtime-registered custom agents
        result.update(Agent._custom_agents)

        # Share reference with metadata query functions
        _set_agents_ref(result)

        return result

    @staticmethod
    def _create_task():
        return asyncio.create_task(Agent._load_agents())

    _state_accessor = Instance.state(_create_task)

    @staticmethod
    async def state() -> Dict[str, AgentInfo]:
        return await Agent._state_accessor()

    @classmethod
    async def refresh(cls) -> Dict[str, AgentInfo]:
        cls.invalidate_cache()
        return await cls.state()

    @classmethod
    def invalidate_cache(cls) -> None:
        """Invalidate cached agent state.

        Dynamic agent prompts depend on the current tool registry, so tool
        plugin refreshes also need a lightweight way to invalidate agents.
        """
        cls._state_accessor.invalidate()  # type: ignore[attr-defined]

    # ── Lookup ──────────────────────────────────────────────────────────────

    @classmethod
    async def get(cls, agent: str) -> Optional[AgentInfo]:
        agents = await cls.state()
        resolved = AGENT_ALIASES.get(agent, agent)
        return agents.get(resolved)

    @classmethod
    async def list(cls) -> List[AgentInfo]:
        cfg = await Config.get()
        agents = await cls.state()

        def sort_key(a: AgentInfo):
            if cfg.default_agent:
                default_name = AGENT_ALIASES.get(cfg.default_agent, cfg.default_agent)
                is_default = default_name == a.name
            else:
                is_default = a.name == "rex"
            return (not is_default, a.name)

        return sorted(agents.values(), key=sort_key)

    @classmethod
    async def default_agent(cls) -> str:
        cfg = await Config.get()
        agents = await cls.state()

        if cfg.default_agent:
            default_name = AGENT_ALIASES.get(cfg.default_agent, cfg.default_agent)
            agent = agents.get(default_name)
            if not agent:
                raise ValueError(f'default agent "{cfg.default_agent}" not found')
            if agent.mode == "subagent":
                raise ValueError(f'default agent "{cfg.default_agent}" is a subagent')
            if agent.hidden:
                raise ValueError(f'default agent "{cfg.default_agent}" is hidden')
            return agent.name

        rex = agents.get("rex")
        if rex and rex.mode != "subagent" and not rex.hidden:
            return rex.name

        for agent in agents.values():
            if agent.mode != "subagent" and not agent.hidden:
                return agent.name

        raise ValueError("no primary visible agent found")

    # ── Additional listing helpers ──────────────────────────────────────────

    @classmethod
    async def list_names(cls) -> List[str]:
        return [a.name for a in await cls.list()]

    @classmethod
    async def list_visible(cls) -> List[AgentInfo]:
        return [a for a in await cls.list() if not a.hidden]

    @classmethod
    async def list_hidden(cls) -> List[AgentInfo]:
        return [a for a in await cls.list() if a.hidden]

    @classmethod
    async def list_subagents(cls) -> List[AgentInfo]:
        return [a for a in await cls.list() if a.mode == "subagent" and not a.hidden]

    @classmethod
    async def list_primary(cls) -> List[AgentInfo]:
        return [a for a in await cls.list() if a.mode in ["primary", "all"] and not a.hidden]

    @classmethod
    async def list_by_mode(cls, mode: str) -> List[AgentInfo]:
        return [a for a in await cls.list() if a.mode == mode]

    @classmethod
    async def is_hidden(cls, agent_name: str) -> bool:
        agent = await cls.get(agent_name)
        return agent.hidden if agent else False

    # ── Prompt / config getters ─────────────────────────────────────────────

    @classmethod
    async def get_system_prompt(cls, agent_name: str) -> Optional[str]:
        agent = await cls.get(agent_name)
        return agent.prompt if agent else None

    @classmethod
    async def get_model_config(cls, agent_name: str) -> Optional[Dict[str, str]]:
        agent = await cls.get(agent_name)
        if not agent or not agent.model:
            return None
        return {
            "providerID": agent.model.provider_id,
            "modelID": agent.model.model_id,
        }

    # ── Tool declaration checking ───────────────────────────────────────────

    @classmethod
    async def has_tool(cls, agent_name: str, tool: str) -> bool:
        agent = await cls.get(agent_name)
        if not agent:
            return False
        if agent_declares_tool(agent, tool):
            return True
        from flocks.tool.catalog import get_always_load_tool_names
        return tool in get_always_load_tool_names()

    # ── Agent generation (LLM-assisted) ────────────────────────────────────

    @classmethod
    async def generate(cls, input_data: Dict[str, Any]) -> Dict[str, Any]:
        if input_data.get("model"):
            target_model = input_data["model"]
            provider_id = target_model["providerID"]
            model_id = target_model["modelID"]
        else:
            default_llm = await Config.resolve_default_llm()
            if default_llm:
                provider_id = default_llm["provider_id"]
                model_id = default_llm["model_id"]
            else:
                provider_id = "openai"
                model_id = "gpt-4"

        from flocks.session.prompt import SystemPrompt
        from flocks.session.prompt_strings import PROMPT_GENERATE
        system = SystemPrompt.header(provider_id)
        if hasattr(system, "append"):
            system.append(PROMPT_GENERATE)
        else:
            system = [PROMPT_GENERATE]

        existing = await cls.list()
        existing_names = ", ".join([a.name for a in existing])

        messages = [
            ChatMessage(role="system", content="\n".join(system)),
            ChatMessage(
                role="user",
                content=(
                    f'Create an agent configuration based on this request: "{input_data["description"]}".\n\n'
                    f"IMPORTANT: The following identifiers already exist and must NOT be used: {existing_names}\n"
                    "  Return ONLY the JSON object, no other text, do not wrap in backticks"
                ),
            ),
        ]

        response = await Provider.chat(model_id=model_id, messages=messages)
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            raise ValueError(f"Failed to generate valid JSON: {content}")

    # ── File watcher ────────────────────────────────────────────────────────

    _watcher: Optional["AgentFileWatcher"] = None

    @classmethod
    def start_watcher(cls) -> None:
        """Start the file watcher that auto-invalidates the agent cache on plugin changes."""
        if cls._watcher is None:
            cls._watcher = AgentFileWatcher()
        cls._watcher.start()

    # ── Runtime registration ────────────────────────────────────────────────

    @classmethod
    def register(cls, name: str, info: AgentInfo) -> None:
        cls._custom_agents[name] = info

    @classmethod
    def unregister(cls, name: str) -> bool:
        if name in cls._custom_agents:
            del cls._custom_agents[name]
            return True
        return False


# ---------------------------------------------------------------------------
# Agent File Watcher
# ---------------------------------------------------------------------------


class AgentFileWatcher:
    """Watch plugin agent directories and auto-invalidate the Agent cache on change.

    Monitors:
    - ``~/.flocks/plugins/agents/``       (user-level)
    - ``<cwd>/.flocks/plugins/agents/``   (project-level)

    Triggers on ``agent.yaml`` or ``*.md`` file changes with a 1.5 s debounce
    so that IDE batch-writes (multiple files saved at once) only cause a single
    cache invalidation.
    """

    _DEBOUNCE_SECONDS = 1.5

    def __init__(self) -> None:
        self._observer: Optional[object] = None
        self._debounce_timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    # ---- public ----

    def start(self) -> None:
        if self._observer is not None:
            return

        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler, FileSystemEvent
        except ImportError:
            log.warn(
                "agent.watcher.watchdog_missing",
                {"msg": "watchdog not installed, agent file watcher disabled"},
            )
            return

        watch_dirs = self._collect_watch_dirs()
        if not watch_dirs:
            log.info("agent.watcher.no_dirs", {"msg": "no agent plugin directories to watch"})
            return

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event: FileSystemEvent) -> None:
                if event.is_directory:
                    return
                src = getattr(event, "src_path", "") or ""
                fname = os.path.basename(src)
                if fname == "agent.yaml" or src.endswith(".md"):
                    watcher._schedule_invalidate()

        handler = _Handler()
        observer = Observer()
        for d in watch_dirs:
            try:
                observer.schedule(handler, d, recursive=True)
                log.debug("agent.watcher.watching", {"directory": d})
            except Exception as e:
                log.warn("agent.watcher.schedule_error", {"directory": d, "error": str(e)})

        observer.daemon = True
        observer.start()
        self._observer = observer
        log.info("agent.watcher.started", {"directories": sorted(watch_dirs)})

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
            log.info("agent.watcher.stopped")

    # ---- internal ----

    def _schedule_invalidate(self) -> None:
        """Debounced cache invalidation."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(
                self._DEBOUNCE_SECONDS, self._do_invalidate
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _do_invalidate(self) -> None:
        Agent.invalidate_cache()
        log.info("agent.watcher.cache_invalidated", {"reason": "agent plugin file changed on disk"})

    def _collect_watch_dirs(self) -> Set[str]:
        """Return all plugin agent directories that exist and should be watched."""
        dirs: Set[str] = set()
        try:
            from flocks.plugin.loader import DEFAULT_PLUGIN_ROOT
            user_dir = str(DEFAULT_PLUGIN_ROOT / "agents")
        except Exception:
            user_dir = str(Path.home() / ".flocks" / "plugins" / "agents")

        if os.path.isdir(user_dir):
            dirs.add(user_dir)

        try:
            project_dir = str(Path.cwd() / ".flocks" / "plugins" / "agents")
            if project_dir != user_dir and os.path.isdir(project_dir):
                dirs.add(project_dir)
        except Exception:
            pass

        return dirs


# ---------------------------------------------------------------------------
# Base permission helper (for user-override created agents)
# ---------------------------------------------------------------------------

def _build_base_permissions(user_perms, cli_overrides):
    """Allow-all default permission set for user-created agents."""
    from flocks.agent.constants import Truncate
    defaults = from_config({
        "*": "allow",
        "doom_loop": "ask",
        "external_directory": {
            "*": "ask",
            f"{Truncate.DIR}": "allow",
            f"{Truncate.GLOB}": "allow",
        },
        "question": "deny",
        "plan_enter": "deny",
        "plan_exit": "deny",
        "read": {
            "*": "allow",
            "*.env": "ask",
            "*.env.*": "ask",
            "*.env.example": "allow",
        },
    })
    return merge(defaults, user_perms, cli_overrides)
