"""
Agents a deployment manages (see :mod:`flocks.content.managed`), as AgentInfo.

A managed agent definition is the plugin ``agent.yaml`` format carried
inline, in one of two shapes:

* Markdown whose YAML front matter holds the fields and whose body is the
  prompt (the shape a sealed agent package carries), or
* a YAML mapping with an inline ``prompt``.

There are no files behind it, so ``prompt_file`` is refused.  The prompt is
sealed (``prompt_protected``): the runtime reads it, the API never shows or
changes it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from flocks.agent.agent import AgentInfo
from flocks.content.managed import describe_parse_error
from flocks.content.models import ContentBody, ContentKind
from flocks.utils.log import Log

log = Log.create(service="agent.managed")


def _split_front_matter(text: str) -> Tuple[Any, str]:
    lines = text.lstrip().splitlines(keepends=True)
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("the front matter has no closing delimiter")
    return yaml.safe_load("".join(lines[1:end])), "".join(lines[end + 1:])


def parse_managed_agent(content_id: str, body: ContentBody) -> AgentInfo:
    """Build the AgentInfo for managed agent ``content_id``; raises ValueError if unusable."""
    from flocks.agent.agent_factory import yaml_to_agent_info
    from flocks.content.managed import managed_location

    text = body.as_text()
    prompt_from_body: Optional[str] = None
    if text.lstrip().startswith("---"):
        raw, rest = _split_front_matter(text)
        prompt_from_body = rest.strip() or None
    else:
        raw = yaml.safe_load(text) if text.strip() else None
    if not isinstance(raw, dict):
        raise ValueError("a managed agent definition must be a mapping")
    raw = dict(raw)
    name = str(raw.get("name") or content_id)
    if name != content_id:
        # The listed id is what the deployment protects; a body naming another
        # agent would slip a definition in under a name nobody reserved.
        raise ValueError(f"the definition names {name!r} but is listed as {content_id!r}")
    if raw.get("prompt_file"):
        raise ValueError("a managed agent carries its prompt inline; prompt_file is not supported")
    if prompt_from_body and not raw.get("prompt"):
        raw["prompt"] = prompt_from_body
    if not raw.get("prompt") and not raw.get("prompt_builder"):
        raise ValueError("a managed agent needs an inline prompt")
    raw["name"] = name
    info = yaml_to_agent_info(raw, Path(managed_location(ContentKind.AGENT, content_id)))
    # The group is a local display choice (config overlays may move it); the
    # factory would derive its lock from where the definition file lives,
    # and there is no file.
    return info.model_copy(update={"native": True, "prompt_protected": True, "group_readonly": False})


def managed_agent_ids() -> frozenset[str]:
    """Names the deployment manages, valid definition or not.

    A name stays reserved even when its definition cannot be used: dropping
    it would let a local agent of the same name take its place.
    """
    from flocks.content.managed import managed_ids

    return managed_ids(ContentKind.AGENT)


def managed_agents() -> Dict[str, AgentInfo]:
    """The usable managed agents, by name.  Unusable definitions are logged and left out."""
    from flocks.content.managed import ManagedContent

    agents: Dict[str, AgentInfo] = {}
    for content_id, body in ManagedContent.bodies(ContentKind.AGENT).items():
        try:
            agents[content_id] = parse_managed_agent(content_id, body)
        except Exception as exc:
            # Never the message: a YAML error quotes the protected definition.
            log.error("agent.managed.invalid", {"name": content_id, "source": body.source, **describe_parse_error(exc)})
    return agents


__all__ = ["managed_agent_ids", "managed_agents", "parse_managed_agent"]
