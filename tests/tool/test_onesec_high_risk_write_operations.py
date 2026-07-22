"""
Test Cases 31-40: High-risk write operations for OneSEC
Tests parameter construction and validation for:
- Test 31: threat_virus_scan (quick scan)
- Test 32: threat_virus_scan (custom path scan)
- Test 33: threat_stop_virus_scan
- Test 34: threat_upgrade_bd_version_task
- Test 35: edr_add_ioc
- Test 36: edr_delete_ioc
- Test 37: edr_isolate_endpoints
- Test 38: edr_unisolate_endpoints
- Test 39: edr_quarantine_files
- Test 40: edr_restore_quarantined_files
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from flocks.tool.registry import ToolContext
from flocks.tool.tool_loader import yaml_to_tool


TEST_UMID = "3db46bf6-1e55-47bb-89537a1e95c268f5"
TEST_IOC_HASH = "44d88612fea8a8f36de82e1278abb02f"


def _load_tool(yaml_name: str):
    yaml_path = (
        Path.cwd()
        / ".flocks"
        / "plugins"
        / "tools"
        / "api"
        / "onesec_v2_8_2"
        / yaml_name
    )
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return yaml_to_tool(raw, yaml_path)


class _FakeResponse:
    def __init__(self, *, status=200, json_payload=None):
        self.status = status
        self._json_payload = json_payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def json(self, content_type=None):
        return self._json_payload


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._responses.pop(0)


# Test 31: Quick virus scan
@pytest.mark.asyncio
async def test_31_quick_virus_scan():
    tool = _load_tool("onesec_threat.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload=12345)])
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {"onesec_credentials": "api-key-1|secret-1"}.get(key)

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="threat_virus_scan",
            agent_list=[TEST_UMID],
            task_type=10110,
            scanmode=1,
        )

    method, url, kwargs = fake_session.calls[0]
    params = kwargs.get("json")

    print(f"\n{'='*60}")
    print(f"Test 31: Quick virus scan on test endpoint")
    print(f"Expected: onesec_threat.threat_virus_scan")
    print(f"URL: {url}")
    print(f"Params: {params}")
    print(f"Result success: {result.success}")

    assert result.success is True
    assert params["task_scope"]["agent_list"] == [TEST_UMID]
    assert params["task_type"] == 10110
    print(f"Status: ✓ PASS")


# Test 32: Custom path virus scan
@pytest.mark.asyncio
async def test_32_custom_path_virus_scan():
    tool = _load_tool("onesec_threat.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload=12346)])
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {"onesec_credentials": "api-key-1|secret-1"}.get(key)

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="threat_virus_scan",
            agent_list=[TEST_UMID],
            task_type=10130,
            scan_paths=["C:\\Temp"],
            scanmode=1,
        )

    method, url, kwargs = fake_session.calls[0]
    params = kwargs.get("json")

    print(f"\n{'='*60}")
    print(f"Test 32: Custom path virus scan on test endpoint")
    print(f"Expected: onesec_threat.threat_virus_scan (task_type=10130)")
    print(f"URL: {url}")
    print(f"Params: {params}")
    print(f"Result success: {result.success}")

    assert result.success is True
    assert params["task_scope"]["agent_list"] == [TEST_UMID]
    assert params["task_type"] == 10130
    assert params["task_content"]["scan_paths"] == ["C:\\Temp"]
    print(f"Status: ✓ PASS")


# Test 33: Stop virus scan
@pytest.mark.asyncio
async def test_33_stop_virus_scan():
    tool = _load_tool("onesec_threat.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 200})])
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {"onesec_credentials": "api-key-1|secret-1"}.get(key)

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="threat_stop_virus_scan",
            agent_list=[TEST_UMID],
        )

    method, url, kwargs = fake_session.calls[0]
    params = kwargs.get("json")

    print(f"\n{'='*60}")
    print(f"Test 33: Stop virus scan on test endpoint")
    print(f"Expected: onesec_threat.threat_stop_virus_scan")
    print(f"URL: {url}")
    print(f"Params: {params}")
    print(f"Result success: {result.success}")

    assert result.success is True
    assert params["task_scope"]["agent_list"] == [TEST_UMID]
    print(f"Status: ✓ PASS")


# Test 34: Upgrade virus database
@pytest.mark.asyncio
async def test_34_upgrade_bd_version():
    tool = _load_tool("onesec_threat.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 200})])
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {"onesec_credentials": "api-key-1|secret-1"}.get(key)

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="threat_upgrade_bd_version_task",
            agent_list=[TEST_UMID],
            bd_upgrade_type=1,  # Cloud latest version
        )

    method, url, kwargs = fake_session.calls[0]
    params = kwargs.get("json")

    print(f"\n{'='*60}")
    print(f"Test 34: Upgrade virus database to latest cloud version")
    print(f"Expected: onesec_threat.threat_upgrade_bd_version_task")
    print(f"URL: {url}")
    print(f"Params: {params}")
    print(f"Result success: {result.success}")

    assert result.success is True
    assert params["task_scope"]["agent_list"] == [TEST_UMID]
    assert params["task_content"]["bd_upgrade_type"] == 1
    print(f"Status: ✓ PASS")


# Test 35: Add IOC
@pytest.mark.asyncio
async def test_35_add_ioc():
    tool = _load_tool("onesec_edr.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 200})])
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {"onesec_credentials": "api-key-1|secret-1"}.get(key)

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="edr_add_ioc",
            iocs=[TEST_IOC_HASH],
            severity=4,  # High severity
            threatName="EICAR-Test",
        )

    method, url, kwargs = fake_session.calls[0]
    params = kwargs.get("json")

    print(f"\n{'='*60}")
    print(f"Test 35: Add test IOC to high-risk list")
    print(f"Expected: onesec_edr.edr_add_ioc")
    print(f"URL: {url}")
    print(f"Params: {params}")
    print(f"Result success: {result.success}")

    assert result.success is True
    assert params["iocs"] == [TEST_IOC_HASH]
    assert params["severity"] == 4
    assert params["threatName"] == "EICAR-Test"
    print(f"Status: ✓ PASS")


# Test 36: Delete IOC
@pytest.mark.asyncio
async def test_36_delete_ioc():
    tool = _load_tool("onesec_edr.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 200})])
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {"onesec_credentials": "api-key-1|secret-1"}.get(key)

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="edr_delete_ioc",
            iocs=[TEST_IOC_HASH],
        )

    method, url, kwargs = fake_session.calls[0]
    params = kwargs.get("json")

    print(f"\n{'='*60}")
    print(f"Test 36: Delete test IOC from list")
    print(f"Expected: onesec_edr.edr_delete_ioc")
    print(f"URL: {url}")
    print(f"Params: {params}")
    print(f"Result success: {result.success}")

    assert result.success is True
    assert params["iocs"] == [TEST_IOC_HASH]
    print(f"Status: ✓ PASS")


# Test 37: Isolate endpoint
@pytest.mark.asyncio
async def test_37_isolate_endpoint():
    tool = _load_tool("onesec_edr.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 200})])
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {"onesec_credentials": "api-key-1|secret-1"}.get(key)

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="edr_isolate_endpoints",
            agent_list=[TEST_UMID],
        )

    method, url, kwargs = fake_session.calls[0]
    params = kwargs.get("json")

    print(f"\n{'='*60}")
    print(f"Test 37: Isolate test endpoint")
    print(f"Expected: onesec_edr.edr_isolate_endpoints")
    print(f"URL: {url}")
    print(f"Params: {params}")
    print(f"Result success: {result.success}")

    assert result.success is True
    assert params["task_scope"]["agent_list"] == [TEST_UMID]
    print(f"Status: ✓ PASS")


# Test 38: Unisolate endpoint
@pytest.mark.asyncio
async def test_38_unisolate_endpoint():
    tool = _load_tool("onesec_edr.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 200})])
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {"onesec_credentials": "api-key-1|secret-1"}.get(key)

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="edr_unisolate_endpoints",
            agent_list=[TEST_UMID],
        )

    method, url, kwargs = fake_session.calls[0]
    params = kwargs.get("json")

    print(f"\n{'='*60}")
    print(f"Test 38: Unisolate test endpoint")
    print(f"Expected: onesec_edr.edr_unisolate_endpoints")
    print(f"URL: {url}")
    print(f"Params: {params}")
    print(f"Result success: {result.success}")

    assert result.success is True
    assert params["task_scope"]["agent_list"] == [TEST_UMID]
    print(f"Status: ✓ PASS")


# Test 39: Quarantine file
@pytest.mark.asyncio
async def test_39_quarantine_file():
    tool = _load_tool("onesec_edr.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 200})])
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {"onesec_credentials": "api-key-1|secret-1"}.get(key)

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="edr_quarantine_files",
            agent_list=[TEST_UMID],
            file_path="C:\\Temp\\bad.exe",
        )

    method, url, kwargs = fake_session.calls[0]
    params = kwargs.get("json")

    print(f"\n{'='*60}")
    print(f"Test 39: Quarantine file on test endpoint")
    print(f"Expected: onesec_edr.edr_quarantine_files")
    print(f"URL: {url}")
    print(f"Params: {params}")
    print(f"Result success: {result.success}")

    assert result.success is True
    assert params["task_scope"]["agent_list"] == [TEST_UMID]
    assert params["task_content_req"][0]["file_path"] == "C:\\Temp\\bad.exe"
    print(f"Status: ✓ PASS")


# Test 40: Restore quarantined file
@pytest.mark.asyncio
async def test_40_restore_quarantined_file():
    tool = _load_tool("onesec_edr.yaml")
    fake_session = _FakeSession([_FakeResponse(json_payload={"response_code": 200})])
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {"onesec_credentials": "api-key-1|secret-1"}.get(key)

    with (
        patch("flocks.security.get_secret_manager", return_value=mock_secret_manager),
        patch(
            "flocks.config.config_writer.ConfigWriter.get_api_service_raw",
            return_value={"apiKey": "{secret:onesec_credentials}"},
        ),
        patch("aiohttp.ClientSession", return_value=fake_session),
        patch("time.time", return_value=1700000000),
    ):
        result = await tool.handler(
            ToolContext(session_id="test", message_id="test"),
            action="edr_restore_quarantined_files",
            agent_list=[TEST_UMID],
            file_path="C:\\Temp\\bad.exe",
        )

    method, url, kwargs = fake_session.calls[0]
    params = kwargs.get("json")

    print(f"\n{'='*60}")
    print(f"Test 40: Restore quarantined file on test endpoint")
    print(f"Expected: onesec_edr.edr_restore_quarantined_files")
    print(f"URL: {url}")
    print(f"Params: {params}")
    print(f"Result success: {result.success}")

    assert result.success is True
    assert params["task_scope"]["agent_list"] == [TEST_UMID]
    assert params["task_content_req"][0]["file_path"] == "C:\\Temp\\bad.exe"
    print(f"Status: ✓ PASS")
