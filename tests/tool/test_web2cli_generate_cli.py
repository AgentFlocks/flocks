import importlib.util
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
    assert 'return self._request("POST", "/api/items/list", data)' in output
    assert 'return self._request("POST", "https://example.com/api/items/list", data)' not in output


def test_generate_postman_collection_uses_endpoint_path():
    module = _load_module()

    collection = module.generate_postman_collection(_sample_requests(), "https://example.com")
    request = collection["item"][0]["request"]

    assert request["url"]["raw"] == "{{base_url}}/api/items/list"
    assert request["url"]["path"] == ["api", "items", "list"]
