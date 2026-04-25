import sys
import types

import flocks.utils.langfuse as lf


class _FakeParent:
    def __init__(self) -> None:
        self.last_span_payload = None
        self.last_generation_payload = None

    def span(self, **kwargs):
        self.last_span_payload = kwargs
        return {"kind": "span", "payload": kwargs}

    def generation(self, **kwargs):
        self.last_generation_payload = kwargs
        return {"kind": "generation", "payload": kwargs}


class _FakeTraceClient:
    def __init__(self):
        self.trace_payload = None

    def trace(self, **kwargs):
        self.trace_payload = kwargs
        return _FakeObservation("trace", kwargs)


class _NewSdkTraceClient:
    def __init__(self):
        self.start_observation_payload = None

    def start_observation(self, **kwargs):
        self.start_observation_payload = kwargs
        return _TrackingObservation("trace", kwargs)


class _FakeObservation:
    """Fake Langfuse observation with end/generation/span support."""

    def __init__(self, kind: str, payload: dict):
        self.kind = kind
        self.payload = payload
        self.end_payload = None

    def generation(self, **kwargs):
        return _FakeObservation("generation", kwargs)

    def span(self, **kwargs):
        return _FakeObservation("span", kwargs)

    def end(self, **kwargs):
        self.end_payload = kwargs


class _TrackingSpan:
    def __init__(self) -> None:
        self.attributes = {}

    def is_recording(self):
        return True

    def set_attribute(self, key, value):
        self.attributes[key] = value


class _TrackingObservation(_FakeObservation):
    def __init__(self, kind: str, payload: dict):
        super().__init__(kind, payload)
        self._otel_span = _TrackingSpan()

    def generation(self, **kwargs):
        return _TrackingObservation("generation", kwargs)

    def span(self, **kwargs):
        return _TrackingObservation("span", kwargs)


class _NewSdkLikeObservation:
    def __init__(self) -> None:
        self.update_payload = None
        self.end_calls = 0

    def update(self, **kwargs):
        self.update_payload = kwargs

    def end(self):
        self.end_calls += 1


def test_create_span_uses_current_observation(monkeypatch):
    monkeypatch.setattr(lf, "_get_client", lambda: object())
    parent = _FakeParent()

    with lf.ObservationScope(parent):
        obs = lf.create_span(
            name="tool.read",
            input={"path": "/tmp/demo.txt"},
        )

    assert obs["kind"] == "span"
    assert parent.last_span_payload is not None
    assert parent.last_span_payload["name"] == "tool.read"


def test_create_generation_uses_current_observation(monkeypatch):
    monkeypatch.setattr(lf, "_get_client", lambda: object())
    parent = _FakeParent()

    with lf.ObservationScope(parent):
        obs = lf.create_generation(
            name="llm.stream",
            model="gpt-5",
            input=[{"role": "user", "content": "hello"}],
        )

    assert obs["kind"] == "generation"
    assert parent.last_generation_payload is not None
    assert parent.last_generation_payload["name"] == "llm.stream"


def test_create_trace_forwards_tags(monkeypatch):
    client = _FakeTraceClient()
    monkeypatch.setattr(lf, "_get_client", lambda: client)

    obs = lf.create_trace(
        name="SessionRunner.step",
        session_id="s1",
        tags=["session:s1", "step:2", "session_step:s1:2"],
        input={"step": 2},
    )

    assert obs.kind == "trace"
    assert client.trace_payload is not None
    assert client.trace_payload["tags"] == ["session:s1", "step:2", "session_step:s1:2"]


def test_create_trace_uses_start_observation_for_new_sdk(monkeypatch):
    client = _NewSdkTraceClient()
    monkeypatch.setattr(lf, "_get_client", lambda: client)

    obs = lf.create_trace(
        name="SessionRunner.step",
        session_id="s1",
        user_id="u1",
        tags=["session:s1", "step:2"],
        input={"step": 2},
        metadata={"provider_id": "openai"},
    )

    assert obs.kind == "trace"
    assert client.start_observation_payload is not None
    assert client.start_observation_payload["as_type"] == "span"
    assert client.start_observation_payload["input"] == {"step": 2}
    assert client.start_observation_payload["metadata"]["provider_id"] == "openai"
    assert client.start_observation_payload["metadata"]["session_id"] == "s1"
    assert client.start_observation_payload["metadata"]["user_id"] == "u1"
    assert client.start_observation_payload["metadata"]["tags"] == ["session:s1", "step:2"]
    assert obs._otel_span.attributes["langfuse.trace.name"] == "SessionRunner.step"
    assert obs._otel_span.attributes["session.id"] == "s1"
    assert obs._otel_span.attributes["user.id"] == "u1"
    assert obs._otel_span.attributes["langfuse.trace.tags"] == ["session:s1", "step:2"]


def test_generation_and_span_inherit_trace_dimensions_from_parent(monkeypatch):
    monkeypatch.setattr(lf, "_get_client", lambda: object())

    parent = _TrackingObservation("trace", {"name": "trace"})
    parent._otel_span.attributes["langfuse.trace.name"] = "SessionRunner.step"
    parent._otel_span.attributes["session.id"] = "s1"
    parent._otel_span.attributes["user.id"] = "u1"
    parent._otel_span.attributes["langfuse.trace.tags"] = ["session:s1", "step:2"]

    gen = lf.create_generation(parent=parent, name="LLM.generate", model="gpt-5", input={"x": 1})
    span = lf.create_span(parent=parent, name="Tool.execute.read", input={"path": "/tmp/a"})

    assert gen._otel_span.attributes["langfuse.trace.name"] == "SessionRunner.step"
    assert gen._otel_span.attributes["session.id"] == "s1"
    assert gen._otel_span.attributes["user.id"] == "u1"
    assert gen._otel_span.attributes["langfuse.trace.tags"] == ["session:s1", "step:2"]
    assert span._otel_span.attributes["langfuse.trace.name"] == "SessionRunner.step"
    assert span._otel_span.attributes["session.id"] == "s1"
    assert span._otel_span.attributes["user.id"] == "u1"
    assert span._otel_span.attributes["langfuse.trace.tags"] == ["session:s1", "step:2"]


def test_initialize_supports_langfuse_base_url(monkeypatch):
    class _FakeLangfuseClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_module = types.SimpleNamespace(Langfuse=_FakeLangfuseClient)
    monkeypatch.setitem(sys.modules, "langfuse", fake_module)

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.delenv("LANGFUSE_BASEURL", raising=False)
    monkeypatch.setenv("FLOCKS_LANGFUSE_ENABLED", "true")

    lf._initialized = False
    lf._client = None
    lf.initialize()

    assert lf._client is not None
    assert lf._client.kwargs["host"] == "https://cloud.langfuse.com"


def test_noop_when_not_configured():
    """Verify all operations are safe no-ops when Langfuse is not configured."""
    prev_client = lf._client
    prev_init = lf._initialized
    try:
        lf._client = None
        lf._initialized = True

        trace = lf.create_trace(name="test", session_id="s1")
        assert isinstance(trace, lf._NoopObservation)

        gen = lf.create_generation(parent=trace, name="gen", model="m")
        assert isinstance(gen, lf._NoopObservation)

        span = lf.create_span(parent=gen, name="sp")
        assert isinstance(span, lf._NoopObservation)

        lf.end_observation(gen, output="done", usage={"prompt_tokens": 10})
        lf.end_observation(trace, output="ok")

        assert not lf.is_active()
    finally:
        lf._client = prev_client
        lf._initialized = prev_init


def test_end_observation_passes_usage(monkeypatch):
    """Verify usage dict is forwarded correctly to observation.end()."""
    client = _FakeTraceClient()
    monkeypatch.setattr(lf, "_get_client", lambda: client)

    trace_obs = lf.create_trace(name="t", session_id="s")
    gen_obs = lf.create_generation(parent=trace_obs, name="g", model="m")
    usage = {"prompt_tokens": 100, "completion_tokens": 50}
    lf.end_observation(gen_obs, output="result", usage=usage)

    assert gen_obs.end_payload is not None
    assert gen_obs.end_payload.get("usage") == usage
    assert gen_obs.end_payload.get("output") == "result"


def test_end_observation_updates_before_end_for_new_sdk():
    """Langfuse v4-style observations require update(...), then end()."""
    obs = _NewSdkLikeObservation()
    usage = {"prompt_tokens": 100, "completion_tokens": 50}

    lf.end_observation(
        obs,
        output={"content": "result"},
        metadata={"status": "ok"},
        usage=usage,
        level="ERROR",
        status_message="done",
    )

    assert obs.update_payload is not None
    assert obs.update_payload["output"] == {"content": "result"}
    assert obs.update_payload["metadata"] == {"status": "ok"}
    assert obs.update_payload["usage_details"] == usage
    assert obs.update_payload["level"] == "ERROR"
    assert obs.update_payload["status_message"] == "done"
    assert obs.end_calls == 1


def test_scope_end_is_idempotent():
    """Calling end() twice on a scope should not raise."""
    noop = lf._NoopObservation("test")
    scope = lf.ObservationScope(noop)
    scope.end(output="first")
    scope.end(output="second")


def test_sanitize_keeps_full_strings_in_full_mode(monkeypatch):
    long_str = "a" * 10000
    monkeypatch.setenv("FLOCKS_LANGFUSE_CAPTURE_MODE", "full")
    result = lf._sanitize_payload(long_str)
    assert result == long_str


def test_sanitize_truncates_long_strings_in_truncated_mode(monkeypatch):
    long_str = "a" * 10000
    monkeypatch.setenv("FLOCKS_LANGFUSE_CAPTURE_MODE", "truncated")
    monkeypatch.setenv("FLOCKS_LANGFUSE_MAX_CHARS", "128")
    result = lf._sanitize_payload(long_str)
    assert len(result) < 10000
    assert "truncated" in result
    assert result.startswith("a" * 128)


def test_observation_scope_exception_handling():
    """ObservationScope.__exit__ should end the observation on exception."""

    class _TrackingObs:
        def __init__(self):
            self.ended = False
            self.end_kwargs = {}

        def end(self, **kwargs):
            self.ended = True
            self.end_kwargs = kwargs

    obs = _TrackingObs()
    try:
        with lf.ObservationScope(obs):
            raise ValueError("test error")
    except ValueError:
        pass

    assert obs.ended
    assert obs.end_kwargs.get("level") == "ERROR"
