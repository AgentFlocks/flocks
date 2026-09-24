import asyncio
from copy import copy
from dataclasses import dataclass
import os
import random
import time
from typing import Any, Callable, Dict, Optional

from flocks.config.config import Config
from flocks.provider.concurrency import (
    PRIORITY_BACKGROUND,
    acquire_llm_slot,
    report_provider_rate_limited,
)
from flocks.provider.provider import ChatMessage, Provider, ProviderConfig
from flocks.provider.sdk.openai_base import LLMResponseSafetyError
from flocks.workflow._async_runtime import (
    run_sync as _run_sync_on_shared_loop,
    run_sync_cancellable as _run_sync_cancellable_on_shared_loop,
)
from flocks.workflow.errors import RunCancelledError


def _run_coro_sync(
    coro,
    cancel_checker: Optional[Callable[[], bool]] = None,
):
    """Run async provider calls from sync workflow code on the shared workflow loop.

    All workflow-triggered async work (config loading, provider.apply_config,
    provider.chat) is routed through a single persistent background loop so
    that loop-bound resources (httpx.AsyncClient, OpenAI streams) never
    outlive their owning loop. See ``flocks.workflow._async_runtime``.
    """
    if cancel_checker is not None:
        return _run_sync_cancellable_on_shared_loop(coro, cancel_checker)
    return _run_sync_on_shared_loop(coro)


# Retries for workflow ``llm`` nodes. Only transient provider failures
# (429 / 5xx / timeouts / connection errors) are retried; the delay grows
# ``retry_delay_s * 2**attempt`` with jitter and honours ``retry-after``.
_ENV_NODE_MAX_RETRIES = "FLOCKS_WORKFLOW_LLM_MAX_RETRIES"
DEFAULT_LLM_NODE_MAX_RETRIES = 2
MAX_RETRY_DELAY_S = 30.0
# Applied the moment a 429 is seen, before the slot is handed on; the caller
# replaces it with the jittered delay once that is known.
MIN_RATE_LIMIT_COOLDOWN_S = 1.0


def default_llm_node_max_retries() -> int:
    raw = os.getenv(_ENV_NODE_MAX_RETRIES)
    if raw is None or not raw.strip():
        return DEFAULT_LLM_NODE_MAX_RETRIES
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return DEFAULT_LLM_NODE_MAX_RETRIES


def _exception_status_code(exc: BaseException) -> Optional[int]:
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        for value in (
            getattr(current, "status_code", None),
            getattr(response, "status_code", None),
        ):
            try:
                normalized = int(value) if value is not None else None
            except (TypeError, ValueError):
                normalized = None
            if normalized is not None and 100 <= normalized <= 599:
                return normalized
        current = current.__cause__ or current.__context__
    return None


def _exception_retry_after_s(exc: BaseException) -> Optional[float]:
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        headers = getattr(getattr(current, "response", None), "headers", None)
        if headers is not None:
            try:
                lowered = {str(key).lower(): value for key, value in dict(headers).items()}
            except (TypeError, ValueError):
                lowered = {}
            for header, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
                raw = lowered.get(header)
                if raw is None:
                    continue
                try:
                    parsed = float(raw)
                except (TypeError, ValueError):
                    continue
                if 0 < parsed < float("inf"):
                    return parsed * scale
        current = current.__cause__ or current.__context__
    return None


_RATE_LIMIT_MESSAGE_PATTERNS = ("rate limit", "rate_limit", "too many requests", "429")


def classify_transient_error(exc: BaseException) -> tuple[bool, bool]:
    """Return ``(retryable, rate_limited)`` for a provider exception.

    Everything is retried except failures that are deterministic by
    construction: HTTP 4xx other than 408/429 (bad request, auth, unknown
    model). Unknown errors without a status code stay retryable so a flaky
    gateway does not fail a workflow node on its first hiccup.

    An explicit status code always wins over the message text: provider
    errors routinely carry request ids and quota hints, so a 401 whose body
    mentions "429" must stay non-retryable. Text matching only fills in when
    no usable status code is attached.
    """
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True, False
    if isinstance(exc, LLMResponseSafetyError):
        # Size/encoding guards carry no status code and their text can contain
        # a byte count like "42900000 bytes received"; matching that as a rate
        # limit would cool the whole provider down over a local guard.
        return False, False
    status = _exception_status_code(exc)
    if status is not None:
        if status == 429:
            return True, True
        if 400 <= status < 500 and status != 408:
            return False, False
        return True, False
    lowered = str(exc).lower()
    if any(pattern in lowered for pattern in _RATE_LIMIT_MESSAGE_PATTERNS):
        return True, True
    return True, False


def _retry_delay_s(base_delay_s: float, attempt: int, exc: BaseException) -> float:
    hinted = _exception_retry_after_s(exc)
    if hinted is not None:
        return min(hinted, MAX_RETRY_DELAY_S) * random.uniform(1.0, 1.25)
    base_delay_s = max(0.0, base_delay_s)
    if base_delay_s == 0:
        return 0.0
    # The cap is reached long before attempt 60; shifting further would only
    # build a huge int and overflow the multiplication.
    base = min(base_delay_s * (2 ** min(attempt, 60)), MAX_RETRY_DELAY_S)
    half = base / 2.0
    return half + random.uniform(0.0, half)


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
            except Exception:
                pass

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

            async def _acquire_and_chat():
                # Same process-wide budget as sessions and subagents; workflow
                # traffic yields to interactive sessions when the cap is hit.
                slot = await acquire_llm_slot(
                    target.provider_id,
                    priority=PRIORITY_BACKGROUND,
                )
                try:
                    return await provider.chat(
                        model_id=target.model_id,
                        messages=[ChatMessage(role="user", content=prompt + "请使用中文输出。")],
                        temperature=temperature,
                    )
                except BaseException as exc:
                    # Set the cooldown before the slot changes hands: the
                    # waiter it goes to would otherwise reach the provider
                    # while the cooldown is still zero. The sync caller
                    # refines it with the jittered delay afterwards, and the
                    # limiter keeps whichever is longer.
                    _, rate_limited = classify_transient_error(exc)
                    if rate_limited:
                        report_provider_rate_limited(
                            target.provider_id,
                            _exception_retry_after_s(exc) or MIN_RATE_LIMIT_COOLDOWN_S,
                            reason="http_429",
                        )
                    raise
                finally:
                    slot.release()

            async def _call():
                # ``timeout_s`` bounds queueing for a slot plus the call itself,
                # so a saturated provider cannot stretch a 30 s budget to minutes.
                if timeout_s is not None and float(timeout_s) > 0:
                    return await asyncio.wait_for(_acquire_and_chat(), timeout=float(timeout_s))
                return await _acquire_and_chat()

            last_exc: Optional[Exception] = None
            for attempt in range(retry_count + 1):
                self._raise_if_cancelled()
                retryable = False
                rate_limited = False
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
                    retryable = True
                except LLMResponseSafetyError as exc:
                    last_exc = exc
                    break
                except Exception as exc:
                    last_exc = exc
                    retryable, rate_limited = classify_transient_error(exc)

                wait_s = _retry_delay_s(delay_s, attempt, last_exc)
                if rate_limited:
                    # Hold every caller of this provider back, even when this
                    # call has no retry budget left (``max_retries=0`` is the
                    # default for ``LazyLLM.ask``): the last 429 is exactly
                    # when the rest should stop piling on.
                    report_provider_rate_limited(
                        target.provider_id, wait_s, reason="http_429"
                    )
                if not retryable:
                    # Bad request / auth / unknown model: retrying cannot help,
                    # move on to the next candidate target (if any).
                    break
                if attempt < retry_count and wait_s > 0:
                    self._sleep_with_cancel(wait_s)

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
) -> LLMClient:
    return LLMClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider_id=provider_id,
        cancel_checker=cancel_checker,
    )


class LazyLLM:
    """Lazy facade for workflow `llm.ask(...)`."""

    def __init__(
        self,
        *,
        cancel_checker: Optional[Callable[[], bool]] = None,
    ):
        self.cancel_checker = cancel_checker

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
) -> LazyLLM:
    if cancel_checker is not None:
        return LazyLLM(cancel_checker=cancel_checker)
    global _lazy_llm_singleton
    if _lazy_llm_singleton is None:
        _lazy_llm_singleton = LazyLLM()
    return _lazy_llm_singleton
