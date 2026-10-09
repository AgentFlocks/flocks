import asyncio
import threading
import time

import pytest

from flocks.workflow.errors import RunCancelledError
from flocks.workflow.llm import LLMClient
from flocks.workflow.engine import WorkflowEngine
from flocks.workflow.models import Workflow
from flocks.provider.sdk.openai_base import LLMResponseTooLargeError


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeModel:
    def __init__(self, model_id: str):
        self.id = model_id


class _FakeProvider:
    def __init__(
        self,
        provider_id: str,
        behavior: str | list[str],
        *,
        configured: bool = True,
        models: list[str] | None = None,
        _shared_state: dict[str, object] | None = None,
    ):
        self.id = provider_id
        self._behavior = behavior
        self._configured = configured
        self._models = models or []
        self._shared_state = _shared_state or {"calls": 0, "last_config": None}

    @property
    def calls(self) -> int:
        return int(self._shared_state["calls"])

    @calls.setter
    def calls(self, value: int) -> None:
        self._shared_state["calls"] = value

    @property
    def last_config(self):
        return self._shared_state["last_config"]

    @last_config.setter
    def last_config(self, value) -> None:
        self._shared_state["last_config"] = value

    def __copy__(self):
        return _FakeProvider(
            self.id,
            self._behavior,
            configured=self._configured,
            models=list(self._models),
            _shared_state=self._shared_state,
        )

    def configure(self, cfg):  # pragma: no cover
        self.last_config = cfg
        return None

    def is_configured(self):
        return self._configured

    def get_models(self):
        return [_FakeModel(model_id) for model_id in self._models]

    async def chat(self, model_id: str, messages, **kwargs):
        self.calls += 1
        behavior = self._behavior
        if isinstance(behavior, list):
            current = behavior.pop(0) if behavior else "ok"
        else:
            current = behavior

        if current == "error":
            raise RuntimeError("simulated failure")
        if current == "too_large":
            raise LLMResponseTooLargeError("response exceeded limit")
        if current == "timeout":
            await asyncio.sleep(0.05)
            return _FakeResponse("late")
        if current == "slow":
            await asyncio.sleep(5)
            return _FakeResponse("late")
        return _FakeResponse(f"{self.id}:{model_id}")


def _patch_provider(monkeypatch, providers: dict[str, _FakeProvider]):
    from flocks.provider import provider as provider_mod

    monkeypatch.setattr(provider_mod.Provider, "_ensure_initialized", lambda: None)

    async def _noop_apply_config(*_args, **_kwargs):
        return None

    monkeypatch.setattr(provider_mod.Provider, "apply_config", _noop_apply_config)
    monkeypatch.setattr(provider_mod.Provider, "get", lambda pid: providers.get(pid))
    monkeypatch.setattr(provider_mod.Provider, "list_models", lambda provider_id=None: providers.get(provider_id).get_models() if provider_id and providers.get(provider_id) else [])

    # Avoid reading user/project config in unit tests.
    from flocks.workflow import llm as workflow_llm_mod

    async def _noop_config_get():
        class _Cfg:
            model = None

            def model_dump(self, **kwargs):  # pragma: no cover
                del kwargs
                return {}

        return _Cfg()

    monkeypatch.setattr(workflow_llm_mod.Config, "get", _noop_config_get)

    async def _noop_default_llm():
        return None

    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", _noop_default_llm)


def test_llm_ask_uses_provider_chat(monkeypatch):
    provider = _FakeProvider("demo", "ok")
    _patch_provider(monkeypatch, {"demo": provider})

    client = LLMClient(provider_id="demo", model="m")
    out = client.ask("hello")

    assert out == "demo:m"
    assert provider.calls == 1


def test_llm_keeps_trust_env_from_workflow_config(monkeypatch):
    provider = _FakeProvider("demo", "ok")
    _patch_provider(monkeypatch, {"demo": provider})

    from flocks.workflow import llm as workflow_llm_mod

    async def _cfg_with_trust_env():
        class _Cfg:
            model = "demo/m"

            def model_dump(self, **kwargs):  # pragma: no cover
                del kwargs
                return {"workflow": {"llm": {"trust_env": True}}}

        return _Cfg()

    async def _resolve_default_llm():
        return {"provider_id": "demo", "model_id": "m"}

    monkeypatch.setattr(workflow_llm_mod.Config, "get", _cfg_with_trust_env)
    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", _resolve_default_llm)

    client = LLMClient()
    out = client.ask("hello")

    assert out == "demo:m"
    assert provider.last_config is not None
    assert provider.last_config.custom_settings.get("trust_env") is True


def test_llm_node_supports_provider_prefixed_model(monkeypatch):
    provider = _FakeProvider("demo", "ok", models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    workflow = Workflow.from_dict(
        {
            "start": "summarize",
            "nodes": [
                {
                    "id": "summarize",
                    "type": "llm",
                    "prompt": "hello",
                    "model": "demo/m",
                    "output_key": "summary",
                }
            ],
            "edges": [],
        }
    )

    step = WorkflowEngine(workflow).run_node("summarize", {})

    assert step.outputs["summary"] == "demo:m"
    assert provider.calls == 1


def test_llm_falls_back_to_default_when_requested_model_missing(monkeypatch):
    requested = _FakeProvider("demo", "ok", models=["other"])
    fallback = _FakeProvider("fallback", "ok", models=["fallback-model"])
    _patch_provider(monkeypatch, {"demo": requested, "fallback": fallback})

    from flocks.workflow import llm as workflow_llm_mod

    async def _resolve_default_llm():
        return {"provider_id": "fallback", "model_id": "fallback-model"}

    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", _resolve_default_llm)

    client = LLMClient(provider_id="demo", model="missing-model")
    out = client.ask("hello")

    assert out == "fallback:fallback-model"
    assert requested.calls == 0
    assert fallback.calls == 1


def test_llm_falls_back_to_default_when_requested_call_fails(monkeypatch):
    requested = _FakeProvider("demo", "error", models=["m"])
    fallback = _FakeProvider("fallback", "ok", models=["fallback-model"])
    _patch_provider(monkeypatch, {"demo": requested, "fallback": fallback})

    from flocks.workflow import llm as workflow_llm_mod

    async def _resolve_default_llm():
        return {"provider_id": "fallback", "model_id": "fallback-model"}

    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", _resolve_default_llm)

    client = LLMClient(provider_id="demo", model="m")
    out = client.ask("hello")

    assert out == "fallback:fallback-model"
    assert requested.calls == 1
    assert fallback.calls == 1


def test_llm_retries_then_succeeds(monkeypatch):
    provider = _FakeProvider("demo", ["error", "error", "ok"], models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    client = LLMClient(provider_id="demo", model="m")
    out = client.ask("hello", max_retries=2, retry_delay_s=0)

    assert out == "demo:m"
    assert provider.calls == 3


def test_llm_does_not_retry_an_oversized_response(monkeypatch):
    provider = _FakeProvider("demo", "too_large", models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    client = LLMClient(provider_id="demo", model="m")
    with pytest.raises(ValueError, match="response exceeded limit"):
        client.ask("hello", max_retries=2, retry_delay_s=0)

    assert provider.calls == 1


@pytest.mark.parametrize("cancellable", [False, True])
def test_llm_timeout_retries_then_raises(monkeypatch, cancellable):
    provider = _FakeProvider("demo", "timeout", models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    polls = 0

    def never_cancel():
        nonlocal polls
        polls += 1
        assert polls < 1000, "LLM timeout was swallowed by the cancellation poll loop"
        return False

    client = LLMClient(
        provider_id="demo", model="m", cancel_checker=never_cancel if cancellable else None,
    )
    with pytest.raises(ValueError, match="timed out after 0.01s"):
        client.ask("hello", timeout_s=0.01, max_retries=2, retry_delay_s=0)

    assert provider.calls == 3


def test_cancellable_llm_can_retry_after_timeout_and_succeed(monkeypatch):
    provider = _FakeProvider("demo", ["timeout", "ok"], models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})
    polls = 0

    def never_cancel():
        nonlocal polls
        polls += 1
        assert polls < 1000
        return False

    client = LLMClient(provider_id="demo", model="m", cancel_checker=never_cancel)
    assert client.ask("hello", timeout_s=0.01, max_retries=1, retry_delay_s=0) == "demo:m"
    assert provider.calls == 2


def test_llm_ask_honors_cancel_checker(monkeypatch):
    provider = _FakeProvider("demo", "slow", models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    cancel_event = threading.Event()
    timer = threading.Timer(0.05, cancel_event.set)
    started = time.perf_counter()
    timer.start()
    try:
        client = LLMClient(
            provider_id="demo",
            model="m",
            cancel_checker=cancel_event.is_set,
        )
        with pytest.raises(RunCancelledError, match="Run cancelled"):
            client.ask("hello")
    finally:
        timer.cancel()

    assert time.perf_counter() - started < 1.0


def test_llm_raises_clear_error_when_default_is_unavailable(monkeypatch):
    provider = _FakeProvider("fallback", "ok", configured=False, models=["fallback-model"])
    _patch_provider(monkeypatch, {"fallback": provider})

    from flocks.workflow import llm as workflow_llm_mod

    async def _resolve_default_llm():
        return {"provider_id": "fallback", "model_id": "fallback-model"}

    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", _resolve_default_llm)

    client = LLMClient()
    with pytest.raises(ValueError, match="Workflow 默认模型不可用"):
        client.ask("hello")


def test_get_llm_client_does_not_stick_to_old_default(monkeypatch):
    first = _FakeProvider("first", "ok", models=["first-model"])
    second = _FakeProvider("second", "ok", models=["second-model"])
    _patch_provider(monkeypatch, {"first": first, "second": second})

    from flocks.workflow import llm as workflow_llm_mod

    state = {"provider_id": "first", "model_id": "first-model"}

    async def _resolve_default_llm():
        return dict(state)

    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", _resolve_default_llm)

    out1 = workflow_llm_mod.get_llm_client().ask("hello")
    state.update({"provider_id": "second", "model_id": "second-model"})
    out2 = workflow_llm_mod.get_llm_client().ask("hello")

    assert out1 == "first:first-model"
    assert out2 == "second:second-model"


# ---------------------------------------------------------------------------
# Provider request limiter + 429 handling (flocks.provider.concurrency)
# ---------------------------------------------------------------------------

class _RateLimitedProvider(_FakeProvider):
    """Fails with HTTP 429 (retry-after: 0.02s) ``failures`` times, then succeeds."""

    def __init__(self, provider_id: str, failures: int, *, status: int = 429, **kwargs):
        super().__init__(provider_id, "ok", **kwargs)
        self._failures = failures
        self._status = status

    def __copy__(self):
        # The workflow client clones providers per call; keep the failure
        # budget shared so the clone keeps failing until it is used up.
        return self

    async def chat(self, model_id: str, messages, **kwargs):
        self.calls += 1
        if self._failures > 0:
            self._failures -= 1

            class _HttpError(RuntimeError):
                pass

            exc = _HttpError(f"HTTP {self._status}")
            exc.status_code = self._status
            exc.response = type("Resp", (), {
                "status_code": self._status,
                "headers": {"retry-after-ms": "20"},
            })()
            raise exc
        return _FakeResponse(f"{self.id}:{model_id}")


@pytest.fixture
def _limiters():
    from flocks.provider import concurrency

    concurrency.reset_limiters_for_testing()
    yield concurrency
    concurrency.reset_limiters_for_testing()


def test_llm_429_is_retried_with_backoff_and_cools_down_the_provider(monkeypatch, _limiters):
    provider = _RateLimitedProvider("demo", failures=2, models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})
    cooldowns: list[tuple[str, float]] = []
    from flocks.workflow import llm as workflow_llm_mod

    monkeypatch.setattr(
        workflow_llm_mod,
        "report_provider_rate_limited",
        lambda pid, cooldown_s, reason="rate_limit": cooldowns.append((pid, cooldown_s)) or cooldown_s,
    )

    client = LLMClient(provider_id="demo", model="m")
    started = time.monotonic()
    out = client.ask("hello", max_retries=2, retry_delay_s=1.0)

    assert out == "demo:m"
    assert provider.calls == 3
    # retry-after-ms: 20 wins over the 1 s base delay (with <= 25 % jitter).
    assert time.monotonic() - started < 0.5
    # Two reports per 429: the floor applied before the slot is handed on,
    # then the jittered delay once the caller knows it.
    assert [pid for pid, _ in cooldowns] == ["demo"] * 4, cooldowns
    assert all(0.02 <= wait <= 0.026 for _, wait in cooldowns), cooldowns


def test_llm_deterministic_4xx_is_not_retried(monkeypatch, _limiters):
    provider = _RateLimitedProvider("demo", failures=5, status=400, models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    client = LLMClient(provider_id="demo", model="m")
    with pytest.raises(ValueError):
        client.ask("hello", max_retries=3, retry_delay_s=0)

    assert provider.calls == 1


def test_llm_ask_takes_a_provider_slot_and_releases_it(monkeypatch, _limiters):
    provider = _FakeProvider("demo", "ok", models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})
    monkeypatch.setenv("FLOCKS_LLM_MAX_CONCURRENT_REQUESTS", "1")

    client = LLMClient(provider_id="demo", model="m")
    assert client.ask("hello") == "demo:m"

    limiter = _limiters.get_provider_limiter("demo")
    assert limiter.limit == 1
    assert limiter.snapshot()["active"] == 0


def test_llm_node_default_retries_come_from_env(monkeypatch):
    from flocks.workflow import llm as workflow_llm_mod

    monkeypatch.delenv("FLOCKS_WORKFLOW_LLM_MAX_RETRIES", raising=False)
    assert workflow_llm_mod.default_llm_node_max_retries() == workflow_llm_mod.DEFAULT_LLM_NODE_MAX_RETRIES
    monkeypatch.setenv("FLOCKS_WORKFLOW_LLM_MAX_RETRIES", "5")
    assert workflow_llm_mod.default_llm_node_max_retries() == 5
    monkeypatch.setenv("FLOCKS_WORKFLOW_LLM_MAX_RETRIES", "-3")
    assert workflow_llm_mod.default_llm_node_max_retries() == 0
    monkeypatch.setenv("FLOCKS_WORKFLOW_LLM_MAX_RETRIES", "x")
    assert workflow_llm_mod.default_llm_node_max_retries() == workflow_llm_mod.DEFAULT_LLM_NODE_MAX_RETRIES


def test_llm_node_retries_transient_failure_by_default(monkeypatch, _limiters):
    provider = _RateLimitedProvider("demo", failures=1, models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})
    from flocks.workflow import llm as workflow_llm_mod

    monkeypatch.setattr(workflow_llm_mod, "report_provider_rate_limited", lambda *a, **k: 0.0)

    workflow = Workflow.from_dict({
        "start": "ask",
        "nodes": [
            {"id": "ask", "type": "llm", "model": "demo/m", "prompt": "hi", "output_key": "answer"},
        ],
        "edges": [],
    })
    step = WorkflowEngine(workflow).run_node("ask", {})
    assert step.outputs["answer"] == "demo:m"
    assert provider.calls == 2


def test_llm_429_cools_down_even_without_retry_budget(monkeypatch, _limiters):
    """LazyLLM.ask defaults to max_retries=0; that 429 must still hold others back."""
    provider = _RateLimitedProvider("demo", failures=5, models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})
    cooldowns: list[tuple[str, float]] = []
    from flocks.workflow import llm as workflow_llm_mod

    monkeypatch.setattr(
        workflow_llm_mod,
        "report_provider_rate_limited",
        lambda pid, cooldown_s, reason="rate_limit": cooldowns.append((pid, cooldown_s)) or cooldown_s,
    )

    client = LLMClient(provider_id="demo", model="m")
    with pytest.raises(ValueError):
        client.ask("hello", max_retries=0, retry_delay_s=0)

    assert provider.calls == 1
    # Reported before the slot is released and again with the final delay;
    # what matters is that a caller with zero budget still cools the provider.
    assert [pid for pid, _ in cooldowns] == ["demo"] * 2, cooldowns
    assert all(wait > 0 for _, wait in cooldowns), cooldowns


def test_llm_status_code_wins_over_message_text(_limiters):
    """A 401 whose body happens to mention 429 must not look like a rate limit."""
    from flocks.workflow.llm import classify_transient_error

    class _Err(RuntimeError):
        pass

    auth = _Err("Invalid API key; request_id=req_429abc")
    auth.status_code = 401
    assert classify_transient_error(auth) == (False, False)

    throttled = _Err("slow down")
    throttled.status_code = 429
    assert classify_transient_error(throttled) == (True, True)

    server = _Err("boom")
    server.status_code = 503
    assert classify_transient_error(server) == (True, False)

    # No usable status code: fall back to the message.
    assert classify_transient_error(_Err("Rate limit exceeded")) == (True, True)
    assert classify_transient_error(_Err("something odd")) == (True, False)


def test_llm_auth_error_mentioning_429_is_not_retried(monkeypatch, _limiters):
    provider = _RateLimitedProvider("demo", failures=5, status=401, models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})
    cooldowns: list = []
    from flocks.workflow import llm as workflow_llm_mod

    monkeypatch.setattr(workflow_llm_mod, "report_provider_rate_limited", lambda *a, **k: cooldowns.append(a))

    client = LLMClient(provider_id="demo", model="m")
    with pytest.raises(ValueError):
        client.ask("hello", max_retries=3, retry_delay_s=0)

    assert provider.calls == 1
    assert cooldowns == []


def test_retry_delay_does_not_overflow_on_huge_attempt_counts():
    """A terminal retry with a large budget must not raise OverflowError."""
    from flocks.workflow.llm import MAX_RETRY_DELAY_S, _retry_delay_s

    exc = RuntimeError("boom")
    assert _retry_delay_s(0.0, 1024, exc) == 0.0
    delay = _retry_delay_s(1.0, 1024, exc)
    assert 0 < delay <= MAX_RETRY_DELAY_S


def test_response_safety_error_is_not_a_rate_limit(_limiters):
    """A byte count containing 429 must not cool the provider down."""
    from flocks.provider.sdk.openai_base import LLMResponseTooLargeError
    from flocks.workflow.llm import classify_transient_error

    exc = LLMResponseTooLargeError("response exceeded limit: 42900000 bytes received")
    assert "429" in str(exc)
    assert classify_transient_error(exc) == (False, False)


def test_oversized_response_does_not_cool_down_the_provider(monkeypatch, _limiters):
    from flocks.provider.sdk.openai_base import LLMResponseTooLargeError

    class _OversizedProvider(_FakeProvider):
        def __copy__(self):
            # The workflow clones providers per call; keep this subclass so
            # the message below survives into the actual call.
            return self

        # The byte count is what makes this a reproducer: plain "response
        # exceeded limit" would never look like a rate limit.
        async def chat(self, model_id: str, messages, **kwargs):
            self.calls += 1
            raise LLMResponseTooLargeError(
                "response exceeded limit: 42900000 bytes received"
            )

    provider = _OversizedProvider("demo", "too_large", models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})
    cooldowns: list = []
    from flocks.workflow import llm as workflow_llm_mod

    monkeypatch.setattr(workflow_llm_mod, "report_provider_rate_limited", lambda *a, **k: cooldowns.append(a))

    client = LLMClient(provider_id="demo", model="m")
    with pytest.raises(ValueError, match="response exceeded limit"):
        client.ask("hello", max_retries=2, retry_delay_s=0)

    assert provider.calls == 1
    assert cooldowns == []
    assert _limiters.get_provider_limiter("demo").cooldown_remaining() == 0
