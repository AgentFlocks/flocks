import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "plugins"
    / "skills"
    / "web2cli"
    / "scripts"
    / "generate-spec.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("web2cli_generate_spec", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample_requests():
    return [
        {
            "type": "XHR",
            "method": "POST",
            "url": "https://example.com/api/ignore",
            "status": 200,
            "response": '{"ok": true}',
            "requestHeaders": {"Content-Type": "application/json"},
        },
        {
            "type": "Fetch",
            "method": "POST",
            "url": "https://example.com/api/items/list?page=1",
            "normalizedUrl": "https://example.com/api/items/list?page=1",
            "origin": "https://example.com",
            "pathname": "/api/items/list",
            "query": {"page": "1"},
            "queryKeys": ["page"],
            "status": 200,
            "captureReason": "nonGet",
            "actionContext": {"lastAction": {"action": "Load data"}},
            "requestHeaders": {
                "Content-Type": "application/json",
                "Cookie": "sid=cookie-123",
                "X-Requested-With": "XMLHttpRequest",
            },
            "requestBody": '{"page": 1, "size": 20}',
            "response": '{"data":{"items":[{"id":"1","title":"Alpha","count":2},{"id":"2","title":"Beta","count":3}]}}',
        },
    ]


def _multi_operation_requests():
    return [
        {
            "type": "Fetch",
            "method": "POST",
            "url": "https://example.com/api/alerts/list",
            "origin": "https://example.com",
            "pathname": "/api/alerts/list",
            "status": 200,
            "captureReason": "nonGet",
            "apiPurpose": {"name": "alert-list", "desc": "List alerts"},
            "requestHeaders": {"Content-Type": "application/json", "Cookie": "sid=cookie-123"},
            "requestBody": '{"page": 1, "size": 20}',
            "response": '{"data":{"items":[{"id":"a-1","title":"Alert 1"}]}}',
        },
        {
            "type": "Fetch",
            "method": "GET",
            "url": "https://example.com/api/alarms/count?page=1",
            "origin": "https://example.com",
            "pathname": "/api/alarms/count",
            "query": {"page": "1"},
            "status": 200,
            "captureReason": "includePattern",
            "apiPurpose": {"name": "alarm-count", "desc": "Count alarms"},
            "requestHeaders": {"Accept": "application/json", "Cookie": "sid=cookie-123"},
            "response": '{"count":5}',
        },
    ]


def test_generate_spec_from_requests_picks_primary_collection_endpoint():
    module = _load_module()

    spec = module.generate_spec_from_requests(_sample_requests())

    assert spec["site"] == "example"
    assert spec["command"] == "list"
    assert spec["strategy"] == "COOKIE"
    assert spec["operation"]["endpoint"] == "/api/items/list"
    assert spec["operation"]["bodyTemplate"] == {"page": "${page}", "size": "${limit}"}
    assert spec["args"] == [
        {"name": "page", "type": "int", "default": 1, "help": "Page number"},
        {"name": "limit", "type": "int", "default": 20, "help": "Page size"},
    ]
    assert spec["rowSource"]["collectionPath"] == "$.data.items[]"
    assert spec["columns"][:2] == [
        {"name": "id", "path": "$.id", "relativePath": "id", "sourceField": "id", "type": "string"},
        {"name": "title", "path": "$.title", "relativePath": "title", "sourceField": "title", "type": "string"},
    ]


def test_generate_spec_from_requests_includes_multi_operation_entries():
    module = _load_module()

    spec = module.generate_spec_from_requests(_multi_operation_requests())

    assert [entry["command"] for entry in spec["operations"]] == ["alert-list", "alarm-count"]
    assert spec["operations"][0]["operation"]["endpoint"] == "/api/alerts/list"
    assert spec["operations"][1]["operation"]["endpoint"] == "/api/alarms/count"
    assert spec["operations"][1]["args"] == [
        {"name": "page", "type": "int", "default": 1, "help": "Page number"}
    ]
    assert spec["operations"][1]["columns"] == [
        {"name": "count", "path": "$.count", "relativePath": "count", "sourceField": "count", "type": "int"}
    ]


def test_main_writes_spec_file(tmp_path, monkeypatch, capsys):
    module = _load_module()
    input_path = tmp_path / "captured.json"
    output_path = tmp_path / "web2cli-spec.json"
    input_path.write_text(json.dumps(_sample_requests()), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate-spec.py",
            str(input_path),
            "--output",
            str(output_path),
        ],
    )

    module.main()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    captured = capsys.readouterr()

    assert payload["verify"]["columns"][:2] == ["id", "title"]
    assert payload["verify"]["rowCount"]["max"] == 2
    assert f"Written to {output_path}" in captured.out
