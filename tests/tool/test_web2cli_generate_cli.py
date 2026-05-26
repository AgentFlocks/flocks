import importlib.util
import json
import sys
import types
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "plugins"
    / "skills"
    / "web2cli"
    / "scripts"
    / "generate-cli.py"
)
SPEC_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "plugins"
    / "skills"
    / "web2cli"
    / "scripts"
    / "generate-spec.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("web2cli_generate_cli", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_spec_module():
    spec = importlib.util.spec_from_file_location("web2cli_generate_spec", SPEC_SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample_requests():
    return [
        {
            "url": "https://example.com/api/items/list?page=1",
            "method": "POST",
            "requestBody": '{"page": 1}',
            "requestHeaders": {
                "Content-Type": "application/json",
            },
            "response": '{"response_code": 0, "data": []}',
            "status": 200,
            "duration": 123,
            "apiPurpose": {
                "name": "列表查询",
                "desc": "查询列表数据",
                "page": "/items",
            },
        }
    ]


def test_group_endpoints_normalizes_absolute_urls():
    module = _load_module()

    groups = module.group_endpoints(_sample_requests())

    assert list(groups.keys()) == ["/api/items/list"]


def test_generate_python_client_uses_endpoint_path():
    module = _load_module()

    output = module.generate_python_client(_sample_requests(), "https://example.com")

    assert 'base_url: str = "https://example.com"' in output
    assert 'cookie_file: str = "auth-state.json"' in output
    assert 'return self._request("POST", "/api/items/list", data)' in output
    assert 'return self._request("POST", "https://example.com/api/items/list", data)' not in output


def test_generate_postman_collection_uses_endpoint_path():
    module = _load_module()

    collection = module.generate_postman_collection(_sample_requests(), "https://example.com")
    request = collection["item"][0]["request"]

    assert request["url"]["raw"] == "{{base_url}}/api/items/list"
    assert request["url"]["path"] == ["api", "items", "list"]


def test_sanitize_python_output_path_returns_importable_filename():
    module = _load_module()

    assert module.sanitize_python_output_path("tdp118-51_cli.py") == "tdp118_51_cli.py"
    assert module.sanitize_python_output_path("123-client.py") == "_123_client.py"
    assert module.sanitize_python_output_path("class.py") == "class_.py"


def test_python_output_normalizes_explicit_filename(tmp_path, monkeypatch, capsys):
    module = _load_module()
    input_path = tmp_path / "captured.json"
    requested_output = tmp_path / "tdp118-51_cli.py"
    expected_output = tmp_path / "tdp118_51_cli.py"

    input_path.write_text(json.dumps(_sample_requests()), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate-cli.py",
            str(input_path),
            "--format",
            "python",
            "--output",
            str(requested_output),
            "--base-url",
            "https://example.com",
        ],
    )

    module.main()

    captured = capsys.readouterr()
    assert not requested_output.exists()
    assert expected_output.exists()
    assert f"Written to {expected_output}" in captured.out
    assert f"{requested_output} -> {expected_output}" in captured.err


class _FakeCookieJar:
    def __init__(self) -> None:
        self.set_calls = []

    def set(self, name, value, **kwargs) -> None:
        self.set_calls.append({"name": name, "value": value, **kwargs})


class _FakeSession:
    def __init__(self) -> None:
        self.cookies = _FakeCookieJar()
        self.headers = {}
        self.request_calls = []

    def request(self, method, url, json=None, params=None, data=None, headers=None):
        self.request_calls.append(
            {"method": method, "url": url, "json": json, "params": params, "data": data, "headers": headers}
        )
        return _FakeResponse({})


def test_generated_client_loads_storage_state_cookie_object(tmp_path, monkeypatch):
    module = _load_module()
    auth_state = tmp_path / "auth-state.json"
    auth_state.write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "sid", "value": "cookie-123", "domain": ".example.com", "path": "/"},
                    {"name": "api", "value": "cookie-456", "domain": "api.example.com", "path": "/api"},
                    {"name": "ignore", "value": "cookie-789", "domain": ".zhihu.com", "path": "/"},
                ],
                "origins": [{"origin": "https://api.example.com", "localStorage": [{"name": "token", "value": "abc"}]}],
            }
        ),
        encoding="utf-8",
    )

    fake_session = _FakeSession()
    fake_requests = types.SimpleNamespace(Session=lambda: fake_session)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(module.generate_python_client(_sample_requests(), "https://api.example.com"), namespace)

    client = namespace["APIClient"](cookie_file=str(auth_state))
    client._request("POST", "/api/items/list", {"page": 1})

    assert client.session is fake_session
    assert fake_session.cookies.set_calls == []
    assert fake_session.request_calls == [
        {
            "method": "POST",
            "url": "https://api.example.com/api/items/list",
            "json": {"page": 1},
            "params": None,
            "data": None,
            "headers": {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json; charset=UTF-8",
                "Cookie": "sid=cookie-123; api=cookie-456",
            },
        }
    ]


def test_generated_client_still_supports_plain_cookie_list(tmp_path, monkeypatch):
    module = _load_module()
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text(
        json.dumps(
            [
                {"name": "sid", "value": "cookie-123"},
                {"name": "api", "value": "cookie-456", "path": "/"},
            ]
        ),
        encoding="utf-8",
    )

    fake_session = _FakeSession()
    fake_requests = types.SimpleNamespace(Session=lambda: fake_session)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(module.generate_python_client(_sample_requests(), "https://example.com"), namespace)

    client = namespace["APIClient"](cookie_file=str(cookie_file))
    client._request("POST", "/api/items/list", {"page": 1})

    assert fake_session.cookies.set_calls == []
    assert fake_session.request_calls == [
        {
            "method": "POST",
            "url": "https://example.com/api/items/list",
            "json": {"page": 1},
            "params": None,
            "data": None,
            "headers": {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json; charset=UTF-8",
                "Cookie": "sid=cookie-123; api=cookie-456",
            },
        }
    ]


def _sample_spec():
    return {
        "schemaVersion": "1.0",
        "site": "example",
        "command": "list_items",
        "description": "List items from example API",
        "baseUrl": "https://example.com",
        "strategy": "COOKIE",
        "auth": {"stateFile": "auth-state.json", "requiredCookies": [], "requiredHeaders": []},
        "operation": {
            "method": "POST",
            "endpoint": "/api/items/list",
            "queryTemplate": {},
            "bodyTemplate": {"page": "${page}", "size": "${limit}"},
            "payloadMode": "json",
            "rawBodyTemplate": "",
            "headers": {"Content-Type": "application/json"},
        },
        "rowSource": {"path": "$.data.items[]", "collectionPath": "$.data.items[]"},
        "args": [
            {"name": "page", "type": "int", "default": 1, "help": "Page number"},
            {"name": "limit", "type": "int", "default": 20, "help": "Page size"},
        ],
        "columns": [
            {"name": "id", "path": "$.data.items[].id", "relativePath": "id", "type": "string"},
            {"name": "title", "path": "$.data.items[].title", "relativePath": "title", "type": "string"},
        ],
        "verify": {
            "args": {"page": 1, "limit": 20},
            "rowCount": {"min": 1, "max": 2},
            "columns": ["id", "title"],
            "types": {"id": "string", "title": "string"},
            "notEmpty": ["id", "title"],
            "patterns": {},
        },
    }


def _multi_operation_spec():
    spec = _sample_spec()
    spec["operations"] = [
        {
            "command": "alert-list",
            "description": "List alerts",
            "operation": {
                "method": "POST",
                "endpoint": "/api/alerts/list",
                "queryTemplate": {},
                "bodyTemplate": {"page": "${page}", "size": "${limit}"},
                "payloadMode": "json",
                "rawBodyTemplate": "",
                "headers": {"Content-Type": "application/json"},
            },
            "rowSource": {"path": "$.data.items[]", "collectionPath": "$.data.items[]"},
            "args": spec["args"],
            "columns": spec["columns"],
            "verify": spec["verify"],
        },
        {
            "command": "alarm-count",
            "description": "Count alarms",
            "operation": {
                "method": "GET",
                "endpoint": "/api/alarms/count",
                "queryTemplate": {"page": "${page}"},
                "bodyTemplate": {},
                "payloadMode": "none",
                "rawBodyTemplate": "",
                "headers": {"Accept": "application/json"},
            },
            "rowSource": {"path": "$", "collectionPath": "$"},
            "args": [{"name": "page", "type": "int", "default": 1, "help": "Page number"}],
            "columns": [{"name": "count", "path": "$.count", "relativePath": "count", "type": "int"}],
            "verify": {
                "args": {"page": 1},
                "rowCount": {"min": 1},
                "columns": ["count"],
                "types": {"count": "int"},
                "notEmpty": ["count"],
                "patterns": {},
            },
        },
    ]
    return spec


def _form_spec():
    spec = _sample_spec()
    spec["command"] = "search_items"
    spec["description"] = "Search items with form payload"
    spec["operation"] = {
        "method": "POST",
        "endpoint": "/api/search",
        "queryTemplate": {},
        "bodyTemplate": {"page": "${page}", "size": "${limit}", "keyword": "alpha"},
        "payloadMode": "form",
        "rawBodyTemplate": "",
        "headers": {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
    }
    spec["verify"]["args"] = {"page": 1, "limit": 20}
    return spec


def _raw_spec():
    spec = _sample_spec()
    spec["command"] = "raw_search"
    spec["description"] = "Search items with raw body"
    spec["operation"] = {
        "method": "POST",
        "endpoint": "/api/raw-search",
        "queryTemplate": {"page": "${page}"},
        "bodyTemplate": {},
        "payloadMode": "raw",
        "rawBodyTemplate": "keyword=alpha",
        "headers": {"Content-Type": "text/plain"},
    }
    spec["args"] = [{"name": "page", "type": "int", "default": 1, "help": "Page number"}]
    spec["verify"]["args"] = {"page": 1}
    return spec


def _header_auth_spec():
    spec = _sample_spec()
    spec["strategy"] = "HEADER"
    spec["auth"] = {
        "stateFile": "auth-state.json",
        "requiredCookies": [],
        "requiredHeaders": [{"name": "X-CSRF-Token", "source": "localStorage", "key": "csrfToken"}],
    }
    return spec


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeRequestSession(_FakeSession):
    def __init__(self, payload) -> None:
        super().__init__()
        self._payload = payload
        self.request_calls = []

    def request(self, method, url, json=None, params=None, data=None, headers=None):
        self.request_calls.append(
            {"method": method, "url": url, "json": json, "params": params, "data": data, "headers": headers}
        )
        return _FakeResponse(self._payload)


def test_generate_verify_materials_from_spec_uses_spec_contract():
    module = _load_module()

    verify = module.generate_verify_materials_from_spec(_sample_spec())

    assert verify["site"] == "example"
    assert verify["command"] == "list_items"
    assert verify["expect"]["columns"] == ["id", "title"]
    assert verify["expect"]["rowCount"]["max"] == 2


def test_generate_python_cli_from_spec_supports_argparse_and_verify():
    module = _load_module()

    output = module.generate_python_cli_from_spec(_sample_spec())

    assert 'parser.add_argument("--format", choices=["json", "csv", "table"]' in output
    assert 'parser.add_argument("--verify", action="store_true"' in output
    assert 'SPEC = {' in output
    assert 'def verify_rows(rows: List[Dict[str, Any]], verify_spec: Dict[str, Any])' in output


def test_generate_python_cli_from_multi_operation_spec_registers_subcommands():
    module = _load_module()

    namespace = {"__name__": "generated_multi_cli"}
    exec(module.generate_python_cli_from_spec(_multi_operation_spec()), namespace)

    parser = namespace["build_parser"]()
    help_text = parser.format_help()
    parsed = parser.parse_args(["alert-list", "--page", "4", "--limit", "10"])

    assert "alert-list" in help_text
    assert "alarm-count" in help_text
    assert parsed.command == "alert-list"
    assert parsed.page == 4
    assert parsed.limit == 10


def test_generated_spec_cli_executes_request_and_projects_rows(tmp_path, monkeypatch):
    module = _load_module()
    auth_state = tmp_path / "auth-state.json"
    auth_state.write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "sid", "value": "cookie-123", "domain": ".example.com", "path": "/"},
                    {"name": "scoped", "value": "cookie-456", "domain": "example.com", "path": "/api/items"},
                    {"name": "ignore", "value": "cookie-789", "domain": ".zhihu.com", "path": "/"},
                ]
            }
        ),
        encoding="utf-8",
    )

    fake_session = _FakeRequestSession(
        {"data": {"items": [{"id": "1", "title": "Alpha"}, {"id": "2", "title": "Beta"}]}}
    )
    fake_requests = types.SimpleNamespace(Session=lambda: fake_session)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {"__name__": "generated_spec_cli"}
    exec(module.generate_python_cli_from_spec(_sample_spec()), namespace)

    client = namespace["APIClient"](auth_state=str(auth_state))
    rows = client.run({"page": 3, "limit": 5})
    errors = namespace["verify_rows"](rows, {"expect": _sample_spec()["verify"]})

    assert rows == [{"id": "1", "title": "Alpha"}, {"id": "2", "title": "Beta"}]
    assert errors == []
    assert fake_session.request_calls == [
        {
            "method": "POST",
            "url": "https://example.com/api/items/list",
            "json": {"page": 3, "size": 5},
            "params": None,
            "data": None,
            "headers": {
                "Content-Type": "application/json",
                "Cookie": "sid=cookie-123; scoped=cookie-456",
            },
        }
    ]
    assert fake_session.cookies.set_calls == []


def test_generated_multi_operation_cli_runs_selected_subcommand(monkeypatch):
    module = _load_module()
    fake_session = _FakeRequestSession({"data": {"items": [{"id": "1", "title": "Alpha"}]}})
    fake_requests = types.SimpleNamespace(Session=lambda: fake_session)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {"__name__": "generated_multi_cli"}
    exec(module.generate_python_cli_from_spec(_multi_operation_spec()), namespace)

    client = namespace["APIClient"]()
    entry = namespace["_operation_by_command"]("alert-list")
    rows = client.run({"page": 2, "limit": 10}, entry)

    assert rows == [{"id": "1", "title": "Alpha"}]
    assert fake_session.request_calls == [
        {
            "method": "POST",
            "url": "https://example.com/api/alerts/list",
            "json": {"page": 2, "size": 10},
            "params": None,
            "data": None,
            "headers": {"Content-Type": "application/json"},
        }
    ]


def test_generated_form_spec_cli_sends_form_data(monkeypatch):
    module = _load_module()
    fake_session = _FakeRequestSession({"data": {"items": [{"id": "1", "title": "Alpha"}]}})
    fake_requests = types.SimpleNamespace(Session=lambda: fake_session)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {"__name__": "generated_form_cli"}
    exec(module.generate_python_cli_from_spec(_form_spec()), namespace)

    client = namespace["APIClient"]()
    rows = client.run({"page": 4, "limit": 30})

    assert rows == [{"id": "1", "title": "Alpha"}]
    assert fake_session.request_calls == [
        {
            "method": "POST",
            "url": "https://example.com/api/search",
            "json": None,
            "params": None,
            "data": {"page": 4, "size": 30, "keyword": "alpha"},
            "headers": {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
        }
    ]


def test_generated_raw_spec_cli_sends_raw_body(monkeypatch):
    module = _load_module()
    fake_session = _FakeRequestSession({"data": {"items": [{"id": "1", "title": "Alpha"}]}})
    fake_requests = types.SimpleNamespace(Session=lambda: fake_session)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {"__name__": "generated_raw_cli"}
    exec(module.generate_python_cli_from_spec(_raw_spec()), namespace)

    client = namespace["APIClient"]()
    rows = client.run({"page": 7})

    assert rows == [{"id": "1", "title": "Alpha"}]
    assert fake_session.request_calls == [
        {
            "method": "POST",
            "url": "https://example.com/api/raw-search",
            "json": None,
            "params": {"page": 7},
            "data": "keyword=alpha",
            "headers": {"Content-Type": "text/plain"},
        }
    ]


def test_generated_multi_operation_cli_sends_get_query_without_body(monkeypatch):
    module = _load_module()
    fake_session = _FakeRequestSession({"count": 5})
    fake_requests = types.SimpleNamespace(Session=lambda: fake_session)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {"__name__": "generated_get_cli"}
    exec(module.generate_python_cli_from_spec(_multi_operation_spec()), namespace)

    client = namespace["APIClient"]()
    entry = namespace["_operation_by_command"]("alarm-count")
    rows = client.run({"page": 9}, entry)

    assert rows == [{"count": 5}]
    assert fake_session.request_calls == [
        {
            "method": "GET",
            "url": "https://example.com/api/alarms/count",
            "json": None,
            "params": {"page": 9},
            "data": None,
            "headers": {"Accept": "application/json"},
        }
    ]


def test_generated_header_strategy_cli_sends_cookie_header_and_required_headers(tmp_path, monkeypatch):
    module = _load_module()
    auth_state = tmp_path / "auth-state.json"
    auth_state.write_text(
        json.dumps(
            {
                "cookies": [{"name": "sid", "value": "cookie-123", "domain": ".example.com", "path": "/"}],
                "origins": [
                    {
                        "origin": "https://example.com",
                        "localStorage": [{"name": "csrfToken", "value": "csrf-abc"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    fake_session = _FakeRequestSession({"data": {"items": [{"id": "1", "title": "Alpha"}]}})
    fake_requests = types.SimpleNamespace(Session=lambda: fake_session)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {"__name__": "generated_header_cli"}
    exec(module.generate_python_cli_from_spec(_header_auth_spec()), namespace)

    client = namespace["APIClient"](auth_state=str(auth_state))
    rows = client.run({"page": 1, "limit": 20})

    assert rows == [{"id": "1", "title": "Alpha"}]
    assert fake_session.request_calls == [
        {
            "method": "POST",
            "url": "https://example.com/api/items/list",
            "json": {"page": 1, "size": 20},
            "params": None,
            "data": None,
            "headers": {
                "Content-Type": "application/json",
                "Cookie": "sid=cookie-123",
            },
        }
    ]
    assert client.session.headers["X-CSRF-Token"] == "csrf-abc"
    assert fake_session.cookies.set_calls == []


def test_capture_to_spec_to_cli_preserves_form_payload_mode(monkeypatch):
    cli_module = _load_module()
    spec_module = _load_spec_module()
    fake_session = _FakeRequestSession({"data": {"items": [{"id": "1", "title": "Alpha"}]}})
    fake_requests = types.SimpleNamespace(Session=lambda: fake_session)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    requests = [
        {
            "type": "Fetch",
            "method": "POST",
            "url": "https://example.com/api/search",
            "origin": "https://example.com",
            "pathname": "/api/search",
            "status": 200,
            "captureReason": "nonGet",
            "requestContentType": "application/x-www-form-urlencoded; charset=UTF-8",
            "requestHeaders": {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            "requestBodyKind": "urlencoded",
            "requestBody": '{\n  "page": "1",\n  "size": "20",\n  "keyword": "alpha"\n}',
            "response": '{"data":{"items":[{"id":"1","title":"Alpha"}]}}',
        }
    ]
    generated_spec = spec_module.generate_spec_from_requests(requests)

    namespace = {"__name__": "generated_capture_form_cli"}
    exec(cli_module.generate_python_cli_from_spec(generated_spec), namespace)

    client = namespace["APIClient"]()
    rows = client.run({"page": 5, "limit": 15})

    assert rows == [{"id": "1", "title": "Alpha"}]
    assert fake_session.request_calls[0]["data"] == {"page": 5, "size": 15, "keyword": "alpha"}
    assert fake_session.request_calls[0]["json"] is None


def test_capture_to_spec_to_cli_preserves_raw_payload_mode(monkeypatch):
    cli_module = _load_module()
    spec_module = _load_spec_module()
    fake_session = _FakeRequestSession({"data": {"items": [{"id": "1", "title": "Alpha"}]}})
    fake_requests = types.SimpleNamespace(Session=lambda: fake_session)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    requests = [
        {
            "type": "Fetch",
            "method": "POST",
            "url": "https://example.com/api/raw-search?page=1",
            "origin": "https://example.com",
            "pathname": "/api/raw-search",
            "query": {"page": "1"},
            "status": 200,
            "captureReason": "nonGet",
            "requestContentType": "text/plain",
            "requestHeaders": {"Content-Type": "text/plain"},
            "requestBodyKind": "text",
            "requestBody": "keyword=alpha",
            "response": '{"data":{"items":[{"id":"1","title":"Alpha"}]}}',
        }
    ]
    generated_spec = spec_module.generate_spec_from_requests(requests)

    namespace = {"__name__": "generated_capture_raw_cli"}
    exec(cli_module.generate_python_cli_from_spec(generated_spec), namespace)

    client = namespace["APIClient"]()
    rows = client.run({"page": 8})

    assert rows == [{"id": "1", "title": "Alpha"}]
    assert fake_session.request_calls[0]["params"] == {"page": 8}
    assert fake_session.request_calls[0]["data"] == "keyword=alpha"
    assert fake_session.request_calls[0]["json"] is None


def test_main_supports_spec_verify_output(tmp_path, monkeypatch, capsys):
    module = _load_module()
    spec_path = tmp_path / "web2cli-spec.json"
    spec_path.write_text(json.dumps(_sample_spec()), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate-cli.py",
            "--spec",
            str(spec_path),
            "--format",
            "verify",
        ],
    )

    module.main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["site"] == "example"
    assert payload["expect"]["types"]["id"] == "string"
