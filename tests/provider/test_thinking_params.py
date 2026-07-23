"""
Regression net for transport-driven thinking-params dispatch.

Background
----------

The original dispatch in ``flocks/provider/options.py`` matched the model name
against a hard-coded substring whitelist (``qwen3`` / ``kimi`` / ``mimo`` / …)
to decide whether to send ``extra_body.enable_thinking: true``.  Models whose
names didn't match the magic substrings were silently sent without the
thinking flag, causing the upstream API to short-circuit with
``finish_reason=stop`` and an empty content block — the user-visible
"agent stopped, please say 'continue'" symptom seen in
``ses_1628dfe6cffe1i5xZY9lv1u20m``.

The new dispatch is transport-driven:

  - ``reasoning_transport == anthropic_messages``  →  ``thinking={type: "enabled", budget_tokens:...}``
  - ``reasoning_transport == generic_chat``        →  provider-specific ``extra_body`` params

The gate is the resolved ``interleaved_capability``: catalog explicit
declaration wins, with the series-token inference in
``interleaved.infer_interleaved_capability`` (qwen3 / glm-* / kimi-k2* /
deepseek-v4* / step-3.5* / minimax-m* / …) as the fallback.  Adding a new
provider or a new model from a known family needs zero dispatcher changes.

These tests verify four properties of the new path:

1. Every (provider, model) pair with ``interleaved != null`` in catalog.json
   produces a non-empty thinking signal (extra_body or thinking=).  This is
   the systematic regression net — any new model added to the catalog with
   interleaved thinking will be covered.
2. The specific GLM-5 / alibaba configuration that triggered the original
   trace bug now emits the right flag.
3. The ``openai_compatible`` provider no longer swallows caller-supplied
   ``extra_body`` in its chat_stream() path.
4. A model not in catalog but matching a known series token still gets the
   flag — the series-token fallback closes the "I forgot to add the model
   to catalog" gap.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, Tuple

import pytest

from flocks.provider import model_catalog
from flocks.provider import options as provider_options

DEEPSEEK_THINKING_EXTRA_BODY = {"thinking": {"type": "enabled"}}
GLM_THINKING_EXTRA_BODY = {"thinking": {"type": "enabled", "clear_thinking": False}}
KIMI_THINKING_EXTRA_BODY = {"thinking": {"type": "enabled"}}
MIMO_THINKING_EXTRA_BODY = {"thinking": {"type": "enabled"}}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_interleaved_catalog_entries() -> Iterator[Tuple[str, str]]:
    """Yield (provider_id, model_id) for every catalog entry that declares interleaved thinking.

    Mirrors the jq query used during the audit:
        .<provider>.models | to_entries[]
        | select(.value.capabilities.interleaved != null)
        | ["<provider>", "<model_id>"]
    """
    raw = model_catalog.get_raw_catalog()
    for provider_id, provider_entry in raw.items():
        if not isinstance(provider_entry, dict):
            continue
        models = provider_entry.get("models")
        if not isinstance(models, dict):
            continue
        for model_id, model_entry in models.items():
            if not isinstance(model_entry, dict):
                continue
            capabilities = model_entry.get("capabilities") or {}
            if not isinstance(capabilities, dict):
                continue
            if capabilities.get("interleaved") is not None:
                yield provider_id, model_id


def _expected_generic_chat_extra_body(
    provider_id: str,
    model_id: str,
) -> Dict[str, Any]:
    """Return the expected OpenAI-compatible thinking control payload."""
    provider_lower = provider_id.lower()
    model_lower = model_id.lower()

    if "deepseek" in model_lower or provider_lower == "deepseek":
        return DEEPSEEK_THINKING_EXTRA_BODY
    if "glm" in model_lower or provider_lower == "zhipu":
        return GLM_THINKING_EXTRA_BODY
    if "mimo" in model_lower:
        return MIMO_THINKING_EXTRA_BODY
    if "kimi-k3" in model_lower:
        return {"reasoning_effort": "max"}
    if "kimi-k2.7" in model_lower:
        return KIMI_THINKING_EXTRA_BODY
    if "kimi" in model_lower:
        return KIMI_THINKING_EXTRA_BODY
    if "minimax" in model_lower or provider_lower == "minimax":
        return {"reasoning_split": True}
    return {"enable_thinking": True}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCatalogInterleavedCoverage:
    """Property test: every interleaved catalog entry resolves to a thinking flag.

    The dispatch is now transport-driven, not provider-driven — every
    interleaved catalog entry should land in *some* thinking signal.  The
    series-token fallback (in ``interleaved.infer_interleaved_capability``)
    is exercised by a separate test below.
    """

    @pytest.mark.parametrize("provider_id,model_id", list(_iter_interleaved_catalog_entries()))
    def test_interleaved_model_gets_thinking_flag(self, provider_id: str, model_id: str) -> None:
        # Patch the interleaved capability so the dispatch gate fires
        # regardless of the test environment's catalog resolution path.
        original = provider_options._resolve_interleaved_capability
        provider_options._resolve_interleaved_capability = lambda *_args, **_kw: {
            "field": "reasoning_content",
            "echo": "tool_calls",
            "cross_provider_policy": "promote",
        }
        try:
            options = provider_options.build_provider_options(
                provider_id,
                model_id,
                resolve_max_tokens=False,
            )
        finally:
            provider_options._resolve_interleaved_capability = original

        # The dispatch should produce SOME thinking signal.  We accept
        # either extra_body (OpenAI-compat family) or a top-level reasoning
        # field (Anthropic/Google family).  The catalog is already filtered
        # to interleaved-only entries so neither should be empty.
        has_extra_body = bool(options.get("extra_body"))
        has_thinking = bool(options.get("thinking"))
        has_reasoning_effort = bool(options.get("reasoningEffort"))
        has_thinking_config = bool(options.get("thinkingConfig"))
        has_thinking_level = bool(options.get("thinkingLevel"))
        assert (
            has_extra_body
            or has_thinking
            or has_reasoning_effort
            or has_thinking_config
            or has_thinking_level
        ), (
            f"{provider_id}/{model_id} declares interleaved in catalog but "
            f"build_provider_options emitted no thinking field. "
            f"options={options!r}"
        )

    @pytest.mark.parametrize("provider_id,model_id", list(_iter_interleaved_catalog_entries()))
    def test_interleaved_model_gets_official_generic_chat_payload(
        self,
        provider_id: str,
        model_id: str,
    ) -> None:
        """Every catalog-declared generic-chat model emits its official payload."""
        options = provider_options.build_provider_options(
            provider_id,
            model_id,
            resolve_max_tokens=False,
        )

        assert options.get("extra_body") == _expected_generic_chat_extra_body(
            provider_id,
            model_id,
        ), f"{provider_id}/{model_id} emitted unexpected options={options!r}"


class TestGLM5TraceReplay:
    """Specific regression for ses_1628dfe6cffe1i5xZY9lv1u20m step 50.

    Trace showed: GLM-5 on alibaba, tools present, returned
    ``finishReason=stop, content=495, toolCallCount=0`` because the request
    went out without a thinking payload.  After the fix, the request body
    should include GLM's official thinking object.
    """

    def test_glm5_alibaba_emits_official_thinking_payload(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args, **_kw: {
                "field": "reasoning_content",
                "echo": "tool_calls",
                "cross_provider_policy": "promote",
            },
        )

        options = provider_options.build_provider_options(
            "alibaba",
            "GLM-5",
            resolve_max_tokens=False,
        )

        assert "extra_body" in options, (
            "alibaba/GLM-5 catalog declares interleaved but no extra_body emitted — "
            "this is the exact regression that caused ses_1628dfe6cffe1i5xZY9lv1u20m"
        )
        assert options["extra_body"] == GLM_THINKING_EXTRA_BODY

    @pytest.mark.parametrize(
        "provider_id",
        ["alibaba", "threatbook-cn-llm", "threatbook-io-llm", "zhipu"],
    )
    def test_glm5_emits_official_thinking_payload_on_every_provider(
        self, provider_id: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args, **_kw: {
                "field": "reasoning_content",
                "echo": "tool_calls",
                "cross_provider_policy": "promote",
            },
        )

        options = provider_options.build_provider_options(
            provider_id,
            "GLM-5",
            resolve_max_tokens=False,
        )
        assert options["extra_body"] == GLM_THINKING_EXTRA_BODY

    @pytest.mark.parametrize(
        "provider_id,model_id,field,expected_extra_body",
        [
            ("threatbook-cn-llm", "minimax-m2.5", "reasoning_details", {"reasoning_split": True}),
            ("threatbook-cn-llm", "minimax-m2.7", "reasoning_details", {"reasoning_split": True}),
            ("threatbook-cn-llm", "minimax-m3", "reasoning_details", {"reasoning_split": True}),
            ("threatbook-io-llm", "minimax-m2.5", "reasoning_details", {"reasoning_split": True}),
            ("threatbook-io-llm", "minimax-m2.7", "reasoning_details", {"reasoning_split": True}),
            ("threatbook-io-llm", "minimax-m3", "reasoning_details", {"reasoning_split": True}),
            ("minimax", "minimax-m2.5", "reasoning_details", {"reasoning_split": True}),
            ("deepseek", "deepseek-v4-pro", "reasoning_content", DEEPSEEK_THINKING_EXTRA_BODY),
            ("stepfun", "step-3.5-flash", "reasoning_content", {"enable_thinking": True}),
        ],
    )
    def test_previously_dropped_models_now_get_flag(
        self,
        provider_id: str,
        model_id: str,
        field: str,
        expected_extra_body: Dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args, **_kw: {
                "field": field,
                "echo": "tool_calls",
                "cross_provider_policy": "promote",
            },
        )

        options = provider_options.build_provider_options(
            provider_id,
            model_id,
            resolve_max_tokens=False,
        )
        assert options.get("extra_body") == expected_extra_body, (
            f"{provider_id}/{model_id} — catalog says interleaved, dispatch "
            f"should have emitted {expected_extra_body}"
        )


class TestDispatchShape:
    """Sanity checks on the dispatch itself now that the
    provider-keyed shape registry is gone.

    The dispatch is now transport-driven: ``anthropic_messages`` →
    ``thinking={type: "enabled", ...}``; ``generic_chat`` →
    provider-specific ``extra_body``.  Catalog explicit declaration wins,
    with the series-token inference in ``interleaved.infer_interleaved_capability``
    as fallback for any model the catalog forgot to declare.
    """

    def test_no_legacy_token_constant(self) -> None:
        """The token-substring whitelist must be gone — that was the bug surface."""
        assert not hasattr(
            provider_options, "_ENABLE_THINKING_EXTRA_BODY_TOKENS"
        ), (
            "_ENABLE_THINKING_EXTRA_BODY_TOKENS should be removed; the catalog "
            "interleaved field is now the only trigger"
        )

    def test_no_shape_registry(self) -> None:
        """The provider-keyed shape registry is gone — every entry produced
        the same dict, so the indirection wasn't earning its keep.  Wire format
        is now decided by ``reasoning_transport`` alone.
        """
        assert not hasattr(provider_options, "_THINKING_REQUEST_SHAPES"), (
            "_THINKING_REQUEST_SHAPES should be removed; dispatch is now "
            "transport-driven, not provider-driven"
        )
        assert not hasattr(provider_options, "_openai_base_thinking_shape"), (
            "_openai_base_thinking_shape should be removed; generic_chat "
            "interleaved emits extra_body inline"
        )

    def test_explicit_reasoning_toggle_propagates(self) -> None:
        """``reasoning_enabled=False`` should produce ``enable_thinking: false``
        on a generic_chat transport, mirroring the old token-matching branch's
        behavior so the upstream API gets an explicit opt-out signal.
        """
        options = provider_options.build_provider_options(
            "threatbook-cn-llm",
            "qwen3.6-plus",
            reasoning_enabled=False,
            resolve_max_tokens=False,
        )
        assert options["extra_body"]["enable_thinking"] is False

    @pytest.mark.parametrize(
        "configured_extra_body",
        [
            {"chat_template_kwargs": {"enable_thinking": True}},
            {"chat_template_kwargs": {"thinking": True}},
        ],
    )
    def test_configured_extra_body_overrides_auto_generic_shape(
        self,
        configured_extra_body: Dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OpenAI-compatible users can declare provider-specific request bodies.

        vLLM/SGLang thinking switches are nested under
        ``chat_template_kwargs``.  If configured, Flocks should forward the
        exact shape instead of adding its default ``enable_thinking`` flag for
        qwen-style generic chat models.
        """
        monkeypatch.setattr(
            provider_options,
            "_resolve_default_extra_body",
            lambda *_args, **_kw: configured_extra_body,
        )

        options = provider_options.build_provider_options(
            "openai-compatible",
            "qwen3-7b",
            resolve_max_tokens=False,
        )

        assert options.get("extra_body") == configured_extra_body

    @pytest.mark.parametrize(
        "configured_extra_body",
        [
            {"chat_template_kwargs": {"enable_thinking": True}},
            {"chat_template_kwargs": {"thinking": True}},
        ],
    )
    def test_configured_extra_body_emits_without_interleaved_inference(
        self,
        configured_extra_body: Dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit extra_body config should not depend on model-name inference."""
        monkeypatch.setattr(
            provider_options,
            "_resolve_default_extra_body",
            lambda *_args, **_kw: configured_extra_body,
        )
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args, **_kw: None,
        )

        options = provider_options.build_provider_options(
            "openai-compatible",
            "local-sglang-model",
            resolve_max_tokens=False,
        )

        assert options.get("extra_body") == configured_extra_body

    def test_anthropic_transport_still_uses_thinking_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Anthropic transport branch is unchanged: it must continue to
        emit ``thinking={type: "enabled", ...}``, never ``extra_body``.

        This pins the contract that the new generic_chat branch did not
        regress the anthropic_messages path.
        """
        from flocks.provider.interleaved import REASONING_TRANSPORT_ANTHROPIC_MESSAGES

        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args, **_kw: {
                "field": "thinking",
                "echo": "tool_calls",
                "cross_provider_policy": "preserve",
            },
        )
        monkeypatch.setattr(
            provider_options,
            "_resolve_reasoning_transport",
            lambda *_args, **_kw: REASONING_TRANSPORT_ANTHROPIC_MESSAGES,
        )
        options = provider_options.build_provider_options(
            "anthropic", "claude-sonnet-4-20250514", resolve_max_tokens=False,
        )
        assert "thinking" in options
        assert options["thinking"]["type"] == "enabled"
        assert "extra_body" not in options

    @pytest.mark.parametrize(
        "model_id,expected_extra_body",
        [
            # A model that is NOT in any catalog but matches a known series
            # token in ``infer_interleaved_capability``.  Demonstrates that
            # the series-token fallback (catalog → inference) still produces
            # the right wire format.  Model ids here are constructed to
            # embed a real token from ``_PROMOTE_REASONING_CONTENT_TOKENS`` /
            # ``_STRICT_REASONING_CONTENT_TOKENS`` so the substring match
            # fires regardless of where Flocks runs the test.
            ("qwen3-7b-uncatalogued", {"enable_thinking": True}),
            ("glm-5-uncatalogued", GLM_THINKING_EXTRA_BODY),
            ("kimi-k2.6-uncatalogued", KIMI_THINKING_EXTRA_BODY),
            ("kimi-k2.7-code-uncatalogued", KIMI_THINKING_EXTRA_BODY),
            ("kimi-k3-uncatalogued", {"reasoning_effort": "max"}),
            ("mimo-v2.5-pro-uncatalogued", MIMO_THINKING_EXTRA_BODY),
            ("minimax-m4-uncatalogued", {"reasoning_split": True}),
            ("step-3.5-flash-uncatalogued", {"enable_thinking": True}),
        ],
    )
    def test_series_token_fallback_emits_expected_extra_body(
        self,
        model_id: str,
        expected_extra_body: Dict[str, Any],
    ) -> None:
        """Models matching a known series token in
        ``infer_interleaved_capability`` get the expected extra_body on the wire
        even when the catalog has no explicit declaration for them.

        This is the regression net for the design choice that the dispatch
        is *not* provider-keyed: a user-configured openai-compatible
        endpoint pointing at a known family Just Works, without requiring
        anyone to edit a per-provider registry.
        """
        options = provider_options.build_provider_options(
            "openai-compatible", model_id, resolve_max_tokens=False,
        )
        assert options.get("extra_body") == expected_extra_body, (
            f"openai-compatible/{model_id} matches a known series token; "
            "series-token fallback should have inferred interleaved and "
            f"emitted {expected_extra_body}. options={options!r}"
        )

    @pytest.mark.parametrize(
        "provider_id,model_id,expected_extra_body",
        [
            ("deepseek", "deepseek-v4-flash", DEEPSEEK_THINKING_EXTRA_BODY),
            ("minimax", "minimax-m3", {"reasoning_split": True}),
            ("stepfun", "step-3.5-flash", {"enable_thinking": True}),
            ("zhipu", "glm-4.7", GLM_THINKING_EXTRA_BODY),
        ],
    )
    def test_real_catalog_chain_emits_expected_extra_body(
        self,
        provider_id: str,
        model_id: str,
        expected_extra_body: Dict[str, Any],
    ) -> None:
        """Exercise catalog/inference/dispatch without monkeypatching."""
        options = provider_options.build_provider_options(
            provider_id,
            model_id,
            resolve_max_tokens=False,
        )
        assert options.get("extra_body") == expected_extra_body


class TestOpenAICompatibleExtraBody:
    """Verify the SDK now propagates caller-supplied ``extra_body`` instead
    of silently swallowing it.  This is the second-order bug: even if
    ``build_provider_options`` produces the right shape, ``chat_stream`` /
    ``chat`` in ``openai_compatible.py`` dropped the kwargs it received.
    """

    def test_chat_non_streaming_propagates_extra_body(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The non-streaming ``chat`` path must preserve extra_body too."""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from flocks.provider.sdk.openai_compatible import OpenAICompatibleProvider

        captured: Dict[str, Any] = {}

        class _FakeCompletions:
            async def create(self, **kwargs: Any) -> Any:
                captured.update(kwargs)
                return SimpleNamespace(
                    id="chatcmpl-test",
                    model="qwen3-235b-a22b-thinking",
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="ok"),
                            finish_reason="stop",
                        )
                    ],
                    usage=SimpleNamespace(
                        prompt_tokens=1,
                        completion_tokens=1,
                        total_tokens=2,
                    ),
                )

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeClient:
            chat = _FakeChat()

        provider = OpenAICompatibleProvider()
        provider._get_client = MagicMock(return_value=_FakeClient())  # type: ignore[method-assign]

        asyncio.run(
            provider.chat(
                "qwen3-235b-a22b-thinking",
                messages=[],
                extra_body={"enable_thinking": True},
            )
        )

        assert captured.get("extra_body") == {"enable_thinking": True}, (
            "openai_compatible.chat swallowed caller-supplied extra_body"
        )

    def test_chat_stream_propagates_extra_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Smoke test that an ``extra_body`` kwarg passed to ``chat_stream``
        ends up in the outgoing request params.

        We mock the OpenAI client so we don't need a live API, then assert
        the captured kwargs include the extra_body we passed in.  The fake
        stream yields one minimal chunk so the empty-response fallback (which
        would call the non-streaming ``chat``) doesn't fire — the non-stream
        path has its own check in
        ``test_chat_non_streaming_propagates_extra_body``.
        """
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from flocks.provider.sdk.openai_compatible import OpenAICompatibleProvider

        captured: Dict[str, Any] = {}

        def _make_fake_response_object() -> Any:
            """Build a minimal response object that satisfies both
            chat_stream's chunk iteration and chat()'s .choices[0].message
            access.  The chunk carries non-empty content so chat_stream's
            ``emitted_substantive_chunk`` flag flips and the empty-response
            fallback (which calls ``self.chat`` and would need a real
            response object) doesn't fire.
            """
            chunk = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="ok", tool_calls=None),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )

            class _FakeStream:
                def __aiter__(self) -> "_FakeStream":
                    return self

                async def __anext__(self):
                    if not getattr(self, "_emitted", False):
                        self._emitted = True
                        return chunk
                    raise StopAsyncIteration

            return _FakeStream()

        class _FakeCompletions:
            async def create(self, **kwargs: Any) -> Any:
                captured.update(kwargs)
                return _make_fake_response_object()

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeClient:
            chat = _FakeChat()

        provider = OpenAICompatibleProvider()
        provider._get_client = MagicMock(return_value=_FakeClient())  # type: ignore[method-assign]

        async def _drive() -> None:
            async for _ in provider.chat_stream(
                "qwen3-235b-a22b-thinking",
                messages=[],
                extra_body={"enable_thinking": True},
            ):
                pass

        asyncio.run(_drive())

        assert captured.get("extra_body") == {"enable_thinking": True}, (
            "openai_compatible.chat_stream swallowed the caller-supplied extra_body; "
            "this is the second-order bug fixed in this change. captured keys: "
            f"{sorted(captured.keys())}"
        )
