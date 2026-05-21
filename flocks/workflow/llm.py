import asyncio
import threading
from dataclasses import dataclass
import time
from typing import Any, Dict, Optional
from weakref import WeakKeyDictionary

from flocks.config.config import Config
from flocks.provider.provider import ChatMessage, Provider, ProviderConfig
from flocks.workflow._async_runtime import run_sync as _run_sync_on_shared_loop
from flocks.workflow import _async_runtime as _async_runtime_mod


_provider_locks_guard = threading.Lock()
_provider_locks: Dict[str, threading.Lock] = {}

# Tracks which event loop currently "owns" each provider's ``_client``.
# Key   : the provider instance (weakly referenced so we don't pin singletons).
# Value : ``id(loop)`` of the loop that created or last validated ``_client``.
#
# Rationale: ``httpx.AsyncClient`` (and the OpenAI/Anthropic SDK clients that
# wrap it) bind themselves to the *currently running* event loop on first
# ``await``. The session uses uvicorn's main loop while workflows use the
# dedicated ``flocks-workflow-llm-loop``. If a workflow call inherits a
# provider client that the session created on the main loop, attempting to
# ``await`` it from the workflow loop raises "got Future attached to a
# different loop" / hangs. We track the owning loop here so that
# ``_prepare_provider`` can reset the client when the caller crosses loops,
# while still reusing the connection pool for back-to-back same-loop calls.
_provider_client_loop_marker: "WeakKeyDictionary[Any, int]" = WeakKeyDictionary()


def _get_provider_lock(provider_id: str) -> threading.Lock:
    """Return a process-wide lock keyed by ``provider_id``.

    Used to serialize the (apply_config -> configure -> reset _client) sequence
    in ``LLMClient._prepare_provider`` against any concurrent session/agent
    request reading ``provider._config`` / ``provider._client``. Locks are
    created lazily and cached forever — the set of provider ids is bounded
    and small.
    """
    lock = _provider_locks.get(provider_id)
    if lock is not None:
        return lock
    with _provider_locks_guard:
        lock = _provider_locks.get(provider_id)
        if lock is None:
            lock = threading.Lock()
            _provider_locks[provider_id] = lock
    return lock


def _workflow_loop_id() -> Optional[int]:
    """Return ``id(loop)`` of the shared workflow async loop, or ``None``.

    The workflow loop is created lazily on first ``_run_sync_on_shared_loop``
    call. We read it directly from ``_async_runtime`` so callers don't need to
    actually submit a coroutine just to discover the loop id.
    """
    loop = getattr(_async_runtime_mod, "_loop", None)
    return id(loop) if loop is not None else None


def _run_coro_sync(coro):
    """Run async provider calls from sync workflow code on the shared workflow loop.

    All workflow-triggered async work (config loading, provider.apply_config,
    provider.chat) is routed through a single persistent background loop so
    that loop-bound resources (httpx.AsyncClient, OpenAI streams) never
    outlive their owning loop. See ``flocks.workflow._async_runtime``.
    """
    return _run_sync_on_shared_loop(coro)


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
    ):
        # NOTE: This module is intentionally synchronous at the edges (workflow runtime),
        # but uses flocks Provider (async) internally for consistency with the rest of flocks.
        # Configuration should come from flocks Provider + Config (Provider.apply_config).
        # Avoid legacy dotenv/env-alias behavior here.

        self.provider_id = ((provider_id or "") or "").strip()
        self.model = ((model or "") or "").strip()
        self.api_key = (api_key or "").strip() or None
        self.base_url = (base_url or "").strip() or None
        workflow_llm_cfg = self._load_workflow_llm_config()
        # Only treat ``trust_env`` as user-specified when it actually appears
        # in workflow.llm config. Otherwise we leave the provider's existing
        # ``custom_settings['trust_env']`` untouched, which prevents workflow
        # calls from silently flipping the trust_env policy chosen by the
        # session / agent that shares the same Provider singleton.
        self.trust_env_explicit = "trust_env" in workflow_llm_cfg
        self.trust_env = _coerce_bool(workflow_llm_cfg.get("trust_env"), False)

        Provider._ensure_initialized()

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
            cfg = _run_coro_sync(Config.get())
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
            default_llm = _run_coro_sync(Config.resolve_default_llm())
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

    def _prepare_provider(self, provider_id: str) -> Any:
        """Apply workflow-side overrides on the shared Provider singleton.

        Concurrency contract:
            * The (apply_config -> configure -> _client reset) sequence is
              serialized per-provider via :func:`_get_provider_lock` so that
              an in-flight session ``provider.chat_stream`` cannot observe a
              half-mutated ``provider._config`` / ``provider._client``.
            * The reconfigure + client reset is *idempotent*: if the desired
              ProviderConfig (api_key / base_url / relevant custom_settings)
              already matches what the provider holds, we skip both
              ``provider.configure(...)`` and ``provider._client = None``.
              This protects long-running session HTTP connection pools from
              being recreated on every workflow ``llm.ask()`` call.
        """
        with _get_provider_lock(provider_id):
            try:
                _run_coro_sync(Provider.apply_config(provider_id=provider_id))
            except Exception:
                # Keep workflow runtime resilient: provider apply_config failure
                # should not block ask() for environments driven by env vars.
                pass

            provider = self._get_provider(provider_id)
            cfg = getattr(provider, "_config", None)
            existing_custom = getattr(cfg, "custom_settings", None) or {}
            custom_settings = (
                dict(existing_custom) if isinstance(existing_custom, dict) else {}
            )
            # Only override ``trust_env`` when the user explicitly set
            # ``workflow.llm.trust_env`` in flocks config. Otherwise inherit
            # whatever the session / agent already configured.
            if self.trust_env_explicit:
                custom_settings["trust_env"] = self.trust_env

            desired_api_key = (
                self.api_key
                if self.api_key is not None
                else getattr(cfg, "api_key", None)
            )
            desired_base_url = (
                self.base_url
                if self.base_url is not None
                else getattr(cfg, "base_url", None)
            )

            # Idempotency check: only reconfigure when something material
            # actually changed. ``custom_settings`` may legitimately carry
            # provider-specific tunables (verify_ssl, region, …) set by the
            # session — comparing the full dict avoids accidental drops.
            unchanged = (
                cfg is not None
                and getattr(cfg, "api_key", None) == desired_api_key
                and getattr(cfg, "base_url", None) == desired_base_url
                and (existing_custom if isinstance(existing_custom, dict) else {})
                == custom_settings
            )
            if not unchanged:
                provider.configure(
                    ProviderConfig(
                        provider_id=provider_id,
                        api_key=desired_api_key,
                        base_url=desired_base_url,
                        custom_settings=custom_settings,
                    )
                )

            # Decide whether the existing SDK client can be reused safely.
            #
            # Two cases force a client reset (i.e. ``_client = None`` so that
            # the next ``_get_client()`` rebuilds it on the workflow loop):
            #
            #   1. The provider config materially changed (handled above).
            #   2. The current ``_client`` was last used by a *different*
            #      event loop than the workflow loop. ``httpx.AsyncClient``
            #      binds to the loop where it first awaited; cross-loop
            #      reuse triggers "got Future attached to a different loop"
            #      or silent hangs.
            workflow_loop_id = _workflow_loop_id()
            client_obj = getattr(provider, "_client", None)
            client_loop_id = (
                _provider_client_loop_marker.get(provider)
                if client_obj is not None
                else None
            )
            must_reset_for_loop = (
                client_obj is not None
                and workflow_loop_id is not None
                and client_loop_id != workflow_loop_id
            )
            if not unchanged or must_reset_for_loop:
                if hasattr(provider, "_client"):
                    try:
                        setattr(provider, "_client", None)
                    except Exception:
                        pass
                # The next ``_get_client()`` will build a fresh client on
                # the workflow loop; remember that ownership.
                if workflow_loop_id is not None:
                    try:
                        _provider_client_loop_marker[provider] = workflow_loop_id
                    except TypeError:
                        # ``provider`` is not weak-referenceable (rare for
                        # test doubles) — fall back to a regular attribute.
                        try:
                            setattr(provider, "_workflow_client_loop_id", workflow_loop_id)
                        except Exception:
                            pass
            elif workflow_loop_id is not None and client_obj is not None:
                # Client is being reused for another workflow call on the
                # same loop — refresh the marker so we keep tracking it.
                try:
                    _provider_client_loop_marker[provider] = workflow_loop_id
                except TypeError:
                    pass
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
        if model is not None or provider_id is not None:
            return LLMClient(
                api_key=self.api_key,
                base_url=self.base_url,
                model=model if model is not None else self.model,
                provider_id=provider_id if provider_id is not None else self.provider_id,
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
            validation_error = self._validate_target(target)
            if validation_error:
                preflight_errors.append((target, validation_error))
                continue

            provider = self._prepare_provider(target.provider_id)

            async def _call():
                coro = provider.chat(
                    model_id=target.model_id,
                    messages=[ChatMessage(role="user", content=prompt + "请使用中文输出。")],
                    temperature=temperature,
                )
                if timeout_s is not None and float(timeout_s) > 0:
                    return await asyncio.wait_for(coro, timeout=float(timeout_s))
                return await coro

            last_exc: Optional[Exception] = None
            for attempt in range(retry_count + 1):
                try:
                    response = _run_coro_sync(_call())
                    self.provider_id = target.provider_id
                    self.model = target.model_id
                    return str(getattr(response, "content", "") or "")
                except asyncio.TimeoutError as exc:
                    total_attempts = retry_count + 1
                    last_exc = TimeoutError(
                        f"LLM call timed out after {timeout_s}s "
                        f"(attempt {attempt + 1}/{total_attempts})"
                    )
                except Exception as exc:
                    last_exc = exc

                if attempt < retry_count and delay_s > 0:
                    time.sleep(delay_s)

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
) -> LLMClient:
    return LLMClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider_id=provider_id,
    )


class LazyLLM:
    """Lazy facade for workflow `llm.ask(...)`."""

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
        return get_llm_client(model=model, provider_id=provider_id).ask(
            prompt,
            temperature=temperature,
            timeout_s=timeout_s,
            max_retries=max_retries,
            retry_delay_s=retry_delay_s,
        )


_lazy_llm_singleton: Optional[LazyLLM] = None


def get_lazy_llm() -> LazyLLM:
    global _lazy_llm_singleton
    if _lazy_llm_singleton is None:
        _lazy_llm_singleton = LazyLLM()
    return _lazy_llm_singleton
