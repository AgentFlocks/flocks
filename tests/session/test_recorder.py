"""
Tests for flocks/session/recorder.py

Covers:
- _safe_truncate(): value truncation
- Recorder.paths(): path construction from env/config
- Recorder.append_jsonl(): file creation and content
- Recorder.record_session_message(): session JSONL records
- Recorder.record_tool_state(): tool state records with truncation
- Recorder.record_workflow_execution(): summary + step records
- LRU lock eviction when _MAX_FILE_LOCKS exceeded
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from flocks.session.recorder import Recorder, _safe_truncate, _record_dir


# ---------------------------------------------------------------------------
# _safe_truncate
# ---------------------------------------------------------------------------

class TestSafeTruncate:
    def test_none_returns_none(self):
        assert _safe_truncate(None) is None

    def test_short_string_unchanged(self):
        s = "hello world"
        assert _safe_truncate(s) == "hello world"

    def test_long_string_truncated(self):
        s = "x" * 25000
        result = _safe_truncate(s, max_chars=20000)
        assert len(result) < len(s)
        assert "truncated" in result

    def test_exact_length_not_truncated(self):
        s = "x" * 20000
        result = _safe_truncate(s, max_chars=20000)
        assert result == s
        assert "truncated" not in result

    def test_non_string_returned_as_is(self):
        assert _safe_truncate(42) == 42
        assert _safe_truncate({"key": "val"}) == {"key": "val"}
        assert _safe_truncate([1, 2, 3]) == [1, 2, 3]

    def test_truncated_suffix_includes_byte_count(self):
        s = "x" * 21000
        result = _safe_truncate(s, max_chars=20000)
        assert "1000" in result  # 21000 - 20000 = 1000 chars trimmed


# ---------------------------------------------------------------------------
# _record_dir
# ---------------------------------------------------------------------------

class TestRecordDir:
    def test_uses_env_var_when_set(self, tmp_path):
        with patch.dict(os.environ, {"FLOCKS_RECORD_DIR": str(tmp_path)}):
            result = _record_dir()
        assert result == tmp_path

    def test_uses_config_path_when_no_env(self):
        with patch.dict(os.environ, {}, clear=False):
            if "FLOCKS_RECORD_DIR" in os.environ:
                del os.environ["FLOCKS_RECORD_DIR"]
            result = _record_dir()
        assert isinstance(result, Path)
        assert "records" in str(result)


# ---------------------------------------------------------------------------
# Recorder.paths()
# ---------------------------------------------------------------------------

class TestRecorderPaths:
    def test_paths_returns_record_paths(self, tmp_path):
        with patch("flocks.session.recorder._record_dir", return_value=tmp_path):
            paths = Recorder.paths()
        assert paths.session_dir == tmp_path / "session"
        assert paths.workflow_dir == tmp_path / "workflow"


# ---------------------------------------------------------------------------
# Recorder.append_jsonl()
# ---------------------------------------------------------------------------

class TestAppendJsonl:
    @pytest.mark.asyncio
    async def test_creates_file_and_writes_line(self, tmp_path):
        path = tmp_path / "test.jsonl"
        obj = {"key": "value", "num": 42}
        await Recorder.append_jsonl(path, obj)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        parsed = json.loads(content.strip())
        assert parsed["key"] == "value"
        assert parsed["num"] == 42

    @pytest.mark.asyncio
    async def test_appends_multiple_lines(self, tmp_path):
        path = tmp_path / "multi.jsonl"
        await Recorder.append_jsonl(path, {"n": 1})
        await Recorder.append_jsonl(path, {"n": 2})
        await Recorder.append_jsonl(path, {"n": 3})
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3
        parsed = [json.loads(l) for l in lines]
        assert [p["n"] for p in parsed] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "a" / "b" / "c" / "test.jsonl"
        await Recorder.append_jsonl(path, {"hello": "world"})
        assert path.exists()

    @pytest.mark.asyncio
    async def test_never_raises_on_permission_error(self, tmp_path):
        # Writing to a non-writable path should not raise
        with patch("asyncio.to_thread", side_effect=PermissionError("denied")):
            # Should NOT raise even on write failure
            await Recorder.append_jsonl(tmp_path / "x.jsonl", {"data": 1})

    @pytest.mark.asyncio
    async def test_json_line_ends_with_newline(self, tmp_path):
        path = tmp_path / "newline.jsonl"
        await Recorder.append_jsonl(path, {"x": 1})
        content = path.read_text(encoding="utf-8")
        assert content.endswith("\n")


# ---------------------------------------------------------------------------
# Recorder.record_session_message()
# ---------------------------------------------------------------------------

class TestRecordSessionMessage:
    @pytest.mark.asyncio
    async def test_writes_correct_fields(self, tmp_path):
        with patch("flocks.session.recorder._record_dir", return_value=tmp_path):
            await Recorder.record_session_message(
                session_id="ses_test",
                message_id="msg_001",
                role="user",
                text="Hello world",
            )

        path = tmp_path / "session" / "ses_test.jsonl"
        assert path.exists()
        record = json.loads(path.read_text(encoding="utf-8").strip())
        assert record["type"] == "session.message"
        assert record["session_id"] == "ses_test"
        assert record["message_id"] == "msg_001"
        assert record["role"] == "user"
        assert record["text"] == "Hello world"
        assert "ts" in record

    @pytest.mark.asyncio
    async def test_truncates_long_text(self, tmp_path):
        with patch("flocks.session.recorder._record_dir", return_value=tmp_path):
            await Recorder.record_session_message(
                session_id="ses_trunc",
                message_id="msg_002",
                role="assistant",
                text="x" * 25000,
            )

        path = tmp_path / "session" / "ses_trunc.jsonl"
        record = json.loads(path.read_text(encoding="utf-8").strip())
        assert "truncated" in record["text"]

    @pytest.mark.asyncio
    async def test_extra_fields_included(self, tmp_path):
        with patch("flocks.session.recorder._record_dir", return_value=tmp_path):
            await Recorder.record_session_message(
                session_id="ses_extra",
                message_id="msg_003",
                role="user",
                text="hi",
                extra={"model": "claude-3-5"},
            )

        path = tmp_path / "session" / "ses_extra.jsonl"
        record = json.loads(path.read_text(encoding="utf-8").strip())
        assert record["extra"]["model"] == "claude-3-5"


# ---------------------------------------------------------------------------
# Recorder.record_tool_state()
# ---------------------------------------------------------------------------

class TestRecordToolState:
    @pytest.mark.asyncio
    async def test_writes_tool_state(self, tmp_path):
        with patch("flocks.session.recorder._record_dir", return_value=tmp_path):
            await Recorder.record_tool_state(
                session_id="ses_tool",
                message_id="msg_t1",
                part_id="part_t1",
                call_id="call_t1",
                tool="bash",
                state={"status": "completed", "output": "result"},
            )

        path = tmp_path / "session" / "ses_tool.jsonl"
        record = json.loads(path.read_text(encoding="utf-8").strip())
        assert record["type"] == "session.tool"
        assert record["tool"] == "bash"
        assert record["call_id"] == "call_t1"

    @pytest.mark.asyncio
    async def test_truncates_raw_and_output_in_state(self, tmp_path):
        with patch("flocks.session.recorder._record_dir", return_value=tmp_path):
            await Recorder.record_tool_state(
                session_id="ses_big",
                message_id="msg_big",
                part_id="part_big",
                call_id="call_big",
                tool="read_file",
                state={
                    "status": "completed",
                    "raw": "r" * 25000,
                    "output": "o" * 25000,
                    "error": "e" * 25000,
                },
            )

        path = tmp_path / "session" / "ses_big.jsonl"
        record = json.loads(path.read_text(encoding="utf-8").strip())
        state = record["state"]
        assert "truncated" in state.get("raw", "")
        assert "truncated" in state.get("output", "")
        assert "truncated" in state.get("error", "")


# ---------------------------------------------------------------------------
# Recorder.record_workflow_execution()
# ---------------------------------------------------------------------------

class TestRecordWorkflowExecution:
    @pytest.mark.asyncio
    async def test_writes_summary_record(self, tmp_path):
        with patch("flocks.session.recorder._record_dir", return_value=tmp_path):
            await Recorder.record_workflow_execution(
                exec_id="exec_001",
                workflow_id="wf_001",
                run_result={"status": "success", "executionLog": []},
            )

        path = tmp_path / "workflow" / "exec_001.jsonl"
        assert path.exists()
        record = json.loads(path.read_text(encoding="utf-8").strip())
        assert record["type"] == "workflow.summary"
        assert record["exec_id"] == "exec_001"
        assert record["workflow_id"] == "wf_001"
        assert record["status"] == "success"

    @pytest.mark.asyncio
    async def test_writes_step_records(self, tmp_path):
        history = [
            {"node_id": "node_1", "inputs": "in1", "outputs": "out1", "duration_ms": 100},
            {"node_id": "node_2", "inputs": "in2", "outputs": "out2", "duration_ms": 200},
        ]
        with patch("flocks.session.recorder._record_dir", return_value=tmp_path):
            await Recorder.record_workflow_execution(
                exec_id="exec_002",
                workflow_id="wf_002",
                run_result={"status": "success", "executionLog": history},
            )

        path = tmp_path / "workflow" / "exec_002.jsonl"
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        records = [json.loads(l) for l in lines]

        summary = [r for r in records if r["type"] == "workflow.summary"]
        steps = [r for r in records if r["type"] == "workflow.step"]

        assert len(summary) == 1
        assert len(steps) == 2
        assert summary[0]["steps"] == 2
        assert steps[0]["node_id"] == "node_1"
        assert steps[1]["node_id"] == "node_2"

    @pytest.mark.asyncio
    async def test_workflow_id_none_allowed(self, tmp_path):
        with patch("flocks.session.recorder._record_dir", return_value=tmp_path):
            await Recorder.record_workflow_execution(
                exec_id="exec_003",
                workflow_id=None,
                run_result={"status": "error", "errorMessage": "fail"},
            )

        path = tmp_path / "workflow" / "exec_003.jsonl"
        record = json.loads(path.read_text(encoding="utf-8").strip())
        assert record["workflow_id"] is None

    @pytest.mark.asyncio
    async def test_compacts_large_workflow_outputs_before_writing_jsonl(self, tmp_path):
        large_alerts = [{"id": idx, "payload": "x" * 20} for idx in range(150)]
        with patch("flocks.session.recorder._record_dir", return_value=tmp_path):
            await Recorder.record_workflow_execution(
                exec_id="exec_004",
                workflow_id="wf_004",
                run_result={
                    "status": "success",
                    "outputResults": {"enriched_alerts": large_alerts, "message": "done"},
                    "executionLog": [
                        {
                            "node_id": "node_1",
                            "inputs": {"source": "sensor"},
                            "outputs": {"raw_alerts": large_alerts, "message": "ok"},
                            "duration_ms": 12,
                        }
                    ],
                },
            )

        path = tmp_path / "workflow" / "exec_004.jsonl"
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        records = [json.loads(line) for line in lines]
        summary = next(record for record in records if record["type"] == "workflow.summary")
        step = next(record for record in records if record["type"] == "workflow.step")

        assert summary["outputs"] == {
            "_enriched_alerts_count": 150,
            "message": "done",
        }
        assert "enriched_alerts" not in summary["outputs"]
        assert step["outputs"] == {
            "_raw_alerts_count": 150,
            "message": "ok",
        }
        assert "raw_alerts" not in step["outputs"]
        assert step["inputs"] == {"source": "sensor"}


# ---------------------------------------------------------------------------
# LRU lock eviction
# ---------------------------------------------------------------------------

class TestLockLruEviction:
    @pytest.mark.asyncio
    async def test_locks_evicted_when_cap_exceeded(self, tmp_path):
        """Creating more than _MAX_FILE_LOCKS should evict old entries."""
        from flocks.session.recorder import _MAX_FILE_LOCKS
        original_locks = Recorder._locks
        Recorder._locks = type(original_locks)()  # fresh OrderedDict

        try:
            for i in range(_MAX_FILE_LOCKS + 10):
                Recorder._get_lock(f"key_{i}")

            assert len(Recorder._locks) <= _MAX_FILE_LOCKS
        finally:
            Recorder._locks = original_locks
