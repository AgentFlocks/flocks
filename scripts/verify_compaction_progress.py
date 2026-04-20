"""Standalone verification for SSE-progress emit in compaction pipeline.

This script does NOT rely on pytest. It stubs the heavy provider /
session dependencies, then drives ``summarize_chunked`` and
``_emit_progress`` directly to confirm:

  1. ``_emit_progress(None, ...)`` is a no-op (no exception).
  2. ``_emit_progress`` swallows callback exceptions (does not bubble
     up into the compaction pipeline).
  3. ``summarize_chunked`` emits exactly one ``chunk_done`` per chunk,
     followed by ``merge_started`` and ``merge_done`` (in that order).
  4. ``chunk_done`` payload includes ``chunk``, ``total``,
     ``duration_ms``, ``ok``.
  5. Failed chunks still emit ``chunk_done`` with ``ok=False`` so the
     UI progress bar always advances.

Run from repo root:

    PYTHONPATH=. python scripts/verify_compaction_progress.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _stub_module(name: str, attrs: dict | None = None) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in (attrs or {}).items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _build_stubs() -> None:
    """Stub ``flocks.utils.log``, ``flocks.session.prompt``, and
    ``flocks.provider.provider`` so ``summary.py`` can import without
    pulling the whole codebase / SDK chain."""

    class _NullLog:
        def info(self, *a, **k): pass
        def warn(self, *a, **k): pass
        def error(self, *a, **k): pass

    class _Log:
        @staticmethod
        def create(service: str = ""):
            return _NullLog()

    _stub_module("flocks", attrs={"__path__": [str(REPO / "flocks")]})
    _stub_module("flocks.utils")
    _stub_module("flocks.utils.log", attrs={"Log": _Log})

    class _SessionPrompt:
        @staticmethod
        def count_tokens(s: str) -> int:
            return len(s) // 4

    _stub_module("flocks.session")
    _stub_module("flocks.session.prompt", attrs={"SessionPrompt": _SessionPrompt})

    class _ChatMessage:
        def __init__(self, role: str, content: str):
            self.role = role
            self.content = content

    _stub_module("flocks.provider")
    _stub_module("flocks.provider.provider", attrs={"ChatMessage": _ChatMessage})


def _load_summary():
    """Load ``summary.py`` in isolation so we can monkey-patch its
    ``_llm_chat_with_timeout`` without touching real LLM code."""
    spec = importlib.util.spec_from_file_location(
        "compaction_summary",
        REPO / "flocks" / "session" / "lifecycle" / "compaction" / "summary.py",
    )
    assert spec and spec.loader, "could not locate summary.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeResp:
    def __init__(self, content: str):
        self.content = content


class _FakeMsg:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


def _load_compaction():
    """Load just the module-level ``_emit_progress`` helper from
    ``compaction.py`` in isolation. We import only the function source
    (the surrounding class needs heavy deps), via a tiny exec capsule."""
    text = (REPO / "flocks" / "session" / "lifecycle" / "compaction" / "compaction.py").read_text()
    # Find the helper definition and a thin module that exposes it.
    needle = "async def _emit_progress("
    start = text.index(needle)
    end = text.index("\nasync def ", start + 1)  # next top-level coroutine
    snippet = text[start:end]
    capsule = (
        "import logging\n"
        "class _Log:\n"
        "    def warn(self, *a, **k): pass\n"
        "log = _Log()\n"
        "from typing import Any, Awaitable, Callable, Dict, Optional\n"
        "ProgressCallback = Callable[[str, Dict[str, Any]], Awaitable[None]]\n"
        + snippet
    )
    ns: dict = {}
    exec(capsule, ns)
    return ns["_emit_progress"]


async def _run_assertions() -> None:
    _build_stubs()
    summary = _load_summary()

    # ------------------------------------------------------------------
    # 1 + 2: _safe_emit semantics (summary.py)
    # ------------------------------------------------------------------
    await summary._safe_emit(None, "load", {"x": 1})

    async def boom(stage, data):
        raise RuntimeError("simulated sink failure")

    await summary._safe_emit(boom, "load", {"x": 1})
    print("[ok] summary._safe_emit: None and exception both safe")

    # ------------------------------------------------------------------
    # 1 + 2 (mirror): compaction._emit_progress
    # ------------------------------------------------------------------
    emit_progress = _load_compaction()
    await emit_progress(None, "load", {"x": 1})
    await emit_progress(boom, "load", {"x": 1})
    print("[ok] compaction._emit_progress: None and exception both safe")

    # ------------------------------------------------------------------
    # 3 + 4: summarize_chunked happy path emits expected stages
    # ------------------------------------------------------------------
    msgs = [_FakeMsg("user", f"msg{i} " * 200) for i in range(6)]
    events: list[tuple[str, dict]] = []

    async def cb(stage, data):
        events.append((stage, dict(data)))

    async def fake_llm(provider_client, *, model_id, messages, max_tokens, timeout=None):
        # Simulate a tiny delay so durations are nonzero.
        await asyncio.sleep(0.01)
        # Echo the request length so test can distinguish merge vs chunk.
        return _FakeResp(f"summary-of-{len(messages[0].content)}-chars")

    summary._llm_chat_with_timeout = fake_llm  # type: ignore[attr-defined]

    result = await summary.summarize_chunked(
        chat_messages=msgs,
        prompt_text="final-prompt",
        target_chars=500,
        provider_client=object(),
        model_id="fake-model",
        max_tokens=2000,
        session_id="sess-1",
        chunk_size=400,
        progress_callback=cb,
    )
    assert result, "summarize_chunked returned empty result"

    stages = [s for s, _ in events]
    chunk_dones = [d for s, d in events if s == "chunk_done"]
    merge_started = [d for s, d in events if s == "merge_started"]
    merge_done = [d for s, d in events if s == "merge_done"]

    assert len(merge_started) == 1, f"expected 1 merge_started, got {len(merge_started)}"
    assert len(merge_done) == 1, f"expected 1 merge_done, got {len(merge_done)}"
    # All chunk_done events must precede merge_started.
    last_chunk_pos = max(i for i, s in enumerate(stages) if s == "chunk_done")
    merge_started_pos = stages.index("merge_started")
    assert last_chunk_pos < merge_started_pos, (
        f"chunk_done at {last_chunk_pos} must precede merge_started at {merge_started_pos}"
    )
    # chunk_done payload sanity
    seen_idx = set()
    for d in chunk_dones:
        assert "chunk" in d and "total" in d and "duration_ms" in d and "ok" in d, d
        seen_idx.add(d["chunk"])
    assert len(seen_idx) == len(chunk_dones), "duplicate chunk indices in chunk_done events"
    assert all(d["ok"] for d in chunk_dones), "some chunks unexpectedly marked ok=False"
    assert merge_done[0]["ok"], "merge_done unexpectedly marked failed"
    print(f"[ok] chunked happy path: {len(chunk_dones)} chunks, then merge_started → merge_done")

    # ------------------------------------------------------------------
    # 5: chunk failures still emit chunk_done with ok=False
    # ------------------------------------------------------------------
    events.clear()

    async def flaky_llm(provider_client, *, model_id, messages, max_tokens, timeout=None):
        await asyncio.sleep(0.01)
        # Fail every odd-indexed chunk (looking at request payload size).
        if "msg1" in messages[0].content or "msg3" in messages[0].content:
            raise RuntimeError("provider 503")
        return _FakeResp("ok-summary")

    summary._llm_chat_with_timeout = flaky_llm  # type: ignore[attr-defined]
    await summary.summarize_chunked(
        chat_messages=msgs,
        prompt_text="final-prompt",
        target_chars=500,
        provider_client=object(),
        model_id="fake-model",
        max_tokens=2000,
        session_id="sess-flaky",
        chunk_size=400,
        progress_callback=cb,
    )
    chunk_dones2 = [d for s, d in events if s == "chunk_done"]
    failed = [d for d in chunk_dones2 if not d["ok"]]
    assert failed, "expected at least one failed chunk_done with ok=False"
    assert all("reason" in d for d in failed), "failed chunk_done must include reason"
    print(f"[ok] flaky path: {len(failed)} chunk_done events have ok=False with reason")

    # ------------------------------------------------------------------
    # 6: callback raising never breaks summarize_chunked
    # ------------------------------------------------------------------
    summary._llm_chat_with_timeout = fake_llm  # type: ignore[attr-defined]

    async def raising_cb(stage, data):
        raise RuntimeError("UI got disconnected")

    res = await summary.summarize_chunked(
        chat_messages=msgs,
        prompt_text="final-prompt",
        target_chars=500,
        provider_client=object(),
        model_id="fake-model",
        max_tokens=2000,
        session_id="sess-raise",
        chunk_size=400,
        progress_callback=raising_cb,
    )
    assert res, "callback exceptions must not break compaction"
    print("[ok] raising callback does not break summarize_chunked")


def main() -> None:
    asyncio.run(_run_assertions())
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
