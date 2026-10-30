"""
Tests for the generic content-source interface (flocks.content).
"""

from pathlib import Path
from typing import List, Optional, Sequence

import pytest

from flocks.content import (
    ContentBody,
    ContentEntry,
    ContentKind,
    ContentNotFound,
    ContentRef,
    ContentSourceError,
    LocalContentSource,
    fetch_content,
    list_entries,
    register_source,
    registered_sources,
    reset_sources,
    try_fetch_content,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_sources()
    yield
    reset_sources()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class StubSource:
    """Minimal in-memory source used to exercise ordering and error paths."""

    def __init__(self, name: str, bodies: dict, kinds: Sequence[str] = (ContentKind.SKILL,)):
        self._name = name
        self._bodies = bodies
        self._kinds = set(kinds)
        self.raise_on_fetch: Optional[Exception] = None

    @property
    def name(self) -> str:
        return self._name

    def supports(self, kind: str) -> bool:
        return kind in self._kinds

    async def list_entries(self, kind: str) -> Sequence[ContentEntry]:
        return [
            ContentEntry(ref=ContentRef(kind=kind, content_id=cid), name=cid, summary=f"from {self._name}")
            for cid in self._bodies
        ]

    async def fetch(self, ref: ContentRef) -> Optional[ContentBody]:
        if self.raise_on_fetch is not None:
            raise self.raise_on_fetch
        if ref.content_id not in self._bodies:
            return None
        return ContentBody(ref=ref, text=self._bodies[ref.content_id])


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# C1-C3: local filesystem source
# ---------------------------------------------------------------------------

async def test_local_source_lists_entries(tmp_path):
    """C1: listing reports each item with an id and a one-line summary."""
    _write(tmp_path / "agents" / "reporter.md", "# Reporter\n\nBuilds weekly reports.\n")
    _write(tmp_path / "agents" / "triage.md", "# Triage\n\nSorts incoming alerts.\n")
    source = LocalContentSource(roots=[tmp_path])

    entries = await source.list_entries(ContentKind.AGENT)

    by_id = {entry.ref.content_id: entry for entry in entries}
    assert set(by_id) == {"reporter", "triage"}
    assert by_id["reporter"].summary == "Builds weekly reports."
    assert by_id["reporter"].ref.version  # mtime-derived version is populated


async def test_local_source_fetches_body(tmp_path):
    """C2: fetch returns the full text for a listed ref."""
    _write(tmp_path / "agents" / "reporter.md", "# Reporter\n\nFull body here.\n")
    source = LocalContentSource(roots=[tmp_path])
    register_source(source, priority=10)

    body = await fetch_content(ContentRef(kind=ContentKind.AGENT, content_id="reporter"))

    assert "Full body here." in body.as_text()
    assert body.source == "local"
    assert body.ref.version is not None


async def test_local_skill_id_comes_from_parent_directory(tmp_path):
    """C3: skills/<id>/SKILL.md is the item <id>, not 'SKILL'."""
    _write(tmp_path / "skills" / "report-helper" / "SKILL.md", "# Report helper\n\nHelps.\n")
    source = LocalContentSource(roots=[tmp_path])

    entries = await source.list_entries(ContentKind.SKILL)

    assert [entry.ref.content_id for entry in entries] == ["report-helper"]


# ---------------------------------------------------------------------------
# C4, C7, C8: resolution failures stay explicit
# ---------------------------------------------------------------------------

async def test_missing_content_raises_not_found():
    """C4: nothing registered for the kind is a miss, not an empty body."""
    with pytest.raises(ContentNotFound):
        await fetch_content(ContentRef(kind=ContentKind.SKILL, content_id="absent"))

    assert await try_fetch_content(ContentRef(kind=ContentKind.SKILL, content_id="absent")) is None


async def test_source_failure_is_reported_not_swallowed():
    """C7: a source that claims a ref then fails must not fall through.

    Falling through would silently serve a different source's version of the
    same id, which is exactly the shadowing the interface has to prevent.
    """
    failing = StubSource("failing", {"doc": "managed"})
    failing.raise_on_fetch = RuntimeError("backend down")
    fallback = StubSource("fallback", {"doc": "local copy"})
    register_source(failing, priority=10)
    register_source(fallback, priority=20)

    with pytest.raises(ContentSourceError) as excinfo:
        await fetch_content(ContentRef(kind=ContentKind.SKILL, content_id="doc"))

    assert "failing" in str(excinfo.value)


async def test_pinned_version_mismatch_does_not_serve_other_bytes(tmp_path):
    """C8: asking for a version the file no longer has is a miss."""
    path = _write(tmp_path / "skills" / "doc" / "SKILL.md", "# Doc\n\nv1\n")
    source = LocalContentSource(roots=[tmp_path])
    register_source(source, priority=10)

    ref = ContentRef(kind=ContentKind.SKILL, content_id="doc", version="1")
    assert path.exists()

    with pytest.raises(ContentNotFound):
        await fetch_content(ref)


# ---------------------------------------------------------------------------
# C5, C6, C9, C10: registry ordering
# ---------------------------------------------------------------------------

async def test_lower_priority_number_is_consulted_first():
    """C5: priority orders the sources; the first hit wins."""
    register_source(StubSource("managed", {"doc": "managed body"}), priority=10)
    register_source(StubSource("local", {"doc": "local body"}), priority=20)

    body = await fetch_content(ContentRef(kind=ContentKind.SKILL, content_id="doc"))

    assert body.as_text() == "managed body"
    assert body.source == "managed"


async def test_exclusive_kind_shadows_lower_priority_sources():
    """C6: an exclusive source owns its kind outright.

    Without this, dropping a file with a managed item's id into a local
    directory would override content the deployment controls.
    """
    register_source(
        StubSource("managed", {"doc": "managed body"}),
        priority=10,
        exclusive_kinds=[ContentKind.SKILL],
    )
    register_source(StubSource("local", {"doc": "local body", "extra": "local only"}), priority=20)

    # The managed source answers for ids it has...
    body = await fetch_content(ContentRef(kind=ContentKind.SKILL, content_id="doc"))
    assert body.as_text() == "managed body"

    # ...and ids it does not have stay unresolved rather than falling through.
    with pytest.raises(ContentNotFound):
        await fetch_content(ContentRef(kind=ContentKind.SKILL, content_id="extra"))

    listed = {entry.ref.content_id for entry in await list_entries(ContentKind.SKILL)}
    assert listed == {"doc"}


async def test_duplicate_source_name_is_rejected():
    """C9: two sources sharing a name would make ordering ambiguous."""
    register_source(StubSource("dup", {"a": "1"}), priority=10)

    with pytest.raises(ValueError, match="已注册"):
        register_source(StubSource("dup", {"b": "2"}), priority=20)

    assert registered_sources() == ["dup"]


async def test_listing_merges_sources_first_wins():
    """C10: the same id from two sources is listed once, highest priority."""
    register_source(StubSource("managed", {"shared": "m"}), priority=10)
    register_source(StubSource("local", {"shared": "l", "only-local": "x"}), priority=20)

    entries = {entry.ref.content_id: entry for entry in await list_entries(ContentKind.SKILL)}

    assert set(entries) == {"shared", "only-local"}
    assert entries["shared"].summary == "from managed"


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------

async def test_local_source_refuses_an_id_that_escapes_its_root(tmp_path):
    """C11: a content id is a name, not a path.

    Ids reach this source from callers -- a skill a model asked for, a request
    parameter -- and were substituted straight into a path, so `../../secret`
    read a file outside the plugin root and returned it as content. Bodies flow
    into a model's context, which makes that an arbitrary-read primitive.
    """
    from flocks.content.local import LocalContentSource

    root = tmp_path / "plugins"
    (root / "agents").mkdir(parents=True)
    secret = tmp_path / "outside"
    secret.mkdir()
    (secret / "stolen.md").write_text("db_password=hunter2\n", encoding="utf-8")

    source = LocalContentSource(roots=[root])
    escaping_id = "../../outside/stolen"

    body = await source.fetch(ContentRef(kind=ContentKind.AGENT, content_id=escaping_id))

    assert body is None


async def test_local_source_refuses_hidden_and_separator_ids(tmp_path):
    """C12: dot-prefixed and separator-bearing ids are not content names."""
    from flocks.content.local import LocalContentSource

    root = tmp_path / "plugins"
    (root / "agents").mkdir(parents=True)
    (root / "agents" / "ok.md").write_text("fine\n", encoding="utf-8")
    source = LocalContentSource(roots=[root])

    for bad in ("../ok", "sub/ok", "sub\\ok", ".hidden", ".."):
        assert await source.fetch(ContentRef(kind=ContentKind.AGENT, content_id=bad)) is None

    good = await source.fetch(ContentRef(kind=ContentKind.AGENT, content_id="ok"))
    assert good is not None and good.as_text().strip() == "fine"


async def test_local_source_refuses_a_symlink_pointing_outside(tmp_path):
    """C13: the id can be clean and still land on a link that points away."""
    import os

    from flocks.content.local import LocalContentSource

    root = tmp_path / "plugins"
    (root / "agents").mkdir(parents=True)
    secret = tmp_path / "outside" / "stolen.md"
    secret.parent.mkdir()
    secret.write_text("db_password=hunter2\n", encoding="utf-8")
    os.symlink(secret, root / "agents" / "linked.md")

    source = LocalContentSource(roots=[root])

    assert await source.fetch(ContentRef(kind=ContentKind.AGENT, content_id="linked")) is None


async def test_local_listing_summary_skips_front_matter(tmp_path):
    """A YAML front matter block is metadata; ``title: x`` must not become the blurb.

    Regression: only the ``---`` fences were skipped, so the first key of the
    block was reported as the summary of every file that carried front matter.
    """
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "with_meta.md").write_text(
        "---\ntitle: x\ntags: [a]\n---\n# Heading\n\nReal summary line.\n", encoding="utf-8"
    )
    (agents / "unterminated.md").write_text("---\ntitle: only\n", encoding="utf-8")

    source = LocalContentSource(roots=[tmp_path])
    by_id = {entry.ref.content_id: entry.summary for entry in await source.list_entries(ContentKind.AGENT)}

    assert by_id["with_meta"] == "Real summary line."
    assert by_id["unterminated"] == ""


async def test_local_source_reports_undecodable_file_as_a_miss(tmp_path):
    """Bytes that are not UTF-8 are a miss with a log line, not an unhandled error.

    Regression: ``read_text`` raised ``UnicodeDecodeError``, which only
    ``OSError`` was caught for, so one GBK file turned ``fetch_content`` into a
    ``ContentSourceError`` for the whole ref.
    """
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "gbk.md").write_bytes("中文内容".encode("gbk"))

    source = LocalContentSource(roots=[tmp_path])
    assert await source.fetch(ContentRef(ContentKind.AGENT, "gbk")) is None

    register_source(source)
    assert await try_fetch_content(ContentRef(ContentKind.AGENT, "gbk")) is None
