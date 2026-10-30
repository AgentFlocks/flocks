import importlib
import io
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console


SCRIPTS_DIR = Path(__file__).resolve().parents[2] / ".flocks" / "plugins" / "skills" / "tdp-use" / "scripts"


@pytest.fixture
def cli_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("THREATBOOK_BASE_URL", "https://tdp.example.com")
    monkeypatch.setenv("THREATBOOK_COOKIE_FILE", str(SCRIPTS_DIR / "auth-state.json"))
    monkeypatch.setenv("THREATBOOK_TOKEN", "test-token")

    sys.path.insert(0, str(SCRIPTS_DIR))
    for module_name in ("tdp_cli", "api_client", "config"):
        sys.modules.pop(module_name, None)

    module = importlib.import_module("tdp_cli")
    yield module

    for module_name in ("tdp_cli", "api_client", "config"):
        sys.modules.pop(module_name, None)
    try:
        sys.path.remove(str(SCRIPTS_DIR))
    except ValueError:
        pass


class FakeThreatBookClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_threat_list(self, **kwargs):
        return {
            "response_code": 0,
            "data": {
                "items": [
                    {
                        "time": 1741536000,
                        "threat": {"name": "Cobalt Strike", "severity": 2},
                        "attacker": "192.168.100.164",
                        "net": {"src_ip": "10.0.0.8", "dest_ip": "10.0.0.9"},
                        "direction": "lateral",
                        "alert_count": 79,
                    }
                ],
                "total_num": 1,
            },
        }

    def search_logs_by_sql(self, **kwargs):
        return {
            "response_code": 0,
            "data": {
                "data": [
                    {
                        "time": 1741536000,
                        "threat": {
                            "id": "threat-1",
                            "level": "attack",
                            "name": "Cobalt Strike",
                        },
                        "net": {
                            "src_ip": "10.0.0.8",
                            "src_port": 443,
                            "dest_ip": "10.0.0.9",
                            "dest_port": 8443,
                            "type": "http",
                        },
                        "data": "https://example.test/beacon",
                    }
                ]
            },
        }


def _run_cli(cli_module, monkeypatch: pytest.MonkeyPatch, args: list[str]) -> str:
    output = io.StringIO()
    monkeypatch.setattr(cli_module, "ThreatBookClient", FakeThreatBookClient)
    monkeypatch.setattr(
        cli_module,
        "console",
        Console(file=output, force_terminal=False, color_system=None, width=120),
    )

    result = CliRunner().invoke(cli_module.cli, args)
    assert result.exit_code == 0, result.output
    return output.getvalue()


@pytest.mark.parametrize(
    ("args", "expected_fragment"),
    [
        (["monitor", "threats"], '"response_code": 0'),
        (["logs", "search"], '"threat-1"'),
    ],
)
def test_cli_defaults_to_json_output(cli_module, monkeypatch: pytest.MonkeyPatch, args: list[str], expected_fragment: str):
    output = _run_cli(cli_module, monkeypatch, args)

    assert expected_fragment in output


@pytest.mark.parametrize(
    ("args", "expected_fragment"),
    [
        (["monitor", "threats", "--table-output"], "威胁列表"),
        (["logs", "search", "--table-output"], "日志搜索结果"),
    ],
)
def test_cli_supports_table_output_switch(
    cli_module,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    expected_fragment: str,
):
    output = _run_cli(cli_module, monkeypatch, args)

    assert expected_fragment in output
