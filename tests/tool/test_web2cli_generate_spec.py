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


def _form_request():
    return [
        {
            "type": "Fetch",
            "method": "POST",
            "url": "https://example.com/api/search",
            "origin": "https://example.com",
            "pathname": "/api/search",
            "status": 200,
            "captureReason": "nonGet",
            "requestContentType": "application/x-www-form-urlencoded; charset=UTF-8",
            "requestHeaders": {
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Cookie": "sid=cookie-123",
            },
            "requestBodyKind": "urlencoded",
            "requestBody": '{\n  "page": "1",\n  "size": "20",\n  "keyword": "alpha"\n}',
            "response": '{"data":{"items":[{"id":"1","title":"Alpha"}]}}',
        }
    ]


def _raw_request():
    return [
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
            "requestHeaders": {"Content-Type": "text/plain", "Cookie": "sid=cookie-123"},
            "requestBodyKind": "text",
            "requestBody": "keyword=alpha",
            "response": '{"data":{"items":[{"id":"1","title":"Alpha"}]}}',
        }
    ]


def _multipart_request():
    return [
        {
            "type": "Fetch",
            "method": "POST",
            "url": "https://example.com/api/upload?page=1",
            "origin": "https://example.com",
            "pathname": "/api/upload",
            "query": {"page": "1"},
            "status": 200,
            "captureReason": "nonGet",
            "requestContentType": "multipart/form-data; boundary=----WebKitFormBoundary123",
            "requestHeaders": {
                "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundary123",
                "X-Requested-With": "XMLHttpRequest",
            },
            "requestBodyKind": "formData",
            "requestBody": '{"page":"1","note":"alpha","upload":"[file]"}',
            "response": '{"data":{"items":[{"id":"1","title":"Alpha"}]}}',
        }
    ]


def _header_auth_request():
    return [
        {
            "type": "Fetch",
            "method": "GET",
            "url": "https://example.com/api/items/list?page=1",
            "origin": "https://example.com",
            "pathname": "/api/items/list",
            "query": {"page": "1"},
            "status": 200,
            "captureReason": "includePattern",
            "requestHeaders": {
                "Authorization": "Bearer secret",
                "X-CSRF-Token": "csrf-abc",
                "Accept": "application/json",
            },
            "response": '{"data":{"items":[{"id":"1","title":"Alpha"}]}}',
        }
    ]


def _mixed_score_requests():
    return [
        {
            "type": "Fetch",
            "method": "POST",
            "url": "https://example.com/api/items/list?page=1",
            "origin": "https://example.com",
            "pathname": "/api/items/list",
            "query": {"page": "1"},
            "status": 200,
            "captureReason": "nonGet",
            "requestHeaders": {"Content-Type": "application/json"},
            "requestBody": '{"page": 1, "size": 20}',
            "response": '{"data":{"items":[{"id":"1","title":"Alpha"}]}}',
        },
        {
            "type": "Fetch",
            "method": "GET",
            "url": "https://example.com/api/items/detail?id=1",
            "origin": "https://example.com",
            "pathname": "/api/items/detail",
            "query": {"id": "1"},
            "status": 200,
            "requestHeaders": {"Accept": "application/json"},
            "response": '{"id":"1","title":"Alpha"}',
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


def test_generate_spec_from_requests_keeps_mid_score_object_endpoints():
    module = _load_module()

    spec = module.generate_spec_from_requests(_mixed_score_requests())

    assert [entry["operation"]["endpoint"] for entry in spec["operations"]] == [
        "/api/items/list",
        "/api/items/detail",
    ]


def test_generate_spec_from_requests_preserves_form_payload_mode():
    module = _load_module()

    spec = module.generate_spec_from_requests(_form_request())

    assert spec["operation"]["payloadMode"] == "form"
    assert spec["operation"]["bodyTemplate"] == {"page": "${page}", "size": "${limit}", "keyword": "alpha"}
    assert spec["operation"]["rawBodyTemplate"] == ""


def test_generate_spec_from_requests_preserves_raw_payload_mode():
    module = _load_module()

    spec = module.generate_spec_from_requests(_raw_request())

    assert spec["operation"]["payloadMode"] == "raw"
    assert spec["operation"]["bodyTemplate"] == {}
    assert spec["operation"]["rawBodyTemplate"] == "keyword=alpha"
    assert spec["args"] == [{"name": "page", "type": "int", "default": 1, "help": "Page number"}]


def test_generate_spec_from_requests_preserves_multipart_payload_mode():
    module = _load_module()

    spec = module.generate_spec_from_requests(_multipart_request())

    assert spec["operation"]["payloadMode"] == "multipart"
    assert spec["operation"]["bodyTemplate"] == {
        "page": "${page}",
        "note": "alpha",
        "upload": "${upload_file}",
    }
    assert spec["operation"]["multipartFileFields"] == ["upload"]
    assert spec["args"] == [
        {"name": "page", "type": "int", "default": 1, "help": "Page number"},
        {"name": "upload_file", "type": "string", "default": "", "help": "File path for multipart field upload"},
    ]
    assert spec["operation"]["headers"] == {"X-Requested-With": "XMLHttpRequest"}


def test_generate_spec_from_requests_marks_manual_header_auth():
    module = _load_module()

    spec = module.generate_spec_from_requests(_header_auth_request())

    assert spec["strategy"] == "HEADER"
    assert spec["auth"]["requiredHeaders"] == [
        {"name": "Authorization", "source": "manual", "key": "authorization"},
        {"name": "x-csrf-token", "source": "manual", "key": "x-csrf-token"},
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
