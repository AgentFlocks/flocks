import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from flocks.tool.registry import ToolContext
from flocks.tool.tool_loader import yaml_to_tool


def _load_tool(yaml_name: str):
    yaml_path = (
        Path.cwd()
        / ".flocks"
        / "plugins"
        / "tools"
        / "api"
        / "qingteng"
        / yaml_name
    )
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    return yaml_to_tool(raw, yaml_path)


def _load_handler_module(script_name: str, module_name: str):
    script_path = (
        Path.cwd()
        / ".flocks"
        / "plugins"
        / "tools"
        / "api"
        / "qingteng"
        / script_name
    )
    spec = importlib.util.spec_from_file_location(module_name, str(script_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeHTTPResponse:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    def read(self):
        import json

        return json.dumps(self._payload).encode("utf-8")


class _FakeHTTPConnection:
    responses: list["_FakeHTTPResponse"] = []
    created: list["_FakeHTTPConnection"] = []

    def __init__(self, host: str, port: int, **kwargs):
        self.host = host
        self.port = port
        self.context = kwargs.get("context")
        self.calls: list[dict] = []
        _FakeHTTPConnection.created.append(self)

    def request(self, method: str, url: str, body=None, headers=None):
        self.calls.append({"method": method, "url": url, "body": body, "headers": headers or {}})

    def getresponse(self):
        return _FakeHTTPConnection.responses.pop(0)

    def close(self):
        return None


def _mock_secrets():
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {
        "qingteng_host": "qt.local",
        "qingteng_username": "alice",
        "qingteng_password": "secret",
    }.get(key)
    return mock_secret_manager


@pytest.mark.asyncio
async def test_qingteng_assets_tool_and_handler_use_signed_sorted_query_params():
    tool = _load_tool("qingteng_assets.yaml")
    module = _load_handler_module("qingteng.handler.py", "qingteng_assets_handler_test")
    mock_secret_manager = _mock_secrets()

    _FakeHTTPConnection.created = []
    _FakeHTTPConnection.responses = [
        _FakeHTTPResponse(
            200,
            {"success": True, "data": {"comId": "corp-1", "jwt": "jwt-1", "signKey": "sign-1"}},
        ),
        _FakeHTTPResponse(200, {"rows": [{"hostname": "srv-1"}], "total": 1}),
    ]

    module.get_secret_manager = lambda: mock_secret_manager
    module.httplib.HTTPConnection = _FakeHTTPConnection
    module.httplib.HTTPSConnection = _FakeHTTPConnection
    original_get_api_service_raw = module.ConfigWriter.get_api_service_raw
    try:
        module.ConfigWriter.get_api_service_raw = lambda service_id: {}
        module.time.time = lambda: 1700000000

        result = await module.assets(
            ToolContext(session_id="test", message_id="test"),
            action="list",
            resource="host",
            os_type="linux",
            page=0,
            size=20,
            sorts="-lastCheckTime",
            groups="1,2",
            keyword="srv",
            businessGroupId="bg-1",
        )

        assert tool.info.provider == "qingteng"
        assert result.success is True
        assert result.metadata["api"] == "assets.list"
        assert result.output["total"] == 1

        query_call = _FakeHTTPConnection.created[1].calls[0]
        assert query_call["method"] == "GET"
        assert (
            query_call["url"]
            == "/external/api/assets/host/linux?page=0&size=20&sorts=-lastCheckTime&groups=1%2C2&keyword=srv&businessGroupId=bg-1"
        )

        raw_sign = "corp-1businessGroupIdbg-1groups1,2keywordsrvpage0size20sorts-lastCheckTime1700000000sign-1"
        expected_sign = hashlib.sha1(raw_sign.encode("utf-8")).hexdigest()
        assert query_call["headers"]["sign"] == expected_sign
    finally:
        module.ConfigWriter.get_api_service_raw = original_get_api_service_raw


@pytest.mark.asyncio
async def test_qingteng_assets_uses_configured_base_url_when_present():
    module = _load_handler_module("qingteng.handler.py", "qingteng_assets_base_url_handler_test")
    mock_secret_manager = _mock_secrets()
    mock_secret_manager.get.side_effect = lambda key: {
        "qingteng_username": "alice",
        "qingteng_password": "secret",
    }.get(key)

    _FakeHTTPConnection.created = []
    _FakeHTTPConnection.responses = [
        _FakeHTTPResponse(
            200,
            {"success": True, "data": {"comId": "corp-1", "jwt": "jwt-1", "signKey": "sign-1"}},
        ),
        _FakeHTTPResponse(200, {"rows": [{"hostname": "srv-1"}], "total": 1}),
    ]

    module.get_secret_manager = lambda: mock_secret_manager
    module.httplib.HTTPConnection = _FakeHTTPConnection
    module.httplib.HTTPSConnection = _FakeHTTPConnection
    original_get_api_service_raw = module.ConfigWriter.get_api_service_raw
    try:
        module.ConfigWriter.get_api_service_raw = lambda service_id: {
            "base_url": "https://qt.example.com:8443/openapi",
        } if service_id == "qingteng" else {}
        module.time.time = lambda: 1700000000

        result = await module.assets(
            ToolContext(session_id="test", message_id="test"),
            action="list",
            resource="host",
            os_type="linux",
        )

        assert result.success is True
        login_conn = _FakeHTTPConnection.created[0]
        query_conn = _FakeHTTPConnection.created[1]
        assert login_conn.host == "qt.example.com"
        assert login_conn.port == 8443
        assert login_conn.calls[0]["url"] == "/openapi/v1/api/auth"
        assert query_conn.calls[0]["url"] == "/openapi/external/api/assets/host/linux"
    finally:
        module.ConfigWriter.get_api_service_raw = original_get_api_service_raw


@pytest.mark.asyncio
async def test_qingteng_assets_reads_username_and_password_from_config_refs():
    module = _load_handler_module("qingteng.handler.py", "qingteng_assets_config_ref_handler_test")
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.side_effect = lambda key: {
        "qingteng_password": "secret-from-ref",
    }.get(key)

    _FakeHTTPConnection.created = []
    _FakeHTTPConnection.responses = [
        _FakeHTTPResponse(
            200,
            {"success": True, "data": {"comId": "corp-1", "jwt": "jwt-1", "signKey": "sign-1"}},
        ),
        _FakeHTTPResponse(200, {"rows": [{"hostname": "srv-1"}], "total": 1}),
    ]

    module.get_secret_manager = lambda: mock_secret_manager
    module.httplib.HTTPConnection = _FakeHTTPConnection
    module.httplib.HTTPSConnection = _FakeHTTPConnection
    original_get_api_service_raw = module.ConfigWriter.get_api_service_raw
    try:
        module.ConfigWriter.get_api_service_raw = lambda service_id: {
            "base_url": "https://qt.example.com:8443/openapi",
            "username": "alice",
            "password": "{secret:qingteng_password}",
        } if service_id == "qingteng" else {}
        module.time.time = lambda: 1700000000

        result = await module.assets(
            ToolContext(session_id="test", message_id="test"),
            action="list",
            resource="host",
            os_type="linux",
        )

        assert result.success is True
        login_call = _FakeHTTPConnection.created[0].calls[0]
        assert '"username": "alice"' in login_call["body"]
        assert '"password": "secret-from-ref"' in login_call["body"]
    finally:
        module.ConfigWriter.get_api_service_raw = original_get_api_service_raw


@pytest.mark.asyncio
async def test_qingteng_https_uses_unverified_context_when_verify_ssl_false():
    module = _load_handler_module("qingteng.handler.py", "qingteng_https_ssl_false_test")
    mock_secret_manager = _mock_secrets()

    _FakeHTTPConnection.created = []
    _FakeHTTPConnection.responses = [
        _FakeHTTPResponse(
            200,
            {"success": True, "data": {"comId": "corp-1", "jwt": "jwt-1", "signKey": "sign-1"}},
        ),
        _FakeHTTPResponse(200, {"rows": [{"hostname": "srv-1"}], "total": 1}),
    ]

    module.get_secret_manager = lambda: mock_secret_manager
    module.httplib.HTTPConnection = _FakeHTTPConnection
    module.httplib.HTTPSConnection = _FakeHTTPConnection
    original_get_api_service_raw = module.ConfigWriter.get_api_service_raw
    try:
        module.ConfigWriter.get_api_service_raw = lambda service_id: {
            "base_url": "https://qt.example.com:8443/openapi",
            "verify_ssl": False,
        } if service_id == "qingteng" else {}
        module.time.time = lambda: 1700000000

        result = await module.assets(
            ToolContext(session_id="test", message_id="test"),
            action="list",
            resource="host",
            os_type="linux",
        )

        assert result.success is True
        assert _FakeHTTPConnection.created[0].context is not None
        assert _FakeHTTPConnection.created[1].context is not None
    finally:
        module.ConfigWriter.get_api_service_raw = original_get_api_service_raw


@pytest.mark.asyncio
async def test_qingteng_https_uses_default_context_when_verify_ssl_true():
    module = _load_handler_module("qingteng.handler.py", "qingteng_https_ssl_true_test")
    mock_secret_manager = _mock_secrets()

    _FakeHTTPConnection.created = []
    _FakeHTTPConnection.responses = [
        _FakeHTTPResponse(
            200,
            {"success": True, "data": {"comId": "corp-1", "jwt": "jwt-1", "signKey": "sign-1"}},
        ),
        _FakeHTTPResponse(200, {"rows": [{"hostname": "srv-1"}], "total": 1}),
    ]

    module.get_secret_manager = lambda: mock_secret_manager
    module.httplib.HTTPConnection = _FakeHTTPConnection
    module.httplib.HTTPSConnection = _FakeHTTPConnection
    original_get_api_service_raw = module.ConfigWriter.get_api_service_raw
    try:
        module.ConfigWriter.get_api_service_raw = lambda service_id: {
            "base_url": "https://qt.example.com:8443/openapi",
            "verify_ssl": True,
        } if service_id == "qingteng" else {}
        module.time.time = lambda: 1700000000

        result = await module.assets(
            ToolContext(session_id="test", message_id="test"),
            action="list",
            resource="host",
            os_type="linux",
        )

        assert result.success is True
        assert _FakeHTTPConnection.created[0].context is None
        assert _FakeHTTPConnection.created[1].context is None
    finally:
        module.ConfigWriter.get_api_service_raw = original_get_api_service_raw


@pytest.mark.asyncio
async def test_qingteng_assets_process_query_only_uses_process_fields():
    module = _load_handler_module("qingteng.handler.py", "qingteng_assets_process_handler_test")
    mock_secret_manager = _mock_secrets()

    _FakeHTTPConnection.created = []
    _FakeHTTPConnection.responses = [
        _FakeHTTPResponse(
            200,
            {"success": True, "data": {"comId": "corp-1", "jwt": "jwt-1", "signKey": "sign-1"}},
        ),
        _FakeHTTPResponse(200, {"rows": [{"processName": "sshd"}], "total": 1}),
    ]

    module.get_secret_manager = lambda: mock_secret_manager
    module.httplib.HTTPConnection = _FakeHTTPConnection
    module.httplib.HTTPSConnection = _FakeHTTPConnection
    original_get_api_service_raw = module.ConfigWriter.get_api_service_raw
    try:
        module.ConfigWriter.get_api_service_raw = lambda service_id: {}
        module.time.time = lambda: 1700000000

        result = await module.assets(
            ToolContext(session_id="test", message_id="test"),
            action="list",
            resource="process",
            os_type="linux",
            page=0,
            size=20,
            processName="sshd",
            processPath="/usr/sbin/sshd",
            processPid=1234,
        )

        assert result.success is True

        query_call = _FakeHTTPConnection.created[1].calls[0]
        assert (
            query_call["url"]
            == "/external/api/assets/process/linux?page=0&size=20&processName=sshd&processPath=%2Fusr%2Fsbin%2Fsshd&processPid=1234"
        )
    finally:
        module.ConfigWriter.get_api_service_raw = original_get_api_service_raw


@pytest.mark.asyncio
async def test_qingteng_assets_process_query_rejects_port_filters():
    module = _load_handler_module("qingteng.handler.py", "qingteng_assets_process_validation_test")

    result = await module.assets(
        ToolContext(session_id="test", message_id="test"),
        action="list",
        resource="process",
        os_type="linux",
        processName="sshd",
        portNumber=22,
    )

    assert result.success is False
    assert result.error == "Unsupported filters for assets.list resource=process: portNumber"


@pytest.mark.asyncio
async def test_qingteng_risk_patch_list_uses_refined_query_fields():
    module = _load_handler_module("qingteng.handler.py", "qingteng_risk_handler_test")
    mock_secret_manager = _mock_secrets()

    _FakeHTTPConnection.created = []
    _FakeHTTPConnection.responses = [
        _FakeHTTPResponse(
            200,
            {"success": True, "data": {"comId": "corp-1", "jwt": "jwt-1", "signKey": "sign-1"}},
        ),
        _FakeHTTPResponse(200, {"rows": [{"id": "patch-1"}], "total": 1}),
    ]

    module.get_secret_manager = lambda: mock_secret_manager
    module.httplib.HTTPConnection = _FakeHTTPConnection
    module.httplib.HTTPSConnection = _FakeHTTPConnection
    original_get_api_service_raw = module.ConfigWriter.get_api_service_raw
    try:
        module.ConfigWriter.get_api_service_raw = lambda service_id: {}
        module.time.time = lambda: 1700000000

        result = await module.risk(
            ToolContext(session_id="test", message_id="test"),
            action="patch_list",
            os_type="linux",
            page=0,
            size=10,
            severity="high",
            status="unfixed",
            hostId="host-1",
            patch_name="openssl",
            cve="CVE-2024-0001",
            groups="10,11",
        )

        assert result.success is True

        query_call = _FakeHTTPConnection.created[1].calls[0]
        assert (
            query_call["url"]
            == "/external/api/vul/patch/linux/list?page=0&size=10&severity=high&status=unfixed&hostId=host-1&groups=10%2C11&patch_name=openssl&cve=CVE-2024-0001"
        )
    finally:
        module.ConfigWriter.get_api_service_raw = original_get_api_service_raw


@pytest.mark.asyncio
async def test_qingteng_risk_patch_list_rejects_weakpwd_specific_field():
    module = _load_handler_module("qingteng.handler.py", "qingteng_patch_validation_test")

    result = await module.risk(
        ToolContext(session_id="test", message_id="test"),
        action="patch_list",
        os_type="linux",
        patch_name="openssl",
        accountName="root",
    )

    assert result.success is False
    assert result.error == "Unsupported filters for risk.patch_list: accountName"


@pytest.mark.asyncio
async def test_qingteng_risk_weakpwd_list_uses_account_specific_field():
    module = _load_handler_module("qingteng.handler.py", "qingteng_weakpwd_handler_test")
    mock_secret_manager = _mock_secrets()

    _FakeHTTPConnection.created = []
    _FakeHTTPConnection.responses = [
        _FakeHTTPResponse(
            200,
            {"success": True, "data": {"comId": "corp-1", "jwt": "jwt-1", "signKey": "sign-1"}},
        ),
        _FakeHTTPResponse(200, {"rows": [{"accountName": "root"}], "total": 1}),
    ]

    module.get_secret_manager = lambda: mock_secret_manager
    module.httplib.HTTPConnection = _FakeHTTPConnection
    module.httplib.HTTPSConnection = _FakeHTTPConnection
    original_get_api_service_raw = module.ConfigWriter.get_api_service_raw
    try:
        module.ConfigWriter.get_api_service_raw = lambda service_id: {}
        module.time.time = lambda: 1700000000

        result = await module.risk(
            ToolContext(session_id="test", message_id="test"),
            action="weakpwd_list",
            os_type="linux",
            page=0,
            size=10,
            accountName="root",
            severity="critical",
        )

        assert result.success is True

        query_call = _FakeHTTPConnection.created[1].calls[0]
        assert (
            query_call["url"]
            == "/external/api/vul/weakpwd/linux/list?page=0&size=10&severity=critical&accountName=root"
        )
    finally:
        module.ConfigWriter.get_api_service_raw = original_get_api_service_raw


@pytest.mark.asyncio
async def test_qingteng_detect_brutecrack_list_uses_time_range_filters():
    module = _load_handler_module("qingteng.handler.py", "qingteng_detect_query_handler_test")
    mock_secret_manager = _mock_secrets()

    _FakeHTTPConnection.created = []
    _FakeHTTPConnection.responses = [
        _FakeHTTPResponse(
            200,
            {"success": True, "data": {"comId": "corp-1", "jwt": "jwt-1", "signKey": "sign-1"}},
        ),
        _FakeHTTPResponse(200, {"rows": [{"ip": "1.1.1.1"}], "total": 1}),
    ]

    module.get_secret_manager = lambda: mock_secret_manager
    module.httplib.HTTPConnection = _FakeHTTPConnection
    module.httplib.HTTPSConnection = _FakeHTTPConnection
    original_get_api_service_raw = module.ConfigWriter.get_api_service_raw
    try:
        module.ConfigWriter.get_api_service_raw = lambda service_id: {}
        module.time.time = lambda: 1700000000

        result = await module.detect(
            ToolContext(session_id="test", message_id="test"),
            action="brutecrack_list",
            os_type="win",
            page=1,
            size=50,
            ip="1.1.1.1",
            account="admin",
            begin_time="2025-01-01 00:00:00",
            end_time="2025-01-02 00:00:00",
        )

        assert result.success is True

        query_call = _FakeHTTPConnection.created[1].calls[0]
        assert (
            query_call["url"]
            == "/external/api/detect/brutecrack/win?page=1&size=50&ip=1.1.1.1&account=admin&begin_time=2025-01-01+00%3A00%3A00&end_time=2025-01-02+00%3A00%3A00"
        )
    finally:
        module.ConfigWriter.get_api_service_raw = original_get_api_service_raw


@pytest.mark.asyncio
async def test_qingteng_detect_webshell_list_rejects_brutecrack_specific_field():
    module = _load_handler_module("qingteng.handler.py", "qingteng_webshell_validation_test")

    result = await module.detect(
        ToolContext(session_id="test", message_id="test"),
        action="webshell_list",
        os_type="linux",
        file_path="/var/www/html/index.php",
        account="admin",
    )

    assert result.success is False
    assert result.error == "Unsupported filters for detect.webshell_list: account"


@pytest.mark.asyncio
async def test_qingteng_detect_honeypot_rule_update_uses_put_and_body_signature():
    module = _load_handler_module("qingteng.handler.py", "qingteng_detect_handler_test")
    mock_secret_manager = _mock_secrets()

    _FakeHTTPConnection.created = []
    _FakeHTTPConnection.responses = [
        _FakeHTTPResponse(
            200,
            {"success": True, "data": {"comId": "corp-1", "jwt": "jwt-1", "signKey": "sign-1"}},
        ),
        _FakeHTTPResponse(200, {"success": True, "data": {"updated": True}}),
    ]

    module.get_secret_manager = lambda: mock_secret_manager
    module.httplib.HTTPConnection = _FakeHTTPConnection
    module.httplib.HTTPSConnection = _FakeHTTPConnection
    original_get_api_service_raw = module.ConfigWriter.get_api_service_raw
    try:
        module.ConfigWriter.get_api_service_raw = lambda service_id: {}
        module.time.time = lambda: 1700000000

        result = await module.detect(
            ToolContext(session_id="test", message_id="test"),
            action="honeypot_rule_update",
            os_type="win",
            id="rule-1",
            enabled=True,
            name="demo-rule",
            port=8080,
            protocol="tcp",
        )

        assert result.success is True
        assert result.output == {"updated": True}

        put_call = _FakeHTTPConnection.created[1].calls[0]
        assert put_call["method"] == "PUT"
        assert put_call["url"] == "/external/api/detect/honeypot/win/rule"
    finally:
        module.ConfigWriter.get_api_service_raw = original_get_api_service_raw
    assert put_call["body"] == '{"id":"rule-1","name":"demo-rule","port":8080,"protocol":"tcp","enabled":true}'

    raw_sign = 'corp-1{"id":"rule-1","name":"demo-rule","port":8080,"protocol":"tcp","enabled":true}1700000000sign-1'
    expected_sign = hashlib.sha1(raw_sign.encode("utf-8")).hexdigest()
    assert put_call["headers"]["sign"] == expected_sign


@pytest.mark.asyncio
async def test_qingteng_baseline_job_delete_validates_spec_id():
    module = _load_handler_module("qingteng.handler.py", "qingteng_baseline_handler_test")

    result = await module.baseline(
        ToolContext(session_id="test", message_id="test"),
        action="job_delete",
        os_type="linux",
    )

    assert result.success is False
    assert result.error == "Missing required parameters for baseline.job_delete: specId"


@pytest.mark.asyncio
async def test_qingteng_baseline_spec_check_result_rejects_job_list_specific_field():
    module = _load_handler_module("qingteng.handler.py", "qingteng_baseline_result_validation_test")

    result = await module.baseline(
        ToolContext(session_id="test", message_id="test"),
        action="spec_check_result",
        os_type="linux",
        specId="spec-1",
        auth_id="auth-1",
    )

    assert result.success is False
    assert result.error == "Unsupported filters for baseline.spec_check_result: auth_id"


@pytest.mark.asyncio
async def test_qingteng_baseline_job_execute_validates_spec_id_or_spec_ids():
    module = _load_handler_module("qingteng.handler.py", "qingteng_baseline_execute_handler_test")

    result = await module.baseline(
        ToolContext(session_id="test", message_id="test"),
        action="job_execute",
        os_type="linux",
    )

    assert result.success is False
    assert result.error == "Missing required parameters for baseline.job_execute: specId/specIds/body"


@pytest.mark.asyncio
async def test_qingteng_system_audit_uses_shared_handler_logic():
    tool = _load_tool("qingteng_system_audit.yaml")
    module = _load_handler_module("qingteng.handler.py", "qingteng_system_handler_test")
    mock_secret_manager = _mock_secrets()

    _FakeHTTPConnection.created = []
    _FakeHTTPConnection.responses = [
        _FakeHTTPResponse(
            200,
            {"success": True, "data": {"comId": "corp-1", "jwt": "jwt-1", "signKey": "sign-1"}},
        ),
        _FakeHTTPResponse(200, {"rows": [{"eventId": "evt-1"}], "total": 1, "charts": {}}),
    ]

    module.get_secret_manager = lambda: mock_secret_manager
    module.httplib.HTTPConnection = _FakeHTTPConnection
    module.time.time = lambda: 1700000000

    result = await module.system_audit(
        ToolContext(session_id="test", message_id="test"),
        eventName="资产清点",
        userName="admin",
        page=0,
        size=20,
        sorts="-eventTime",
    )

    assert tool.info.provider == "qingteng"
    assert result.success is True
    assert result.metadata["api"] == "system.audit"
    assert result.output["total"] == 1


@pytest.mark.asyncio
async def test_qingteng_login_returns_clear_error_when_configuration_missing():
    module = _load_handler_module("qingteng.handler.py", "qingteng_login_handler_test")
    mock_secret_manager = MagicMock()
    mock_secret_manager.get.return_value = None
    module.get_secret_manager = lambda: mock_secret_manager
    module.ConfigWriter.get_api_service_raw = lambda service_id: {}

    result = await module.login(ToolContext(session_id="test", message_id="test"))

    assert result.success is False
    assert result.error == "Missing configuration: qingteng base_url/qingteng_host, qingteng_username, qingteng_password"
