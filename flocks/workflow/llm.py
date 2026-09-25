import asyncio
from copy import copy
from dataclasses import dataclass
import inspect
import logging
import time
from typing import Any, Callable, Dict, Optional

from flocks.config.config import Config
from flocks.provider.provider import ChatMessage, Provider, ProviderConfig
from flocks.provider.sdk.openai_base import LLMResponseSafetyError
from flocks.workflow._async_runtime import (
    run_sync as _run_sync_on_shared_loop,
    run_sync_cancellable as _run_sync_cancellable_on_shared_loop,
)
from flocks.workflow.errors import RunCancelledError


_log = logging.getLogger(__name__)
_CLIENT_CLOSE_TIMEOUT_S = 5.0


async def _close_provider_client(provider: Any) -> None:
    """Release the SDK client owned by one isolated workflow attempt."""
    client = getattr(provider, "_client", None)
    if client is None:
        return
    provider._client = None  # A retry must not reuse a closed client.
    # Google owns separate async and sync clients; OpenAI/Anthropic expose
    # async close() on the client itself. Close them on the request's loop.
    owners = (getattr(client, "aio", None), client)
    for owner in owners:
        close = getattr(owner, "aclose", None) or getattr(owner, "close", None)
        if not callable(close):
            continue
        try:
            result = close()
            if inspect.isawaitable(result):
                await asyncio.wait_for(result, timeout=_CLIENT_CLOSE_TIMEOUT_S)
        except Exception:
            _log.warning("Failed to close workflow provider client", exc_info=True)


def _run_coro_sync(
    coro,
    cancel_checker: Optional[Callable[[], bool]] = None,
):
    """Run async provider calls from sync workflow code on the shared workflow loop.

    All workflow-triggered async work (config loading, provider.apply_config,
    provider.chat, the llm.before/after hook stages) is routed through one
    persistent loop -- the server's own loop when it registered itself, else
    a private background loop -- so loop-bound resources (httpx.AsyncClient,
    OpenAI streams, hook gRPC channels) are always used from the loop that
    owns them. See ``flocks.workflow._async_runtime``.
    """
    if cancel_checker is not None:
        return _run_sync_cancellable_on_shared_loop(coro, cancel_checker)
    return _run_sync_on_shared_loop(coro)


_DEFAULT_LLM_HOOK_CONTEXT: Dict[str, Any] = {
    "sessionID": "workflow",
    "messageID": "",
    "agent": "workflow",
    "workspace": None,
    "step": 0,
}


def llm_hook_context_from_registry(registry: Any) -> Dict[str, Any]:
    """Derive LLM hook metadata (session/message/agent) from a workflow tool registry.

    ``messageID`` is deliberately left empty: hook implementations restore
    redaction placeholders by rewriting the session message with that id,
    and a workflow must never overwrite a parent chat message. Placeholder
    restoration for workflow responses is applied locally from the
    ``llm.before`` output instead.
    """
    meta = dict(_DEFAULT_LLM_HOOK_CONTEXT)
    # ``workspace`` lets the pipeline resolve project hooks without a session
    # lookup (the runner passes the session directory the same way).
    try:
        import os

        meta["workspace"] = os.getcwd()
    except OSError:
        pass
    ctx = getattr(registry, "_ctx", None)
    extra = getattr(ctx, "extra", None)
    workspace = extra.get("workspace_dir") if isinstance(extra, dict) else None
    if isinstance(workspace, str) and workspace.strip():
        meta["workspace"] = workspace
    session_id = str(getattr(ctx, "session_id", "") or "").strip()
    if session_id:
        meta["sessionID"] = session_id
    agent = str(getattr(ctx, "agent", "") or "").strip()
    if agent:
        meta["agent"] = agent
    return meta


async def _run_llm_before_stage(
    hook_meta: Dict[str, Any],
    messages: list,
    provider_options: Dict[str, Any],
) -> tuple[list, list, Dict[str, Any]]:
    """Run ``llm.before`` hooks (Pro registers prompt redaction here).

    Returns rewritten messages, placeholder replacements, and effective
    provider options. Mirrors the session runner's request shape.
    """
    from flocks.hooks.pipeline import HookPipeline, HookStage
    from flocks.session.llm_hook_utils import (
        apply_hook_request_output,
        serialize_chat_message,
        stream_text_replacements_from_hook_output,
    )

    if not await HookPipeline.has_stage_handlers(HookStage.LLM_BEFORE, hook_meta):
        return messages, [], provider_options
    hook_ctx = await HookPipeline.run_llm_before(
        {
            **hook_meta,
            "request": {
                "messageCount": len(messages),
                "messages": [serialize_chat_message(message) for message in messages],
                "toolCount": 0,
                "tools": [],
                "providerOptions": dict(provider_options),
                "providerToolsEnabled": False,
            },
        }
    )
    hook_output = getattr(hook_ctx, "output", None) or {}
    updated_messages, options = apply_hook_request_output(messages, provider_options, hook_output)
    return updated_messages, stream_text_replacements_from_hook_output(hook_output), options


async def _run_llm_after_stage(
    hook_meta: Dict[str, Any],
    *,
    content: str,
    duration_ms: int,
    error: Optional[BaseException] = None,
) -> None:
    from flocks.hooks.pipeline import HookPipeline, HookStage

    if not await HookPipeline.has_stage_handlers(HookStage.LLM_AFTER, hook_meta):
        return
    payload: Dict[str, Any] = {
        "durationMs": duration_ms,
        "response": {"role": "assistant", "content": content, "reasoning": "", "toolCalls": []},
        "usage": None,
    }
    if error is not None:
        payload["error"] = {"type": type(error).__name__, "message": str(error)}
    await HookPipeline.run_llm_after(hook_meta, payload)


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


@dataclass(frozen=True)
class _ResolvedTarget:
    provider_id: str
    model_id: str
    source: str


class LLMClient:
    """Minimal workflow LLM client backed by flocks Provider."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        *,
        provider_id: Optional[str] = None,
        cancel_checker: Optional[Callable[[], bool]] = None,
        hook_context: Optional[Dict[str, Any]] = None,
    ):
        # NOTE: This module is intentionally synchronous at the edges (workflow runtime),
        # but uses flocks Provider (async) internally for consistency with the rest of flocks.
        # Configuration should come from flocks Provider + Config (Provider.apply_config).
        # Avoid legacy dotenv/env-alias behavior here.

        self.provider_id = ((provider_id or "") or "").strip()
        self.model = ((model or "") or "").strip()
        self.api_key = (api_key or "").strip() or None
        self.base_url = (base_url or "").strip() or None
        self.cancel_checker = cancel_checker
        self.hook_context = {**_DEFAULT_LLM_HOOK_CONTEXT, **(hook_context or {})}
        workflow_llm_cfg = self._load_workflow_llm_config()
        # Only treat ``trust_env`` as user-specified when it actually appears
        # in workflow.llm config. Otherwise we leave the provider's existing
        # ``custom_settings['trust_env']`` untouched, which prevents workflow
        # calls from silently flipping the trust_env policy chosen by the
        # session / agent that shares the same Provider singleton.
        self.trust_env_explicit = "trust_env" in workflow_llm_cfg
        self.trust_env = _coerce_bool(workflow_llm_cfg.get("trust_env"), False)

        Provider._ensure_initialized()

    def _cancel_requested(self) -> bool:
        try:
            return bool(self.cancel_checker and self.cancel_checker())
        except Exception:
            return False

    def _raise_if_cancelled(self) -> None:
        if self._cancel_requested():
            raise RunCancelledError("<llm>")

    def _sleep_with_cancel(self, delay_s: float) -> None:
        if delay_s <= 0:
            return
        if self.cancel_checker is None:
            time.sleep(delay_s)
            return
        deadline = time.monotonic() + delay_s
        while True:
            self._raise_if_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.1, remaining))

    def _get_provider(self, provider_id: str) -> Any:
        provider = Provider.get(provider_id)
        if provider is None:
            raise ValueError(
                f"LLM provider not found: {provider_id!r}. "
                "Set provider config in ~/.flocks/config/flocks.json."
            )
        return provider

    def _load_workflow_llm_config(self) -> Dict[str, Any]:
        try:
            cfg = _run_coro_sync(Config.get(), self.cancel_checker)
        except Exception:
            return {}
        if not hasattr(cfg, "model_dump"):
            return {}
        dumped = cfg.model_dump(by_alias=True, exclude_none=True, mode="json")
        workflow_cfg = dumped.get("workflow") if isinstance(dumped, dict) else None
        if not isinstance(workflow_cfg, dict):
            return {}
        llm_cfg = workflow_cfg.get("llm")
        return llm_cfg if isinstance(llm_cfg, dict) else {}

    def _resolve_default_target(self) -> Optional[_ResolvedTarget]:
        try:
            default_llm = _run_coro_sync(
                Config.resolve_default_llm(),
                self.cancel_checker,
            )
        except Exception:
            return None
        if not default_llm:
            return None
        provider_id = str(default_llm.get("provider_id") or "").strip()
        model_id = str(default_llm.get("model_id") or "").strip()
        if provider_id and model_id:
            return _ResolvedTarget(provider_id=provider_id, model_id=model_id, source="default")
        return None

    def _split_model_reference(self, model: str) -> tuple[Optional[str], str]:
        model = str(model or "").strip()
        if "/" not in model:
            return None, model
        candidate_provider, candidate_model = model.split("/", 1)
        candidate_provider = candidate_provider.strip()
        candidate_model = candidate_model.strip()
        if not candidate_provider or not candidate_model:
            return None, model
        provider = Provider.get(candidate_provider)
        if provider is None:
            return None, model
        return candidate_provider, candidate_model

    def _resolve_requested_target(self) -> Optional[_ResolvedTarget]:
        provider_id = str(self.provider_id or "").strip()
        model_id = str(self.model or "").strip()
        if not model_id and not provider_id:
            return None

        if model_id and not provider_id:
            parsed_provider, parsed_model = self._split_model_reference(model_id)
            if parsed_provider:
                provider_id = parsed_provider
                model_id = parsed_model

        if provider_id and not model_id:
            default_target = self._resolve_default_target()
            if default_target and default_target.provider_id == provider_id:
                model_id = default_target.model_id

        if not provider_id and model_id:
            default_target = self._resolve_default_target()
            if default_target:
                provider_id = default_target.provider_id

        if provider_id and model_id:
            return _ResolvedTarget(provider_id=provider_id, model_id=model_id, source="requested")
        return None

    def _build_candidate_targets(self) -> list[_ResolvedTarget]:
        targets: list[_ResolvedTarget] = []
        seen: set[tuple[str, str]] = set()

        for target in [self._resolve_requested_target(), self._resolve_default_target()]:
            if target is None:
                continue
            key = (target.provider_id, target.model_id)
            if key in seen:
                continue
            seen.add(key)
            targets.append(target)

        if targets:
            return targets

        raise ValueError(
            "Workflow LLM 未配置可用模型。请在 `default_models.llm` 中设置全局默认模型，"
            "或在 workflow 节点/`llm.ask()` 中显式指定模型。"
        )

    def _validate_target(self, target: _ResolvedTarget) -> Optional[str]:
        try:
            provider = self._prepare_provider(target.provider_id)
        except Exception:
            return f"provider '{target.provider_id}' 不存在"

        is_configured = True
        if hasattr(provider, "is_configured"):
            try:
                is_configured = bool(provider.is_configured())
            except Exception:
                is_configured = True
        if not is_configured:
            return f"provider '{target.provider_id}' 未配置"

        try:
            models = Provider.list_models(target.provider_id)
        except Exception:
            models = []
        if models:
            available = {
                (getattr(model, "id", "") or "").strip()
                for model in models
                if (getattr(model, "id", "") or "").strip()
            }
            if available and target.model_id not in available:
                return (
                    f"model '{target.model_id}' 在 provider '{target.provider_id}' 中不存在"
                )
        return None

    def _clone_provider_for_workflow(self, shared_provider: Any) -> Any:
        """Create a workflow-local provider instance from the shared singleton.

        Workflow calls run on a dedicated background loop while session / agent
        calls run on the server loop. Sharing the same provider instance across
        those callers makes both ``_config`` and any cached async client
        (``_client``) a process-wide race point. The workflow therefore uses an
        isolated provider instance seeded from the shared provider's current
        config/runtime state, but never mutates the shared singleton itself.
        """
        try:
            isolated_provider = type(shared_provider)()
        except Exception:
            isolated_provider = copy(shared_provider)

        if isolated_provider is shared_provider:
            raise RuntimeError("Provider cannot create an isolated workflow instance")

        for attr in ("id", "name", "_api_key", "_base_url", "_endpoint"):
            if hasattr(shared_provider, attr):
                try:
                    setattr(isolated_provider, attr, getattr(shared_provider, attr))
                except Exception:
                    pass

        for attr in ("_config_models", "_custom_models"):
            if hasattr(shared_provider, attr):
                value = getattr(shared_provider, attr)
                cloned_value = list(value) if isinstance(value, list) else value
                try:
                    setattr(isolated_provider, attr, cloned_value)
                except Exception:
                    pass

        if hasattr(isolated_provider, "_client"):
            try:
                setattr(isolated_provider, "_client", None)
            except Exception as exc:
                raise RuntimeError("Cannot isolate workflow provider client") from exc

        return isolated_provider

    def _prepare_provider(self, provider_id: str) -> Any:
        """Build a workflow-local provider without mutating the shared singleton."""
        try:
            _run_coro_sync(
                Provider.apply_config(provider_id=provider_id),
                self.cancel_checker,
            )
        except Exception:
            # Keep workflow runtime resilient: provider apply_config failure
            # should not block ask() for environments driven by env vars.
            pass

        shared_provider = self._get_provider(provider_id)
        provider = self._clone_provider_for_workflow(shared_provider)
        cfg = getattr(shared_provider, "_config", None)
        existing_custom = getattr(cfg, "custom_settings", None) or {}
        custom_settings = (
            dict(existing_custom) if isinstance(existing_custom, dict) else {}
        )
        if self.trust_env_explicit:
            custom_settings["trust_env"] = self.trust_env

        desired_api_key = (
            self.api_key
            if self.api_key is not None
            else getattr(cfg, "api_key", None)
        )
        if desired_api_key is None:
            desired_api_key = getattr(shared_provider, "_api_key", None)

        desired_base_url = (
            self.base_url
            if self.base_url is not None
            else getattr(cfg, "base_url", None)
        )
        if desired_base_url is None:
            desired_base_url = getattr(shared_provider, "_base_url", None)
        if desired_base_url is None:
            desired_base_url = getattr(shared_provider, "_endpoint", None)

        if (
            cfg is not None
            or self.api_key is not None
            or self.base_url is not None
            or self.trust_env_explicit
        ):
            provider.configure(
                ProviderConfig(
                    provider_id=provider_id,
                    api_key=desired_api_key,
                    base_url=desired_base_url,
                    custom_settings=custom_settings,
                )
            )

        return provider

    def _format_target(self, target: _ResolvedTarget) -> str:
        return f"{target.provider_id}/{target.model_id}"

    def _raise_resolution_error(
        self,
        *,
        targets: list[_ResolvedTarget],
        preflight_errors: list[tuple[_ResolvedTarget, str]],
        runtime_errors: list[tuple[_ResolvedTarget, Exception]],
    ) -> None:
        details: list[str] = []
        if preflight_errors:
            details.extend(
                f"{self._format_target(target)}: {message}"
                for target, message in preflight_errors
            )
        if runtime_errors:
            details.extend(
                f"{self._format_target(target)}: {type(exc).__name__}: {exc}"
                for target, exc in runtime_errors
            )

        if self._resolve_requested_target() is not None and len(targets) > 1:
            raise ValueError(
                "Workflow 显式指定模型不可用，且回退到全局默认模型也失败。"
                + (" 详细信息: " + "；".join(details) if details else "")
            )
        raise ValueError(
            "Workflow 默认模型不可用。"
            + (" 详细信息: " + "；".join(details) if details else "")
        )

    def ask(
        self,
        prompt: str,
        temperature: float = 0.2,
        *,
        model: Optional[str] = None,
        provider_id: Optional[str] = None,
        timeout_s: Optional[float] = None,
        max_retries: int = 0,
        retry_delay_s: float = 1.0,
    ) -> str:
        self._raise_if_cancelled()
        if model is not None or provider_id is not None:
            return LLMClient(
                api_key=self.api_key,
                base_url=self.base_url,
                model=model if model is not None else self.model,
                provider_id=provider_id if provider_id is not None else self.provider_id,
                cancel_checker=self.cancel_checker,
                hook_context=self.hook_context,
            ).ask(
                prompt,
                temperature=temperature,
                timeout_s=timeout_s,
                max_retries=max_retries,
                retry_delay_s=retry_delay_s,
            )

        targets = self._build_candidate_targets()
        preflight_errors: list[tuple[_ResolvedTarget, str]] = []
        runtime_errors: list[tuple[_ResolvedTarget, Exception]] = []
        retry_count = max(0, int(max_retries))
        delay_s = max(0.0, float(retry_delay_s))

        for target in targets:
            self._raise_if_cancelled()
            validation_error = self._validate_target(target)
            if validation_error:
                preflight_errors.append((target, validation_error))
                continue

            provider = self._prepare_provider(target.provider_id)

            hook_meta = {
                **self.hook_context,
                "model": {"providerID": target.provider_id, "modelID": target.model_id},
            }
            if not hook_meta.get("workspace"):
                try:
                    import os

                    hook_meta["workspace"] = os.getcwd()
                except OSError:
                    pass

            async def _call_inner():
                from flocks.session.response_rewriter import transform_complete_response
                from flocks.provider.options import build_provider_options

                messages = [ChatMessage(role="user", content=prompt + "请使用中文输出。")]
                # Options belong to the actual attempt's target. The common
                # hook pipeline must see and be able to transform them too.
                # Workflow nodes call the non-streaming chat(); the session
                # defaults (extended thinking, catalog max_tokens) target the
                # streaming path. Anthropic's SDK rejects non-streaming calls
                # whose max_tokens may run past ten minutes, and Gemini thought
                # summaries would be mixed into the answer text.
                provider_options = build_provider_options(
                    target.provider_id,
                    target.model_id,
                    reasoning_enabled=False,
                    resolve_max_tokens=False,
                )
                provider_options["temperature"] = temperature
                # ``llm.before`` is the redaction call point; a failing hook must
                # not let the raw prompt through (same fail-closed rule as sessions).
                messages, replacements, provider_options = await _run_llm_before_stage(
                    hook_meta, messages, provider_options,
                )
                started = time.perf_counter()
                try:
                    coro = provider.chat(
                        model_id=target.model_id,
                        messages=messages,
                        **provider_options,
                    )
                    if timeout_s is not None and float(timeout_s) > 0:
                        response = await asyncio.wait_for(coro, timeout=float(timeout_s))
                    else:
                        response = await coro
                except Exception as exc:
                    try:
                        await _run_llm_after_stage(
                            hook_meta,
                            content="",
                            duration_ms=int((time.perf_counter() - started) * 1000),
                            error=exc,
                        )
                    except Exception:
                        pass
                    raise
                raw_content = str(getattr(response, "content", "") or "")
                # Same post-processing as every other model call site: a
                # registered response transform (Pro placeholder restoration /
                # rewriting) wins, otherwise the llm.before replacements are
                # restored locally.
                content = await transform_complete_response(
                    raw_content,
                    hook_meta,
                    legacy_replacements=replacements,
                )
                if content != raw_content:
                    try:
                        response.content = content
                    except Exception:
                        pass
                try:
                    await _run_llm_after_stage(
                        hook_meta,
                        content=content,
                        duration_ms=int((time.perf_counter() - started) * 1000),
                    )
                except Exception:
                    pass
                return response

            async def _call():
                try:
                    return await _call_inner()
                finally:
                    await _close_provider_client(provider)

            last_exc: Optional[Exception] = None
            for attempt in range(retry_count + 1):
                self._raise_if_cancelled()
                try:
                    response = _run_coro_sync(_call(), self.cancel_checker)
                    self.provider_id = target.provider_id
                    self.model = target.model_id
                    return str(getattr(response, "content", "") or "")
                except RunCancelledError:
                    raise
                except asyncio.CancelledError as exc:
                    raise RunCancelledError("<llm>") from exc
                except asyncio.TimeoutError as exc:
                    total_attempts = retry_count + 1
                    last_exc = TimeoutError(
                        f"LLM call timed out after {timeout_s}s "
                        f"(attempt {attempt + 1}/{total_attempts})"
                    )
                except LLMResponseSafetyError as exc:
                    last_exc = exc
                    break
                except Exception as exc:
                    last_exc = exc

                if attempt < retry_count and delay_s > 0:
                    self._sleep_with_cancel(delay_s)

            if last_exc is not None:
                runtime_errors.append((target, last_exc))

        self._raise_resolution_error(
            targets=targets,
            preflight_errors=preflight_errors,
            runtime_errors=runtime_errors,
        )


def get_llm_client(
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    provider_id: Optional[str] = None,
    cancel_checker: Optional[Callable[[], bool]] = None,
    hook_context: Optional[Dict[str, Any]] = None,
) -> LLMClient:
    return LLMClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider_id=provider_id,
        cancel_checker=cancel_checker,
        hook_context=hook_context,
    )


class LazyLLM:
    """Lazy facade for workflow `llm.ask(...)`."""

    def __init__(
        self,
        *,
        cancel_checker: Optional[Callable[[], bool]] = None,
        hook_context: Optional[Dict[str, Any]] = None,
    ):
        self.cancel_checker = cancel_checker
        self.hook_context = hook_context

    def ask(
        self,
        prompt: str,
        temperature: float = 0.2,
        *,
        model: Optional[str] = None,
        provider_id: Optional[str] = None,
        timeout_s: Optional[float] = None,
        max_retries: int = 0,
        retry_delay_s: float = 1.0,
    ) -> str:
        return get_llm_client(
            model=model,
            provider_id=provider_id,
            cancel_checker=self.cancel_checker,
            hook_context=self.hook_context,
        ).ask(
            prompt,
            temperature=temperature,
            timeout_s=timeout_s,
            max_retries=max_retries,
            retry_delay_s=retry_delay_s,
        )


_lazy_llm_singleton: Optional[LazyLLM] = None


def get_lazy_llm(
    *,
    cancel_checker: Optional[Callable[[], bool]] = None,
    hook_context: Optional[Dict[str, Any]] = None,
) -> LazyLLM:
    if cancel_checker is not None or hook_context is not None:
        return LazyLLM(cancel_checker=cancel_checker, hook_context=hook_context)
    global _lazy_llm_singleton
    if _lazy_llm_singleton is None:
        _lazy_llm_singleton = LazyLLM()
    return _lazy_llm_singleton
