"""
Tests for Todo management

Validates todo CRUD operations and event publishing.
"""

import pytest
from flocks.session.features.todo import Todo, TodoInfo, TodoStatus, TodoPriority
from flocks.bus.bus import Bus
from flocks.storage.storage import Storage


@pytest.fixture(autouse=True)
async def cleanup():
    """Clear storage and subscriptions before/after each test"""
    from flocks.storage.storage import Storage
    
    Bus.clear_subscriptions()
    
    # Clear storage before test
    await Storage.init()
    keys = await Storage.list_keys(prefix="todo:")
    for key in keys:
        await Storage.delete(key)
    
    yield
    
    # Clear after test
    keys = await Storage.list_keys(prefix="todo:")
    for key in keys:
        await Storage.delete(key)
    Bus.clear_subscriptions()


@pytest.mark.asyncio
async def test_create_todo():
    """Test creating todo info"""
    todo = TodoInfo(
        id="1",
        content="Implement feature X",
        activeForm="Implementing feature X",
        status="in_progress",
        priority="high"
    )
    
    assert todo.id == "1"
    assert todo.content == "Implement feature X"
    assert todo.activeForm == "Implementing feature X"
    assert todo.status == "in_progress"
    assert todo.priority == "high"


@pytest.mark.asyncio
async def test_todo_defaults():
    """Test todo default values"""
    todo = TodoInfo(id="2", content="Test task")
    
    assert todo.status == "pending"
    assert todo.priority == "medium"


@pytest.mark.asyncio
async def test_update_todos():
    """Test updating session todos"""
    session_id = "test_session"
    
    todos = [
        TodoInfo(id="1", content="Task 1", status="pending"),
        TodoInfo(id="2", content="Task 2", activeForm="Working on task 2", status="in_progress"),
    ]
    
    # Update
    await Todo.update(session_id, todos)
    
    # Verify stored
    retrieved = await Todo.get(session_id)
    assert len(retrieved) == 2
    assert retrieved[0].id == "1"
    assert retrieved[1].id == "2"
    assert retrieved[1].activeForm == "Working on task 2"


@pytest.mark.asyncio
async def test_get_empty_todos():
    """Test getting todos when none exist"""
    todos = await Todo.get("nonexistent_session")
    assert todos == []


@pytest.mark.asyncio
async def test_add_todo():
    """Test adding a single todo"""
    session_id = "test_add"
    
    # Add first todo
    todo1 = TodoInfo(id="1", content="First task")
    await Todo.add(session_id, todo1)
    
    todos = await Todo.get(session_id)
    assert len(todos) == 1
    assert todos[0].id == "1"
    
    # Add second todo
    todo2 = TodoInfo(id="2", content="Second task")
    await Todo.add(session_id, todo2)
    
    todos = await Todo.get(session_id)
    assert len(todos) == 2


@pytest.mark.asyncio
async def test_remove_todo():
    """Test removing a todo"""
    session_id = "test_remove"
    
    # Add todos
    todos = [
        TodoInfo(id="1", content="Task 1"),
        TodoInfo(id="2", content="Task 2"),
        TodoInfo(id="3", content="Task 3"),
    ]
    await Todo.update(session_id, todos)
    
    # Remove one
    remaining = await Todo.remove(session_id, "2")
    
    assert len(remaining) == 2
    assert all(t.id != "2" for t in remaining)


@pytest.mark.asyncio
async def test_update_status():
    """Test updating todo status"""
    session_id = "test_status"
    
    todos = [
        TodoInfo(id="1", content="Task 1", status="pending"),
    ]
    await Todo.update(session_id, todos)
    
    # Update status
    updated = await Todo.update_status(session_id, "1", "completed")
    
    assert updated is not None
    assert updated.status == "completed"
    
    # Verify persisted
    todos = await Todo.get(session_id)
    assert todos[0].status == "completed"


@pytest.mark.asyncio
async def test_update_status_not_found():
    """Test updating status of non-existent todo"""
    session_id = "test_notfound"
    
    result = await Todo.update_status(session_id, "nonexistent", "completed")
    assert result is None


@pytest.mark.asyncio
async def test_clear_todos():
    """Test clearing all todos"""
    session_id = "test_clear"
    
    # Add todos
    todos = [
        TodoInfo(id="1", content="Task 1"),
        TodoInfo(id="2", content="Task 2"),
    ]
    await Todo.update(session_id, todos)
    
    # Clear
    await Todo.clear(session_id)
    
    # Should be empty
    todos = await Todo.get(session_id)
    assert len(todos) == 0


@pytest.mark.asyncio
async def test_get_by_status():
    """Test filtering todos by status"""
    session_id = "test_filter_status"
    
    todos = [
        TodoInfo(id="1", content="Task 1", status="pending"),
        TodoInfo(id="2", content="Task 2", status="in_progress"),
        TodoInfo(id="3", content="Task 3", status="completed"),
        TodoInfo(id="4", content="Task 4", status="pending"),
    ]
    await Todo.update(session_id, todos)
    
    # Filter by pending
    pending = await Todo.get_by_status(session_id, "pending")
    assert len(pending) == 2
    assert all(t.status == "pending" for t in pending)
    
    # Filter by completed
    completed = await Todo.get_by_status(session_id, "completed")
    assert len(completed) == 1


@pytest.mark.asyncio
async def test_get_by_priority():
    """Test filtering todos by priority"""
    session_id = "test_filter_priority"
    
    todos = [
        TodoInfo(id="1", content="Task 1", priority="high"),
        TodoInfo(id="2", content="Task 2", priority="medium"),
        TodoInfo(id="3", content="Task 3", priority="high"),
        TodoInfo(id="4", content="Task 4", priority="low"),
    ]
    await Todo.update(session_id, todos)
    
    # Filter by high
    high = await Todo.get_by_priority(session_id, "high")
    assert len(high) == 2
    assert all(t.priority == "high" for t in high)


@pytest.mark.asyncio
async def test_todo_event_published():
    """Test that update publishes event"""
    session_id = "test_event"
    
    received = []
    
    def handler(event):
        received.append(event)
    
    # Subscribe to todo.updated event
    Bus.subscribe(Todo.Updated, handler)
    
    # Update todos
    todos = [TodoInfo(id="1", content="Test")]
    await Todo.update(session_id, todos)
    
    # Should receive event
    assert len(received) == 1
    assert received[0]["type"] == "todo.updated"
    assert received[0]["properties"]["sessionID"] == session_id
    assert len(received[0]["properties"]["todos"]) == 1
