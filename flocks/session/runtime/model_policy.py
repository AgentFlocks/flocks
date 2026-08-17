"""Session-owned model routing and cross-model candidate policy."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Optional

from flocks.session.runtime.contracts import RuntimeModel
from flocks.provider.provider import Provider
from flocks.session.message import Message
from flocks.session.session import Session, is_model_auto_session_category
from flocks.utils.log import Log


log = Log.create(service="session.model_policy")


@dataclass
class AutoFailoverCooldown:
    """Process-local starting candidate cooldown for automatic routing."""

    model: RuntimeModel
    primary: RuntimeModel
    expires_at: float
    reason: str


ModelValidator = Callable[..., Awaitable[tuple[bool, str]]]


class ModelRoutingPolicy:
    """Own candidate discovery, per-turn routing, and failover cooldown state."""

    def __init__(self) -> None:
        self.cooldowns: dict[str, AutoFailoverCooldown] = {}

    def clear(self, session_id: str) -> None:
        """Clear process-local routing state for one session."""
        self.cooldowns.pop(session_id, None)

    async def validate_runtime_model(
        self,
        provider_id: str,
        model_id: str,
        *,
        config: Optional[Any] = None,
    ) -> tuple[bool, str]:
        """Validate a configured LLM candidate without a network health probe."""
        from flocks.config.config import Config
        from flocks.provider.model_manager import get_model_manager
        from flocks.provider.types import ModelType

        Provider._ensure_initialized()
        config = config or await Config.get()
        if provider_id in (getattr(config, "disabled_providers", None) or []):
            return False, "provider_disabled"
        enabled_providers = getattr(config, "enabled_providers", None) or []
        if enabled_providers and provider_id not in enabled_providers:
            return False, "provider_disabled"
        try:
            await Provider.apply_config(config, provider_id=provider_id)
        except Exception as exc:
            log.warn(
                "session.model.candidate_config_failed",
                {
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "error": str(exc),
                },
            )
            return False, "provider_config_error"

        provider = Provider.get(provider_id)
        if provider is None:
            return False, "provider_not_found"

        model_manager = get_model_manager()
        definition = model_manager.get_model(provider_id, model_id)
        if definition is None:
            return False, "model_not_found"
        if getattr(definition, "model_type", None) != ModelType.LLM:
            return False, "not_llm"

        setting = model_manager.get_setting(provider_id, model_id)
        if setting is not None and not setting.enabled:
            return False, "model_disabled"
        if not provider.is_configured():
            return False, "provider_not_configured"
        return True, "available"

    async def build_candidates(
        self,
        primary: RuntimeModel,
        *,
        route_seed: str,
        preferred: Optional[RuntimeModel] = None,
        config: Optional[Any] = None,
        validate_model: Optional[ModelValidator] = None,
    ) -> list[RuntimeModel]:
        """Build a configured chain or stable automatic discovery chain."""
        from flocks.config.config import Config
        from flocks.provider.model_manager import get_model_manager
        from flocks.provider.types import ModelType

        validate_model = validate_model or self.validate_runtime_model
        config = config or await Config.get()
        await Provider.apply_config(config)

        configured_fallbacks = getattr(config, "fallback_providers", None) or []
        if configured_fallbacks:
            candidates = [primary]
            seen = {(primary.provider_id, primary.model_id)}
            for index, raw in enumerate(configured_fallbacks):
                provider_id = raw.get("provider_id") if isinstance(raw, dict) else raw.provider_id
                model_id = raw.get("model_id") if isinstance(raw, dict) else raw.model_id
                candidate = RuntimeModel(provider_id=provider_id, model_id=model_id)
                identity = (candidate.provider_id, candidate.model_id)
                if identity in seen:
                    continue
                seen.add(identity)

                available, reason = await validate_model(
                    candidate.provider_id,
                    candidate.model_id,
                    config=config,
                )
                if not available:
                    log.warn(
                        "session.model.fallback_skipped",
                        {
                            "provider_id": candidate.provider_id,
                            "model_id": candidate.model_id,
                            "configured_index": index,
                            "reason": reason,
                        },
                    )
                    continue
                candidates.append(candidate)
            return candidates

        definitions = get_model_manager().list_models(
            model_type=ModelType.LLM,
            enabled_only=True,
        )
        discovered = {RuntimeModel(definition.provider_id, definition.id) for definition in definitions}
        discovered.discard(primary)

        same_provider: list[RuntimeModel] = []
        other_providers: list[RuntimeModel] = []
        for candidate in sorted(
            discovered,
            key=lambda item: (item.provider_id, item.model_id),
        ):
            available, reason = await validate_model(
                candidate.provider_id,
                candidate.model_id,
                config=config,
            )
            if not available:
                log.debug(
                    "session.model.fallback_skipped",
                    {
                        "provider_id": candidate.provider_id,
                        "model_id": candidate.model_id,
                        "reason": reason,
                    },
                )
                continue
            if candidate.provider_id == primary.provider_id:
                same_provider.append(candidate)
            else:
                other_providers.append(candidate)

        candidates = [primary]
        for tier, pool in (
            ("same_provider", same_provider),
            ("other_provider", other_providers),
        ):
            if not pool:
                continue
            selected = (
                preferred
                if preferred is not None and preferred in pool
                else self._stable_candidate_choice(pool, route_seed, tier)
            )
            candidates.append(selected)
        return candidates

    @staticmethod
    def _stable_candidate_choice(
        candidates: list[RuntimeModel],
        route_seed: str,
        tier: str,
    ) -> RuntimeModel:
        """Choose pseudo-randomly without process-randomized hash values."""
        ordered = sorted(
            candidates,
            key=lambda item: (item.provider_id, item.model_id),
        )
        digest = hashlib.sha256(f"{route_seed}\0{tier}".encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], "big") % len(ordered)
        return ordered[index]

    async def validate_auto_configuration(self) -> tuple[bool, str]:
        """Validate that a newly selected Auto mode has a usable primary."""
        from flocks.config.config import Config

        default_llm = await Config.resolve_default_llm()
        if not default_llm:
            return False, "default_model_missing"
        available, reason = await self.validate_runtime_model(
            default_llm["provider_id"],
            default_llm["model_id"],
        )
        if not available:
            return False, f"primary_{reason}"
        return True, "available"

    def active_cooldown_model(
        self,
        session_id: str,
        primary: RuntimeModel,
    ) -> Optional[RuntimeModel]:
        """Return a valid cooldown target for the current primary model."""
        cooldown = self.cooldowns.get(session_id)
        if cooldown is None:
            return None
        if cooldown.expires_at <= time.monotonic() or cooldown.primary != primary:
            self.cooldowns.pop(session_id, None)
            return None
        return cooldown.model

    def cooldown_candidate_index(
        self,
        session_id: str,
        candidates: list[RuntimeModel],
    ) -> int:
        """Resolve the candidate index selected by an active cooldown."""
        if not candidates:
            return 0
        cooldown_model = self.active_cooldown_model(session_id, candidates[0])
        if cooldown_model is None:
            return 0
        try:
            return candidates.index(cooldown_model)
        except ValueError:
            self.cooldowns.pop(session_id, None)
            return 0

    @staticmethod
    def select_candidate(context: Any, index: int) -> None:
        """Activate a candidate and invalidate model-specific runner caches."""
        candidate = context.model_candidates[index]
        context.candidate_index = index
        context.provider_id = candidate.provider_id
        context.model_id = candidate.model_id
        context.session.provider = candidate.provider_id
        context.session.model = candidate.model_id
        tool_loop_guard = context.step_static_cache.get("tool_loop_guard")
        context.step_static_cache.clear()
        if tool_loop_guard is not None:
            context.step_static_cache["tool_loop_guard"] = tool_loop_guard

    async def reset_turn_candidates(
        self,
        context: Any,
        primary: RuntimeModel,
        user_message_id: str,
        config: Any,
    ) -> int:
        """Rebuild and activate the model chain for one logical user turn."""
        configured = bool(getattr(config, "fallback_providers", None))
        if configured:
            self.clear(context.session.id)
            preferred = None
        else:
            preferred = self.active_cooldown_model(context.session.id, primary)

        context.model_candidates = await self.build_candidates(
            primary,
            route_seed=f"{context.session.id}:{user_message_id}",
            preferred=preferred,
            config=config,
        )
        context.model_candidate_policy = "configured" if configured else "automatic"
        context.auto_failover = True
        next_index = (
            0
            if configured
            else self.cooldown_candidate_index(
                context.session.id,
                context.model_candidates,
            )
        )
        self.select_candidate(context, next_index)
        return next_index

    async def prepare_turn(self, context: Any, last_user: Any) -> bool:
        """Synchronize model routing when a new real user turn begins."""
        if last_user.id == context.turn_user_id:
            return False

        parts = await Message.parts(last_user.id, context.session.id)
        if any(bool(getattr(part, "synthetic", False)) for part in parts):
            return False

        if context.turn_user_id is None:
            context.turn_user_id = last_user.id
            if context.auto_failover and context.auto_failover_allowed:
                from flocks.config.config import Config

                await self.reset_turn_candidates(
                    context,
                    context.model_candidates[0],
                    last_user.id,
                    config=await Config.get(),
                )
            return True

        context.turn_user_id = last_user.id
        persisted_session = await Session.get_by_id(context.session.id)
        persisted_model_auto = bool(
            persisted_session
            and is_model_auto_session_category(getattr(persisted_session, "category", "user"))
            and getattr(persisted_session, "model_auto", False)
        )
        persisted_auto = persisted_model_auto and context.auto_failover_allowed

        user_model = getattr(last_user, "model", None)
        user_provider_id = None
        user_model_id = None
        if isinstance(user_model, dict):
            user_provider_id = user_model.get("providerID") or user_model.get("provider_id")
            user_model_id = user_model.get("modelID") or user_model.get("model_id")

        if not persisted_auto:
            context.auto_failover = False
            if not persisted_model_auto:
                self.clear(context.session.id)
                context.auto_failover_allowed = False
            provider_id = (
                getattr(persisted_session, "provider", None)
                if Session.has_pinned_model(persisted_session)
                else user_provider_id
            ) or context.provider_id
            model_id = (
                getattr(persisted_session, "model", None)
                if Session.has_pinned_model(persisted_session)
                else user_model_id
            ) or context.model_id
            context.model_candidates = [RuntimeModel(provider_id, model_id)]
            context.model_candidate_policy = "fixed"
            self.select_candidate(context, 0)
            log.info(
                "session.model.auto_disabled_for_turn",
                {
                    "session_id": context.session.id,
                    "provider_id": provider_id,
                    "model_id": model_id,
                },
            )
            return True

        from flocks.config.config import Config

        config = await Config.get()
        previous = RuntimeModel(context.provider_id, context.model_id)
        default_llm = await Config.resolve_default_llm()
        primary = RuntimeModel(
            provider_id=(default_llm or {}).get("provider_id") or user_provider_id or context.provider_id,
            model_id=(default_llm or {}).get("model_id") or user_model_id or context.model_id,
        )
        next_index = await self.reset_turn_candidates(
            context,
            primary,
            last_user.id,
            config=config,
        )
        active = context.model_candidates[next_index]
        log.info(
            "session.model.auto_turn_reset",
            {
                "session_id": context.session.id,
                "from_provider_id": previous.provider_id,
                "from_model_id": previous.model_id,
                "to_provider_id": active.provider_id,
                "to_model_id": active.model_id,
                "cooldown_active": next_index > 0,
            },
        )
        return True


DEFAULT_MODEL_ROUTING_POLICY = ModelRoutingPolicy()
