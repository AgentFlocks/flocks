"""Requirements declared by the deployment descriptor, and the hook-stage
capabilities a supervisor can require."""

import json

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.plugin.readiness import ENV_REQUIRED_CAPABILITIES, check_startup_readiness


@pytest.fixture(autouse=True)
def _clean_hooks():
    HookPipeline.reset()
    yield
    HookPipeline.reset()


def _descriptor(root, payload) -> None:
    path = root / "config" / "deployment.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")


def test_descriptor_requirements_hold_a_runtime_started_by_hand(tmp_path) -> None:
    """No FLOCKS_REQUIRED_* in the environment, yet the descriptor's list applies."""
    _descriptor(tmp_path, {"mode": "managed", "requirements": {"capabilities": ["response_transform"]}})

    report = check_startup_readiness(env={"FLOCKS_ROOT": str(tmp_path)})

    assert not report.satisfied
    assert report.missing_capabilities == frozenset({"response_transform"})


def test_descriptor_without_requirements_declares_nothing(tmp_path) -> None:
    _descriptor(tmp_path, {"mode": "managed"})

    assert check_startup_readiness(env={"FLOCKS_ROOT": str(tmp_path)}).satisfied


def test_unreadable_descriptor_is_unmet_not_empty(tmp_path) -> None:
    _descriptor(tmp_path, "{not json")

    report = check_startup_readiness(env={"FLOCKS_ROOT": str(tmp_path)})

    assert not report.satisfied
    assert any("部署描述文件" in error for error in report.errors)


def test_descriptor_requirements_must_be_string_lists(tmp_path) -> None:
    _descriptor(tmp_path, {"mode": "managed", "requirements": {"capabilities": "response_transform"}})

    report = check_startup_readiness(env={"FLOCKS_ROOT": str(tmp_path)})

    assert not report.satisfied and report.errors


def test_no_descriptor_is_no_declaration(tmp_path) -> None:
    assert check_startup_readiness(env={"FLOCKS_ROOT": str(tmp_path)}).satisfied


class _Redactor(HookBase):
    async def llm_before(self, ctx) -> None:
        return None


class _Gate(HookBase):
    async def tool_before(self, ctx) -> None:
        return None


class _AfterOnly(HookBase):
    async def tool_after(self, ctx) -> None:
        return None


@pytest.mark.parametrize(
    ("capability", "hook"),
    [("llm_request_hook", _Redactor), ("tool_gate_hook", _Gate)],
)
def test_hook_stage_capabilities(tmp_path, capability, hook) -> None:
    env = {"FLOCKS_ROOT": str(tmp_path), ENV_REQUIRED_CAPABILITIES: capability}

    missing = check_startup_readiness(env=env)
    assert not missing.satisfied and missing.missing_capabilities == frozenset({capability})

    # A hook for a different stage does not count.
    HookPipeline.register("unrelated", _AfterOnly())
    assert not check_startup_readiness(env=env).satisfied

    HookPipeline.register("provider", hook())
    assert check_startup_readiness(env=env).satisfied
