import importlib.util
import io
import sys
import types
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console


SCRIPT = Path(__file__).resolve().parents[2] / ".flocks" / "plugins" / "skills" / "onesec-use" / "scripts" / "onesec_cli.py"


class FakeOneSecClient:
    def __init__(self, **_kwargs) -> None:
        pass

    def search_threat_actions(self, **_kwargs):
        return {"code": 0, "data": {"items": [], "total": 0}}

    def get_threat_name_top(self, **_kwargs):
        return {"code": 0, "data": []}

    def __getattr__(self, _name):
        return lambda *args, **kwargs: {"code": 0, "data": {}}


@pytest.fixture
def run_cli(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(
        sys.modules,
        "api_client",
        types.SimpleNamespace(
            OneSecAPIError=RuntimeError,
            OneSecClient=FakeOneSecClient,
            resolve_auth_file=lambda: None,
        ),
    )
    fake_config = types.SimpleNamespace(BASE_URL="https://onesec.example.com", TOKEN="test-token")  # secret-guard: allow
    monkeypatch.setitem(sys.modules, "config", fake_config)
    spec = importlib.util.spec_from_file_location("onesec_cli_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = io.StringIO()
    monkeypatch.setattr(module, "console", Console(file=output, force_terminal=False, color_system=None, width=120))

    def run(args: list[str]) -> str:
        result = CliRunner().invoke(module.cli, args)
        assert result.exit_code == 0, result.output
        return output.getvalue()

    return run


@pytest.mark.parametrize(
    "args",
    [
        ["threat", "search"],
        ["threat", "top"],
        ["log", "search", "1=1"],
        ["log", "types"],
        ["log", "trend"],
    ],
)
def test_cli_defaults_to_json_output(run_cli, args: list[str]):
    # Click 8.1 / 8.2 took the default from the --table-output switch unless it declares one too.
    assert '"code": 0' in run_cli(args)


@pytest.mark.parametrize(
    ("args", "expected_fragment"),
    [
        (["threat", "search", "--table-output"], "威胁行动"),
        (["threat", "top", "--table-output"], "TOP 威胁"),
    ],
)
def test_cli_supports_table_output_switch(run_cli, args: list[str], expected_fragment: str):
    output = run_cli(args)
    assert expected_fragment in output
    assert '"code": 0' not in output
