"""Validation and atomic persistence for editable workflow definitions.

File tools use the same checks as native saves. This module does not execute
node code, initialize storage, or evaluate credentials while validating JSON.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import ValidationError

_UNSET = object()


class WorkflowDefinitionError(ValueError):
    def __init__(self, filename: str, reason: str, *, line: int | None = None, column: int | None = None):
        self.filename = filename
        self.line = line
        self.column = column
        self.reason = reason
        location = f" (line {line}, column {column})" if line is not None else ""
        super().__init__(f"Invalid workflow definition: {filename}{location}: {reason}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": "WORKFLOW_DEFINITION_INVALID", "file": self.filename,
            "line": self.line, "column": self.column, "message": str(self),
        }


def is_definition_path(path: str | Path) -> bool:
    """Recognize native workflow roots, including the supported legacy roots."""
    path = Path(path)
    if path.name not in {"workflow.json", "meta.json"}:
        return False
    root = path.parent.parent
    return root.name in {"workflow", "workflows"} and (
        root.parent.name == ".flocks"
        or (root.parent.name == "plugins" and root.parent.parent.name == ".flocks")
    )


def parse_definition(text: str, filename: str, *, validate_graph: bool = False) -> dict[str, Any]:
    try:
        data = json.loads(text.removeprefix("\ufeff"))
    except json.JSONDecodeError as exc:
        raise WorkflowDefinitionError(filename, exc.msg, line=exc.lineno, column=exc.colno) from exc
    if not isinstance(data, dict):
        raise WorkflowDefinitionError(filename, "Expected a JSON object")
    if filename == "workflow.json":
        for field in ("name", "description", "category"):
            if field in data and data[field] is not None and not isinstance(data[field], str):
                raise WorkflowDefinitionError(filename, f"{field} must be a string or null")
        for field in ("nodes", "edges"):
            if field in data and (not isinstance(data[field], list) or any(not isinstance(item, dict) for item in data[field])):
                raise WorkflowDefinitionError(filename, f"{field} must be an array of objects")
    if filename == "meta.json":
        for field in ("name", "description", "category", "status"):
            if field in data and data[field] is not None and not isinstance(data[field], str):
                raise WorkflowDefinitionError(filename, f"{field} must be a string or null")
    if filename == "workflow.json" and validate_graph:
        from flocks.workflow.models import Workflow
        from flocks.workflow.errors import WorkflowValidationError
        from flocks.workflow.validation import validate_workflow_python

        # The native empty draft is a supported non-executable definition.
        empty_draft = data.get("start") == "" and data.get("nodes") == [] and data.get("edges") == []
        if not empty_draft:
            try:
                model = Workflow.model_validate(data)
                validate_workflow_python(model)
            except ValidationError as exc:
                errors = exc.errors(include_input=False, include_context=False, include_url=False)
                reason = "; ".join(
                    f"{'.'.join(map(str, item['loc'])) or 'workflow'}: {item['msg']}"
                    for item in errors[:5]
                )
                raise WorkflowDefinitionError(filename, reason) from exc
            except (ValueError, WorkflowValidationError) as exc:
                raise WorkflowDefinitionError(filename, str(exc)) from exc
    if filename == "meta.json":
        from flocks.workflow.fs_store import normalize_workflow_group

        if "group" in data:
            try:
                normalize_workflow_group(data["group"])
            except ValueError as exc:
                raise WorkflowDefinitionError(filename, str(exc)) from exc
    return data


def validate_file_write(path: str, text: str) -> None:
    if is_definition_path(path):
        parse_definition(text, Path(path).name, validate_graph=True)


def _atomic_write(path: str, text: str, *, before, newline: str | None = None) -> None:
    """Publish complete bytes through a no-follow parent directory.

    Recheck the original identity and contents after staging, so permission
    waits and competing edits cannot silently replace a newer definition.
    """
    from flocks.tool import nofollow

    holder, name = nofollow.parent(path, create=True)
    temporary = f".{name}.{secrets.token_hex(12)}.tmp"
    with holder:
        def check_original():
            try:
                fd = holder.open(name, os.O_RDONLY)
            except FileNotFoundError:
                if before is not None:
                    raise nofollow.StaleFile(path) from None
                return None
            with open(fd, "r", encoding="utf-8", errors="replace", newline=newline) as stream:
                status = os.fstat(stream.fileno())
                if before is None or nofollow.identity(status) != before.identity or stream.read() != before.text:
                    raise nofollow.StaleFile(path)
                return status

        previous = check_original()
        try:
            fd = holder.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with open(fd, "w", encoding="utf-8", newline=newline) as stream:
                if previous is not None:
                    if hasattr(os, "fchmod"):
                        os.fchmod(stream.fileno(), stat.S_IMODE(previous.st_mode))
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            check_original()
            if before is None:
                # A new definition must not overwrite an entry created meanwhile.
                nofollow._rename_noreplace(holder, temporary, holder, name)
            else:
                if holder.fd is None:
                    os.replace(holder._join(temporary), holder._join(name))
                else:
                    holder.rename(temporary, holder, name)
        finally:
            try:
                holder.unlink(temporary)
            except FileNotFoundError:
                pass


@contextmanager
def _definition_lock(path: str):
    """Serialize cooperating writers, using a lock opened without following links."""
    from flocks.tool import nofollow

    holder, name = nofollow.parent(path, create=True)
    with holder:
        lock_name = f".{name}.lock"
        flags = os.O_RDWR | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = holder.open(lock_name, flags | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            fd = holder.open(lock_name, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("Workflow lock must be a regular file")
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def write_definition(path: str | Path, text: str, *, before=_UNSET, create_only: bool = False,
                     newline: str | None = None, validate_graph: bool = False) -> None:
    """Validate before staging, and retain one readable previous version."""
    path = str(Path(path).absolute())
    parse_definition(text, Path(path).name, validate_graph=validate_graph)
    with _definition_lock(path):
        _write_definition(path, text, before=before, create_only=create_only,
                          newline=newline, validate_graph=validate_graph)


def _write_definition(path: str, text: str, *, before, create_only: bool,
                      newline: str | None, validate_graph: bool) -> None:
    from flocks.tool import nofollow

    if before is _UNSET:
        before = None if create_only else nofollow.read_text(path, newline=newline)
    observed = nofollow.read_text(path, newline=newline)
    if observed != before:
        raise nofollow.StaleFile(path)
    backup = path + ".last-valid"
    if before is not None:
        try:
            parse_definition(before.text, Path(path).name, validate_graph=True)
        except WorkflowDefinitionError:
            pass  # A repair must not replace the last good backup with broken text.
        else:
            old_backup = nofollow.read_text(backup, newline=newline)
            _atomic_write(backup, before.text, before=old_backup, newline=newline)
    else:
        # Even a freshly created definition can be restored after an external
        # editor or shell command corrupts it.
        old_backup = nofollow.read_text(backup, newline=newline)
        if old_backup is None:
            _atomic_write(backup, text, before=None, newline=newline)
    _atomic_write(path, text, before=before, newline=newline)
