import asyncio
import gc
import importlib.util
import json
import mmap
import sys
import tarfile
import time
import tracemalloc
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks.diagnostics import memory
from flocks.diagnostics.common import GC_RECORD, Journal, process_sample


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    value = memory.Recorder(tmp_path, {"allocations": False, "duration_s": 60, "trace_seconds": 600})
    monkeypatch.setattr(memory, "_recorder", value)
    yield value
    value.stop_trace("test_cleanup")


def load_cli(name, monkeypatch):
    directory = Path(memory.__file__).parent
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location(f"diagnostic_{name}", directory / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_off_has_no_recording_side_effect(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "_recorder", None)
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(memory, "_metadata", lambda *a: pytest.fail("disabled hook inspected inputs"))

    @memory.observe("workflow")
    def original(value):
        return value

    value = object()
    assert original(value) is value
    memory.count_syslog(100)
    memory.start()
    assert memory._recorder is None
    assert not list(tmp_path.iterdir())


def test_return_exception_and_no_payload_retention(recorder):
    class Payload:
        def __len__(self):
            raise AssertionError("must not inspect arbitrary payload")

    @memory.observe("llm")
    def call(self, prompt):
        return prompt

    value = Payload()
    ref = weakref.ref(value)
    assert call(None, value) is value
    del value
    assert ref() is None
    assert recorder.active == {}

    error = RuntimeError("SENSITIVE ERROR")

    @memory.observe("workflow")
    def fail():
        raise error

    with pytest.raises(RuntimeError) as caught:
        fail()
    assert caught.value is error
    events = list(recorder.events.queue)
    assert "SENSITIVE ERROR" not in json.dumps(events)
    assert any(item.get("error_type") == "RuntimeError" for item in events)


@pytest.mark.asyncio
async def test_async_cancellation_and_stream_no_buffer(recorder):
    ready = asyncio.Event()

    @memory.observe("http")
    async def streaming(self, scope, receive, send):
        await send({"type": "http.response.body", "body": b"private"})
        ready.set()
        await asyncio.Event().wait()

    chunks = []

    async def send(message):
        chunks.append(message)

    task = asyncio.create_task(streaming(None, {"type": "http", "path": "/api/event/user-secret", "query_string": b"secret"}, None, send))
    await ready.wait()
    assert chunks == [{"type": "http.response.body", "body": b"private"}]
    assert len(recorder.active) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not recorder.active
    rendered = json.dumps(list(recorder.events.queue))
    assert "CancelledError" in rendered
    assert "private" not in rendered and "secret" not in rendered


def test_hooks_fail_open(recorder, monkeypatch):
    monkeypatch.setattr(recorder, "begin", lambda *a: (_ for _ in ()).throw(OSError("disk")))

    @memory.observe("workflow")
    def work():
        return 42

    assert work() == 42


def test_bounded_active_queue_and_cardinality(recorder):
    tokens = [recorder.begin({"kind": "node", "workflow": str(i), "node": "n"}) for i in range(4000)]
    assert len(recorder.active) <= memory.MAX_ACTIVE
    assert len(recorder.stats) <= memory.MAX_KEYS + 1
    for token in tokens:
        recorder.finish(token, None, {"type": "other"})
    assert not recorder.active
    assert sum(row["started"] for row in recorder.stats.values()) == 4000
    assert sum(row["finished"] for row in recorder.stats.values()) == 4000
    for i in range(3000):
        recorder.emit({"type": "test", "id": i})
    assert recorder.events.qsize() == 1024
    assert recorder.dropped > 0


def test_keyword_node_metadata(recorder):
    owner = SimpleNamespace(workflow=SimpleNamespace(id="wf"))
    node = SimpleNamespace(id="node")

    @memory.observe("node")
    def execute(self, node, inputs):
        return inputs

    assert execute(owner, node=node, inputs={"private": 1}) == {"private": 1}
    event = recorder.events.get_nowait()
    assert event["workflow"] == "wf" and event["node"] == "node"
    assert "private" not in json.dumps(list(recorder.events.queue))


def test_gc_callback_uses_shared_scalar_record_only(recorder, monkeypatch):
    recorder.gc_map = mmap.mmap(-1, 4096)
    monkeypatch.setattr(recorder, "emit", lambda *a: pytest.fail("GC must not queue/log"))
    recorder.gc_callback("start", {"generation": 2})
    start = GC_RECORD.unpack_from(recorder.gc_map)
    assert start[0] % 2 == 0 and start[1:3] == (1, 2)
    recorder.gc_callback("stop", {"generation": 2, "collected": 17, "uncollectable": 0})
    end = GC_RECORD.unpack_from(recorder.gc_map)
    assert end[0] > start[0] and end[1] == 0 and end[5] == 17
    assert recorder.gc_totals == [0, 0, 1]
    recorder.gc_map.close()


def test_snapshot_identifies_retained_allocation(recorder):
    recorder.start_trace()
    recorder.trace_step({})
    retained = [bytearray(1024 * 1024) for _ in range(8)]
    recorder.trace_started -= 11
    recorder.trace_step({})
    rows = [json.loads(line) for line in (recorder.directory / "allocations.jsonl").read_text().splitlines()]
    snapshot = [row for row in rows if row["type"] == "allocation_snapshot"][-1]
    found = [row for row in snapshot["top"] if row["file"].endswith("test_memory.py") and row["bytes"] > 8 * 1024 * 1024]
    assert found, snapshot
    assert found[0]["delta_bytes"] > 8 * 1024 * 1024
    assert recorder.trace_previous and all(type(value) is tuple for value in recorder.trace_previous.values())
    assert len(retained) == 8


@pytest.mark.parametrize("limit,reason", [
    ("TRACE_METADATA_LIMIT", "metadata_limit"), ("TRACE_HEAP_LIMIT", "traced_heap_limit"),
])
def test_trace_guard_does_not_take_snapshot_after_limit(recorder, monkeypatch, limit, reason):
    recorder.start_trace()
    monkeypatch.setattr(memory, limit, 0)
    monkeypatch.setattr(tracemalloc, "take_snapshot", lambda: pytest.fail("unsafe snapshot"))
    recorder.trace_step({})
    assert not recorder.trace_owned
    assert reason in (recorder.directory / "allocations.jsonl").read_text()


def test_does_not_stop_external_tracer(recorder):
    tracemalloc.start(1)
    try:
        recorder.start_trace()
        assert not recorder.trace_owned
        recorder.stop_trace("test")
        assert tracemalloc.is_tracing()
    finally:
        tracemalloc.stop()


def test_snapshot_slow_guard(recorder, monkeypatch):
    recorder.start_trace()
    original = tracemalloc.take_snapshot

    def delayed():
        snapshot = original()
        recorder.trace_started -= 3
        monkeypatch.setattr(memory.time, "monotonic", lambda: 5000)
        return snapshot

    recorder.trace_started = 1000
    monkeypatch.setattr(memory.time, "monotonic", lambda: 1001)
    monkeypatch.setattr(tracemalloc, "take_snapshot", delayed)
    recorder.trace_step({})
    assert not recorder.trace_owned
    assert "snapshot_took_over_2s" in (recorder.directory / "allocations.jsonl").read_text()


@pytest.mark.asyncio
async def test_lifespan_wraps_exception_and_cleanup(monkeypatch):
    calls = []
    monkeypatch.setattr(memory, "start", lambda: calls.append("start"))
    monkeypatch.setattr(memory, "stop", lambda: calls.append("stop"))

    @memory.diagnostic_lifespan
    @asynccontextmanager
    async def lifecycle():
        try:
            calls.append("enter")
            yield "value"
        finally:
            calls.append("exit")

    with pytest.raises(ValueError):
        async with lifecycle() as value:
            assert value == "value"
            raise ValueError("test")
    assert calls == ["start", "enter", "exit", "stop"]


@pytest.mark.asyncio
async def test_start_stop_cleanup_and_invalid_config(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "_recorder", None)
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    path = tmp_path / "memory-diagnostics.json"
    path.write_text("not json")
    memory.start()
    assert memory._recorder is None
    path.write_text(json.dumps({"enabled": True, "allocations": False}))
    initial = len(gc.callbacks)
    memory.start()
    recorder = memory._recorder
    assert recorder is not None
    assert len(gc.callbacks) == initial + 1
    memory.stop()
    await asyncio.to_thread(recorder.thread.join, 3)
    assert not recorder.thread.is_alive()
    assert len(gc.callbacks) == initial
    assert recorder.gc_map.closed


def test_journal_rotation_and_permissions(tmp_path):
    journal = Journal(tmp_path / "log.jsonl", max_bytes=256)
    for i in range(100):
        journal.write({"type": "sample", "index": i})
    paths = list(tmp_path.iterdir())
    assert len(paths) == 3
    assert all(path.stat().st_size < 256 for path in paths)
    assert all(path.stat().st_mode & 0o077 == 0 for path in paths)


def test_proc_parser_handles_space_and_parentheses(tmp_path):
    base = tmp_path / "123"
    base.mkdir()
    fields = ["S"] + [str(i) for i in range(1, 50)]
    (base / "stat").write_text("123 (python ) name) " + " ".join(fields))
    (base / "status").write_text("VmRSS:\t1024 kB\nVmSwap:\t512 kB\nThreads:\t20\n")
    value = process_sample(123, tmp_path)
    assert value["start_ticks"] == 19 and value["major_faults"] == 9
    assert value["VmRSS"] == 1024 and value["VmSwap"] == 512 and value["Threads"] == 20


def test_cli_collect_excludes_business_files(monkeypatch, tmp_path):
    control = load_cli("control", monkeypatch)
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    assert control.main(["enable", "--allocations"]) is None
    capture = tmp_path / "workspace" / "outputs" / "2026-01-01" / "memory-diagnostics" / "test"
    capture.mkdir(parents=True)
    control.write_json(tmp_path / "memory-diagnostics-latest.json", {"directory": str(capture), "pid": 123})
    (capture / "runtime.jsonl").write_text('{"type":"test"}\n')
    (capture / "backend.log").write_text("SECRET")
    (capture / "workflow.db").write_text("SECRET")
    target = control.collect(capture)
    with tarfile.open(target) as archive:
        names = archive.getnames()
        assert "memory-evidence/runtime.jsonl" in names
        assert "memory-evidence/collection.json" in names
        assert not any("backend" in name or "workflow.db" in name for name in names)
    assert control.main(["mark", "growth"]) == 0
    assert control.main(["trace"]) == 0
    assert (capture / "TRACE").exists()
    control.main(["disable"])
    assert (capture / "STOP").exists()
    assert not json.loads((tmp_path / "memory-diagnostics.json").read_text())["enabled"]


def test_cli_refuses_foreign_directory(monkeypatch, tmp_path):
    control = load_cli("control", monkeypatch)
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    control.write_json(tmp_path / "memory-diagnostics-latest.json", {"directory": str(tmp_path), "pid": 123})
    with pytest.raises(ValueError):
        control.latest()


def test_sidecar_pid_reuse_exits_without_signalling(monkeypatch, tmp_path):
    sidecar = load_cli("sidecar", monkeypatch)
    monkeypatch.setattr(sidecar, "process_sample", lambda pid: {"start_ticks": 999, "state": "S"})
    monkeypatch.setattr(sys, "argv", ["sidecar", "--pid", "123", "--start-ticks", "1", "--directory", str(tmp_path)])
    sidecar.main()
    records = [json.loads(line) for line in (tmp_path / "os.jsonl").read_text().splitlines()]
    assert records[-1]["reason"] == "target_exited_or_reused"


def test_sidecar_rejects_torn_gc_record(monkeypatch, tmp_path):
    sidecar = load_cli("sidecar", monkeypatch)
    (tmp_path / "gc-state.bin").write_bytes(GC_RECORD.pack(3, 1, 2, time.monotonic(), 17, 0, 0))
    assert sidecar.gc_sample(tmp_path) == {"inconsistent": True}


def test_concurrent_hook_counts_are_consistent(recorder):
    @memory.observe("workflow")
    def work(*, workflow, inputs):
        return inputs

    def batch():
        for _ in range(1000):
            assert work(workflow={"name": "concurrent"}, inputs={"n": 1}) == {"n": 1}

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: batch(), range(8)))
    assert not recorder.active
    assert sum(row["started"] for row in recorder.stats.values()) == 8000
    assert sum(row["finished"] for row in recorder.stats.values()) == 8000
    assert len(recorder.stats) == 1


def test_real_engine_emits_node_and_workflow_labels(recorder):
    from flocks.workflow.engine import WorkflowEngine
    from flocks.workflow.models import Workflow

    workflow = Workflow.model_validate({"name": "diagnostic-test", "start": "step", "nodes": [
        {"id": "step", "type": "python", "code": "outputs['result'] = inputs['n'] + 1"}], "edges": []})
    engine = WorkflowEngine(workflow=workflow)
    outputs, _ = engine._execute_node(workflow.nodes[0], {"n": 4})
    assert outputs == {"result": 5}
    event = recorder.events.get_nowait()
    assert event["workflow"] == "diagnostic-test" and event["node"] == "step"
    assert memory._workflow_label(SimpleNamespace(workflow=workflow, workflow_path="/plugins/triage/workflow.json")) == "triage"


def test_overlay_preserves_custom_route_and_restores(monkeypatch, tmp_path):
    directory = Path(memory.__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("overlay", directory / "scripts/memory_diagnostics_overlay.py")
    overlay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(overlay)
    archive = tmp_path / "patch.zip"
    overlay.build(directory, archive)
    bundle = tmp_path / "bundle"
    import zipfile
    import subprocess
    with zipfile.ZipFile(archive) as packed:
        packed.extractall(bundle)
    target = tmp_path / "release"
    original = {}
    for name in overlay.HOOKS:
        data = subprocess.check_output(["git", "show", f"{overlay.BASE}:{name}"], cwd=directory)
        if name == "flocks/server/app.py":
            data += b"\n# local knowledge router stays untouched\n"
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        original[name] = data
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "data"))
    overlay.install(target, bundle)
    assert b"local knowledge router stays untouched" in (target / "flocks/server/app.py").read_bytes()
    backup = next((tmp_path / "data/diagnostic-backups").iterdir())
    # Refuse overwriting edits made after installation, before touching any file.
    runner = target / "flocks/workflow/runner.py"
    patched = runner.read_bytes()
    runner.write_bytes(patched + b"\n# concurrent edit\n")
    with pytest.raises(ValueError):
        overlay.restore(backup)
    runner.write_bytes(patched)
    overlay.restore(backup)
    assert all((target / name).read_bytes() == data for name, data in original.items())
    assert not (target / "flocks/diagnostics/memory.py").exists()
    assert (backup / "removed-diagnostics/flocks/diagnostics/memory.py").exists()


def test_overlay_wrong_baseline_has_no_partial_writes(monkeypatch, tmp_path):
    directory = Path(memory.__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("overlay", directory / "scripts/memory_diagnostics_overlay.py")
    overlay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(overlay)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    source = tmp_path / "release/flocks/workflow/runner.py"
    source.parent.mkdir(parents=True)
    source.write_text("# unexpected source\n")
    (bundle / "manifest.json").write_text(json.dumps({"changes": [{"path": "flocks/workflow/runner.py",
                                                                  "base_sha256": "wrong"}], "new_files": {}}))
    with pytest.raises(ValueError):
        overlay.install(tmp_path / "release", bundle)
    assert source.read_text() == "# unexpected source\n"
