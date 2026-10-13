"""Pure resource contracts; run only with --noconftest and the external safety guard.

These tests never import the flocks package, routes, runtime, or application.
Actual resource modules are compiled into an in-memory package; all HTTP uses
MockTransport. Route handlers may be extracted with AST and run with injected
in-memory dependencies, never by importing their module. No project fixtures,
async plugins, or on-disk state are needed.
"""

import ast
import asyncio
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest


_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def resources():
    package_name = "_pure_knowledgebase_resources"
    package = ModuleType(package_name)
    package.__path__ = []
    sys.modules[package_name] = package
    loaded = {}
    try:
        for name in ("errors", "schemas", "ragflow", "service", "client"):
            qualified_name = f"{package_name}.{name}"
            path = _ROOT / "flocks" / "knowledgebase" / f"{name}.py"
            module = ModuleType(qualified_name)
            module.__file__ = str(path)
            module.__package__ = package_name
            sys.modules[qualified_name] = module
            loaded[name] = module
            exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
        yield SimpleNamespace(**loaded)
    finally:
        for name in loaded:
            sys.modules.pop(f"{package_name}.{name}", None)
        sys.modules.pop(package_name, None)


def _client(resources, handler):
    return resources.client.KnowledgebaseClient(
        resources.client.Connection("https://ragflow.invalid/prefix", "synthetic-resource-token-" * 2),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize(("payload", "expected"), [
    ({"name": " Renamed "}, {"name": "Renamed"}),
    ({"description": ""}, {"description": ""}),
    ({"description": "   "}, {"description": ""}),
    ({"description": " New description "}, {"description": "New description"}),
    ({"description": " First line\nSecond\tline "}, {"description": "First line\nSecond\tline"}),
    ({"description": "First line\r\nSecond\tline"}, {"description": "First line\r\nSecond\tline"}),
    ({"name": "Renamed", "description": "First\nSecond"}, {"name": "Renamed", "description": "First\nSecond"}),
    ({"name": "Renamed", "description": ""}, {"name": "Renamed", "description": ""}),
])
def test_valid_metadata_is_normalized_but_update_is_blocked_before_http(resources, payload, expected):
    assert resources.schemas.DatasetUpdate(**payload).model_dump(exclude_unset=True) == expected

    async def scenario():
        client = _client(resources, lambda _request: pytest.fail("unsupported update reached HTTP"))
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.update_dataset("dataset-1", payload)
            assert (caught.value.status, caught.value.code) == (400, "dataset_update_unsupported")
            assert "disabled" in caught.value.message
            assert "atomically" in caught.value.message
        finally:
            await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("description", ["First\nSecond\tline", "First\r\nSecond\tline"])
@pytest.mark.parametrize(("initial_bindings", "latest_bindings"), [
    ([], [{"id": "new-connector", "auto_parse": "0"}]),
    ([{"id": "connector-1", "auto_parse": "1"}], []),
    ([{"id": "connector-1", "auto_parse": "1"}], [{"id": "connector-1", "auto_parse": "0"}]),
])
def test_update_never_replays_bindings_changed_after_opening_detail(resources, description, initial_bindings, latest_bindings):
    dataset = {"id": "dataset-1", "name": "Original", "description": description, "connectors": initial_bindings}
    methods = []

    def handler(request):
        methods.append(request.method)
        assert request.url.path == "/prefix/api/v1/datasets/dataset-1"
        if request.method != "GET":
            pytest.fail("metadata editing must never mutate or compensate connector bindings")
        return httpx.Response(200, json={"code": 0, "data": dataset})

    async def scenario():
        client = _client(resources, handler)
        try:
            current = await client.dataset("dataset-1")
            assert current["description"] == description
            assert "connectors" not in current
            dataset["connectors"] = latest_bindings
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.update_dataset("dataset-1", {"name": "Renamed"})
            assert (caught.value.status, caught.value.code) == (400, "dataset_update_unsupported")
        finally:
            await client.close()

    asyncio.run(scenario())
    assert methods == ["GET"]  # Only the explicit detail read; no update lookup, write or relink.
    assert dataset["name"] == "Original"
    assert dataset["description"] == description
    assert dataset["connectors"] == latest_bindings


@pytest.mark.parametrize("model_name", ["DatasetCreate", "DatasetUpdate"])
@pytest.mark.parametrize("description", ["First\nSecond\tline", "First\r\nSecond\tline"])
def test_dataset_description_accepts_textarea_content_without_changing_line_endings(resources, model_name, description):
    model = getattr(resources.schemas, model_name)(name="Dataset", description=description)
    assert model.description == description


@pytest.mark.parametrize("model_name", ["DatasetCreate", "DatasetUpdate"])
@pytest.mark.parametrize("control", [chr(code) for code in (*range(32), *range(127, 160))])
def test_dataset_control_characters_remain_rejected(resources, model_name, control):
    model = getattr(resources.schemas, model_name)
    with pytest.raises(ValueError):
        model(name=f"bad{control}name")
    if control not in "\n\t":
        with pytest.raises(ValueError):
            model(name="Dataset", description=f"bad{control}description")


@pytest.mark.parametrize("payload", [
    {},
    {"name": ""},
    {"name": "   "},
    {"name": None},
    {"name": 3},
    {"name": "bad\nname"},
    {"name": "n" * 256},
    {"description": None},
    {"description": 3},
    {"description": "bad\x00description"},
    {"description": "d" * 4001},
    {"name": "Renamed", "extra": "not allowed"},
    {"name": "Renamed", "connectors": []},
    {"name": "Renamed", "connectors": None},
    {"name": "Renamed", "connectors": [{"id": "connector-1", "auto_parse": "1"}]},
    {"description": "New", "parser_config": {}},
    [],
])
def test_update_validation_fails_before_http(resources, payload):
    def handler(_request):
        pytest.fail("invalid update must not reach the upstream")

    async def scenario():
        client = _client(resources, handler)
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.update_dataset("dataset-1", payload)
            assert caught.value.status == 422
            assert caught.value.code == "validation_error"
        finally:
            await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("dataset_id", ["", "..", "bad/id", "a?b", "a" * 129])
def test_update_rejects_invalid_dataset_id(resources, dataset_id):
    async def scenario():
        client = _client(resources, lambda _request: pytest.fail("invalid ID reached HTTP"))
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.update_dataset(dataset_id, {"name": "Renamed"})
            assert caught.value.status == 422
        finally:
            await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [
    {"name": "Renamed"}, {"description": ""}, {"name": "Renamed", "description": "New"},
])
def test_direct_adapter_metadata_update_is_blocked_without_lookup_or_http(resources, payload):
    async def scenario():
        adapter = resources.ragflow.RagflowAdapter(
            "https://ragflow.invalid/prefix", "synthetic-token",
            transport=httpx.MockTransport(lambda _request: pytest.fail("unsupported update reached HTTP")),
        )
        adapter.get_dataset = AsyncMock(side_effect=AssertionError("unsupported update attempted a lookup"))
        try:
            with pytest.raises(resources.errors.KBError) as caught:
                await adapter.update_dataset("dataset-1", payload)
            assert (caught.value.status, caught.value.code) == (400, "dataset_update_unsupported")
            adapter.get_dataset.assert_not_awaited()
        finally:
            await adapter.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("binding_fields", [
    {},
    {"connectors": None},
    {"connectors": False},
    {"connectors": {}},
    {"connectors": "[]"},
    {"connectors": ["connector-1"]},
    {"connectors": [None]},
    {"connectors": [{}]},
    {"connectors": [{"auto_parse": "0"}]},
    {"connectors": [{"id": None, "auto_parse": "0"}]},
    {"connectors": [{"id": 1, "auto_parse": "0"}]},
    {"connectors": [{"id": "", "auto_parse": "0"}]},
    {"connectors": [{"id": "bad/id", "auto_parse": "0"}]},
    {"connectors": [{"id": "..", "auto_parse": "0"}]},
    {"connectors": [{"id": " padded ", "auto_parse": "0"}]},
    {"connectors": [{"id": "x" * 129, "auto_parse": "0"}]},
    {"connectors": [{"id": "connector-1"}]},
    {"connectors": [{"id": "connector-1", "auto_parse": None}]},
    {"connectors": [{"id": "connector-1", "auto_parse": False}]},
    {"connectors": [{"id": "connector-1", "auto_parse": 0}]},
    {"connectors": [{"id": "connector-1", "auto_parse": ""}]},
    {"connectors": [{"id": "connector-1", "auto_parse": "2"}]},
    {"connectors": [{"id": "connector-1", "auto_parse": " 0 "}]},
    {"connectors": [{"id": "connector-1", "auto_parse": {}}]},
    {"connectors": [{"id": "connector-1", "auto_parse": "0"}] * 2},
    {"connectors": [{"id": "connector-1", "auto_parse": "0"}, {"id": "connector-1", "auto_parse": "1"}]},
    {"connectors": [{"id": "connector-1", "auto_parse": "0"}, {"id": "connector-2"}]},
])
def test_binding_snapshot_validator_rejects_missing_or_invalid_data(resources, binding_fields):
    # Preserve the helper's unit contract; updates no longer rely on a snapshot.
    with pytest.raises(resources.errors.KBError) as caught:
        resources.ragflow.RagflowAdapter._dataset_update_connectors({
            "id": "dataset-1", "name": "Original", **binding_fields,
        })
    assert caught.value.status == 502
    assert caught.value.code == "dataset_connectors_unverified"
    assert "connector bindings" in caught.value.message
    assert "synchronization options" in caught.value.message


@pytest.mark.parametrize(("status", "envelope", "expected_status"), [
    (200, {"code": 0, "data": {"id": "another-dataset", "connectors": []}}, 502),
    (200, {"code": 0, "data": {"connectors": []}}, 502),
    (200, {"code": 0, "data": None}, 502),
    (200, {"code": 0, "data": []}, 502),
    (200, {"code": 0, "data": {"errors": ["partial failure"]}}, 502),
    (200, {"code": 102, "message": "Invalid Dataset ID"}, 404),
    (401, {"code": 401, "message": "Authentication failed"}, 502),
    (403, {"code": 403}, 502),
    (404, {"code": 404}, 404),
    (503, {"code": 503}, 502),
])
def test_dataset_lookup_rejects_failed_or_wrong_dataset_responses(resources, status, envelope, expected_status):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/prefix/api/v1/datasets/dataset-1"
        return httpx.Response(status, json=envelope)

    async def scenario():
        client = _client(resources, handler)
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.dataset("dataset-1")
            assert caught.value.status == expected_status
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(requests) == 1


@pytest.mark.parametrize("failure", ["timeout", "malformed_json"])
def test_dataset_lookup_does_not_retry_unreadable_responses(resources, failure):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(200, content=b"not json")

    async def scenario():
        client = _client(resources, handler)
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.dataset("dataset-1")
            assert caught.value.status == 502
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(requests) == 1


@pytest.mark.parametrize("payload", [
    {"name": "Changed", "connectors": []},
    {"name": "Changed", "connectors": None},
    {"name": "Changed", "connectors": [{"id": "connector-1", "auto_parse": "1"}]},
    {"description": "Changed", "parser_config": {}},
    {"name": None},
    {},
])
def test_adapter_rejects_caller_control_of_bindings_before_any_request(resources, payload):
    async def scenario():
        adapter = resources.ragflow.RagflowAdapter(
            "https://ragflow.invalid/prefix", "synthetic-token",
            transport=httpx.MockTransport(lambda _request: pytest.fail("non-metadata update reached HTTP")),
        )
        try:
            with pytest.raises(resources.errors.KBError) as caught:
                await adapter.update_dataset("dataset-1", payload)
            assert caught.value.status == 400
            assert caught.value.code == "invalid_request"
        finally:
            await adapter.close()

    asyncio.run(scenario())


def test_repeated_metadata_updates_never_retry_or_compensate(resources):
    async def scenario():
        client = _client(resources, lambda _request: pytest.fail("update attempted a lookup, write or relink"))
        try:
            for payload in ({"name": "First"}, {"description": "Second"}):
                with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                    await client.update_dataset("dataset-1", payload)
                assert (caught.value.status, caught.value.code) == (400, "dataset_update_unsupported")
        finally:
            await client.close()

    asyncio.run(scenario())


def test_service_dataset_update_has_explicit_metadata_allowlist(resources):
    # Even future model extensions must not forward connector controls from UI.
    class ExtendedUpdate(resources.schemas.DatasetUpdate):
        connectors: list[dict]

    calls = []

    async def update(dataset_id, payload):
        calls.append((dataset_id, payload))

    api = resources.service.KnowledgeAPI(SimpleNamespace(update_dataset=update))
    payload = ExtendedUpdate(name="Changed", connectors=[{"id": "untrusted", "auto_parse": "1"}])
    asyncio.run(api.update_dataset("dataset-1", payload))
    assert calls == [("dataset-1", {"name": "Changed"})]


@pytest.mark.parametrize(("value", "expected"), [
    (0, "1970-01-01T00:00:00.000Z"),
    (1728897061948, "2024-10-14T09:11:01.948Z"),
    (None, None),
    ("1728897061948", None),
    (True, None),
    (-1, None),
    (1728897061948.0, None),
    (float("nan"), None),
    (float("inf"), None),
    (10 ** 100, None),
    ({}, None),
    ([], None),
])
def test_creation_time_uses_only_resource_unix_milliseconds(resources, value, expected):
    item = {"id": "resource-1", "name": "source.txt", "create_time": value, "update_time": value}
    assert resources.service.file_view(item)["created_at"] == expected
    assert resources.service.file_view(item)["updated_at"] == expected
    assert resources.service.document_view("dataset-1", item)["created_at"] == expected
    assert resources.service.document_view("dataset-1", item)["updated_at"] == expected


@pytest.mark.parametrize(("value", "expected"), [
    (0, 0), (42, 42), (None, None), (-1, None), (True, None),
    (42.0, None), ("42", None), ({}, None), ([], None),
])
def test_size_requires_nonnegative_integer(resources, value, expected):
    item = {"id": "resource-1", "name": "source.txt", "size": value}
    assert resources.service.file_view(item)["size"] == expected
    assert resources.service.document_view("dataset-1", item)["size"] == expected


def test_missing_metadata_is_null_without_fabricated_file_identity_or_time(resources):
    item = {
        "id": "document-1",
        "name": "same-as-file.txt",
        "file_id": "unverified-file-field",
        "source_file_id": "unverified-source-field",
        "created_at": "2026-01-01T00:00:00Z",
        "create_date": "Mon, 14 Oct 2024 09:11:01 GMT",
        "update_time": 1728897061948,
        "upload_time": 1728897061948,
        "meta_fields": {"create_time": 1728897061948, "file_id": "metadata-file"},
    }
    file = resources.service.file_view(item)
    document = resources.service.document_view("dataset-1", item)
    assert file["created_at"] is None
    assert file["size"] is None
    assert document["created_at"] is None
    assert document["updated_at"] == "2024-10-14T09:11:01.948Z"
    assert document["size"] is None
    assert document["source_file_id"] is None
    assert "meta_fields" not in document


def test_document_modification_time_does_not_fall_back_to_creation(resources):
    item = {"id": "document-1", "name": "source.txt", "create_time": 1728897061948}
    assert resources.service.document_view("dataset-1", item)["updated_at"] is None
    item["update_time"] = 1728897062948
    assert resources.service.document_view("dataset-1", item)["updated_at"] == "2024-10-14T09:11:02.948Z"


def test_document_metadata_survives_adapter_service_and_client(resources):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/prefix/api/v1/datasets/dataset-1/documents"
        return httpx.Response(200, json={"code": 0, "data": {"total": 1, "docs": [{
            "id": "document-1", "name": "source.txt", "create_time": 1728897061948,
            "size": 42, "chunk_count": 3, "progress": 1, "run": "DONE",
        }]}})

    async def scenario():
        client = _client(resources, handler)
        try:
            page = await client.documents("dataset-1")
            assert page["items"][0] == {
                "id": "document-1", "dataset_id": "dataset-1", "name": "source.txt",
                "source_file_id": None, "created_at": "2024-10-14T09:11:01.948Z",
                "updated_at": None,
                "size": 42, "chunk_count": 3, "progress": 1, "status": "DONE",
            }
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(requests) == 1  # No file scan or guessed document-to-file mapping.


def test_document_content_uses_authoritative_document_download(resources):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/prefix/api/v1/datasets/dataset-1/documents/document-1"
        return httpx.Response(200, content=b"original document", headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": 'attachment; filename="original.txt"',
        })

    async def scenario():
        client = _client(resources, handler)
        try:
            assert await client.document_content("dataset-1", "document-1") == (b"original document", "text/plain")
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(requests) == 1


@pytest.mark.parametrize(("dataset_id", "document_id"), [("..", "document-1"), ("dataset-1", "bad/id")])
def test_document_content_validates_both_resource_ids(resources, dataset_id, document_id):
    async def scenario():
        client = _client(resources, lambda _request: pytest.fail("invalid ID reached HTTP"))
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.document_content(dataset_id, document_id)
            assert caught.value.status == 422
        finally:
            await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("document", [False, True])
@pytest.mark.parametrize("attachment", [False, True])
def test_shared_content_reader_distinguishes_json_files_from_error_envelopes(resources, document, attachment):
    headers = {"Content-Type": "application/json"}
    if attachment:
        headers["Content-Disposition"] = 'attachment; filename="original.json"'
    content = b'{"code":102,"message":"not a service error when attached"}'

    async def scenario():
        client = _client(resources, lambda _request: httpx.Response(200, content=content, headers=headers))
        try:
            operation = client.document_content("dataset-1", "document-1") if document else client.content("file-1")
            if attachment:
                assert await operation == (content, "application/json")
            else:
                with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                    await operation
                assert caught.value.status == 502
        finally:
            await client.close()

    asyncio.run(scenario())


def _resource_handler(name):
    # Parse, never import or execute the route module/application.
    path = _ROOT / "flocks" / "server" / "routes" / "knowledgebase.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == name)


@pytest.mark.parametrize("parent_id", [None, "folder-1"])
@pytest.mark.parametrize("limit", [2, 3])
def test_upload_handler_forwards_parent_and_enforces_limit_without_runtime(resources, parent_id, limit):
    handler = _resource_handler("upload")
    # Dependencies/defaults normally resolved by FastAPI are supplied in memory.
    handler.decorator_list = []
    handler.args.defaults = []
    for argument in handler.args.args:
        argument.annotation = None
    created = {"id": "uploaded", "parent_id": parent_id}
    current = SimpleNamespace(
        connection=SimpleNamespace(max_upload_bytes=limit),
        upload=AsyncMock(return_value=created),
    )
    get_client = Mock(return_value=current)
    namespace = {
        "client": get_client,
        "KnowledgebaseError": resources.errors.KnowledgebaseError,
        "_error": lambda error: error.public(),
    }
    module = ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[]))
    exec(compile(module, "<pure upload handler>", "exec"), namespace)
    file = SimpleNamespace(
        filename="note.txt", content_type="text/plain",
        read=AsyncMock(side_effect=[b"abc", b""]),
    )
    result = asyncio.run(namespace["upload"](None, file, parent_id, "user"))
    get_client.assert_called_once_with()
    if limit < 3:
        assert result["error"]["code"] == "request_too_large"
        current.upload.assert_not_awaited()
        file.read.assert_awaited_once_with(1024 * 1024)
    else:
        assert result == {"data": created}
        current.upload.assert_awaited_once_with("note.txt", b"abc", "text/plain", parent_id=parent_id)


@pytest.mark.parametrize(("name", "arguments", "code"), [
    ("update_dataset", ("dataset-1", {"name": "Renamed"}), "dataset_update_unsupported"),
    ("delete_file", ("folder",), "folder_delete_unsupported"),
])
def test_unsafe_mutation_routes_return_safety_errors_instead_of_204(resources, name, arguments, code):
    handler = _resource_handler(name)
    handler.decorator_list = []
    handler.args.defaults = []
    path = _ROOT / "flocks" / "server" / "routes" / "knowledgebase.py"
    tree_ast = ast.parse(path.read_text(encoding="utf-8"))
    error_handler = next(node for node in ast.walk(tree_ast) if isinstance(node, ast.FunctionDef) and node.name == "_error")
    tree = _FileTree(
        _file_resource("folder", "Empty", kind="folder"),
        write_handler=lambda _request: pytest.fail("blocked route issued an upstream write"),
    )

    async def scenario():
        client = _client(resources, tree)
        namespace = {
            "client": lambda: client,
            "KnowledgebaseError": resources.errors.KnowledgebaseError,
            "JSONResponse": resources.client.JSONResponse,
            "Response": lambda **_kwargs: pytest.fail("blocked mutation reported success"),
        }
        module = ast.fix_missing_locations(ast.Module(body=[error_handler, handler], type_ignores=[]))
        exec(compile(module, "<pure mutation handlers>", "exec"), namespace)
        try:
            response = await namespace[name](*arguments, "user")
            assert response.status_code == 400
            error = json.loads(response.body)["error"]
            assert error["code"] == code
            assert "disabled" in error["message"]
            assert error["details"] == {}
        finally:
            await client.close()

    asyncio.run(scenario())
    assert tree.writes == []
    if name == "update_dataset":
        assert tree.requests == []


def test_update_route_statically_declares_authenticated_put_and_empty_204():
    handler = _resource_handler("update_dataset")
    decorator = handler.decorator_list[0]
    assert isinstance(decorator, ast.Call)
    assert decorator.func.attr == "put"
    assert ast.literal_eval(decorator.args[0]) == "/datasets/{dataset_id}"
    assert any(keyword.arg == "status_code" and ast.literal_eval(keyword.value) == 204 for keyword in decorator.keywords)
    assert any(isinstance(default, ast.Call) and ast.unparse(default) == "Depends(require_user)" for default in handler.args.defaults)
    calls = [node for node in ast.walk(handler) if isinstance(node, ast.Call)]
    assert any(ast.unparse(call) == "client().update_dataset(dataset_id, body)" for call in calls)
    returns = [node for node in ast.walk(handler) if isinstance(node, ast.Return)]
    assert any(ast.unparse(node.value) == "Response(status_code=204)" for node in returns)
    assert not any(isinstance(node.value, ast.Dict) for node in returns)


def test_document_content_route_statically_reuses_safe_file_response():
    handler = _resource_handler("document_content")
    decorator = handler.decorator_list[0]
    assert decorator.func.attr == "get"
    assert ast.literal_eval(decorator.args[0]) == "/datasets/{dataset_id}/documents/{document_id}/content"
    assert any(isinstance(default, ast.Call) and ast.unparse(default) == "Depends(require_user)" for default in handler.args.defaults)
    calls = [ast.unparse(node) for node in ast.walk(handler) if isinstance(node, ast.Call)]
    assert "client().document_content(dataset_id, document_id)" in calls
    assert "file_content_response(body, media, inline=inline)" in calls


def _files_response(files, total):
    return httpx.Response(200, json={"code": 0, "data": {
        "files": files, "total": total, "parent_folder": {"id": "root"},
    }})


def test_direct_list_files_preserves_lightweight_single_page_probe_contract(resources):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/prefix/api/v1/files"
        assert dict(request.url.params) == {"page": "1", "page_size": "1"}
        # A large directory must not trigger resource aggregation or its cap here.
        return _files_response([{"id": "file-1", "name": "File"}], 10001)

    async def scenario():
        client = _client(resources, handler)
        try:
            result = await client._api.list_files(page=1, page_size=1, keywords=None)
            assert result["total"] == 10001
            assert [item["id"] for item in result["items"]] == ["file-1"]
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(requests) == 1


def test_files_filter_all_pages_before_badge_count_and_pagination(resources):
    rows = [{"id": f"folder-{index}", "name": "Hidden", "type": "folder"} for index in range(100)]
    # Names do not establish type, including names resembling hidden folders.
    visible = [
        {"id": "file-1", "name": ".knowledgebase", "type": "doc"},
        {"id": "file-2", "name": "folder", "type": "file"},
        {"id": "file-3", "name": "folder/"},
    ]
    rows.extend(visible)
    requests = []

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/prefix/api/v1/files"
        assert set(request.url.params) == {"page", "page_size"}
        assert request.url.params["page_size"] == "100"
        page = int(request.url.params["page"])
        requests.append(page)
        start = (page - 1) * 100
        return _files_response(rows[start:start + 100], len(rows))

    async def scenario():
        client = _client(resources, handler)
        try:
            badge = await client.files(page_size=1)
            listing = await client.files(page_size=20)
            second = await client.files(page=2, page_size=1)
            beyond = await client.files(page=2, page_size=20)
            assert {badge["total"], listing["total"], second["total"], beyond["total"]} == {3}
            assert [item["id"] for item in badge["items"]] == ["file-1"]
            assert [item["id"] for item in listing["items"]] == ["file-1", "file-2", "file-3"]
            assert [item["id"] for item in second["items"]] == ["file-2"]
            assert second["page"] == 2
            assert beyond == {"items": [], "total": 3, "page": 2}
        finally:
            await client.close()

    asyncio.run(scenario())
    assert requests == [1, 2] * 4


def test_file_keywords_filter_complete_current_directory_before_count_and_page(resources):
    rows = [{"id": f"folder-{index}", "name": "PLAN folder", "type": "folder"} for index in range(99)]
    rows.extend([
        {"id": "file-1", "name": "PLAN first.txt"},
        {"id": "file-2", "name": "unrelated.txt"},
        {"id": "file-3", "name": "Second plan.txt"},
        {"id": "file-4", "name": "plan third.txt"},
    ])
    requests = []

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/prefix/api/v1/files"
        # Upstream keywords would traverse the subtree; only local filtering is allowed.
        assert set(request.url.params) == {"page", "page_size"}
        page = int(request.url.params["page"])
        page_size = int(request.url.params["page_size"])
        requests.append(page)
        start = (page - 1) * page_size
        return _files_response(rows[start:start + page_size], len(rows))

    async def scenario():
        client = _client(resources, handler)
        try:
            badge = await client.files(page_size=1, q="PlAn")
            listing = await client.files(page_size=20, q="PlAn")
            second = await client.files(page=2, page_size=1, q="PlAn")
            missing = await client.files(q="missing")
            assert {badge["total"], listing["total"], second["total"]} == {3}
            assert [item["id"] for item in badge["items"]] == ["file-1"]
            assert [item["id"] for item in listing["items"]] == ["file-1", "file-3", "file-4"]
            assert [item["id"] for item in second["items"]] == ["file-3"]
            assert missing == {"items": [], "total": 0, "page": 1}
        finally:
            await client.close()

    asyncio.run(scenario())
    assert requests == [1, 2] * 4


@pytest.mark.parametrize(("mode", "calls", "code"), [
    ("repeat_page", 2, "upstream_invalid_response"),
    ("repeat_id", 1, "upstream_invalid_response"),
    ("total_grows", 2, "upstream_invalid_response"),
    ("total_shrinks", 2, "upstream_invalid_response"),
    ("empty_page", 2, "upstream_invalid_response"),
    ("short_page", 1, "upstream_invalid_response"),
    ("overfull_page", 1, "upstream_invalid_response"),
    ("invalid_id", 1, "upstream_invalid_response"),
    ("invalid_total", 1, "upstream_invalid_response"),
    ("over_limit", 1, "upstream_listing_limit"),
])
def test_file_scan_rejects_incomplete_or_unstable_totals_without_looping(resources, monkeypatch, mode, calls, code):
    monkeypatch.setattr(resources.service, "_FILE_SCAN_PAGE_SIZE", 2)
    monkeypatch.setattr(resources.service, "_FILE_SCAN_MAX_PAGES", 2)
    rows = [{"id": f"file-{index}", "name": f"File {index}"} for index in range(4)]
    requests = []

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/prefix/api/v1/files"
        page = int(request.url.params["page"])
        assert 1 <= page <= 2
        requests.append(page)
        total = len(rows)
        batch = rows[(page - 1) * 2:page * 2]
        if mode == "repeat_page" and page == 2:
            batch = rows[:2]
        elif mode == "repeat_id":
            batch = [rows[0], rows[0]]
        elif mode == "total_grows" and page == 2:
            total += 1
        elif mode == "total_shrinks" and page == 2:
            total -= 1
        elif mode == "empty_page" and page == 2:
            batch = []
        elif mode == "short_page":
            batch = batch[:1]
        elif mode == "overfull_page":
            batch = rows[:3]
        elif mode == "invalid_id":
            batch = [{"name": "Hidden", "type": "folder"}, rows[1]]
        elif mode == "invalid_total":
            total = True
        elif mode == "over_limit":
            total += 1
        return _files_response(batch, total)

    async def scenario():
        client = _client(resources, handler)
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.files(page_size=1)
            assert caught.value.status == 502
            assert caught.value.code == code
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(requests) == calls


@pytest.mark.parametrize(("hidden", "visible"), [(0, 0), (4, 0), (0, 4), (2, 2)])
def test_file_scan_empty_and_exact_collection_limit(resources, monkeypatch, hidden, visible):
    monkeypatch.setattr(resources.service, "_FILE_SCAN_PAGE_SIZE", 2)
    monkeypatch.setattr(resources.service, "_FILE_SCAN_MAX_PAGES", 2)
    rows = [{"id": f"folder-{index}", "name": "Folder", "type": "folder"} for index in range(hidden)]
    rows.extend({"id": f"file-{index}", "name": "File"} for index in range(visible))
    requests = []

    def handler(request):
        assert request.method == "GET"
        page = int(request.url.params["page"])
        requests.append(page)
        return _files_response(rows[(page - 1) * 2:page * 2], len(rows))

    async def scenario():
        client = _client(resources, handler)
        try:
            result = await client.files()
            assert result["total"] == visible
            assert len(result["items"]) == visible
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(requests) == max(1, (hidden + visible + 1) // 2)


def test_file_scan_overall_timeout_cancels_inflight_page_without_partial_total(resources, monkeypatch):
    monkeypatch.setattr(resources.service, "_FILE_SCAN_PAGE_SIZE", 1)
    monkeypatch.setattr(resources.service, "_FILE_SCAN_TIMEOUT_SECONDS", 0.05)
    requests = []
    cancelled = []

    async def handler(request):
        assert request.method == "GET"
        page = int(request.url.params["page"])
        requests.append(page)
        if page == 2:
            try:
                await asyncio.Future()  # MockTransport only; cancelled by the whole-scan deadline.
            finally:
                cancelled.append(page)
        return _files_response([{"id": f"file-{page}", "name": "File"}], 2)

    async def scenario():
        client = _client(resources, handler)
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.files(page_size=1)
            assert caught.value.status == 502
            assert caught.value.code == "upstream_timeout"
        finally:
            await client.close()

    asyncio.run(scenario())
    assert requests == [1, 2]
    assert cancelled == [2]


def _file_resource(ident, name, parent="root", kind="doc", source=""):
    return {
        "id": ident, "name": name, "parent_id": parent, "type": kind,
        "source_type": source, "size": 42,
        "create_time": 0, "update_time": 1728897061948,
    }


class _FileTree:
    """In-memory v0.27.2 file responses, never a real server or filesystem."""

    def __init__(self, *records, write_handler=None):
        root = _file_resource("root", "/", kind="folder")
        self.nodes = {node["id"]: node for node in [root, *records]}
        self.requests = []
        self.writes = []
        self.write_handler = write_handler

    @staticmethod
    def ok(data):
        return httpx.Response(200, json={"code": 0, "data": data})

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path.removeprefix("/prefix/api/v1")
        if request.method != "GET":
            self.writes.append(request)
            if self.write_handler:
                return self.write_handler(request)
            payload = json.loads(request.content)
            if path == "/files" and request.method == "POST":
                assert payload["type"] == "folder"
                return self.ok(_file_resource("created", payload["name"], payload["parent_id"], "folder"))
            if path == "/files/move" and request.method == "POST":
                return self.ok(True)
            if path == "/files" and request.method == "DELETE":
                return self.ok({"success_count": 1})
            pytest.fail(f"unexpected mutation {request.method} {path}")
        if path.endswith("/ancestors"):
            ident = path.split("/")[-2]
            result = []
            while True:
                item = self.nodes[ident]
                result.append(item)
                if item["parent_id"] == ident:
                    return self.ok({"parent_folders": result})
                ident = item["parent_id"]
        assert path == "/files"
        assert set(request.url.params) <= {"page", "page_size", "parent_id"}
        ident = request.url.params.get("parent_id", "root")
        current = self.nodes[ident]
        rows = [item for item in self.nodes.values() if item["parent_id"] == ident and item["id"] != ident]
        if ident == "root":
            rows = [item for item in rows if item["name"] != "skills"]
        page = int(request.url.params["page"])
        size = int(request.url.params["page_size"])
        start = (page - 1) * size
        return self.ok({
            "files": rows[start:start + size], "total": len(rows),
            # The list response is the current folder's PARENT, not current.
            "parent_folder": self.nodes[current["parent_id"]],
        })


def test_directory_keeps_folders_and_counts_unsearched_files_across_pages(resources, monkeypatch):
    monkeypatch.setattr(resources.service, "_FILE_SCAN_PAGE_SIZE", 2)
    tree = _FileTree(
        _file_resource("folder", "Notes", kind="folder"),
        _file_resource("system", ".knowledgebase", kind="folder", source="knowledgebase"),
        _file_resource("alpha", "Alpha.txt"),
        _file_resource("beta", "Beta.txt"),
        _file_resource("remote", "Remote.txt", source="google_drive"),
        _file_resource("nested", "Not in root.txt", parent="folder"),
    )

    async def scenario():
        client = _client(resources, tree)
        try:
            full = await client.files(include_folders=True)
            assert full["total"] == 5
            assert full["file_total"] == 3
            assert [item["kind"] for item in full["items"]] == ["folder", "folder", "file", "file", "file"]
            assert {item["id"]: item["can_manage"] for item in full["items"]} == {
                "folder": True, "system": False, "alpha": True, "beta": True, "remote": False,
            }
            assert full["current_folder"] == {"id": "root", "name": "/", "parent_id": "root", "can_write": True}
            assert full["breadcrumbs"] == [full["current_folder"]]
            filtered = await client.files(include_folders=True, q="alpha", page_size=1)
            assert filtered["total"] == 1
            assert filtered["file_total"] == 3
            assert filtered["items"][0]["id"] == "alpha"
            assert filtered["items"][0]["updated_at"] == "2024-10-14T09:11:01.948Z"
            flat = await client.files()
            assert flat["total"] == 3
            assert "current_folder" not in flat
            assert all("kind" not in item for item in flat["items"])
        finally:
            await client.close()

    asyncio.run(scenario())
    assert all(request.url.path.endswith("/files") for request in tree.requests)
    assert len(tree.requests) == 9


def test_nested_directory_uses_authoritative_current_metadata_not_parent_folder(resources):
    tree = _FileTree(
        _file_resource("folder", "Notes", kind="folder"),
        _file_resource("child", "More", parent="folder", kind="folder"),
        _file_resource("file", "Read.txt", parent="folder"),
        _file_resource("deep", "Excluded.txt", parent="child"),
    )

    async def scenario():
        client = _client(resources, tree)
        try:
            result = await client.files(include_folders=True, parent_id="folder")
            assert result["current_folder"] == {"id": "folder", "name": "Notes", "parent_id": "root", "can_write": True}
            assert [item["id"] for item in result["breadcrumbs"]] == ["root", "folder"]
            assert [item["id"] for item in result["items"]] == ["child", "file"]
            assert result["file_total"] == 1
            assert result["total"] == 2
        finally:
            await client.close()

    asyncio.run(scenario())
    assert [request.url.path for request in tree.requests] == [
        "/prefix/api/v1/files/folder/ancestors", "/prefix/api/v1/files",
    ]
    assert tree.requests[-1].url.params["parent_id"] == "folder"


@pytest.mark.parametrize(("name", "source"), [
    ("Internal", "knowledgebase"), ("skills", ""), (".knowledgebase", ""), ("External", "s3"),
    ("SKILLS", ""), (".KNOWLEDGEBASE", ""),
])
def test_system_ancestors_allow_browsing_but_not_management(resources, name, source):
    tree = _FileTree(
        _file_resource("system", name, kind="folder", source=source),
        _file_resource("folder", "Nested", parent="system", kind="folder"),
        _file_resource("file", "Read.txt", parent="folder"),
    )

    async def scenario():
        client = _client(resources, tree)
        try:
            result = await client.files(include_folders=True, parent_id="folder")
            assert result["current_folder"]["can_write"] is False
            assert result["items"][0]["can_manage"] is False
            assert [item["can_write"] for item in result["breadcrumbs"]] == [True, False, False]
            operations = [
                lambda: client.create_folder({"name": "New", "parent_id": "folder"}),
                lambda: client.upload("Upload.txt", b"content", "text/plain", parent_id="folder"),
                lambda: client.update_file("file", {"name": "Renamed.txt"}),
                lambda: client.delete_file("file"),
            ]
            for operation in operations:
                with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                    await operation()
                assert caught.value.status == 403
                assert caught.value.code == "resource_read_only"
        finally:
            await client.close()

    asyncio.run(scenario())
    assert tree.writes == []


def test_missing_source_marker_is_read_only_without_hiding_folder(resources):
    folder = _file_resource("folder", "Unknown", kind="folder")
    folder.pop("source_type")
    tree = _FileTree(folder)

    async def scenario():
        client = _client(resources, tree)
        try:
            root = await client.files(include_folders=True)
            assert root["items"][0]["kind"] == "folder"
            assert root["items"][0]["can_manage"] is False
            result = await client.files(include_folders=True, parent_id="folder")
            assert result["current_folder"]["can_write"] is False
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.delete_file("folder")
            assert caught.value.status == 403
        finally:
            await client.close()

    asyncio.run(scenario())
    assert tree.writes == []


@pytest.mark.parametrize("mode", ["empty", "wrong_start", "broken_parent", "cycle", "nonfolder_parent", "too_deep"])
def test_directory_rejects_invalid_ancestor_metadata(resources, mode):
    root = _file_resource("root", "/", kind="folder")
    folder = _file_resource("folder", "Folder", kind="folder")
    chain = [folder, root]
    if mode == "empty":
        chain = []
    elif mode == "wrong_start":
        folder["id"] = "wrong"
    elif mode == "broken_parent":
        folder["parent_id"] = "missing"
    elif mode == "cycle":
        chain.append(folder)
    elif mode == "nonfolder_parent":
        root["type"] = "doc"
    elif mode == "too_deep":
        chain = [folder] * 129

    def handler(request):
        assert request.url.path.endswith("/folder/ancestors")
        return _FileTree.ok({"parent_folders": chain})

    async def scenario():
        client = _client(resources, handler)
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.files(include_folders=True, parent_id="folder")
            assert caught.value.code == "upstream_invalid_response"
        finally:
            await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("parent_id", [None, "folder"])
def test_create_folder_posts_explicit_folder_type_and_actual_parent(resources, parent_id):
    tree = _FileTree(_file_resource("folder", "Folder", kind="folder"))

    async def scenario():
        client = _client(resources, tree)
        try:
            payload = {"name": " New folder "}
            if parent_id is not None:
                payload["parent_id"] = parent_id
            result = await client.create_folder(payload)
            assert result["name"] == "New folder"
            assert result["kind"] == "folder"
            assert result["can_manage"] is True
            assert result["parent_id"] == (parent_id or "root")
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(tree.writes) == 1
    assert tree.writes[0].url.path == "/prefix/api/v1/files"
    assert json.loads(tree.writes[0].content) == {"name": "New folder", "parent_id": parent_id or "root", "type": "folder"}


@pytest.mark.parametrize("name", ["skills", ".knowledgebase", "SKILLS", ".KNOWLEDGEBASE"])
def test_root_reserved_names_are_rejected_but_same_nested_names_are_local(resources, name):
    tree = _FileTree(_file_resource("folder", "Folder", kind="folder"))

    async def scenario():
        client = _client(resources, tree)
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.create_folder({"name": name})
            assert caught.value.code == "reserved_name"
            result = await client.create_folder({"name": name, "parent_id": "folder"})
            assert result["can_manage"] is True
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(tree.writes) == 1


@pytest.mark.parametrize("parent_id", [None, "folder"])
def test_upload_passes_actual_remote_parent_in_multipart(resources, parent_id):
    target = parent_id or "root"

    def upload(request):
        assert request.method == "POST"
        assert request.url.path == "/prefix/api/v1/files"
        assert b'name="parent_id"\r\n\r\n' + target.encode() + b"\r\n" in request.content
        assert b'name="file"; filename="Upload.txt"' in request.content
        assert b"file bytes" in request.content
        return _FileTree.ok([_file_resource("uploaded", "Upload.txt", parent=target)])

    tree = _FileTree(_file_resource("folder", "Folder", kind="folder"), write_handler=upload)

    async def scenario():
        client = _client(resources, tree)
        try:
            result = await client.upload("Upload.txt", b"file bytes", "text/plain", parent_id=parent_id)
            assert result["parent_id"] == target
            assert result["kind"] == "file"
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(tree.writes) == 1


@pytest.mark.parametrize(("ident", "payload", "expected"), [
    ("file", {"name": "Renamed.txt"}, {"src_file_ids": ["file"], "new_name": "Renamed.txt"}),
    ("file", {"name": "ORIGINAL.TXT"}, {"src_file_ids": ["file"], "new_name": "ORIGINAL.TXT"}),
    ("folder", {"name": "Renamed"}, {"src_file_ids": ["folder"], "new_name": "Renamed"}),
    ("file", {"parent_id": "folder"}, {"src_file_ids": ["file"], "dest_file_id": "folder"}),
    ("file", {"parent_id": "folder", "name": "Renamed.txt"}, {"src_file_ids": ["file"], "dest_file_id": "folder", "new_name": "Renamed.txt"}),
])
def test_rename_and_file_move_use_exact_remote_contract(resources, ident, payload, expected):
    tree = _FileTree(
        _file_resource("folder", "Folder", kind="folder"),
        _file_resource("file", "Original.txt"),
    )

    async def scenario():
        client = _client(resources, tree)
        try:
            assert await client.update_file(ident, payload) is None
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(tree.writes) == 1
    assert tree.writes[0].method == "POST"
    assert tree.writes[0].url.path == "/prefix/api/v1/files/move"
    assert json.loads(tree.writes[0].content) == expected


@pytest.mark.parametrize(("ident", "payload", "code"), [
    ("file", {"name": "Original.txt"}, "no_changes"),
    ("file", {"name": "Original.pdf"}, "file_extension_change"),
    ("file", {"parent_id": "root"}, "invalid_move"),
    ("file", {"parent_id": "file"}, "invalid_move"),
    ("file", {"parent_id": "other-file"}, "not_a_folder"),
    ("folder", {"parent_id": "nested"}, "folder_move_unsupported"),
    ("folder", {"parent_id": "folder"}, "invalid_move"),
    ("folder", {"name": ".knowledgebase"}, "reserved_name"),
    ("folder", {"name": ".KNOWLEDGEBASE"}, "reserved_name"),
    ("folder", {"name": "SKILLS"}, "reserved_name"),
    ("folder", {"name": "fOlDeR"}, "case_only_folder_rename_unsupported"),
    ("root", {"name": "Renamed"}, "resource_read_only"),
])
def test_invalid_moves_and_renames_never_mutate_remote_files(resources, ident, payload, code):
    tree = _FileTree(
        _file_resource("folder", "Folder", kind="folder"),
        _file_resource("nested", "Nested", parent="folder", kind="folder"),
        _file_resource("file", "Original.txt"),
        _file_resource("other-file", "Other.txt"),
    )

    async def scenario():
        client = _client(resources, tree)
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.update_file(ident, payload)
            assert caught.value.code == code
        finally:
            await client.close()

    asyncio.run(scenario())
    assert tree.writes == []


@pytest.mark.parametrize("operation", ["create", "upload", "rename", "move"])
@pytest.mark.parametrize("existing_name", ["Same.txt", "same.txt", "SAME.TXT"])
def test_name_conflicts_are_detected_before_mutation(resources, operation, existing_name):
    tree = _FileTree(
        _file_resource("folder", "Folder", kind="folder"),
        _file_resource("source", "Same.txt"),
        _file_resource("existing", existing_name, parent="folder"),
        _file_resource("other", "Other.txt", parent="folder"),
    )

    async def scenario():
        client = _client(resources, tree)
        try:
            operations = {
                "create": lambda: client.create_folder({"name": "Same.txt", "parent_id": "folder"}),
                "upload": lambda: client.upload("Same.txt", b"data", "text/plain", parent_id="folder"),
                "rename": lambda: client.update_file("other", {"name": "Same.txt"}),
                "move": lambda: client.update_file("source", {"parent_id": "folder"}),
            }
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await operations[operation]()
            assert caught.value.status == 409
            assert caught.value.code == "name_conflict"
        finally:
            await client.close()

    asyncio.run(scenario())
    assert tree.writes == []


def test_delete_file_checks_single_success_without_listing_a_folder(resources):
    tree = _FileTree(
        _file_resource("folder", "Empty", kind="folder"),
        _file_resource("file", "File.txt"),
    )

    async def scenario():
        client = _client(resources, tree)
        try:
            assert await client.delete_file("file") is None
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(tree.writes) == 1
    assert tree.writes[0].method == "DELETE"
    assert json.loads(tree.writes[0].content) == {"ids": ["file"]}
    assert not any(request.method == "GET" and request.url.path.endswith("/files") for request in tree.requests)


@pytest.mark.parametrize("concurrent_child", [False, True])
def test_folder_delete_is_blocked_before_listing_or_mutation(resources, concurrent_child):
    tree = _FileTree(
        _file_resource("folder", "Empty", kind="folder"),
        write_handler=lambda _request: pytest.fail("folder deletion reached an upstream mutation"),
    )

    def handler(request):
        response = tree(request)
        if concurrent_child and request.url.path.endswith("/folder/ancestors"):
            tree.nodes["new-child"] = _file_resource("new-child", "Concurrent.txt", parent="folder")
        return response

    async def scenario():
        client = _client(resources, handler)
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.delete_file("folder")
            assert (caught.value.status, caught.value.code) == (400, "folder_delete_unsupported")
            assert "disabled" in caught.value.message
            assert "atomic empty-only" in caught.value.message
        finally:
            await client.close()

    asyncio.run(scenario())
    assert tree.writes == []
    assert [(request.method, request.url.path) for request in tree.requests] == [
        ("GET", "/prefix/api/v1/files/folder/ancestors"),
    ]
    if concurrent_child:
        assert tree.nodes["new-child"]["parent_id"] == "folder"


@pytest.mark.parametrize("kind", ["folder", "doc"])
def test_nonempty_folder_delete_is_rejected_without_recursive_walk(resources, kind):
    tree = _FileTree(
        _file_resource("folder", "Nonempty", kind="folder"),
        _file_resource("child", "Child", parent="folder", kind=kind),
    )

    async def scenario():
        client = _client(resources, tree)
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.delete_file("folder")
            assert caught.value.status == 400
            assert caught.value.code == "folder_delete_unsupported"
            with pytest.raises(resources.errors.KnowledgebaseError) as root_error:
                await client.delete_file("root")
            assert root_error.value.code == "resource_read_only"
        finally:
            await client.close()

    asyncio.run(scenario())
    assert tree.writes == []
    assert not any("/child/" in request.url.path or request.url.params.get("parent_id") == "child" for request in tree.requests)


@pytest.mark.parametrize("count", [0, 2, None, True, "1"])
def test_delete_does_not_report_silently_skipped_or_unexpected_success(resources, count):
    tree = _FileTree(_file_resource("file", "File.txt"), write_handler=lambda _request: _FileTree.ok({"success_count": count}))

    async def scenario():
        client = _client(resources, tree)
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.delete_file("file")
            assert caught.value.code == "upstream_invalid_response"
        finally:
            await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("folder", [False, True])
def test_linking_accepts_explicit_file_but_never_expands_folder(resources, folder):
    def linked(request):
        assert request.method == "POST"
        assert request.url.path == "/prefix/api/v1/files/link-to-datasets"
        assert dict(request.url.params) == {"mode": "add"}
        assert json.loads(request.content) == {"file_ids": ["selected"], "kb_ids": ["dataset"]}
        return _FileTree.ok(True)

    tree = _FileTree(_file_resource("selected", "Selection", kind="folder" if folder else "doc"), write_handler=linked)

    async def scenario():
        client = _client(resources, tree)
        try:
            if folder:
                with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                    await client.link_files("dataset", ["selected"])
                assert caught.value.code == "folder_link_unsupported"
            else:
                assert await client.link_files("dataset", ["selected"]) == {"dataset_id": "dataset", "file_ids": ["selected"]}
        finally:
            await client.close()

    asyncio.run(scenario())
    assert len(tree.writes) == (0 if folder else 1)


@pytest.mark.parametrize("payload", [
    {}, {"name": None}, {"parent_id": None}, {"name": ""}, {"name": ".."},
    {"name": "bad/name"}, {"name": "bad\\name"}, {"name": "bad\x00name"},
    {"name": "bad\nname"}, {"name": " "}, {"name": "n" * 256},
    {"parent_id": "bad/id"}, {"parent_id": ".."}, {"name": "Name", "extra": True},
])
def test_file_update_validation_precedes_remote_lookups(resources, payload):
    async def scenario():
        client = _client(resources, lambda _request: pytest.fail("invalid update reached upstream"))
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.update_file("file", payload)
            assert caught.value.status == 422
        finally:
            await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("options", [
    {"parent_id": "folder"}, {"include_folders": "true"},
    {"include_folders": True, "parent_id": "../folder"},
])
def test_directory_query_validation_precedes_remote_lookups(resources, options):
    async def scenario():
        client = _client(resources, lambda _request: pytest.fail("invalid query reached upstream"))
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.files(**options)
            assert caught.value.status == 422
        finally:
            await client.close()

    asyncio.run(scenario())


def test_file_management_routes_statically_keep_auth_and_204_contract():
    for name, method, route in [
        ("create_folder", "post", "/folders"),
        ("update_file", "put", "/files/{file_id}"),
        ("delete_file", "delete", "/files/{file_id}"),
    ]:
        handler = _resource_handler(name)
        decorator = handler.decorator_list[0]
        assert decorator.func.attr == method
        assert ast.literal_eval(decorator.args[0]) == route
        assert any(isinstance(default, ast.Call) and ast.unparse(default) == "Depends(require_user)" for default in handler.args.defaults)
        if method != "post":
            assert any(keyword.arg == "status_code" and ast.literal_eval(keyword.value) == 204 for keyword in decorator.keywords)
            assert any(isinstance(node, ast.Return) and ast.unparse(node.value) == "Response(status_code=204)" for node in ast.walk(handler))
    listing = _resource_handler("files")
    assert {arg.arg for arg in listing.args.args} >= {"parent_id", "include_folders"}
    upload = _resource_handler("upload")
    assert any(isinstance(default, ast.Call) and ast.unparse(default.func) == "Form" for default in upload.args.defaults)
    assert any(isinstance(node, ast.keyword) and node.arg == "parent_id" for node in ast.walk(upload))
