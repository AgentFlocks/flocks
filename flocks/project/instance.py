"""
Project Instance management module

Handles project instance lifecycle, state management, and context
"""

import asyncio
from typing import Optional, Dict, Any, TypeVar, Generic, Callable, Awaitable
from pathlib import Path
from contextvars import ContextVar

from flocks.project.project import Project, ProjectInfo
from flocks.utils.log import Log

log = Log.create(service="project.instance")


# Type for state values
T = TypeVar('T')


class InstanceContext:
    """Context for a project instance"""
    
    def __init__(self, directory: str, worktree: str, project: ProjectInfo):
        self.directory = directory
        self.worktree = worktree
        self.project = project


# Context variable for current instance
_current_instance: ContextVar[Optional[InstanceContext]] = ContextVar('current_instance', default=None)


class StateManager:
    """
    Manages instance-scoped state with lazy initialization and cleanup
    
    States are keyed by directory and lazily initialized on first access.
    When an instance is disposed, all associated states are cleaned up.
    """
    
    def __init__(self):
        self._states: Dict[str, Dict[int, Any]] = {}  # directory -> {state_id -> state}
        self._disposers: Dict[str, Dict[int, Callable[[Any], Awaitable[None]]]] = {}
        self._next_id = 0
        self._lock = asyncio.Lock()
    
    def create(
        self,
        get_key: Callable[[], str],
        init: Callable[[], T],
        dispose: Optional[Callable[[T], Awaitable[None]]] = None
    ) -> Callable[[], T]:
        """
        Create a new state accessor
        
        Args:
            get_key: Function to get the state key (usually Instance.directory)
            init: State initializer function
            dispose: Optional cleanup function
            
        Returns:
            Function that returns the state value.
            The returned function also has an ``invalidate()`` method that
            removes the cached state so the next access triggers re-init.
        """
        state_id = self._next_id
        self._next_id += 1
        
        def accessor() -> T:
            key = get_key()
            
            if key not in self._states:
                self._states[key] = {}
                self._disposers[key] = {}
            
            if state_id not in self._states[key]:
                # Initialize state
                value = init()
                self._states[key][state_id] = value
                
                if dispose:
                    self._disposers[key][state_id] = dispose
            
            return self._states[key][state_id]

        def invalidate() -> None:
            """Remove cached state so the next access triggers re-init."""
            key = get_key()
            if key in self._states:
                self._states[key].pop(state_id, None)
            if key in self._disposers:
                self._disposers[key].pop(state_id, None)

        accessor.invalidate = invalidate  # type: ignore[attr-defined]
        return accessor
    
    async def dispose(self, key: str) -> None:
        """
        Dispose all states for a given key
        
        Args:
            key: State key (usually directory path)
        """
        async with self._lock:
            if key not in self._states:
                return
            
            # Call disposers
            if key in self._disposers:
                for state_id, disposer in self._disposers[key].items():
                    state = self._states[key].get(state_id)
                    if state is not None:
                        try:
                            result = disposer(state)
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            log.error("state.dispose.error", {
                                "key": key,
                                "state_id": state_id,
                                "error": str(e)
                            })
                
                del self._disposers[key]
            
            del self._states[key]
            log.info("state.disposed", {"key": key})


# Global state manager
_state_manager = StateManager()


class Instance:
    """
    Project Instance namespace
    
    Manages project instances and provides context for the current working project.
    Each directory can have one active instance at a time.
    """
    
    # Cache of created instances
    _cache: Dict[str, asyncio.Task[InstanceContext]] = {}
    _lock = asyncio.Lock()
    
    @classmethod
    async def provide(
        cls,
        directory: str,
        init: Optional[Callable[[], Awaitable[Any]]] = None,
        fn: Optional[Callable[[], T]] = None
    ) -> T:
        """
        Provide an instance context for a directory and execute a function within it
        
        Args:
            directory: Directory path
            init: Optional initialization function
            fn: Function to execute within the context
            
        Returns:
            Result of fn()
        """
        async with cls._lock:
            if directory not in cls._cache:
                log.info("creating_instance", {"directory": directory})
                
                async def create_context():
                    result = await Project.from_directory(directory)
                    project = result["project"]
                    sandbox = result["sandbox"]
                    
                    ctx = InstanceContext(
                        directory=directory,
                        worktree=sandbox,
                        project=project
                    )
                    
                    # Run init within context
                    if init:
                        token = _current_instance.set(ctx)
                        try:
                            await init()
                        finally:
                            _current_instance.reset(token)
                    
                    return ctx
                
                cls._cache[directory] = asyncio.create_task(create_context())
        
        # Wait for context to be ready.  If initialization fails, drop the
        # failed task so a later request can retry after transient storage or
        # startup recovery completes.
        task = cls._cache[directory]
        try:
            ctx = await task
        except Exception:
            async with cls._lock:
                if cls._cache.get(directory) is task:
                    cls._cache.pop(directory, None)
            raise
        
        # Execute fn within context
        if fn:
            token = _current_instance.set(ctx)
            try:
                result = fn()
                if asyncio.iscoroutine(result):
                    return await result
                return result
            finally:
                _current_instance.reset(token)
        
        return None  # type: ignore
    
    @classmethod
    @property
    def directory(cls) -> str:
        """Get the current instance directory"""
        ctx = _current_instance.get()
        if not ctx:
            raise RuntimeError("No instance context available")
        return ctx.directory
    
    @classmethod
    @property
    def worktree(cls) -> str:
        """Get the current instance worktree"""
        ctx = _current_instance.get()
        if not ctx:
            raise RuntimeError("No instance context available")
        return ctx.worktree
    
    @classmethod
    @property
    def project(cls) -> ProjectInfo:
        """Get the current instance project"""
        ctx = _current_instance.get()
        if not ctx:
            raise RuntimeError("No instance context available")
        return ctx.project
    
    @classmethod
    def get_directory(cls) -> Optional[str]:
        """Get current directory (returns None if no context)"""
        ctx = _current_instance.get()
        return ctx.directory if ctx else None
    
    @classmethod
    def get_worktree(cls) -> Optional[str]:
        """Get current worktree (returns None if no context)"""
        ctx = _current_instance.get()
        return ctx.worktree if ctx else None
    
    @classmethod
    def get_project(cls) -> Optional[ProjectInfo]:
        """Get current project (returns None if no context)"""
        ctx = _current_instance.get()
        return ctx.project if ctx else None
    
    @classmethod
    def contains_path(cls, filepath: str) -> bool:
        """
        Check if a path is within the project boundary
        
        Returns True if path is inside Instance.directory OR Instance.worktree.
        Paths within the worktree but outside the working directory should not
        trigger external_directory permission.
        
        Args:
            filepath: Path to check
            
        Returns:
            True if path is within project boundary
        """
        ctx = _current_instance.get()
        if not ctx:
            return False
        
        # Normalize paths
        filepath = str(Path(filepath).resolve())
        directory = str(Path(ctx.directory).resolve())
        worktree = str(Path(ctx.worktree).resolve())
        
        # Check if within directory
        if filepath.startswith(directory + '/') or filepath == directory:
            return True
        
        # Non-git projects set worktree to "/" which would match ANY absolute path
        # Skip worktree check in this case to preserve external_directory permissions
        if worktree == "/":
            return False
        
        # Check if within worktree
        return filepath.startswith(worktree + '/') or filepath == worktree
    
    @classmethod
    def state(
        cls,
        init: Callable[[], T],
        dispose: Optional[Callable[[T], Awaitable[None]]] = None
    ) -> Callable[[], T]:
        """
        Create an instance-scoped state
        
        States are lazily initialized on first access and automatically
        cleaned up when the instance is disposed.
        
        Args:
            init: State initializer function
            dispose: Optional cleanup function
            
        Returns:
            Function that returns the state value
        """
        return _state_manager.create(
            lambda: cls.get_directory() or "default",
            init,
            dispose
        )
    
    @classmethod
    def get_any_cached_context(cls) -> Optional[InstanceContext]:
        """Return the first resolved InstanceContext from the cache, or None.

        Useful when no request-scoped context is available (e.g. background
        tasks) and the caller just needs *some* valid project context.
        """
        for task_future in cls._cache.values():
            if task_future.done():
                try:
                    return task_future.result()
                except Exception:
                    continue
        return None

    @classmethod
    async def dispose(cls) -> None:
        """Dispose the current instance"""
        ctx = _current_instance.get()
        if not ctx:
            return
        
        log.info("disposing_instance", {"directory": ctx.directory})
        
        # Dispose states
        await _state_manager.dispose(ctx.directory)
        
        # Remove from cache
        async with cls._lock:
            cls._cache.pop(ctx.directory, None)
    
    @classmethod
    async def dispose_all(cls) -> None:
        """Dispose all instances"""
        log.info("disposing_all_instances")
        
        async with cls._lock:
            for directory, task in list(cls._cache.items()):
                try:
                    ctx = await task
                    token = _current_instance.set(ctx)
                    try:
                        await cls.dispose()
                    finally:
                        _current_instance.reset(token)
                except Exception as e:
                    log.error("dispose_all.error", {
                        "directory": directory,
                        "error": str(e)
                    })
            
            cls._cache.clear()


# Helper functions for getting instance attributes without raising

def get_current_directory() -> Optional[str]:
    """Get current instance directory or None"""
    return Instance.get_directory()


def get_current_worktree() -> Optional[str]:
    """Get current instance worktree or None"""
    return Instance.get_worktree()


def get_current_project() -> Optional[ProjectInfo]:
    """Get current instance project or None"""
    return Instance.get_project()
