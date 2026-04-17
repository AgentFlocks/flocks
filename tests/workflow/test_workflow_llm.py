import asyncio

import pytest

from flocks.workflow.llm import LLMClient
from flocks.workflow.engine import WorkflowEngine
from flocks.workflow.models import Workflow


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
    ):
        self.id = provider_id
        self._behavior = behavior
        self._configured = configured
        self._models = models or []
        self.calls = 0
        self.close_calls = 0
        self.last_config = None

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
        if current == "timeout":
            await asyncio.sleep(0.05)
            return _FakeResponse("late")
        return _FakeResponse(f"{self.id}:{model_id}")

    async def _close_client_safely(self):
        self.close_calls += 1


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
    assert provider.close_calls == 1


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
    assert requested.close_calls == 1
    assert fallback.close_calls == 1


def test_llm_retries_then_succeeds(monkeypatch):
    provider = _FakeProvider("demo", ["error", "error", "ok"], models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    client = LLMClient(provider_id="demo", model="m")
    out = client.ask("hello", max_retries=2, retry_delay_s=0)

    assert out == "demo:m"
    assert provider.calls == 3
    assert provider.close_calls == 3


def test_llm_timeout_retries_then_raises(monkeypatch):
    provider = _FakeProvider("demo", "timeout", models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    client = LLMClient(provider_id="demo", model="m")
    with pytest.raises(ValueError, match="timed out after 0.01s"):
        client.ask("hello", timeout_s=0.01, max_retries=2, retry_delay_s=0)

    assert provider.calls == 3
    assert provider.close_calls == 3


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


def test_llm_cleanup_does_not_emit_loop_closed_noise_on_success(monkeypatch, capsys):
    provider = _FakeProvider("demo", "ok", models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    client = LLMClient(provider_id="demo", model="m")
    assert client.ask("hello") == "demo:m"

    captured = capsys.readouterr()
    assert provider.close_calls == 1
    assert "Event loop is closed" not in captured.err


def test_llm_cleanup_does_not_emit_loop_closed_noise_on_error(monkeypatch, capsys):
    provider = _FakeProvider("demo", "error", models=["m"])
    _patch_provider(monkeypatch, {"demo": provider})

    client = LLMClient(provider_id="demo", model="m")
    with pytest.raises(ValueError, match="Workflow 默认模型不可用"):
        client.ask("hello")

    captured = capsys.readouterr()
    assert provider.close_calls == 1
    assert "Event loop is closed" not in captured.err

