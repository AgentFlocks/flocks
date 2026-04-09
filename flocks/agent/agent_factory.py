"""
Agent Factory — YAML-based agent loader.

Scans agent folders, parses agent.yaml files, resolves static prompts from
prompt.md or marks agents for dynamic prompt injection via prompt_builder.py.
Resolves each agent to a concrete ``tools`` list. If legacy ``permission`` is
present, it is expanded against the current tool registry for compatibility.
If neither ``tools`` nor ``permission`` is declared, the static tool list stays
empty and runtime exposure falls back to always-load tools only.

Extension point:
  Built-in agents:         flocks/agent/agents/<name>/          native=True
  Project plugin agents:   <cwd>/.flocks/plugins/agents/<name>/ native=True
  User plugin agents:      ~/.flocks/plugins/agents/<name>/     native=False

The ``native`` field is derived from the loading directory and must NOT be
declared in agent.yaml.
"""

from __future__ import annotations

import importlib
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from flocks.agent.agent import AgentInfo, AgentModel, AgentPromptMetadata, DelegationTrigger
from flocks.agent.toolset import resolve_agent_initial_tools
from flocks.utils.log import Log

log = Log.create(service="agent.factory")

# Directory containing built-in agent folders
_BUILTIN_AGENTS_DIR = Path(__file__).parent / "agents"

# Default plugin root (same as plugin loader convention)
try:
    from flocks.plugin.loader import DEFAULT_PLUGIN_ROOT
    _PLUGIN_AGENTS_DIR = DEFAULT_PLUGIN_ROOT / "agents"
except ImportError:
    _PLUGIN_AGENTS_DIR = Path.home() / ".flocks" / "plugins" / "agents"

# ---------------------------------------------------------------------------
# Prompt metadata parsing
# ---------------------------------------------------------------------------

def _parse_prompt_metadata(raw: dict) -> Optional[AgentPromptMetadata]:
    """Parse ``prompt_metadata`` section from a raw YAML dict."""
    meta = raw.get("prompt_metadata")
    if not meta or not isinstance(meta, dict):
        return None
    triggers = [
        DelegationTrigger(domain=t["domain"], trigger=t["trigger"])
        for t in meta.get("triggers", [])
        if isinstance(t, dict) and "domain" in t and "trigger" in t
    ]
    return AgentPromptMetadata(
        category=meta.get("category", "plugin"),
        cost=meta.get("cost", "medium"),
        triggers=triggers,
        use_when=meta.get("use_when"),
        avoid_when=meta.get("avoid_when"),
        dedicated_section=meta.get("dedicated_section"),
        prompt_alias=meta.get("prompt_alias"),
        key_trigger=meta.get("key_trigger"),
    )


# ---------------------------------------------------------------------------
# Core loading function
# ---------------------------------------------------------------------------

def load_agent(agent_dir: Path, native: bool = False) -> Optional[AgentInfo]:
    """
    Load a single agent from its folder.

    The folder must contain ``agent.yaml``.  The prompt is resolved from:
      1. ``prompt.md``  → static prompt, read at load time.
      2. ``prompt_builder.py`` → dynamic prompt, injected later via inject().
    If neither exists, prompt stays None (valid for build/plan agents).

    Permission is generated from the ``tools`` list in agent.yaml.
    If ``tools`` is absent, the agent keeps an empty static tool list and only
    runtime always-load tools remain available until the session expands them.

    Args:
        agent_dir: Path to the agent folder.
        native:    Whether this agent is considered built-in/project-level.
                   Determined by the calling scan context, not the YAML file.

    Returns None if the folder has no agent.yaml or fails to parse.
    """
    yaml_path = agent_dir / "agent.yaml"
    if not yaml_path.is_file():
        return None

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        log.error("agent.factory.yaml_parse_error", {
            "path": str(yaml_path),
            "error": str(e),
        })
        return None

    name = raw.get("name") or agent_dir.name
    if not name:
        log.warn("agent.factory.missing_name", {"path": str(yaml_path)})
        return None

    # ── Prompt resolution ──────────────────────────────────────────────────
    prompt: Optional[str] = None
    prompt_builder: Optional[str] = None

    prompt_md = agent_dir / "prompt.md"
    prompt_builder_py = agent_dir / "prompt_builder.py"

    if prompt_md.is_file():
        prompt = prompt_md.read_text(encoding="utf-8").strip()
    elif prompt_builder_py.is_file():
        # Derive Python module path from file location, relative to flocks package root
        try:
            rel = prompt_builder_py.relative_to(Path(__file__).parent.parent.parent)
            module_path = str(rel.with_suffix("")).replace("/", ".").replace("\\", ".")
        except ValueError:
            # Fallback: use absolute path notation
            module_path = str(prompt_builder_py)
        prompt_builder = f"{module_path}:inject"

    # ── Tools / legacy permission compatibility ─────────────────────────────
    tools_list_raw: Optional[List[str]] = raw.get("tools")
    perm_raw = raw.get("permission")
    tools_list, legacy_permission = resolve_agent_initial_tools(tools_list_raw, perm_raw)

    # ── Model ────────────────────────────────────────────────────────────────
    model_raw = raw.get("model")
    model = AgentModel(**model_raw) if isinstance(model_raw, dict) else None

    desc_cn = raw.get("description_cn")
    if desc_cn is None and isinstance(raw.get("descriptionCn"), str):
        desc_cn = raw.get("descriptionCn")

    return AgentInfo(
        name=name,
        description=raw.get("description"),
        description_cn=desc_cn,
        mode=raw.get("mode", "subagent"),
        native=native,
        hidden=raw.get("hidden", False),
        color=raw.get("color"),
        permission=legacy_permission,
        model=model,
        prompt=prompt,
        prompt_builder=prompt_builder,
        tools=tools_list,
        options=raw.get("options", {}),
        steps=raw.get("steps"),
        delegatable=raw.get("delegatable"),
        temperature=raw.get("temperature"),
        top_p=raw.get("top_p"),
        prompt_metadata=_parse_prompt_metadata(raw),
        tags=raw.get("tags", []),
    )


# ---------------------------------------------------------------------------
# Directory scanning
# ---------------------------------------------------------------------------

def scan_and_load(dirs: Optional[List[Path]] = None) -> Dict[str, AgentInfo]:
    """
    Scan agent directories and load all valid agents.

    Scans built-in agents first, then user plugin agents, then project plugin
    agents.  Name conflicts are skipped with a warning (first wins).

    ``native`` is determined by the source directory, not by agent.yaml:
    - ``_BUILTIN_AGENTS_DIR``           → native=True
    - ``<cwd>/.flocks/plugins/agents/`` → native=True  (project-level)
    - ``~/.flocks/plugins/agents/``     → native=False (user custom)
    - ``dirs`` extra directories        → native=False

    Args:
        dirs: Additional directories to scan (appended after built-in + plugin).

    Returns:
        Dict mapping agent name → AgentInfo.
    """
    # (directory, is_native) pairs — order determines first-wins conflict resolution
    search_dirs: List[tuple[Path, bool]] = [(_BUILTIN_AGENTS_DIR, True)]
    if _PLUGIN_AGENTS_DIR.exists():
        search_dirs.append((_PLUGIN_AGENTS_DIR, False))
    # Project-level plugin agents: resolved at call time because cwd is dynamic
    project_agents_dir = Path.cwd() / ".flocks" / "plugins" / "agents"
    if project_agents_dir.exists() and project_agents_dir != _PLUGIN_AGENTS_DIR:
        search_dirs.append((project_agents_dir, True))
    if dirs:
        search_dirs.extend((d, False) for d in dirs)

    result: Dict[str, AgentInfo] = {}

    for scan_dir, is_native in search_dirs:
        if not scan_dir.is_dir():
            continue
        for agent_dir in sorted(scan_dir.iterdir()):
            if not agent_dir.is_dir() or agent_dir.name.startswith("_"):
                continue
            agent = load_agent(agent_dir, native=is_native)
            if agent is None:
                continue
            if agent.name in result:
                log.warn("agent.factory.name_conflict", {
                    "name": agent.name,
                    "existing_source": "previous scan",
                    "skipped_source": str(agent_dir),
                })
                continue
            result[agent.name] = agent
            log.debug("agent.factory.loaded", {
                "name": agent.name,
                "dir": str(agent_dir),
                "native": is_native,
                "has_prompt": agent.prompt is not None,
                "has_builder": agent.prompt_builder is not None,
            })

    return result


# ---------------------------------------------------------------------------
# Dynamic prompt injection (Phase 2)
# ---------------------------------------------------------------------------

def inject_dynamic_prompts(
    agents: Dict[str, AgentInfo],
    available_agents: list,
    tools: list,
    skills: list,
    categories: list,
    workflows: Optional[list] = None,
) -> None:
    """
    Inject dynamic prompts for all agents that have a ``prompt_builder``.

    Dynamically imports each agent's prompt_builder module and calls its
    ``inject(agent_info, available_agents, tools, skills, categories, workflows)``
    function.  The inject function is expected to set ``agent_info.prompt``
    directly.

    This runs after ALL agents are loaded so ``available_agents`` is complete.
    """
    for name, agent in agents.items():
        if not agent.prompt_builder:
            continue
        try:
            module_path, func_name = agent.prompt_builder.rsplit(":", 1)
            module = importlib.import_module(module_path)
            inject_fn = getattr(module, func_name)
            inject_fn(agent, available_agents, tools, skills, categories, workflows or [])
            log.debug("agent.factory.prompt_injected", {"name": name})
        except Exception as e:
            log.error("agent.factory.prompt_inject_error", {
                "name": name,
                "builder": agent.prompt_builder,
                "error": str(e),
            })


# ---------------------------------------------------------------------------
# YAML CRUD helpers (for plugin agents via API routes)
# ---------------------------------------------------------------------------

def _find_yaml_file(name: str) -> Optional[Path]:
    """Find the YAML source file for a plugin agent by name."""
    for suffix in (".yaml", ".yml"):
        candidate = _PLUGIN_AGENTS_DIR / name / f"agent{suffix}"
        if candidate.is_file():
            return candidate
        # Legacy: flat file layout (name.yaml)
        flat = _PLUGIN_AGENTS_DIR / f"{name}{suffix}"
        if flat.is_file():
            return flat
    return None


def _read_yaml_raw(yaml_path: Path) -> Dict[str, Any]:
    return yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}


def _write_yaml(yaml_path: Path, data: Dict[str, Any]) -> None:
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    yaml_path.write_text(content, encoding="utf-8")


def yaml_to_agent_info(raw: dict, yaml_path: Path) -> AgentInfo:
    """
    Convert a parsed YAML dict into an AgentInfo.

    This is the ``yaml_item_factory`` wired into the AGENTS extension point
    in the PluginLoader.  Unlike ``load_agent()``, the YAML here may contain a
    ``permission`` key (old-style plugin) instead of a ``tools`` list.

    Parameters
    ----------
    raw:      Parsed YAML document (a dict).
    yaml_path: Absolute path to the YAML file (used for prompt_file resolution).
    """
    name = raw.get("name")
    if not name:
        raise ValueError(f"Agent YAML missing required 'name' field: {yaml_path}")

    # Prompt resolution: inline or external file
    prompt: Optional[str] = None
    if raw.get("prompt"):
        prompt = raw["prompt"]
    elif raw.get("prompt_file"):
        prompt_path = yaml_path.parent / raw["prompt_file"]
        if prompt_path.is_file():
            prompt = prompt_path.read_text(encoding="utf-8").strip()

    model_raw = raw.get("model")
    model = AgentModel(**model_raw) if isinstance(model_raw, dict) else None

    # Tools: prefer new tools list; fall back to old permission dict
    tools_list_raw: Optional[List[str]] = raw.get("tools")
    perm_raw = raw.get("permission")
    tools_list, legacy_permission = resolve_agent_initial_tools(tools_list_raw, perm_raw)

    desc_cn = raw.get("description_cn")
    if desc_cn is None and isinstance(raw.get("descriptionCn"), str):
        desc_cn = raw.get("descriptionCn")

    return AgentInfo(
        name=name,
        description=raw.get("description"),
        description_cn=desc_cn,
        mode=raw.get("mode", "subagent"),
        native=False,
        hidden=raw.get("hidden", False),
        color=raw.get("color"),
        permission=legacy_permission,
        tools=tools_list,
        model=model,
        prompt=prompt,
        prompt_builder=raw.get("prompt_builder"),
        options=raw.get("options", {}),
        steps=raw.get("steps"),
        delegatable=raw.get("delegatable"),
        temperature=raw.get("temperature"),
        top_p=raw.get("top_p"),
        prompt_metadata=_parse_prompt_metadata(raw),
        tags=raw.get("tags", []),
    )


def find_yaml_agent(name: str) -> Optional[Path]:
    """Return the YAML path for a plugin agent, or None."""
    return _find_yaml_file(name)


def read_yaml_agent(name: str) -> Optional[Dict[str, Any]]:
    """Read the raw YAML dict for a plugin agent. Returns None if not found."""
    path = _find_yaml_file(name)
    if path is None:
        return None
    try:
        return _read_yaml_raw(path)
    except Exception as e:
        log.error("agent.factory.read_failed", {"name": name, "error": str(e)})
        return None


def update_yaml_agent(name: str, updates: Dict[str, Any]) -> bool:
    """Apply partial updates to a YAML plugin agent file.

    Returns True on success, False if the YAML file was not found.
    """
    path = _find_yaml_file(name)
    if path is None:
        return False

    try:
        data = _read_yaml_raw(path)

        prompt_update = updates.pop("prompt", None)
        if prompt_update is not None:
            prompt_file = path.parent / "prompt.md"
            if prompt_file.is_file():
                prompt_file.write_text(prompt_update, encoding="utf-8")
            else:
                data["prompt"] = prompt_update

        if "model" in updates and updates["model"] is not None:
            model = updates.pop("model")
            data["model"] = (
                {"provider_id": model["providerID"], "model_id": model["modelID"]}
                if isinstance(model, dict) and "modelID" in model
                else model
            )
        elif "model" in updates:
            updates.pop("model")
            data.pop("model", None)

        for key, value in updates.items():
            if value is not None:
                data[key] = value

        _write_yaml(path, data)
        log.info("agent.factory.yaml_updated", {"name": name, "path": str(path)})
        return True
    except Exception as e:
        log.error("agent.factory.update_failed", {"name": name, "error": str(e)})
        return False


def delete_yaml_agent(name: str) -> bool:
    """Delete a plugin agent folder or YAML file.

    For subdirectory-layout agents (<name>/agent.yaml), the entire folder is
    removed.  For legacy flat-file agents (<name>.yaml), only the YAML and its
    sibling prompt file are removed.

    Returns True on success, False if not found.
    """
    path = _find_yaml_file(name)
    if path is None:
        return False

    try:
        parent = path.parent
        if parent.name == name and parent != _PLUGIN_AGENTS_DIR:
            # Subdirectory layout: remove the whole agent folder
            shutil.rmtree(parent)
            log.info("agent.factory.dir_deleted", {"name": name, "path": str(parent)})
        else:
            # Legacy flat-file layout: remove YAML and sibling prompt file
            prompt_md = parent / f"{name}.prompt.md"
            if prompt_md.is_file():
                prompt_md.unlink()
            path.unlink()
            log.info("agent.factory.yaml_deleted", {"name": name, "path": str(path)})
        return True
    except Exception as e:
        log.error("agent.factory.delete_failed", {"name": name, "error": str(e)})
        return False
