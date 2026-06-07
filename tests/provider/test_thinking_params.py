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
  - ``reasoning_transport == generic_chat``        →  ``extra_body.enable_thinking: bool``

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


class TestGLM5TraceReplay:
    """Specific regression for ses_1628dfe6cffe1i5xZY9lv1u20m step 50.

    Trace showed: GLM-5 on alibaba, tools present, returned
    ``finishReason=stop, content=495, toolCallCount=0`` because the request
    went out without ``enable_thinking: true``.  After the fix, the request
    body should include the flag.
    """

    def test_glm5_alibaba_emits_enable_thinking(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
        assert options["extra_body"]["enable_thinking"] is True

    @pytest.mark.parametrize(
        "provider_id",
        ["alibaba", "threatbook-cn-llm", "threatbook-io-llm", "zhipu"],
    )
    def test_glm5_emits_enable_thinking_on_every_provider(
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
        assert options["extra_body"]["enable_thinking"] is True

    @pytest.mark.parametrize(
        "provider_id,model_id",
        [
            ("threatbook-cn-llm", "minimax-m2.5"),
            ("threatbook-cn-llm", "minimax-m2.7"),
            ("threatbook-cn-llm", "minimax-m3"),
            ("threatbook-io-llm", "minimax-m2.5"),
            ("threatbook-io-llm", "minimax-m2.7"),
            ("threatbook-io-llm", "minimax-m3"),
            ("minimax", "minimax-m2.5"),
            ("deepseek", "deepseek-reasoner"),
            ("stepfun", "step-3.5-flash"),
        ],
    )
    def test_previously_dropped_models_now_get_flag(
        self,
        provider_id: str,
        model_id: str,
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
            provider_id,
            model_id,
            resolve_max_tokens=False,
        )
        assert options.get("extra_body", {}).get("enable_thinking") is True, (
            f"{provider_id}/{model_id} — catalog says interleaved, dispatch "
            "should have emitted enable_thinking"
        )


class TestDispatchShape:
    """Sanity checks on the dispatch itself now that the
    provider-keyed shape registry is gone.

    The dispatch is now transport-driven: ``anthropic_messages`` →
    ``thinking={type: "enabled", ...}``; ``generic_chat`` →
    ``extra_body.enable_thinking``.  Catalog explicit declaration wins,
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
            "interleaved just emits extra_body.enable_thinking inline"
        )

    def test_deepseek_v3_is_not_a_thinking_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``deepseek-chat`` (V3) has no ``interleaved`` capability in the
        catalog and must not receive ``enable_thinking`` on the wire.

        This is the regression net for the prior
        ``_deepseek_thinking_shape`` model-name branching.  Catalog is the
        primary source of truth: V3 stays non-thinking because the catalog
        says so, and the series-token inference in ``interleaved.py`` also
        does not include V3 in its tokens (only ``deepseek-v4`` family and
        ``deepseek-reasoner``/``deepseek-r1``).  If a future catalog change
        adds ``interleaved`` to V3, this test should be removed and the
        model will then exercise the regular positive path.
        """
        # Sanity-check the assumption: catalog must not declare V3 as interleaved.
        catalog = model_catalog.get_raw_catalog()
        deepseek_models = catalog.get("deepseek", {}).get("models", {})
        v3_models = {
            mid: m for mid, m in deepseek_models.items()
            if m.get("family", "").startswith("deepseek-v3")
        }
        assert v3_models, "expected at least one deepseek-v3 family model in catalog"
        for mid, m in v3_models.items():
            assert m.get("capabilities", {}).get("interleaved") is None, (
                f"deepseek/{mid} now declares interleaved — remove this test "
                "and let the catalog coverage test exercise it instead"
            )

        # Dispatcher must produce no enable_thinking flag for a V3 model.
        monkeypatch.setattr(
            provider_options,
            "_resolve_interleaved_capability",
            lambda *_args, **_kw: None,  # V3 has no interleaved in catalog
        )
        options = provider_options.build_provider_options(
            "deepseek", "deepseek-chat", resolve_max_tokens=False,
        )
        assert "extra_body" not in options or not options["extra_body"].get(
            "enable_thinking"
        ), (
            f"deepseek-chat is V3 (non-thinking in catalog) but dispatcher "
            f"emitted enable_thinking — catalog gate is broken. options={options!r}"
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
        "model_id",
        [
            # A model that is NOT in any catalog but matches a known series
            # token in ``infer_interleaved_capability``.  Demonstrates that
            # the series-token fallback (catalog → inference) still produces
            # the right wire format.  Model ids here are constructed to
            # embed a real token from ``_PROMOTE_REASONING_CONTENT_TOKENS`` /
            # ``_STRICT_REASONING_CONTENT_TOKENS`` so the substring match
            # fires regardless of where Flocks runs the test.
            "qwen3-7b-uncatalogued",        # contains "qwen3"
            "glm-5-uncatalogued",           # contains "glm-5"
            "kimi-k2.6-uncatalogued",       # contains "kimi-k2.6"
            "minimax-m4-uncatalogued",      # contains "minimax"
            "step-3.5-flash-uncatalogued",  # contains "step-3.5-flash"
        ],
    )
    def test_series_token_fallback_emits_enable_thinking(self, model_id: str) -> None:
        """Models matching a known series token in
        ``infer_interleaved_capability`` get ``enable_thinking`` on the wire
        even when the catalog has no explicit declaration for them.

        This is the regression net for the design choice that the dispatch
        is *not* provider-keyed: a user-configured openai-compatible
        endpoint pointing at a known family Just Works, without requiring
        anyone to edit a per-provider registry.
        """
        options = provider_options.build_provider_options(
            "openai-compatible", model_id, resolve_max_tokens=False,
        )
        assert options.get("extra_body", {}).get("enable_thinking") is True, (
            f"openai-compatible/{model_id} matches a known series token; "
            "series-token fallback should have inferred interleaved and "
            f"emitted enable_thinking. options={options!r}"
        )


class TestOpenAICompatibleExtraBody:
    """Verify the SDK now propagates caller-supplied ``extra_body`` instead
    of silently swallowing it.  This is the second-order bug: even if
    ``build_provider_options`` produces the right shape, ``chat_stream`` /
    ``chat`` in ``openai_compatible.py`` dropped the kwargs it received.
    """

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
