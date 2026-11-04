"""
Local filesystem content source -- the default every Flocks install uses.

It serves the same directories the plugin loader scans, so content dropped into
``~/.flocks/plugins/agents`` or a project's ``.flocks/plugins/skills`` resolves
through the generic interface without any extra configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from flocks.content.models import ContentBody, ContentEntry, ContentKind, ContentRef
from flocks.utils.log import Log

log = Log.create(service="content.local")


@dataclass(frozen=True)
class _KindLayout:
    """Where one content kind lives on disk and how its files are named."""

    subdir: str
    patterns: Sequence[str]
    media_type: str = "text/markdown"
    # When set, the item id comes from the containing directory rather than the
    # file stem.  Skills use this: ``skills/report-helper/SKILL.md`` is the item
    # ``report-helper``, not ``SKILL``.
    id_from_parent: bool = False


_LAYOUTS: Dict[str, _KindLayout] = {
    ContentKind.AGENT: _KindLayout(subdir="agents", patterns=("*.md", "*.yaml", "*.yml")),
    ContentKind.SKILL: _KindLayout(
        subdir="skills",
        patterns=("*/SKILL.md", "*/*/SKILL.md"),
        id_from_parent=True,
    ),
    ContentKind.WORKFLOW: _KindLayout(
        subdir="workflows",
        patterns=("*.yaml", "*.yml", "*.json"),
        media_type="application/yaml",
    ),
    ContentKind.TOOL: _KindLayout(subdir="tools", patterns=("*.py",), media_type="text/x-python"),
    ContentKind.PROMPT: _KindLayout(subdir="prompts", patterns=("*.md", "*.txt"), media_type="text/plain"),
}

_MAX_SUMMARY_CHARS = 200


def _is_safe_content_id(content_id: str) -> bool:
    """Whether an id may be turned into a path under a root.

    An id arrives from a caller -- a model-chosen skill name, a request
    parameter -- and is substituted into a path.  ``../../etc/passwd`` has to be
    refused here rather than resolved, and a leading dot would reach hidden
    files that are not content.  A registered source is expected to apply the
    same check to the ids it is handed.
    """

    if not content_id or len(content_id) > 200:
        return False
    if content_id in {".", ".."} or content_id.startswith("."):
        return False
    if any(sep in content_id for sep in ("/", "\\")):
        return False
    return "\x00" not in content_id


def _contained(candidate: Path, resolved_base: Path) -> bool:
    """Whether *candidate* really sits under *resolved_base* once links resolve.

    Rejecting ``..`` in the id is not enough on its own: a symlink inside a
    plugin directory points wherever it likes, and the content body is read
    into a model's context.

    Takes the base already resolved, because a listing checks every file it
    finds and resolving the same directory once per file is a syscall per
    entry for an answer that cannot change during the walk.
    """

    try:
        return candidate.resolve().is_relative_to(resolved_base)
    except (OSError, ValueError):
        return False


def default_roots() -> List[Path]:
    """The plugin roots a stock install reads, global first then project."""

    from flocks.plugin.loader import DEFAULT_PLUGIN_ROOT

    roots = [DEFAULT_PLUGIN_ROOT]
    project_root = Path.cwd() / ".flocks" / "plugins"
    if project_root != DEFAULT_PLUGIN_ROOT:
        roots.append(project_root)
    return roots


def _summarize(text: str) -> str:
    """First non-empty, non-heading line, clipped -- a listing-sized blurb."""

    lines = text.splitlines()
    # A leading YAML front matter block is metadata, not prose: skip the whole
    # block, not just its ``---`` fences, or ``title: x`` becomes the summary.
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                lines = lines[index + 1:]
                break
        else:
            # The block does not close inside the header slice we read.
            return ""

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("---"):
            continue
        return line[:_MAX_SUMMARY_CHARS]
    return ""


class LocalContentSource:
    """Reads content from one or more plugin roots on the local filesystem."""

    def __init__(
        self,
        roots: Optional[Iterable[Path]] = None,
        *,
        name: str = "local",
    ) -> None:
        self._roots = [Path(root) for root in roots] if roots is not None else None
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def roots(self) -> List[Path]:
        # Resolved lazily so a source constructed at import time still sees the
        # working directory the process actually ends up running in.
        return self._roots if self._roots is not None else default_roots()

    def supports(self, kind: str) -> bool:
        return kind in _LAYOUTS

    # -- listing ----------------------------------------------------------

    def _iter_files(self, kind: str):
        layout = _LAYOUTS[kind]
        for root in self.roots:
            base = root / layout.subdir
            if not base.is_dir():
                continue
            try:
                resolved_base = base.resolve()
            except OSError:
                continue
            for pattern in layout.patterns:
                for path in sorted(base.glob(pattern)):
                    if not path.is_file():
                        continue
                    # A glob follows symlinks, so a link planted in a plugin
                    # directory would list, and serve, a file from anywhere on
                    # the host. Fetch falls back to this scan, so the check has
                    # to live here as well as on the direct path.
                    if not _contained(path, resolved_base):
                        log.warn(
                            "content.local.escapes_root",
                            {"kind": kind, "path": str(path)},
                        )
                        continue
                    yield layout, path

    def _content_id(self, layout: _KindLayout, path: Path) -> str:
        return path.parent.name if layout.id_from_parent else path.stem

    async def list_entries(self, kind: str) -> Sequence[ContentEntry]:
        if not self.supports(kind):
            return []

        entries: List[ContentEntry] = []
        seen: set[str] = set()
        for layout, path in self._iter_files(kind):
            content_id = self._content_id(layout, path)
            if content_id in seen:
                continue
            seen.add(content_id)

            summary = ""
            try:
                stat = path.stat()
                # Listing stays cheap: read a header slice, never the whole body.
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    summary = _summarize(handle.read(2048))
            except OSError as exc:
                log.warn("content.local.stat_failed", {"path": str(path), "error": str(exc)})
                continue

            entries.append(
                ContentEntry(
                    ref=ContentRef(kind=kind, content_id=content_id, version=str(int(stat.st_mtime))),
                    name=content_id,
                    summary=summary,
                    source=self._name,
                    metadata={"path": str(path)},
                )
            )
        return entries

    # -- fetching ---------------------------------------------------------

    def _candidate_paths(self, kind: str, content_id: str):
        """Paths this id could live at, without walking the whole directory.

        Resolving one item by scanning every file in every root costs the same
        as a full listing, and body fetches happen far more often than
        listings. The id maps onto a small set of concrete paths, so those are
        tried directly.
        """
        layout = _LAYOUTS[kind]
        if not _is_safe_content_id(content_id):
            log.warn("content.local.unsafe_id", {"kind": kind, "content_id": content_id})
            return
        for root in self.roots:
            base = root / layout.subdir
            if not base.is_dir():
                continue
            for pattern in layout.patterns:
                # Substitute the id for the wildcard segment the id comes from.
                if layout.id_from_parent:
                    candidate = base / pattern.replace("*", content_id, 1)
                else:
                    suffix = Path(pattern).suffix
                    candidate = base / f"{content_id}{suffix}"
                if not candidate.is_file():
                    continue
                # Second gate, against a symlink rather than the id: the id was
                # already checked, but what it lands on can still point away.
                try:
                    resolved_base = base.resolve()
                except OSError:
                    continue
                if not _contained(candidate, resolved_base):
                    log.warn(
                        "content.local.escapes_root",
                        {"kind": kind, "content_id": content_id, "path": str(candidate)},
                    )
                    continue
                yield layout, candidate

    async def fetch(self, ref: ContentRef) -> Optional[ContentBody]:
        if not self.supports(ref.kind):
            return None
        if not _is_safe_content_id(ref.content_id):
            log.warn(
                "content.local.unsafe_id",
                {"kind": ref.kind, "content_id": ref.content_id},
            )
            return None

        candidates = list(self._candidate_paths(ref.kind, ref.content_id))
        if not candidates:
            # Nested layouts (a skill two directories deep, say) will not match
            # a direct path, so fall back to the scan for those.
            candidates = [
                (layout, path)
                for layout, path in self._iter_files(ref.kind)
                if self._content_id(layout, path) == ref.content_id
            ]

        for layout, path in candidates:
            try:
                stat = path.stat()
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                log.warn("content.local.read_failed", {"path": str(path), "error": str(exc)})
                return None
            except UnicodeDecodeError as exc:
                # Content is served as text into a model's context; bytes this
                # source cannot decode are reported as a miss rather than as
                # an error from a source that claimed the ref, and never as
                # replacement characters posing as the real body.
                log.warn("content.local.decode_failed", {"path": str(path), "error": str(exc)})
                return None

            version = str(int(stat.st_mtime))
            if ref.version is not None and ref.version != version:
                # A pinned version this source can no longer produce: report a
                # miss rather than quietly serving different bytes than asked.
                log.warn(
                    "content.local.version_mismatch",
                    {"path": str(path), "requested": ref.version, "available": version},
                )
                return None

            return ContentBody(
                ref=ref.pinned(version),
                text=text,
                media_type=layout.media_type,
                source=self._name,
                metadata={"path": str(path)},
            )
        return None


__all__ = ["LocalContentSource", "default_roots"]
