"""Setup shared by the updater tests."""

import pytest

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
