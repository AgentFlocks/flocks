"""Setup shared by the updater tests."""

import pytest

from flocks.updater import updater

#: The updater refuses a source release a machine cannot build; test_upgrade_recovery tests that.
REAL_TOOLCHAIN_REFUSAL = updater._frontend_toolchain_refusal

#: What points uv at a project or environment other than a test's throwaway one.
#: Some tests here run the real ``uv sync``, and the updater lays its subprocess
#: environment over this process's (``updater._run_async``), so these have to go
#: from the process itself: a sync where they point would rewrite whatever
#: environment they name, a developer's own venv included.
_ELSEWHERE = ("UV_PROJECT_ENVIRONMENT", "UV_PROJECT")


@pytest.fixture(autouse=True)
def _uv_stays_in_the_test_project(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ELSEWHERE:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _a_machine_that_can_build_the_webui(monkeypatch: pytest.MonkeyPatch) -> None:
    """What Node.js the machine running the tests happens to have is not what these tests are about."""
    monkeypatch.setattr(updater, "_frontend_toolchain_refusal", lambda **_kwargs: None)
