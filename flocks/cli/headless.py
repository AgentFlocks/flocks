"""Noninteractive session execution. stdout rendering lives in commands/exec.py."""
from __future__ import annotations

import base64
import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from flocks.config.config import Config, ConfigInfo
from flocks.config.runtime import runtime_config
from flocks.session.runtime_controls import RuntimeControls, runtime_controls


@dataclass
class ExecOptions:
    directory: Path
    prompt: str | None = None
    model: str | None = None
    agent: str | None = None
    session_id: str | None = None
    last: bool = False
    create_only: bool = False
    schema: Any = None
    images: list[dict] = field(default_factory=list)
    config: list[str] = field(default_factory=list)
    strict_config: bool = False
    sandbox: str | None = None
    controls: RuntimeControls = field(default_factory=RuntimeControls)


def load_schema(path: Path) -> Any:
    from jsonschema.validators import validator_for

    schema = json.loads(path.read_text(encoding="utf-8"))
    validator_for(schema).check_schema(schema)
    # Local schemas only; validating output must never fetch external resources.
    def check_refs(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef"} and isinstance(item, str) and not item.startswith("#"):
                    raise ValueError("JSON Schema references must be local (#...)")
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)
    check_refs(schema)
    return schema


def load_images(paths: list[Path]) -> list[dict]:
    from PIL import Image
    import io

    result = []
    for path in paths:
        data = path.read_bytes()
        with Image.open(io.BytesIO(data)) as img:
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}.get(img.format)
            if mime is None:
                raise ValueError(f"Unsupported image format: {path}")
            img.verify()
        result.append({"mime": mime, "filename": path.name, "url": f"data:{mime};base64,{base64.b64encode(data).decode()}"})
    return result


def apply_overrides(data: dict, overrides: list[str]) -> dict:
    result = copy.deepcopy(data)
    for item in overrides:
        key, sep, value = item.partition("=")
        parts = key.split(".")
        if not sep or any(not part or part.strip() != part for part in parts):
            raise ValueError(f"Expected dotted.key=value: {item}")
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
        target = result
        for part in parts[:-1]:
            if target.get(part) is None:
                target[part] = {}
            if not isinstance(target[part], dict):
                raise ValueError(f"Cannot descend into non-object config field: {key}")
            target = target[part]
        target[parts[-1]] = parsed
    return result


def canonical_override(item: str) -> str:
    """Accept both Python field names and JSON aliases without duplicate keys."""
    from typing import get_args, get_origin
    from pydantic import BaseModel

    key, separator, value = item.partition("=")
    annotation = ConfigInfo
    result = []
    for part in key.split("."):
        # Config fields often wrap a model or a map in Optional/Union.
        args = get_args(annotation)
        if args and get_origin(annotation) is not dict:
            annotation = next((arg for arg in args if arg is not type(None)), Any)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            match = next(((name, field) for name, field in annotation.model_fields.items()
                          if part in {name, field.alias}), None)
            if match:
                name, model_field = match
                result.append(model_field.alias or name)
                annotation = model_field.annotation
                continue
        if get_origin(annotation) is dict:
            annotation = get_args(annotation)[1]
        else:
            annotation = Any
        result.append(part)
    return ".".join(result) + separator + value


async def build_config(options: ExecOptions) -> ConfigInfo:
    from jsonschema import Draft202012Validator

    original = await Config.get()
    data = apply_overrides(original.model_dump(by_alias=True, exclude_none=True), [canonical_override(item) for item in options.config])
    if options.strict_config:
        schema = ConfigInfo.model_json_schema()
        def close_objects(node):
            if isinstance(node, dict):
                if "properties" in node and node.get("title") not in {
                    "ProviderConfig", "ProviderOptionsConfig", "ModelConfig", "ModelVariantConfig",
                }:
                    node["additionalProperties"] = False
                for child in node.values():
                    close_objects(child)
            elif isinstance(node, list):
                for child in node:
                    close_objects(child)
        close_objects(schema)
        Draft202012Validator(schema).validate(data)
    if options.sandbox is not None:
        sandbox = data.setdefault("sandbox", {})
        sandbox["mode"] = "off" if options.sandbox == "off" else "on"
        if options.sandbox != "off":
            sandbox["workspace_access"] = "rw" if options.sandbox == "workspace-write" else "ro"
            sandbox["scope"] = "session"
        # Explicit invocation flags override per-agent sandbox overrides too.
        for agent in (data.get("agent") or {}).values():
            agent.pop("sandbox", None)
    return ConfigInfo.model_validate(data)


async def resolve_session(options: ExecOptions):
    from flocks.project.project import Project
    from flocks.session.session import Session

    if options.last and options.session_id:
        raise ValueError("--session and --last are mutually exclusive")
    project = (await Project.from_directory(str(options.directory)))["project"]
    worktree = Project.worktree_for_directory(str(options.directory))
    def matches(session):
        return bool(session.directory and Project.worktree_for_directory(session.directory) == worktree)
    if options.session_id:
        session = await Session.get_by_id(options.session_id)
        if session is None or not matches(session):
            raise ValueError(f"Session not found in current worktree: {options.session_id}")
        if session.status != "active":
            raise ValueError(f"Session is {session.status}: {session.id}")
        return session
    if options.last:
        sessions = await Session.list(project.id)
        sessions = [s for s in sessions if not s.parent_id and s.status == "active" and matches(s)]
        if not sessions:
            raise ValueError("No active session in current worktree")
        return max(sessions, key=lambda s: s.time.updated)
    return await Session.create(project_id=project.id, directory=str(options.directory), agent=options.agent)


async def run_headless(options: ExecOptions, emit: Callable[[dict], None] | None = None) -> dict:
    """Execute a complete turn without terminal input; always return a result envelope."""
    from flocks.agent.registry import Agent
    from flocks.mcp import MCP
    from flocks.provider.provider import Provider
    from flocks.session.message import FilePart, Message, MessageRole
    from flocks.session.runner import RunnerCallbacks
    from flocks.session.session_loop import LoopCallbacks, SessionLoop
    from flocks.tool.registry import ToolRegistry
    from flocks.sandbox.config import resolve_sandbox_config_for_agent

    emit = emit or (lambda event: None)
    controls = options.controls
    result = {"type": "result", "session_id": None, "success": False, "result": "", "error": None}
    config_token = policy_token = None
    mcp_started = False
    try:
        config = await build_config(options)
        config_token = runtime_config.set(config)
        policy_token = runtime_controls.set(controls)
        Agent.invalidate_cache()
        controls.check_budget()
        session = await resolve_session(options)
        result["session_id"] = session.id
        emit({"type": "session", "session_id": session.id})
        if options.create_only:
            result["success"] = True
            return result
        agent_name = options.agent or session.agent or await Agent.default_agent()
        agent = await Agent.get(agent_name)
        if agent is None:
            raise ValueError(f"Unknown agent: {agent_name}")
        sandbox_cfg = resolve_sandbox_config_for_agent(config.model_dump(), agent_name)
        controls.sandbox_required = sandbox_cfg.mode == "on"
        if controls.sandbox_required:
            from flocks.sandbox.context import resolve_sandbox_context
            sandbox = await resolve_sandbox_context(
                config_data=config.model_dump(), session_key=session.id,
                agent_id=agent_name, main_session_key=session.id, workspace_dir=str(options.directory),
            )
            if sandbox is None:
                raise RuntimeError("Required sandbox is unavailable")
        await Provider.init()
        await Provider.apply_config(config)
        provider_id = model_id = None
        if options.model:
            if "/" not in options.model:
                raise ValueError("--model must be provider/model")
            provider_id, model_id = options.model.split("/", 1)
        provider_id, model_id = await SessionLoop._resolve_model(session.model_copy(update={"agent": agent_name}), provider_id, model_id)
        provider = Provider.get(provider_id)
        if provider is None or not provider.is_configured():
            raise ValueError(f"Provider is not configured: {provider_id}")
        from flocks.provider.usage_service import resolve_usage_pricing
        controls.check_budget(resolve_usage_pricing(provider_id, model_id), require_pricing=True)
        ToolRegistry.init()
        mcp_started = True
        await MCP.init()
        prompt = options.prompt
        if prompt is None:
            if not await Message.list(session.id):
                raise ValueError("Session has no messages; provide --prompt")
            prompt = "Continue the previous task."
        if options.schema is not None:
            prompt = (prompt or "Continue the previous task.") + "\nReturn only JSON matching this JSON Schema:\n" + json.dumps(options.schema, ensure_ascii=False)
        if prompt is not None:
            user = await Message.create(
                session_id=session.id, role=MessageRole.USER, content=prompt,
                agent=agent_name, model={"providerID": provider_id, "modelID": model_id},
            )
            for attachment in options.images:
                await Message.add_part(session.id, user.id, FilePart(sessionID=session.id, messageID=user.id, **attachment))

        async def on_text(delta):
            emit({"type": "text_delta", "delta": delta})
        async def on_tool_start(name, arguments):
            emit({"type": "tool_start", "tool": name, "arguments": arguments})
        async def on_tool_end(name, value):
            emit({"type": "tool_end", "tool": name, "success": value.success, "output": value.output, "error": value.error})
        loop_errors = []
        async def on_error(error):
            loop_errors.append(error)
        async def on_permission(request):
            # Tool membership is enforced by ToolRegistry; path approvals are separate.
            from flocks.project.instance import Instance
            if request.permission == "external_directory":
                return bool(request.patterns) and all(Instance.contains_path(p) for p in request.patterns)
            return controls.denial(request.permission) is None
        async def on_step_start(step):
            controls.check_budget()
        async def on_compaction():
            if controls.max_budget is not None:
                raise RuntimeError("Compaction requires an auxiliary model call; resume without --max-budget or start a new session")
        callbacks = LoopCallbacks(
            on_error=on_error, on_compaction=on_compaction,
            on_step_start=on_step_start,
            runner_callbacks=RunnerCallbacks(on_text_delta=on_text, on_tool_start=on_tool_start,
                                            on_tool_end=on_tool_end, on_permission_request=on_permission),
        )
        from flocks.project.instance import Instance
        outcome = await Instance.provide(str(options.directory), fn=lambda: SessionLoop.run(
            session_id=session.id, provider_id=provider_id, model_id=model_id,
            agent_name=agent_name, callbacks=callbacks, working_directory=str(options.directory),
        ))
        if loop_errors:
            raise RuntimeError(loop_errors[-1])
        if controls.failure:
            raise RuntimeError(controls.failure)
        if outcome.error or outcome.action != "stop":
            raise RuntimeError(outcome.error or f"Session did not complete: {outcome.action}")
        if outcome.last_message is None:
            raise RuntimeError("Session completed without an assistant message")
        if outcome.last_message.finish == "error" or outcome.last_message.error:
            raise RuntimeError(str(outcome.last_message.error or "Assistant request failed"))
        parts = await Message.parts(outcome.last_message.id, session_id=session.id)
        result["result"] = "\n".join(p.text for p in parts if p.type == "text" and p.text and not p.ignored)
        if options.schema is not None:
            from jsonschema import validate
            structured = json.loads(result["result"])
            validate(structured, options.schema)
            result["structured_output"] = structured
        result["success"] = True
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        if mcp_started:
            try:
                await MCP.shutdown()
            except Exception:
                pass
        if policy_token is not None:
            runtime_controls.reset(policy_token)
        if config_token is not None:
            runtime_config.reset(config_token)
            Agent.invalidate_cache()
        result["usage"] = {"input_tokens": controls.input_tokens, "output_tokens": controls.output_tokens,
                           "requests": controls.requests, "cost_by_currency": controls.costs}
    return result
