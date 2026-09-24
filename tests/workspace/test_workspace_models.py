"""
Unit tests for Workspace Pydantic models
"""

import pytest
from flocks.workspace.models import WorkspaceNode, WorkspaceStats


class TestWorkspaceNode:
    def test_file_node_defaults(self):
        node = WorkspaceNode(name="file.txt", path="uploads/file.txt", type="file")
        assert node.name == "file.txt"
        assert node.path == "uploads/file.txt"
        assert node.type == "file"
        assert node.size is None
        assert node.modified_at is None
        assert node.is_text_file is False
        assert node.editable is False
        assert node.children is None

    def test_directory_node_with_children(self):
        child = WorkspaceNode(name="nested.md", path="uploads/nested.md", type="file", is_text_file=True)
        node = WorkspaceNode(
            name="uploads",
            path="uploads",
            type="directory",
            children=[child],
        )
        assert node.type == "directory"
        assert len(node.children) == 1
        assert node.children[0].name == "nested.md"

    def test_file_node_full_fields(self):
        node = WorkspaceNode(
            name="report.pdf",
            path="uploads/report.pdf",
            type="file",
            size=204800,
            modified_at=1741900000.0,
            is_text_file=False,
            editable=True,
        )
        assert node.size == 204800
        assert node.modified_at == pytest.approx(1741900000.0)
        assert node.is_text_file is False
        assert node.editable is True

    def test_invalid_type_raises(self):
        with pytest.raises(Exception):
            WorkspaceNode(name="x", path="x", type="symlink")  # type: ignore[arg-type]

    def test_serialization_roundtrip(self):
        node = WorkspaceNode(
            name="data.csv",
            path="knowledge/data.csv",
            type="file",
            size=512,
            is_text_file=True,
        )
        data = node.model_dump()
        restored = WorkspaceNode(**data)
        assert restored == node


class TestWorkspaceStats:
    def test_defaults(self):
        s = WorkspaceStats(
            file_count=0,
            dir_count=0,
            total_size_bytes=0,
            memory_file_count=0,
            memory_total_size_bytes=0,
        )
        assert s.file_count == 0
        assert s.total_size_bytes == 0

    def test_non_zero_values(self):
        s = WorkspaceStats(
            file_count=42,
            dir_count=5,
            total_size_bytes=1_048_576,
            memory_file_count=3,
            memory_total_size_bytes=8192,
        )
        assert s.file_count == 42
        assert s.dir_count == 5
        assert s.total_size_bytes == 1_048_576
        assert s.memory_file_count == 3
        assert s.memory_total_size_bytes == 8192

    def test_serialization(self):
        s = WorkspaceStats(
            file_count=10,
            dir_count=2,
            total_size_bytes=2048,
            memory_file_count=1,
            memory_total_size_bytes=1024,
        )
        data = s.model_dump()
        assert data["file_count"] == 10
        assert data["total_size_bytes"] == 2048
