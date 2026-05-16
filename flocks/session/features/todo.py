"""
Todo management

Manages todo items for sessions.
Based on Flocks' ported src/session/todo.ts
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field
from flocks.bus.bus_event import BusEvent
from flocks.bus.bus import Bus
from flocks.storage.storage import Storage
from flocks.utils.log import Log


log = Log.create(service="session.todo")


# Todo status types
TodoStatus = Literal["pending", "in_progress", "completed", "cancelled"]

# Todo priority types
TodoPriority = Literal["high", "medium", "low"]


class TodoInfo(BaseModel):
    """
    Todo item information
    
    Matches TypeScript Todo.Info from todo.ts
    """
    id: str = Field(..., description="Unique identifier for the todo item")
    content: str = Field(..., description="Brief description of the task")
    activeForm: Optional[str] = Field(
        None,
        description="Optional active/progressive form used while the task is in progress",
    )
    status: TodoStatus = Field(
        "pending",
        description="Current status of the task: pending, in_progress, completed, cancelled"
    )
    priority: TodoPriority = Field(
        "medium",
        description="Priority level of the task: high, medium, low"
    )


class TodoUpdateEventProps(BaseModel):
    """Properties for todo.updated event"""
    session_id: str = Field(..., alias="sessionID")
    todos: List[TodoInfo]


class Todo:
    """
    Todo management namespace
    
    Mirrors original Flocks Todo namespace from todo.ts
    
    Example:
        >>> # Create todos
        >>> todos = [
        ...     TodoInfo(id="1", content="Implement feature X", status="in_progress"),
        ...     TodoInfo(id="2", content="Write tests", status="pending"),
        ... ]
        >>> 
        >>> # Update session todos
        >>> await Todo.update(session_id="abc123", todos=todos)
        >>> 
        >>> # Get session todos
        >>> todos = await Todo.get(session_id="abc123")
    """
    
    # Define event
    Updated = BusEvent.define("todo.updated", TodoUpdateEventProps)
    
    @classmethod
    async def update(cls, session_id: str, todos: List[TodoInfo]) -> None:
        """
        Update todos for a session
        
        Matches TypeScript Todo.update()
        
        Args:
            session_id: Session ID
            todos: List of todo items
        """
        # Validate todos
        validated_todos = [
            todo if isinstance(todo, TodoInfo) else TodoInfo(**todo)
            for todo in todos
        ]
        
        # Store in storage
        await Storage.set(
            f"todo:{session_id}",
            [todo.model_dump(exclude_none=True) for todo in validated_todos],
            "todo"
        )
        
        # Publish event
        await Bus.publish(cls.Updated, {
            "sessionID": session_id,
            "todos": validated_todos,
        })
        
        log.info("todo.updated", {
            "session_id": session_id,
            "count": len(validated_todos),
        })
    
    @classmethod
    async def get(cls, session_id: str) -> List[TodoInfo]:
        """
        Get todos for a session
        
        Matches TypeScript Todo.get()
        
        Args:
            session_id: Session ID
            
        Returns:
            List of todo items (empty list if none)
        """
        try:
            # Storage.get returns raw dict/list data, not a Pydantic model
            data = await Storage.get(f"todo:{session_id}")
            if not data:
                return []
            
            # Convert dict items to TodoInfo models
            if isinstance(data, list):
                todos = [TodoInfo(**item) if isinstance(item, dict) else item for item in data]
                return todos
            
            return []
        except Exception as e:
            log.warn("todo.get.error", {
                "session_id": session_id,
                "error": str(e),
            })
            return []
    
    @classmethod
    async def add(cls, session_id: str, todo: TodoInfo) -> List[TodoInfo]:
        """
        Add a single todo to session
        
        Args:
            session_id: Session ID
            todo: Todo item to add
            
        Returns:
            Updated list of todos
        """
        todos = await cls.get(session_id)
        todos.append(todo)
        await cls.update(session_id, todos)
        return todos
    
    @classmethod
    async def remove(cls, session_id: str, todo_id: str) -> List[TodoInfo]:
        """
        Remove a todo from session
        
        Args:
            session_id: Session ID
            todo_id: Todo ID to remove
            
        Returns:
            Updated list of todos
        """
        todos = await cls.get(session_id)
        todos = [t for t in todos if t.id != todo_id]
        await cls.update(session_id, todos)
        return todos
    
    @classmethod
    async def update_status(
        cls,
        session_id: str,
        todo_id: str,
        status: TodoStatus,
    ) -> Optional[TodoInfo]:
        """
        Update status of a specific todo
        
        Args:
            session_id: Session ID
            todo_id: Todo ID
            status: New status
            
        Returns:
            Updated todo or None if not found
        """
        todos = await cls.get(session_id)
        
        for todo in todos:
            if todo.id == todo_id:
                todo.status = status
                await cls.update(session_id, todos)
                log.info("todo.status_updated", {
                    "session_id": session_id,
                    "todo_id": todo_id,
                    "status": status,
                })
                return todo
        
        log.warn("todo.not_found", {
            "session_id": session_id,
            "todo_id": todo_id,
        })
        return None
    
    @classmethod
    async def clear(cls, session_id: str) -> None:
        """
        Clear all todos for a session
        
        Args:
            session_id: Session ID
        """
        await cls.update(session_id, [])
        log.info("todo.cleared", {"session_id": session_id})
    
    @classmethod
    async def get_by_status(
        cls,
        session_id: str,
        status: TodoStatus,
    ) -> List[TodoInfo]:
        """
        Get todos filtered by status
        
        Args:
            session_id: Session ID
            status: Status to filter by
            
        Returns:
            Filtered list of todos
        """
        todos = await cls.get(session_id)
        return [t for t in todos if t.status == status]
    
    @classmethod
    async def get_by_priority(
        cls,
        session_id: str,
        priority: TodoPriority,
    ) -> List[TodoInfo]:
        """
        Get todos filtered by priority
        
        Args:
            session_id: Session ID
            priority: Priority to filter by
            
        Returns:
            Filtered list of todos
        """
        todos = await cls.get(session_id)
        return [t for t in todos if t.priority == priority]
