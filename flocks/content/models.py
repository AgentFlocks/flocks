"""
Content objects exchanged over the generic content-source interface.

The interface deliberately carries only *resolved* content: an entry lists what
exists, a body carries the bytes a caller may use.  How a source obtained that
body -- reading a file, calling a service, unpacking a delivered package -- is
entirely the source's business.  Callers never see transport, container format
or credential details, so a source can change its storage without touching any
caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class ContentKind:
    """Well-known content kinds.

    Plain strings, not an enum: a source may serve kinds this build does not
    know about, and an unknown kind must stay addressable rather than raise.
    """

    AGENT = "agent"
    SKILL = "skill"
    WORKFLOW = "workflow"
    TOOL = "tool"
    PROMPT = "prompt"


@dataclass(frozen=True)
class ContentRef:
    """Addresses one content item.

    ``version`` is optional on request and populated on results.  Pinning it
    lets a single task keep using one version even when a source publishes a
    newer one mid-run.
    """

    kind: str
    content_id: str
    version: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("content kind 不能为空")
        if not self.content_id:
            raise ValueError("content_id 不能为空")

    @property
    def key(self) -> str:
        return f"{self.kind}/{self.content_id}"

    def pinned(self, version: Optional[str]) -> "ContentRef":
        return ContentRef(kind=self.kind, content_id=self.content_id, version=version)


@dataclass(frozen=True)
class ContentEntry:
    """Directory-level description of one item: enough to list and choose it.

    Listing must stay cheap, so this carries no body.  A Skill picker, for
    example, shows ``name``/``summary`` for every installed Skill and only
    fetches the body once the user selects one.
    """

    ref: ContentRef
    name: str
    summary: str = ""
    source: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContentBody:
    """The resolved payload for one ref.

    ``text`` is the common case (Markdown skill body, YAML workflow, prompt).
    ``data`` carries bytes for sources that serve non-text payloads.  Exactly
    one of them is normally set; both being empty is a valid empty document.
    """

    ref: ContentRef
    text: Optional[str] = None
    data: Optional[bytes] = None
    media_type: str = "text/markdown"
    source: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_text(self, *, encoding: str = "utf-8") -> str:
        if self.text is not None:
            return self.text
        if self.data is not None:
            return self.data.decode(encoding)
        return ""


class ContentNotFound(LookupError):
    """No registered source could resolve the ref."""

    def __init__(self, ref: ContentRef) -> None:
        self.ref = ref
        super().__init__(f"内容不存在: {ref.key}")


class ContentSourceError(RuntimeError):
    """A source failed while resolving content it claimed to serve."""

    def __init__(self, source: str, ref: ContentRef, cause: BaseException | None = None) -> None:
        self.source = source
        self.ref = ref
        message = f"内容来源 {source} 处理 {ref.key} 失败"
        if cause is not None:
            message = f"{message}: {cause}"
        super().__init__(message)


__all__ = [
    "ContentBody",
    "ContentEntry",
    "ContentKind",
    "ContentNotFound",
    "ContentRef",
    "ContentSourceError",
]
