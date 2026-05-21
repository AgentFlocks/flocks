import importlib
import io
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console


SCRIPTS_DIR = Path(__file__).resolve().parents[2] / ".flocks" / "plugins" / "skills" / "skyeye-sensor-data-fetch" / "scripts"


@pytest.fixture
def cli_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SKYEYE_SENSOR_BASE_URL", "https://sensor.example.com")
    monkeypatch.setenv("SKYEYE_SENSOR_AUTH_STATE", str(SCRIPTS_DIR / "auth-state.json"))
    monkeypatch.setenv("SKYEYE_SENSOR_CSRF_TOKEN", "test-token")

    sys.path.insert(0, str(SCRIPTS_DIR))
    for module_name in ("skyeye_sensor_cli", "api_client", "config"):
        sys.modules.pop(module_name, None)

    module = importlib.import_module("skyeye_sensor_cli")
    yield module

    for module_name in ("skyeye_sensor_cli", "api_client", "config"):
        sys.modules.pop(module_name, None)
    try:
        sys.path.remove(str(SCRIPTS_DIR))
    except ValueError:
        pass


class FakeSkyeyeSensorClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_alarm_count_filtered(self, **kwargs):
        return {
            "status": 200,
            "items": [
                {"time": 1773386502971, "value": 2},
                {"time": 1773386503971, "value": 5},
            ],
        }

    def get_alarm_list(self, **kwargs):
        return {
            "status": 200,
            "items": [
                {
                    "access_time": 1773386502971,
                    "hazard_level": "high",
                    "threat_name": "测试告警",
                    "sip": "1.1.1.1",
                    "dip": "2.2.2.2",
                    "status": "unhandled",
                }
            ],
            "total": 1,
        }


def _run_cli(cli_module, monkeypatch: pytest.MonkeyPatch, args: list[str]) -> str:
    output = io.StringIO()
    monkeypatch.setattr(cli_module, "SkyeyeSensorClient", FakeSkyeyeSensorClient)
    monkeypatch.setattr(
        cli_module,
        "console",
        Console(file=output, force_terminal=False, color_system=None, width=120),
    )

    result = CliRunner().invoke(cli_module.cli, args)
    assert result.exit_code == 0, result.output
    return output.getvalue() + result.output


@pytest.mark.parametrize(
    ("args", "expected_fragment"),
    [
        (["alarm", "count", "--table", "--days", "1"], "告警趋势"),
        (["alarm", "list", "--table", "--days", "1"], "测试告警"),
    ],
)
def test_skyeye_sensor_skill_cli_commands(
    cli_module,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    expected_fragment: str,
):
    output = _run_cli(cli_module, monkeypatch, args)
    assert expected_fragment in output
