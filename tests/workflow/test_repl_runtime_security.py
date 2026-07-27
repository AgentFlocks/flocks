from __future__ import annotations

import pytest

from flocks.workflow.repl_runtime import PythonExecRuntime


def test_python_exec_runtime_allows_installed_requirement_imports() -> None:
    pytest.importorskip("pydantic")
    pytest.importorskip("yaml")
    pytest.importorskip("httpx")

    outputs, _stdout = PythonExecRuntime().execute(
        "\n".join(
            [
                "import httpx",
                "import pydantic",
                "import yaml",
                "outputs['ok'] = bool(httpx and pydantic and yaml)",
            ]
        ),
        {},
    )

    assert outputs == {"ok": True}
