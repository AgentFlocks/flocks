import importlib
import io
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console


SCRIPTS_DIR = Path(__file__).resolve().parents[2] / ".flocks" / "plugins" / "skills" / "skyeye-use" / "scripts"


@pytest.fixture
def cli_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SKYEYE_BASE_URL", "https://skyeye.example.com")
    monkeypatch.setenv("SKYEYE_AUTH_STATE", str(SCRIPTS_DIR / "auth-state.json"))
    monkeypatch.setenv("SKYEYE_CSRF_TOKEN", "test-token")

    sys.path.insert(0, str(SCRIPTS_DIR))
    for module_name in ("skyeye_cli", "api_client", "config"):
        sys.modules.pop(module_name, None)

    module = importlib.import_module("skyeye_cli")
    yield module

    for module_name in ("skyeye_cli", "api_client", "config"):
        sys.modules.pop(module_name, None)
    try:
        sys.path.remove(str(SCRIPTS_DIR))
    except ValueError:
        pass


class FakeSkyeyeClient:
    last_alarm_list_kwargs = None
    last_alarm_count_kwargs = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_alarm_list(self, **kwargs):
        type(self).last_alarm_list_kwargs = kwargs
        return {
            "code": 0,
            "data": {
                "items": [
                    {
                        "access_time": 1773386502971,
                        "threat_name": "测试告警",
                        "threat_type": "木马通信",
                        "alarm_sip": "10.0.0.8",
                        "attack_sip": "1.1.1.1",
                        "hazard_level": "high",
                        "status": "unhandled",
                    }
                ],
                "total": 1,
            },
        }

    def get_alarm_count(self, **kwargs):
        type(self).last_alarm_count_kwargs = kwargs
        return {"code": 0, "data": {"high": 3, "critical": 1}}

    def search_log_analysis(self, **kwargs):
        return {
            "code": 0,
            "data": {
                "tbBaseLogList": [
                    {
                        "@timestamp": 1773386502971,
                        "log_type": "alarm",
                        "host_name": "server-01",
                        "event_name": "暴力破解",
                    }
                ],
                "total": 5,
            },
        }


def _run_cli(cli_module, monkeypatch: pytest.MonkeyPatch, args: list[str]) -> str:
    output = io.StringIO()
    FakeSkyeyeClient.last_alarm_list_kwargs = None
    FakeSkyeyeClient.last_alarm_count_kwargs = None
    monkeypatch.setattr(cli_module, "SkyeyeClient", FakeSkyeyeClient)
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
        (["--table", "alarm", "list", "--days", "1"], "测试告警"),
        (["--table", "alarm", "count", "--days", "1"], "告警统计"),
        (["--table", "log", "search", "alarm_sip:(10.0.0.1)", "--mode", "expert_model"], "日志搜索结果"),
    ],
)
def test_skyeye_skill_cli_commands(
    cli_module,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    expected_fragment: str,
):
    output = _run_cli(cli_module, monkeypatch, args)
    assert expected_fragment in output


def test_skyeye_skill_cli_alarm_list_forwards_hazard_level_filter(
    cli_module,
    monkeypatch: pytest.MonkeyPatch,
):
    _run_cli(
        cli_module,
        monkeypatch,
        ["alarm", "list", "--days", "1", "--filter", "hazard_level=3,2"],
    )

    assert FakeSkyeyeClient.last_alarm_list_kwargs is not None
    assert FakeSkyeyeClient.last_alarm_list_kwargs["hazard_level"] == "3,2"


def test_skyeye_skill_cli_alarm_count_forwards_hazard_level_filter(
    cli_module,
    monkeypatch: pytest.MonkeyPatch,
):
    _run_cli(
        cli_module,
        monkeypatch,
        ["alarm", "count", "--days", "1", "--filter", "hazard_level=3,2"],
    )

    assert FakeSkyeyeClient.last_alarm_count_kwargs is not None
    assert FakeSkyeyeClient.last_alarm_count_kwargs["hazard_level"] == "3,2"
