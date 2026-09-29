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
from unittest.mock import AsyncMock

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


def _resource_handler(name):
    # Parse, never import or execute the route module/application.
    path = _ROOT / "flocks" / "server" / "routes" / "knowledgebase.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == name)


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
        pytest.fail(f"unexpected read {request.method} {path}")


@pytest.mark.parametrize(("name", "source"), [
    ("Internal", "knowledgebase"), ("skills", ""), (".knowledgebase", ""), ("External", "s3"),
    ("SKILLS", ""), (".KNOWLEDGEBASE", ""),
])
@pytest.mark.parametrize("file_id", ["system", "folder", "file"])
def test_system_ancestors_reject_deletion(resources, name, source, file_id):
    tree = _FileTree(
        _file_resource("system", name, kind="folder", source=source),
        _file_resource("folder", "Nested", parent="system", kind="folder"),
        _file_resource("file", "Read.txt", parent="folder"),
    )

    async def scenario():
        client = _client(resources, tree)
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.delete_file(file_id)
            assert caught.value.status == 403
            assert caught.value.code == "resource_read_only"
        finally:
            await client.close()

    asyncio.run(scenario())
    assert tree.writes == []


def test_missing_source_marker_is_read_only_for_deletion(resources):
    folder = _file_resource("folder", "Unknown", kind="folder")
    folder.pop("source_type")
    tree = _FileTree(folder)

    async def scenario():
        client = _client(resources, tree)
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.delete_file("folder")
            assert caught.value.status == 403
            assert caught.value.code == "resource_read_only"
        finally:
            await client.close()

    asyncio.run(scenario())
    assert tree.writes == []


@pytest.mark.parametrize("mode", ["empty", "wrong_start", "broken_parent", "cycle", "nonfolder_parent", "too_deep"])
def test_delete_rejects_invalid_ancestor_metadata(resources, mode):
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
        assert request.method == "GET"
        assert request.url.path.endswith("/folder/ancestors")
        return _FileTree.ok({"parent_folders": chain})

    async def scenario():
        client = _client(resources, handler)
        try:
            with pytest.raises(resources.errors.KnowledgebaseError) as caught:
                await client.delete_file("folder")
            assert caught.value.status == 502
            assert caught.value.code == "upstream_invalid_response"
        finally:
            await client.close()

    asyncio.run(scenario())


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
            assert (root_error.value.status, root_error.value.code) == (403, "resource_read_only")
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
