"""Workflow-loop LLM budget: ``workflow.llm.max_concurrency`` caps what a
workflow (``llm.ask`` and delegate_task subagents) can take from the provider
quota shared with interactive sessions."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from flocks.provider import llm_budget
from flocks.workflow import _async_runtime
from flocks.workflow.llm import LLMClient
from flocks.workflow.runner import _resolve_workflow_llm_concurrency


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content


class _CountingProvider:
    """Records how many chat() calls overlap."""

    def __init__(self, provider_id: str, hold_s: float = 0.15) -> None:
        self.id = provider_id
        self.hold_s = hold_s
        self.now = 0
        self.peak = 0
        self.calls = 0
        self._lock = threading.Lock()

    def __copy__(self):
        return self

    def configure(self, cfg):
        return None

    def is_configured(self):
        return True

    def get_models(self):
        return []

    async def chat(self, model_id: str, messages, **kwargs):
        with self._lock:
            self.calls += 1
            self.now += 1
            self.peak = max(self.peak, self.now)
        try:
            await asyncio.sleep(self.hold_s)
        finally:
            with self._lock:
                self.now -= 1
        return _Response(f"{self.id}:{model_id}")


@pytest.fixture
def counting_provider(monkeypatch):
    from flocks.provider import provider as provider_mod
    from flocks.workflow import llm as workflow_llm_mod

    provider = _CountingProvider("demo")
    monkeypatch.setattr(provider_mod.Provider, "_ensure_initialized", lambda: None)

    async def _noop_apply_config(*_args, **_kwargs):
        return None

    monkeypatch.setattr(provider_mod.Provider, "apply_config", _noop_apply_config)
    monkeypatch.setattr(provider_mod.Provider, "get", lambda pid: provider if pid == "demo" else None)
    monkeypatch.setattr(provider_mod.Provider, "list_models", lambda provider_id=None: [])

    async def _noop_config_get():
        class _Cfg:
            def model_dump(self, **kwargs):
                return {}

        return _Cfg()

    async def _noop_default_llm():
        return None

    monkeypatch.setattr(workflow_llm_mod.Config, "get", _noop_config_get)
    monkeypatch.setattr(workflow_llm_mod.Config, "resolve_default_llm", _noop_default_llm)
    return provider


@pytest.fixture
def restore_workflow_budget():
    yield
    _async_runtime.configure_llm_concurrency(_async_runtime.DEFAULT_LLM_MAX_CONCURRENCY)


def _ask_from_threads(count: int) -> list[str]:
    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(lambda _: LLMClient(provider_id="demo", model="m").ask("hi"), range(count)))


def test_workflow_loop_has_the_default_budget():
    _async_runtime.run_sync(asyncio.sleep(0))  # make sure the loop exists
    loop, _ = _async_runtime._get_loop_for_testing()
    assert loop is not None
    assert _async_runtime.llm_concurrency_limit() == _async_runtime.DEFAULT_LLM_MAX_CONCURRENCY
    assert llm_budget.get_loop_budget(loop) == _async_runtime.DEFAULT_LLM_MAX_CONCURRENCY
    assert _async_runtime.run_sync(_report_is_workflow_loop()) is True


async def _report_is_workflow_loop() -> bool:
    return _async_runtime.is_workflow_loop()


async def test_is_workflow_loop_false_on_other_loops():
    assert _async_runtime.is_workflow_loop() is False


def test_llm_ask_concurrency_is_capped_by_the_workflow_budget(counting_provider, restore_workflow_budget):
    _async_runtime.configure_llm_concurrency(2)
    assert _async_runtime.llm_concurrency_limit() == 2

    results = _ask_from_threads(6)

    assert results == ["demo:m"] * 6
    assert counting_provider.calls == 6
    assert counting_provider.peak == 2


def test_budget_zero_lifts_the_cap(counting_provider, restore_workflow_budget):
    _async_runtime.configure_llm_concurrency(0)
    assert _async_runtime.llm_concurrency_limit() == 0

    _ask_from_threads(6)

    assert counting_provider.peak == 6


def test_budget_change_takes_effect_for_later_calls(counting_provider, restore_workflow_budget):
    _async_runtime.configure_llm_concurrency(1)
    _ask_from_threads(3)
    assert counting_provider.peak == 1

    _async_runtime.configure_llm_concurrency(3)
    counting_provider.peak = 0
    _ask_from_threads(3)
    assert counting_provider.peak == 3


@pytest.mark.parametrize(
    "config, expected",
    [
        (None, _async_runtime.DEFAULT_LLM_MAX_CONCURRENCY),
        ({}, _async_runtime.DEFAULT_LLM_MAX_CONCURRENCY),
        ({"workflow": {"llm": {}}}, _async_runtime.DEFAULT_LLM_MAX_CONCURRENCY),
        ({"workflow": {"llm": {"max_concurrency": 3}}}, 3),
        ({"workflow": {"llm": {"max_concurrency": "5"}}}, 5),
        ({"workflow": {"llm": {"max_concurrency": 0}}}, 0),
        ({"workflow": {"llm": {"max_concurrency": -4}}}, 0),
        ({"workflow": {"llm": {"max_concurrency": "many"}}}, _async_runtime.DEFAULT_LLM_MAX_CONCURRENCY),
        ({"workflow": "nope"}, _async_runtime.DEFAULT_LLM_MAX_CONCURRENCY),
    ],
)
def test_resolve_workflow_llm_concurrency(config, expected):
    assert _resolve_workflow_llm_concurrency(config) == expected
