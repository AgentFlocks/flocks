"""Tests for curated USER.md and MEMORY.md entry operations."""

from unittest.mock import patch

import pytest

from flocks.memory.config import MemoryConfig
from flocks.memory.manager import MemoryManager


@pytest.fixture
def manager(tmp_path):
    return MemoryManager(
        project_id="test",
        workspace_dir=str(tmp_path),
        config=MemoryConfig(),
    )


@pytest.mark.asyncio
async def test_add_replace_remove_curated_entry(manager, tmp_path) -> None:
    with patch("flocks.config.Config.get_data_path", return_value=tmp_path):
        added = await manager.update_curated_memory(
            target="user",
            action="add",
            content="Prefers concise answers.",
        )
        duplicate = await manager.update_curated_memory(
            target="user",
            action="add",
            content="Prefers concise answers.",
        )
        replaced = await manager.update_curated_memory(
            target="user",
            action="replace",
            old_text="concise",
            content="Prefers answers with examples.",
        )
        removed = await manager.update_curated_memory(
            target="user",
            action="remove",
            old_text="with examples",
        )

    assert added["changed"] is True
    assert duplicate["changed"] is False
    assert "Prefers answers with examples." in replaced["content"]
    assert removed["content"] == ""


@pytest.mark.asyncio
async def test_replace_rejects_ambiguous_old_text(manager, tmp_path) -> None:
    memory_path = tmp_path / "memory" / "MEMORY.md"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text("Uses Python.\n\nUses Python tooling.\n", encoding="utf-8")

    with (
        patch("flocks.config.Config.get_data_path", return_value=tmp_path),
        pytest.raises(ValueError, match="matched multiple entries"),
    ):
        await manager.update_curated_memory(
            target="memory",
            action="replace",
            old_text="Uses Python",
            content="Uses uv-managed Python.",
        )


@pytest.mark.asyncio
async def test_replace_entry_under_markdown_heading(manager, tmp_path) -> None:
    memory_path = tmp_path / "memory" / "MEMORY.md"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(
        "# Long-Term Memory\n\n## Preferences\n- Uses Python.\n",
        encoding="utf-8",
    )

    with patch("flocks.config.Config.get_data_path", return_value=tmp_path):
        result = await manager.update_curated_memory(
            target="memory",
            action="replace",
            old_text="Uses Python",
            content="- Uses uv-managed Python.",
        )

    assert result["content"] == (
        "# Long-Term Memory\n\n## Preferences\n- Uses uv-managed Python.\n"
    )
