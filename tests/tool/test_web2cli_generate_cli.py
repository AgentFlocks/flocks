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


def _load_module():
    spec = importlib.util.spec_from_file_location("web2cli_generate_cli", SCRIPT_PATH)
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

    def request(self, method, url, json=None):
        raise AssertionError("request should not be called in cookie-loading tests")


def test_generated_client_loads_storage_state_cookie_object(tmp_path, monkeypatch):
    module = _load_module()
    auth_state = tmp_path / "auth-state.json"
    auth_state.write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "sid", "value": "cookie-123", "domain": ".zhihu.com", "path": "/"},
                    {"name": "api", "value": "cookie-456", "domain": "api.zhihu.com", "path": "/api"},
                ],
                "origins": [{"origin": "https://www.zhihu.com", "localStorage": [{"name": "token", "value": "abc"}]}],
            }
        ),
        encoding="utf-8",
    )

    fake_session = _FakeSession()
    fake_requests = types.SimpleNamespace(Session=lambda: fake_session)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    namespace = {}
    exec(module.generate_python_client(_sample_requests(), "https://example.com"), namespace)

    client = namespace["APIClient"](cookie_file=str(auth_state))

    assert client.session is fake_session
    assert fake_session.cookies.set_calls == [
        {"name": "sid", "value": "cookie-123", "domain": ".zhihu.com", "path": "/"},
        {"name": "api", "value": "cookie-456", "domain": "api.zhihu.com", "path": "/api"},
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

    namespace["APIClient"](cookie_file=str(cookie_file))

    assert fake_session.cookies.set_calls == [
        {"name": "sid", "value": "cookie-123"},
        {"name": "api", "value": "cookie-456", "path": "/"},
    ]
