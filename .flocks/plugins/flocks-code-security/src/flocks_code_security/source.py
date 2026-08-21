"""Bounded source access that never escapes a stored snapshot."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import stat
from collections import Counter
from pathlib import Path
from typing import Any

from flocks_code_security.models import SessionBinding, SnapshotFile
from flocks_code_security.snapshot import TargetSnapshotService, normalize_relative_path
from flocks_code_security.store import ScanStore

SOURCE_ROLES = {"threat_modeler", "baseline", "investigator", "verifier", "prober"}


class AuditSourceRepository:
    def __init__(self, store: ScanStore):
        self.store = store

    def binding(self, session_id: str) -> SessionBinding:
        return self.store.require_binding(session_id, SOURCE_ROLES)

    def inventory(
        self,
        session_id: str,
        *,
        offset: int = 0,
        limit: int = 500,
    ) -> dict[str, Any]:
        binding = self.binding(session_id)
        assigned = self._assigned_paths(binding)
        files = [
            item
            for item in self.store.list_snapshot_files(binding.snapshot_id)
            if self._in_assigned_scope(item.relative_path, assigned)
        ]
        omissions = [
            item
            for item in self.store.list_snapshot_omissions(binding.snapshot_id)
            if self._in_assigned_scope(item.relative_path, assigned)
        ]
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 500))
        page = files[offset : offset + limit]
        self.store.record_source_accesses(
            binding,
            [
                {
                    "operation": "inventory",
                    "relative_path": item.relative_path,
                    "blob_digest": getattr(item, "blob_digest", None),
                }
                for item in [*page, *omissions]
            ],
        )
        languages = Counter(item.language for item in files)
        return {
            "snapshot_id": binding.snapshot_id,
            "file_count": len(files),
            "total_bytes": sum(item.size_bytes for item in files),
            "languages": dict(sorted(languages.items())),
            "offset": offset,
            "limit": limit,
            "next_offset": offset + len(page) if offset + len(page) < len(files) else None,
            "omitted_file_count": len(omissions),
            "omitted_files": [
                {
                    "path": item.relative_path,
                    "reason": item.reason,
                    "size_bytes": item.size_bytes,
                }
                for item in omissions[:500]
            ],
            "omissions_truncated": len(omissions) > 500,
            "files": [
                {
                    "path": item.relative_path,
                    "blob_digest": item.blob_digest,
                    "size_bytes": item.size_bytes,
                    "line_count": item.line_count,
                    "language": item.language,
                    "is_binary": item.is_binary,
                }
                for item in page
            ],
        }

    def validate_coverage_paths(
        self,
        binding: SessionBinding,
        paths: list[str],
        *,
        allow_omitted: bool,
    ) -> list[str]:
        if binding.work_unit_id is None:
            raise ValueError("Coverage requires a bound work unit")
        work_unit = self.store.get_work_unit(binding.work_unit_id)
        if work_unit is None or work_unit["scan_id"] != binding.scan_id:
            raise ValueError("Bound work unit not found")
        assigned = [normalize_relative_path(item, allow_root=True) for item in work_unit["paths"]]
        snapshot_paths = {item.relative_path for item in self.store.list_snapshot_files(binding.snapshot_id)}
        omitted_paths = {item.relative_path for item in self.store.list_snapshot_omissions(binding.snapshot_id)}

        def exists_in_snapshot(path: str) -> bool:
            if path == ".":
                return True
            candidates = snapshot_paths | (omitted_paths if allow_omitted else set())
            return path in candidates or any(item.startswith(f"{path}/") for item in candidates)

        validated: list[str] = []
        for raw_path in paths:
            path = normalize_relative_path(raw_path, allow_root=True)
            if not self._in_assigned_scope(path, assigned):
                raise ValueError(f"Coverage path is outside the work-unit scope: {path}")
            if not exists_in_snapshot(path):
                raise ValueError(f"Coverage path is not present in the snapshot: {path}")
            if path not in validated:
                validated.append(path)
        return validated

    def read(
        self,
        session_id: str,
        relative_path: str,
        *,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        binding = self.binding(session_id)
        normalized = normalize_relative_path(relative_path)
        if not self._in_assigned_scope(normalized, self._assigned_paths(binding)):
            raise ValueError("Path is outside the bound work-unit scope")
        record = self._record(binding.snapshot_id, normalized)
        if record.is_binary:
            raise ValueError("Binary snapshot files cannot be read as source text")
        if start_line < 1:
            raise ValueError("start_line must be at least 1")
        final_line = end_line if end_line is not None else start_line + 199
        if final_line < start_line or final_line - start_line + 1 > 400:
            raise ValueError("A single read may include at most 400 lines")
        data = self._verified_bytes(binding.snapshot_id, record)
        lines = data.decode("utf-8", errors="replace").splitlines()
        selected = lines[start_line - 1 : final_line]
        text = "\n".join(selected)
        actual_end = start_line + len(selected) - 1 if selected else start_line - 1
        self.store.record_source_access(
            binding,
            operation="read",
            relative_path=normalized,
            blob_digest=record.blob_digest,
            start_line=start_line,
            end_line=actual_end,
        )
        return {
            "snapshot_id": binding.snapshot_id,
            "relative_path": normalized,
            "blob_digest": record.blob_digest,
            "start_line": start_line,
            "end_line": actual_end,
            "excerpt_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "text": text,
        }

    def search(
        self,
        session_id: str,
        query: str,
        *,
        path_glob: str | None = None,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> dict[str, Any]:
        binding = self.binding(session_id)
        needle = str(query or "")
        if not needle or len(needle) > 500:
            raise ValueError("query must contain between 1 and 500 characters")
        limit = max(1, min(int(max_results or 100), 200))
        comparable_needle = needle if case_sensitive else needle.casefold()
        matches: list[dict[str, Any]] = []
        accesses: list[dict[str, Any]] = []
        truncated = False
        assigned = self._assigned_paths(binding)
        for record in self.store.list_snapshot_files(binding.snapshot_id):
            if not self._in_assigned_scope(record.relative_path, assigned):
                continue
            if record.is_binary:
                continue
            if path_glob and not fnmatch.fnmatch(record.relative_path, path_glob):
                continue
            data = self._verified_bytes(binding.snapshot_id, record)
            accesses.append(
                {
                    "operation": "search",
                    "relative_path": record.relative_path,
                    "blob_digest": record.blob_digest,
                    "start_line": 1,
                    "end_line": record.line_count,
                }
            )
            for line_number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
                comparable_line = line if case_sensitive else line.casefold()
                if comparable_needle not in comparable_line:
                    continue
                matches.append(
                    {
                        "relative_path": record.relative_path,
                        "line": line_number,
                        "blob_digest": record.blob_digest,
                        "text": line[:500],
                    }
                )
                if len(matches) >= limit:
                    truncated = True
                    break
            if truncated:
                break
        self.store.record_source_accesses(binding, accesses)
        return {
            "snapshot_id": binding.snapshot_id,
            "query": needle,
            "matches": matches,
            "truncated": truncated,
        }

    def validate_evidence(
        self,
        binding: SessionBinding,
        evidence: list[dict[str, Any]],
        *,
        allowed_extra_fields: frozenset[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        if not evidence:
            raise ValueError("At least one evidence reference is required")
        validated: list[dict[str, Any]] = []
        required_fields = (
            "relative_path",
            "blob_digest",
            "start_line",
            "end_line",
        )
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                raise ValueError("Each evidence reference must be an object")
            missing = [field for field in required_fields if field not in item or item[field] in (None, "")]
            unexpected = sorted(set(item) - set(required_fields) - allowed_extra_fields)
            field_errors: list[str] = []
            if missing:
                field_errors.append("missing " + ", ".join(missing))
            if unexpected:
                field_errors.append("unsupported " + ", ".join(unexpected))
            if field_errors:
                raise ValueError(
                    f"evidence[{index}] has invalid fields ({'; '.join(field_errors)}); "
                    "expected relative_path, "
                    "blob_digest, start_line, and end_line"
                )
            relative_path = normalize_relative_path(str(item.get("relative_path") or ""))
            record = self._record(binding.snapshot_id, relative_path)
            if str(item.get("blob_digest") or "") != record.blob_digest:
                raise ValueError(f"Evidence digest mismatch: {relative_path}")
            if isinstance(item["start_line"], bool) or not isinstance(item["start_line"], int):
                raise ValueError(f"evidence[{index}].start_line must be an integer")
            if isinstance(item["end_line"], bool) or not isinstance(item["end_line"], int):
                raise ValueError(f"evidence[{index}].end_line must be an integer")
            start_line = item["start_line"]
            end_line = item["end_line"]
            if start_line < 1 or end_line < start_line or end_line > record.line_count:
                raise ValueError(f"Invalid evidence line range: {relative_path}")
            excerpt = self.read(
                binding.session_id,
                relative_path,
                start_line=start_line,
                end_line=end_line,
            )
            if not excerpt["text"].strip():
                raise ValueError(f"Evidence excerpt is empty: {relative_path}")
            validated.append(
                {
                    "relative_path": relative_path,
                    "blob_digest": record.blob_digest,
                    "start_line": start_line,
                    "end_line": end_line,
                    "excerpt_hash": excerpt["excerpt_hash"],
                }
            )
        return validated

    def evidence_excerpt(
        self,
        snapshot_id: str,
        evidence: dict[str, Any],
        *,
        max_characters: int = 4_000,
    ) -> dict[str, Any]:
        """Read a persisted digest-bound evidence range for parent adjudication."""
        relative_path = normalize_relative_path(str(evidence.get("relative_path") or ""))
        record = self._record(snapshot_id, relative_path)
        if record.is_binary:
            raise ValueError("Binary snapshot files cannot be used as evidence text")
        if evidence.get("blob_digest") != record.blob_digest:
            raise ValueError(f"Evidence digest mismatch: {relative_path}")
        start_line = evidence.get("start_line")
        end_line = evidence.get("end_line")
        if (
            isinstance(start_line, bool)
            or not isinstance(start_line, int)
            or isinstance(end_line, bool)
            or not isinstance(end_line, int)
            or start_line < 1
            or end_line < start_line
            or end_line > record.line_count
        ):
            raise ValueError(f"Invalid evidence line range: {relative_path}")
        lines = (
            self._verified_bytes(snapshot_id, record)
            .decode(
                "utf-8",
                errors="replace",
            )
            .splitlines()
        )
        text = "\n".join(lines[start_line - 1 : end_line])
        excerpt_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        persisted_hash = evidence.get("excerpt_hash")
        if persisted_hash is not None and persisted_hash != excerpt_hash:
            raise ValueError(f"Evidence excerpt hash mismatch: {relative_path}")
        limit = max(1, min(int(max_characters), 20_000))
        return {
            "relative_path": relative_path,
            "blob_digest": record.blob_digest,
            "start_line": start_line,
            "end_line": end_line,
            "excerpt_hash": excerpt_hash,
            "text": text[:limit],
            "text_truncated": len(text) > limit,
        }

    def evidence_context(
        self,
        snapshot_id: str,
        evidence: dict[str, Any],
        *,
        context_lines: int = 8,
        max_bytes: int = 64 * 1024,
    ) -> dict[str, Any]:
        """Return bounded context only after verifying persisted evidence."""
        verified = self.evidence_excerpt(snapshot_id, evidence, max_characters=20_000)
        record = self._record(snapshot_id, verified["relative_path"])
        lines = (
            self._verified_bytes(snapshot_id, record)
            .decode(
                "utf-8",
                errors="replace",
            )
            .splitlines()
        )
        padding = max(0, min(int(context_lines), 50))
        start_line = max(1, int(verified["start_line"]) - padding)
        end_line = min(len(lines), int(verified["end_line"]) + padding)
        text = "\n".join(lines[start_line - 1 : end_line])
        encoded = text.encode("utf-8")
        limit = max(1, min(int(max_bytes), 64 * 1024))
        truncated = len(encoded) > limit
        if truncated:
            text = encoded[:limit].decode("utf-8", errors="replace")
        return {
            "relative_path": verified["relative_path"],
            "start_line": start_line,
            "end_line": end_line,
            "text": text,
            "text_truncated": truncated,
        }

    def _assigned_paths(self, binding: SessionBinding) -> list[str]:
        if binding.work_unit_id is None:
            raise ValueError("Source access requires a bound work unit")
        work_unit = self.store.get_work_unit(binding.work_unit_id)
        if work_unit is None or work_unit["scan_id"] != binding.scan_id:
            raise ValueError("Bound work unit not found")
        return [normalize_relative_path(item, allow_root=True) for item in work_unit["paths"]]

    @staticmethod
    def _in_assigned_scope(path: str, assigned: list[str]) -> bool:
        return any(scope == "." or path == scope or path.startswith(f"{scope}/") for scope in assigned)

    def _record(self, snapshot_id: str, relative_path: str) -> SnapshotFile:
        record = self.store.get_snapshot_file(snapshot_id, relative_path)
        if record is None:
            raise ValueError("Path is not present in the bound snapshot")
        return record

    def _verified_bytes(self, snapshot_id: str, record: SnapshotFile) -> bytes:
        snapshot = self.store.get_snapshot(snapshot_id)
        if snapshot is None:
            raise ValueError("Bound snapshot no longer exists")
        root_descriptor = TargetSnapshotService._open_directory(Path(snapshot.root_path))
        descriptor: int | None = None
        try:
            descriptor = TargetSnapshotService._open_snapshot_file(
                root_descriptor,
                record.relative_path,
            )
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError("Snapshot entry is no longer a regular file")
            if file_stat.st_size != record.size_bytes:
                raise ValueError("Snapshot content size mismatch")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(64 * 1024, record.size_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > record.size_bytes:
                    raise ValueError("Snapshot content size mismatch")
            data = b"".join(chunks)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(root_descriptor)
        if hashlib.sha256(data).hexdigest() != record.blob_digest:
            raise ValueError("Snapshot content digest mismatch")
        return data
