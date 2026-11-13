"""A child session never keeps Memory its parent does not."""

import pytest

from flocks.session.session import Session


@pytest.mark.asyncio
async def test_child_of_session_without_memory_has_none(tmp_path):
    parent = await Session.create(project_id="p", directory=str(tmp_path), agent="rex", memory_enabled=False)

    delegate = await Session.create(project_id="p", directory=str(tmp_path), agent="explore", parent_id=parent.id)
    # Asking for Memory does not override the parent's choice either.
    asked = await Session.create(
        project_id="p", directory=str(tmp_path), agent="explore", parent_id=parent.id, memory_enabled=True,
    )
    grandchild = await Session.create(project_id="p", directory=str(tmp_path), agent="explore", parent_id=delegate.id)

    assert parent.memory_enabled is False
    assert delegate.memory_enabled is False
    assert asked.memory_enabled is False
    assert grandchild.memory_enabled is False
    stored = await Session.get_by_id(delegate.id)
    assert stored is not None and stored.memory_enabled is False


@pytest.mark.asyncio
async def test_child_of_session_with_memory_keeps_its_own_default(tmp_path):
    parent = await Session.create(project_id="p", directory=str(tmp_path), agent="rex")
    child = await Session.create(project_id="p", directory=str(tmp_path), agent="explore", parent_id=parent.id)
    opted_out = await Session.create(
        project_id="p", directory=str(tmp_path), agent="explore", parent_id=parent.id, memory_enabled=False,
    )

    assert parent.memory_enabled is True
    assert child.memory_enabled is True
    assert opted_out.memory_enabled is False
