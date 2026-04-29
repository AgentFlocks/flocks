"""
AuthorManifest contract: append-only, tombstone-deletes, concurrent-safe.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from flocks.evolution.manifest import AuthorManifest


def _read_lines(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_append_creates_file_and_records_name():
    m = AuthorManifest.get()
    m.append({"name": "alpha", "scope": "user", "skill_dir": "/tmp/alpha"})
    assert m.is_authored("alpha") is True
    assert m.names() == ["alpha"]


def test_forget_creates_tombstone_without_rewriting():
    m = AuthorManifest.get()
    m.append({"name": "alpha", "scope": "user", "skill_dir": "/tmp/alpha"})
    m.forget("alpha", scope="user")

    assert m.is_authored("alpha") is False
    assert m.names() == []

    # Both records (live + tombstone) survive on disk.
    raw = _read_lines(m.path())
    assert len(raw) == 2
    assert raw[1].get("deleted") is True


def test_records_filtered_by_scope():
    m = AuthorManifest.get()
    m.append({"name": "alpha", "scope": "user", "skill_dir": "/tmp/u/alpha"})
    m.append({"name": "beta", "scope": "project", "skill_dir": "/tmp/p/beta"})

    assert sorted(m.names()) == ["alpha", "beta"]
    assert m.names(scope="user") == ["alpha"]
    assert m.names(scope="project") == ["beta"]


def test_concurrent_appends_preserve_every_record():
    """O_APPEND-based writes must not interleave on Windows or POSIX."""
    m = AuthorManifest.get()

    barrier = threading.Barrier(8)

    def worker(idx: int):
        barrier.wait()
        for j in range(20):
            m.append({"name": f"s-{idx}-{j}", "scope": "user", "skill_dir": "/x"})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(_read_lines(m.path())) == 8 * 20
    assert len(set(m.names())) == 8 * 20
