"""
Test Tool System - Comprehensive test suite for tools

Tests cover:
- Tool registration and discovery
- P0 Core tools (6): read, write, edit, bash, grep, glob
- P1 tools (4): webfetch, todo, question, websearch
- P2 tools: task, lsp, skill
- P3 tools (2): websearch, apply_patch
- Permission system integration
- Error handling
"""

import pytest
import asyncio
import json
import os
import tempfile
import shutil
import uuid
from pathlib import Path
from typing import Dict, Any, List

# Import the tool system
from flocks.tool import (
    ToolRegistry,
    Tool,
    ToolContext,
    ToolResult,
    ToolInfo,
    ToolSchema,
    ToolParameter,
    PermissionRequest,
    ToolCategory,
    ParameterType,
)
from flocks.tool.code import bash as bash_module
import flocks.tool.system.question as question_module


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="module")
def temp_dir():
    """Create a temporary directory for file operation tests"""
    temp_path = tempfile.mkdtemp(prefix="flocks_test_")
    yield temp_path
    # Cleanup after tests
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def tool_context():
    """Create a tool context for testing"""
    return ToolContext(
        session_id="test-session-001",
        message_id="test-message-001",
        agent="test",
    )


@pytest.fixture
def tool_context_with_permission():
    """Create a tool context with permission tracking"""
    permissions_requested: List[PermissionRequest] = []
    
    async def track_permission(request: PermissionRequest):
        permissions_requested.append(request)
    
    ctx = ToolContext(
        session_id="test-session-002",
        message_id="test-message-002",
        agent="test",
        permission_callback=track_permission,
    )
    ctx._permissions_requested = permissions_requested
    return ctx


@pytest.fixture
async def clean_todo_storage():
    """Clear persistent todo storage before and after todo tool tests."""
    from flocks.storage.storage import Storage

    await Storage.init()
    keys = await Storage.list_keys(prefix="todo:")
    for key in keys:
        await Storage.delete(key)

    yield

    keys = await Storage.list_keys(prefix="todo:")
    for key in keys:
        await Storage.delete(key)


@pytest.fixture
def test_files(temp_dir):
    """Create test files in temporary directory"""
    # Text file
    text_file = os.path.join(temp_dir, "test.txt")
    with open(text_file, 'w') as f:
        f.write("Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n")
    
    # Python file
    py_file = os.path.join(temp_dir, "test.py")
    with open(py_file, 'w') as f:
        f.write('def hello():\n    print("Hello, World!")\n\nhello()\n')
    
    # JSON file
    json_file = os.path.join(temp_dir, "config.json")
    with open(json_file, 'w') as f:
        f.write('{"name": "test", "version": "1.0.0"}\n')
    
    # Create subdirectory
    subdir = os.path.join(temp_dir, "subdir")
    os.makedirs(subdir, exist_ok=True)
    
    sub_file = os.path.join(subdir, "nested.txt")
    with open(sub_file, 'w') as f:
        f.write("Nested file content\n")
    
    return {
        "text_file": text_file,
        "py_file": py_file,
        "json_file": json_file,
        "subdir": subdir,
        "nested_file": sub_file,
    }


# =============================================================================
# Tool Registry Tests
# =============================================================================

class TestToolRegistry:
    """Test the ToolRegistry class"""
    
    def test_registry_initialization(self):
        """Test that registry initializes with built-in tools"""
        # Registry should be initialized when flocks.tool is imported
        tools = ToolRegistry.all_tool_ids()
        assert len(tools) >= 21, f"Expected at least 21 tools, got {len(tools)}: {tools}"
    
    def test_expected_tools_registered(self):
        """Test all expected tools are registered"""
        expected_tools = [
            # P0 Core tools (6)
            "read", "write", "edit", "bash", "grep", "glob",
            # P1 tools
            "webfetch", "todo", "question",
            # P2 tools
            "task", "lsp", "skill_load",
            # P3 tools (2)
            "websearch", "apply_patch",
        ]
        
        registered_tools = ToolRegistry.all_tool_ids()
        
        for tool_name in expected_tools:
            assert tool_name in registered_tools, f"Tool '{tool_name}' not registered"
    
    def test_get_tool_by_name(self):
        """Test getting a tool by name"""
        tool = ToolRegistry.get("read")
        assert tool is not None
        assert tool.info.name == "read"
        assert tool.info.category == ToolCategory.FILE
    
    def test_get_nonexistent_tool(self):
        """Test getting a nonexistent tool returns None"""
        tool = ToolRegistry.get("nonexistent_tool")
        assert tool is None
    
    def test_list_tools_by_category(self):
        """Test listing tools by category"""
        file_tools = ToolRegistry.list_tools(category=ToolCategory.FILE)
        file_tool_names = [t.name for t in file_tools]
        
        # At least read, write, edit should be in FILE category
        assert "read" in file_tool_names
        assert "write" in file_tool_names
        assert "edit" in file_tool_names
    
    def test_tool_schema_generation(self):
        """Test that tools generate valid schemas"""
        schema = ToolRegistry.get_schema("read")
        assert schema is not None
        assert "filePath" in schema.properties
        assert "filePath" in schema.required


# =============================================================================
# P0 Core Tools Tests
# =============================================================================

class TestReadTool:
    """Test the read tool"""
    
    @pytest.mark.asyncio
    async def test_read_text_file(self, tool_context, test_files):
        """Test reading a text file"""
        result = await ToolRegistry.execute(
            "read",
            ctx=tool_context,
            filePath=test_files["text_file"]
        )
        
        assert result.success
        assert "Line 1" in result.output
        assert "Line 5" in result.output
    
    @pytest.mark.asyncio
    async def test_read_with_offset_and_limit(self, tool_context, test_files):
        """Test reading with offset and limit"""
        result = await ToolRegistry.execute(
            "read",
            ctx=tool_context,
            filePath=test_files["text_file"],
            offset=1,
            limit=2
        )
        
        assert result.success
        assert "Line 2" in result.output
        assert "Line 3" in result.output
    
    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self, tool_context, temp_dir):
        """Test reading a nonexistent file"""
        result = await ToolRegistry.execute(
            "read",
            ctx=tool_context,
            filePath=os.path.join(temp_dir, "nonexistent.txt")
        )
        
        assert not result.success
        assert "not found" in result.error.lower()
    
    @pytest.mark.asyncio
    async def test_read_permission_requested(self, tool_context_with_permission, test_files):
        """Test that read requests permission"""
        result = await ToolRegistry.execute(
            "read",
            ctx=tool_context_with_permission,
            filePath=test_files["text_file"]
        )
        
        assert result.success
        permissions = tool_context_with_permission._permissions_requested
        assert len(permissions) > 0
        assert any(p.permission == "read" for p in permissions)


class TestWriteTool:
    """Test the write tool"""
    
    @pytest.mark.asyncio
    async def test_write_new_file(self, tool_context, temp_dir):
        """Test writing a new file"""
        filepath = os.path.join(temp_dir, "new_file.txt")
        content = "This is new content\n"
        
        result = await ToolRegistry.execute(
            "write",
            ctx=tool_context,
            filePath=filepath,
            content=content
        )
        
        assert result.success
        assert os.path.exists(filepath)
        
        with open(filepath, 'r') as f:
            assert f.read() == content
    
    @pytest.mark.asyncio
    async def test_write_overwrite_file(self, tool_context, test_files):
        """Test overwriting an existing file"""
        new_content = "Overwritten content\n"
        
        result = await ToolRegistry.execute(
            "write",
            ctx=tool_context,
            filePath=test_files["text_file"],
            content=new_content
        )
        
        assert result.success
        
        with open(test_files["text_file"], 'r') as f:
            assert f.read() == new_content
    
    @pytest.mark.asyncio
    async def test_write_creates_directories(self, tool_context, temp_dir):
        """Test that write creates parent directories"""
        filepath = os.path.join(temp_dir, "new_dir", "sub", "file.txt")
        content = "Nested file\n"
        
        result = await ToolRegistry.execute(
            "write",
            ctx=tool_context,
            filePath=filepath,
            content=content
        )
        
        assert result.success
        assert os.path.exists(filepath)


class TestEditTool:
    """Test the edit tool"""
    
    @pytest.mark.asyncio
    async def test_edit_string_replacement(self, tool_context, temp_dir):
        """Test basic string replacement"""
        # Create a test file
        filepath = os.path.join(temp_dir, "edit_test.txt")
        with open(filepath, 'w') as f:
            f.write("Hello World\nGoodbye World\n")
        
        result = await ToolRegistry.execute(
            "edit",
            ctx=tool_context,
            filePath=filepath,
            oldString="Hello",
            newString="Hi"
        )
        
        assert result.success
        
        with open(filepath, 'r') as f:
            content = f.read()
            assert "Hi World" in content
            assert "Goodbye World" in content
    
    @pytest.mark.asyncio
    async def test_edit_multiline_replacement(self, tool_context, temp_dir):
        """Test multiline string replacement"""
        filepath = os.path.join(temp_dir, "edit_multiline.txt")
        with open(filepath, 'w') as f:
            f.write("def foo():\n    return 1\n\ndef bar():\n    return 2\n")
        
        result = await ToolRegistry.execute(
            "edit",
            ctx=tool_context,
            filePath=filepath,
            oldString="def foo():\n    return 1",
            newString="def foo():\n    return 42"
        )
        
        assert result.success
        
        with open(filepath, 'r') as f:
            content = f.read()
            assert "return 42" in content
    
    @pytest.mark.asyncio
    async def test_edit_replace_all(self, tool_context, temp_dir):
        """Test replace all occurrences"""
        filepath = os.path.join(temp_dir, "edit_replace_all.txt")
        with open(filepath, 'w') as f:
            f.write("foo bar foo baz foo\n")
        
        result = await ToolRegistry.execute(
            "edit",
            ctx=tool_context,
            filePath=filepath,
            oldString="foo",
            newString="qux",
            replaceAll=True
        )
        
        assert result.success
        
        with open(filepath, 'r') as f:
            content = f.read()
            assert "foo" not in content
            assert content.count("qux") == 3

    def test_edit_schema_supports_batch_edits(self):
        """Test edit schema exposes pi-style edits[] plus legacy compatibility."""
        tool = ToolRegistry.get("edit")
        assert tool is not None

        schema = tool.info.get_schema()
        edits_prop = schema.properties["edits"]

        assert edits_prop["type"] == "array"
        assert edits_prop["items"]["type"] == "object"
        assert edits_prop["items"]["required"] == ["oldString", "newString"]
        assert "oldString" in edits_prop["items"]["properties"]
        assert "newString" in edits_prop["items"]["properties"]
        assert schema.required == ["filePath"]


class TestBashTool:
    """Test the bash tool."""

    def test_registered_description_references_dedicated_tools(self):
        tool = ToolRegistry.get("bash")

        assert tool is not None
        description = tool.info.description
        assert "Read file contents -> `read`" in description
        assert "Write a new file -> `write`" in description
        assert "Edit an existing file -> `edit`" in description
        assert "Search file names or directories -> `glob`" in description
        assert "Search file contents -> `grep`" in description
        assert "use `glob` instead of `find` or `ls`" in description

    def test_get_description_windows_mentions_powershell_guidance(self, monkeypatch):
        monkeypatch.setattr(bash_module.sys, "platform", "win32")
        monkeypatch.setattr(bash_module, "_detect_windows_powershell_shell", lambda: "powershell")

        description = bash_module.get_description("/workspace")

        assert "Execute PowerShell commands with optional timeout." in description
        assert "Do not prefix commands with `cd` or `Set-Location`" in description
        assert "Avoid bash-only syntax such as `export NAME=value`" in description
        assert "Windows PowerShell 5.1 notes:" in description
        assert "Pipeline chain operators `&&` and `||` are not available." in description

    def test_get_description_windows_pwsh_omits_powershell_51_notes(self, monkeypatch):
        monkeypatch.setattr(bash_module.sys, "platform", "win32")
        monkeypatch.setattr(bash_module, "_detect_windows_powershell_shell", lambda: "pwsh")

        description = bash_module.get_description("/workspace")

        assert "Execute PowerShell commands with optional timeout." in description
        assert "PowerShell syntax notes:" in description
        assert "Windows PowerShell 5.1 notes:" not in description

    def test_get_description_non_windows_omits_windows_guidance(self, monkeypatch):
        monkeypatch.setattr(bash_module.sys, "platform", "linux")

        description = bash_module.get_description("/workspace")

        assert "Execute shell commands with optional timeout." in description
        assert "PowerShell syntax notes:" not in description
        assert "Windows PowerShell 5.1 notes:" not in description
    
    @pytest.mark.asyncio
    async def test_bash_simple_command(self, tool_context):
        """Test executing a simple command"""
        result = await ToolRegistry.execute(
            "bash",
            ctx=tool_context,
            command="echo 'Hello from bash'"
        )
        
        assert result.success
        assert "Hello from bash" in result.output
    
    @pytest.mark.asyncio
    async def test_bash_with_working_directory(self, tool_context, temp_dir):
        """Test bash with working directory"""
        result = await ToolRegistry.execute(
            "bash",
            ctx=tool_context,
            command="pwd",
            workdir=temp_dir
        )
        
        assert result.success
        assert temp_dir in result.output
    
    @pytest.mark.asyncio
    async def test_bash_exit_code(self, tool_context):
        """Test that exit code is captured"""
        result = await ToolRegistry.execute(
            "bash",
            ctx=tool_context,
            command="exit 0"
        )
        
        assert result.success
        assert result.metadata.get("exit") == 0
    
    @pytest.mark.asyncio
    async def test_bash_failed_command(self, tool_context):
        """Test handling of failed command"""
        result = await ToolRegistry.execute(
            "bash",
            ctx=tool_context,
            command="exit 1"
        )
        
        assert not result.success
        assert result.metadata.get("exit") == 1


class TestGrepTool:
    """Test the grep tool"""
    
    @pytest.mark.asyncio
    async def test_grep_basic_pattern(self, tool_context, test_files, temp_dir):
        """Test basic pattern matching"""
        result = await ToolRegistry.execute(
            "grep",
            ctx=tool_context,
            pattern="Line",
            path=temp_dir
        )
        
        assert result.success
        # Should find matches in test.txt
        assert "test.txt" in result.output or "matches" in result.output.lower()
    
    @pytest.mark.asyncio
    async def test_grep_with_include_filter(self, tool_context, temp_dir, test_files):
        """Test grep with file type filter"""
        result = await ToolRegistry.execute(
            "grep",
            ctx=tool_context,
            pattern="def|print",
            path=temp_dir,
            include="*.py"
        )
        
        assert result.success
    
    @pytest.mark.asyncio
    async def test_grep_no_matches(self, tool_context, temp_dir):
        """Test grep with no matches"""
        result = await ToolRegistry.execute(
            "grep",
            ctx=tool_context,
            pattern="XYZNONEXISTENT123",
            path=temp_dir
        )
        
        assert result.success
        assert "No files found" in result.output or result.metadata.get("matches") == 0


class TestGlobTool:
    """Test the glob tool"""
    
    @pytest.mark.asyncio
    async def test_glob_find_files(self, tool_context, temp_dir, test_files):
        """Test finding files by pattern"""
        result = await ToolRegistry.execute(
            "glob",
            ctx=tool_context,
            pattern="*.txt",
            path=temp_dir
        )
        
        assert result.success
        assert "test.txt" in result.output
    
    @pytest.mark.asyncio
    async def test_glob_recursive_pattern(self, tool_context, temp_dir, test_files):
        """Test recursive glob pattern"""
        result = await ToolRegistry.execute(
            "glob",
            ctx=tool_context,
            pattern="**/*.txt",
            path=temp_dir
        )
        
        assert result.success
        # Should find nested.txt in subdir
        assert "nested.txt" in result.output
    
    @pytest.mark.asyncio
    async def test_glob_no_matches(self, tool_context, temp_dir):
        """Test glob with no matches"""
        result = await ToolRegistry.execute(
            "glob",
            ctx=tool_context,
            pattern="*.xyz",
            path=temp_dir
        )
        
        assert result.success
        assert "No files found" in result.output


# =============================================================================
# P1 Tools Tests
# =============================================================================

@pytest.mark.usefixtures("clean_todo_storage")
class TestTodoTools:
    """Test the todo tools"""
    
    @pytest.mark.asyncio
    async def test_todo_write_create_todos(self, tool_context):
        """Test creating todos"""
        todos = [
            {"id": "1", "content": "First task", "status": "pending"},
            {"id": "2", "content": "Second task", "activeForm": "Working on second task", "status": "in_progress"},
        ]
        
        result = await ToolRegistry.execute(
            "todo",
            ctx=tool_context,
            action="write",
            todos=todos,
        )
        
        assert result.success
        payload = json.loads(result.output)
        assert payload["oldTodos"] == []
        assert payload["newTodos"][0]["content"] == "First task"
        assert payload["newTodos"][1]["activeForm"] == "Working on second task"
        assert payload["verificationNudgeNeeded"] is False

    def test_todo_schema_requires_structured_items(self):
        """Tool schema should expose object items, not string arrays."""
        schema = ToolRegistry.get_schema("todo")

        assert schema is not None
        assert schema.properties["action"]["enum"] == ["read", "write"]
        assert schema.properties["todos"]["type"] == "array"
        assert schema.properties["todos"]["items"]["type"] == "object"
        assert schema.properties["todos"]["items"]["required"] == ["id", "content", "status"]
        assert "activeForm" in schema.properties["todos"]["items"]["properties"]
    
    @pytest.mark.asyncio
    async def test_todo_read_get_todos(self, tool_context):
        """Test reading todos"""
        # First write some todos
        todos = [
            {"id": "1", "content": "Test task", "status": "pending"},
        ]
        await ToolRegistry.execute(
            "todo",
            ctx=tool_context,
            action="write",
            todos=todos,
        )
        
        # Then read them
        result = await ToolRegistry.execute(
            "todo",
            ctx=tool_context,
            action="read",
        )
        
        assert result.success
        payload = json.loads(result.output)
        assert payload[0]["content"] == "Test task"

    @pytest.mark.asyncio
    async def test_todo_write_rejects_string_arrays(self, tool_context):
        """Invalid todo payloads should fail loudly instead of returning []."""
        result = await ToolRegistry.execute(
            "todo",
            ctx=tool_context,
            action="write",
            todos=[
                "1. First task",
                "2. Second task",
            ],
        )

        assert not result.success
        assert "todos[0] must be an object" in (result.error or "")

    @pytest.mark.asyncio
    async def test_todo_write_persists_to_session_todo_store(self, clean_todo_storage):
        """todo tools should use the shared session todo store."""
        from flocks.session.features.todo import Todo

        session_id = f"test-session-{uuid.uuid4()}"
        ctx = ToolContext(
            session_id=session_id,
            message_id="test-message-persist",
            agent="test",
        )
        todos = [
            {"id": "persist", "content": "Persist todo", "status": "pending"},
        ]

        result = await ToolRegistry.execute("todo", ctx=ctx, action="write", todos=todos)

        assert result.success
        stored = await Todo.get(session_id)
        assert len(stored) == 1
        assert stored[0].id == "persist"
        assert stored[0].content == "Persist todo"

    @pytest.mark.asyncio
    async def test_todo_write_clears_storage_when_all_todos_are_terminal(self, clean_todo_storage):
        """Completed/cancelled-only todo lists should be cleared from persistence."""
        from flocks.session.features.todo import Todo

        session_id = f"test-session-{uuid.uuid4()}"
        ctx = ToolContext(
            session_id=session_id,
            message_id="test-message-terminal",
            agent="test",
        )

        await ToolRegistry.execute(
            "todo",
            ctx=ctx,
            action="write",
            todos=[{"id": "1", "content": "Still open", "status": "in_progress"}],
        )

        result = await ToolRegistry.execute(
            "todo",
            ctx=ctx,
            action="write",
            todos=[
                {"id": "1", "content": "Done task", "status": "completed"},
                {"id": "2", "content": "Cancelled task", "status": "cancelled"},
            ],
        )

        payload = json.loads(result.output)
        assert payload["newTodos"][0]["status"] == "completed"
        assert payload["newTodos"][1]["status"] == "cancelled"
        assert await Todo.get(session_id) == []

    @pytest.mark.asyncio
    async def test_todo_write_sets_verification_nudge_for_completed_batches(self, tool_context):
        """Large completed batches without verification work should return a nudge."""
        result = await ToolRegistry.execute(
            "todo",
            ctx=tool_context,
            action="write",
            todos=[
                {"id": "1", "content": "Implement feature", "status": "completed"},
                {"id": "2", "content": "Fix bug", "status": "completed"},
                {"id": "3", "content": "Ship branch", "status": "completed"},
            ],
        )

        payload = json.loads(result.output)
        assert payload["verificationNudgeNeeded"] is True


class TestQuestionTool:
    """Test the question tool"""
    
    @pytest.mark.asyncio
    async def test_question_tool_exists(self):
        """Test that question tool is registered"""
        tool = ToolRegistry.get("question")
        assert tool is not None
        assert tool.info.name == "question"


class TestWebFetchTool:
    """Test the webfetch tool"""
    
    @pytest.mark.asyncio
    async def test_webfetch_tool_exists(self):
        """Test that webfetch tool is registered"""
        tool = ToolRegistry.get("webfetch")
        assert tool is not None
        assert tool.info.name == "webfetch"
    
    @pytest.mark.asyncio
    async def test_webfetch_schema(self):
        """Test webfetch tool schema"""
        schema = ToolRegistry.get_schema("webfetch")
        assert schema is not None
        assert "url" in schema.properties


# =============================================================================
# P2 Tools Tests
# =============================================================================

class TestTaskTool:
    """Test the task tool"""
    
    @pytest.mark.asyncio
    async def test_task_exists(self):
        """Test that task tool is registered"""
        tool = ToolRegistry.get("task")
        assert tool is not None


class TestBatchExecution:
    """Test registry batch execution helpers"""

    @pytest.mark.asyncio
    async def test_batch_execute_multiple(self, tool_context, temp_dir, test_files):
        """Test batch execution of multiple tools"""
        # Execute multiple read operations in parallel via registry
        calls = [
            {"name": "read", "params": {"filePath": test_files["text_file"]}},
            {"name": "read", "params": {"filePath": test_files["py_file"]}},
        ]
        
        results = await ToolRegistry.execute_batch(calls, ctx=tool_context, parallel=True)
        
        assert len(results) == 2
        assert results[0].success
        assert results[1].success
        assert "Line 1" in results[0].output
        assert "def hello" in results[1].output
    
    @pytest.mark.asyncio
    async def test_batch_execute_sequential(self, tool_context, temp_dir, test_files):
        """Test batch execution in sequential mode"""
        calls = [
            {"name": "read", "params": {"filePath": test_files["text_file"]}},
            {"name": "glob", "params": {"pattern": "*.txt", "path": temp_dir}},
        ]
        
        results = await ToolRegistry.execute_batch(calls, ctx=tool_context, parallel=False)
        
        assert len(results) == 2
        assert all(r.success for r in results)


class TestLSPTool:
    """Test the LSP tool"""
    
    @pytest.mark.asyncio
    async def test_lsp_exists(self):
        """Test that lsp tool is registered"""
        tool = ToolRegistry.get("lsp")
        assert tool is not None


class TestSkillLoadTool:
    """Test the skill_load tool"""
    
    @pytest.mark.asyncio
    async def test_skill_load_exists(self):
        """Test that skill_load tool is registered"""
        tool = ToolRegistry.get("skill_load")
        assert tool is not None


# =============================================================================
# P3 Tools Tests
# =============================================================================

class TestWebSearchTool:
    """Test the websearch tool"""
    
    @pytest.mark.asyncio
    async def test_websearch_exists(self):
        """Test that websearch tool is registered"""
        tool = ToolRegistry.get("websearch")
        assert tool is not None


class TestApplyPatchTool:
    """Test the apply_patch tool"""
    
    @pytest.mark.asyncio
    async def test_apply_patch_exists(self):
        """Test that apply_patch tool is registered"""
        tool = ToolRegistry.get("apply_patch")
        assert tool is not None


# =============================================================================
# Sample Tools Tests (via ToolRegistry.init())
# =============================================================================

class TestSampleTools:
    """Test sample tools registered via ToolRegistry.init()"""
    
    @pytest.mark.asyncio
    async def test_init_registers_sample_tools(self, tool_context):
        """Test that init() registers sample tools"""
        # Call init to register sample tools
        ToolRegistry.init()
        
        # After init(), get_time should be available
        time_tool = ToolRegistry.get("get_time")
        
        assert time_tool is not None, "get_time tool should be registered after init()"
    
    @pytest.mark.asyncio
    async def test_get_time_tool_after_init(self, tool_context):
        """Test the get_time tool after init"""
        ToolRegistry.init()
        
        result = await ToolRegistry.execute(
            "get_time",
            ctx=tool_context
        )
        
        assert result.success
        # Should return ISO format datetime
        assert "T" in result.output  # ISO format contains 'T'


# =============================================================================
# ToolContext Tests
# =============================================================================

class TestToolContext:
    """Test the ToolContext class"""
    
    def test_context_creation(self):
        """Test creating a tool context"""
        ctx = ToolContext(
            session_id="test-session",
            message_id="test-message",
            agent="test-agent"
        )
        
        assert ctx.session_id == "test-session"
        assert ctx.message_id == "test-message"
        assert ctx.agent == "test-agent"
    
    def test_context_abort(self):
        """Test abort functionality"""
        ctx = ToolContext(
            session_id="test-session",
            message_id="test-message"
        )
        
        assert not ctx.aborted
        ctx.abort.set()
        assert ctx.aborted
    
    @pytest.mark.asyncio
    async def test_context_permission_request(self):
        """Test permission request through context"""
        permissions_requested = []
        
        async def track_permission(request: PermissionRequest):
            permissions_requested.append(request)
        
        ctx = ToolContext(
            session_id="test-session",
            message_id="test-message",
            permission_callback=track_permission
        )
        
        await ctx.ask(
            permission="read",
            patterns=["/path/to/file"],
            always=["*"],
            metadata={}
        )
        
        assert len(permissions_requested) == 1
        assert permissions_requested[0].permission == "read"
    
    def test_context_metadata(self):
        """Test metadata updates"""
        metadata_updates = []
        
        def track_metadata(data: Dict[str, Any]):
            metadata_updates.append(data.copy())
        
        ctx = ToolContext(
            session_id="test-session",
            message_id="test-message",
            metadata_callback=track_metadata
        )
        
        ctx.metadata({"title": "Test Title"})
        ctx.metadata({"metadata": {"key": "value"}})
        
        assert len(metadata_updates) == 2


# =============================================================================
# ToolResult Tests
# =============================================================================

class TestToolResult:
    """Test the ToolResult class"""
    
    def test_successful_result(self):
        """Test creating a successful result"""
        result = ToolResult(
            success=True,
            output="Operation completed",
            title="Test"
        )
        
        assert result.success
        assert result.output == "Operation completed"
        assert result.error is None
    
    def test_failed_result(self):
        """Test creating a failed result"""
        result = ToolResult(
            success=False,
            error="Something went wrong"
        )
        
        assert not result.success
        assert result.error == "Something went wrong"
    
    def test_result_with_metadata(self):
        """Test result with metadata"""
        result = ToolResult(
            success=True,
            output="Output",
            metadata={"count": 10, "truncated": False}
        )
        
        assert result.metadata["count"] == 10
        assert not result.metadata["truncated"]
    
    def test_result_with_attachments(self):
        """Test result with attachments (for images/PDFs)"""
        result = ToolResult(
            success=True,
            output="Image loaded",
            attachments=[{
                "id": "attach-1",
                "type": "file",
                "mime": "image/png",
                "url": "data:image/png;base64,..."
            }]
        )
        
        assert result.attachments is not None
        assert len(result.attachments) == 1
        assert result.attachments[0]["mime"] == "image/png"


# =============================================================================
# Error Handling Tests
# =============================================================================

class TestErrorHandling:
    """Test error handling across tools"""
    
    @pytest.mark.asyncio
    async def test_missing_required_parameter(self, tool_context):
        """Test error when required parameter is missing"""
        result = await ToolRegistry.execute(
            "read",
            ctx=tool_context
            # Missing filePath parameter
        )
        
        assert not result.success
        assert "required" in result.error.lower() or "missing" in result.error.lower()
    
    @pytest.mark.asyncio
    async def test_nonexistent_tool(self, tool_context):
        """Test error when calling nonexistent tool"""
        result = await ToolRegistry.execute(
            "nonexistent_tool_xyz",
            ctx=tool_context
        )
        
        assert not result.success
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_builtin_tool_rejects_unknown_parameter(self, tool_context, temp_dir):
        """Built-in tools should reject unknown parameters via schema precheck."""
        result = await ToolRegistry.execute(
            "read",
            ctx=tool_context,
            filePath=os.path.join(temp_dir, "no-file.txt"),
            unknownParam="x",
        )
        assert not result.success
        assert "unknown parameters" in (result.error or "").lower()
        assert "allowed parameters" in (result.error or "").lower()
    
    @pytest.mark.asyncio
    async def test_tool_handles_exceptions(self, tool_context, temp_dir):
        """Test that tools handle exceptions gracefully"""
        # Try to read from an invalid path that might cause an exception
        result = await ToolRegistry.execute(
            "read",
            ctx=tool_context,
            filePath="/\0invalid/path"  # Invalid path with null character
        )
        
        # Should return error, not raise exception
        assert not result.success

    @pytest.mark.asyncio
    async def test_schema_param_alias_remap_accepts_case_separator_variants(self, tool_context):
        """All tools should remap obvious key variants (file_path -> filePath)."""
        async def _handler(ctx: ToolContext, filePath: str) -> ToolResult:
            return ToolResult(success=True, output=filePath)

        tool_name = "test_schema_precheck_alias"
        ToolRegistry.register(
            Tool(
                info=ToolInfo(
                    name=tool_name,
                    description="Test schema alias remap",
                    category=ToolCategory.CUSTOM,
                    parameters=[
                        ToolParameter(
                            name="filePath",
                            type=ParameterType.STRING,
                            description="Path",
                            required=True,
                        )
                    ],
                    source="plugin_py",
                    native=True,
                    enabled=True,
                ),
                handler=_handler,
            )
        )
        try:
            result = await ToolRegistry.execute(
                tool_name,
                ctx=tool_context,
                file_path="/tmp/demo.txt",
            )
            assert result.success
            assert result.output == "/tmp/demo.txt"
        finally:
            ToolRegistry.unregister(tool_name)

    @pytest.mark.asyncio
    async def test_unknown_params_returns_schema_hint_for_all_tools(self, tool_context):
        """All tools should reject unknown params with schema guidance."""
        async def _handler(ctx: ToolContext, query: str) -> ToolResult:
            return ToolResult(success=True, output=query)

        tool_name = "test_schema_precheck_unknown"
        ToolRegistry.register(
            Tool(
                info=ToolInfo(
                    name=tool_name,
                    description="Test unknown parameter handling",
                    category=ToolCategory.CUSTOM,
                    parameters=[
                        ToolParameter(
                            name="query",
                            type=ParameterType.STRING,
                            description="Query",
                            required=True,
                        )
                    ],
                    source="plugin_py",
                    native=True,
                    enabled=True,
                ),
                handler=_handler,
            )
        )
        try:
            result = await ToolRegistry.execute(
                tool_name,
                ctx=tool_context,
                keyword="abc",
            )
            assert not result.success
            assert "Invalid arguments" in (result.error or "")
            assert "Allowed parameters: query" in (result.error or "")
            assert isinstance(result.metadata, dict)
            assert "schema_precheck" in result.metadata
        finally:
            ToolRegistry.unregister(tool_name)


# =============================================================================
# Integration Tests
# =============================================================================

class TestToolIntegration:
    """Integration tests combining multiple tools"""
    
    @pytest.mark.asyncio
    async def test_write_then_read(self, tool_context, temp_dir):
        """Test writing a file then reading it"""
        filepath = os.path.join(temp_dir, "integration_test.txt")
        content = "Integration test content\n"
        
        # Write
        write_result = await ToolRegistry.execute(
            "write",
            ctx=tool_context,
            filePath=filepath,
            content=content
        )
        assert write_result.success
        
        # Read
        read_result = await ToolRegistry.execute(
            "read",
            ctx=tool_context,
            filePath=filepath
        )
        assert read_result.success
        assert "Integration test content" in read_result.output
    
    @pytest.mark.asyncio
    async def test_bash_and_glob(self, tool_context, temp_dir):
        """Test creating files with bash then finding with glob"""
        # Create files with bash
        await ToolRegistry.execute(
            "bash",
            ctx=tool_context,
            command="touch test_a.log test_b.log test_c.log",
            workdir=temp_dir
        )
        
        # Find with glob
        result = await ToolRegistry.execute(
            "glob",
            ctx=tool_context,
            pattern="*.log",
            path=temp_dir
        )
        
        assert result.success
        # Should find the created files
        assert "test_a.log" in result.output or result.metadata.get("count", 0) >= 3
    
    @pytest.mark.asyncio
    async def test_parallel_tool_execution(self, tool_context, temp_dir):
        """Test executing multiple tools in parallel"""
        # Create test files for parallel reading
        files = []
        for i in range(3):
            filepath = os.path.join(temp_dir, f"parallel_test_{i}.txt")
            with open(filepath, 'w') as f:
                f.write(f"Content {i}\n")
            files.append(filepath)
        
        calls = [
            {"name": "read", "params": {"filePath": files[0]}},
            {"name": "read", "params": {"filePath": files[1]}},
            {"name": "read", "params": {"filePath": files[2]}},
        ]
        
        results = await ToolRegistry.execute_batch(calls, ctx=tool_context, parallel=True)
        
        assert len(results) == 3
        assert all(r.success for r in results)
        assert "Content 0" in results[0].output
        assert "Content 1" in results[1].output
        assert "Content 2" in results[2].output


# =============================================================================
# Tool Schema Tests
# =============================================================================

class TestToolSchemas:
    """Test tool schema validation"""
    
    def test_all_tools_have_valid_schemas(self):
        """Test that all tools have valid schemas"""
        tool_ids = ToolRegistry.all_tool_ids()
        
        for tool_id in tool_ids:
            schema = ToolRegistry.get_schema(tool_id)
            assert schema is not None, f"Tool '{tool_id}' has no schema"
            assert isinstance(schema.properties, dict), f"Tool '{tool_id}' has invalid properties"
            assert isinstance(schema.required, list), f"Tool '{tool_id}' has invalid required list"
    
    def test_all_tools_have_descriptions(self):
        """Test that all tools have descriptions"""
        tools = ToolRegistry.list_tools()
        
        for tool_info in tools:
            assert tool_info.description, f"Tool '{tool_info.name}' has no description"
            assert len(tool_info.description) > 10, f"Tool '{tool_info.name}' has very short description"


# =============================================================================
# Advanced Tool Tests
# =============================================================================

class TestReadToolAdvanced:
    """Advanced tests for read tool"""
    
    @pytest.mark.asyncio
    async def test_read_empty_file(self, tool_context, temp_dir):
        """Test reading an empty file"""
        filepath = os.path.join(temp_dir, "empty.txt")
        with open(filepath, 'w') as f:
            pass  # Create empty file
        
        result = await ToolRegistry.execute(
            "read",
            ctx=tool_context,
            filePath=filepath
        )
        
        assert result.success
    
    @pytest.mark.asyncio
    async def test_read_large_offset(self, tool_context, temp_dir):
        """Test reading with offset beyond file length"""
        filepath = os.path.join(temp_dir, "short.txt")
        with open(filepath, 'w') as f:
            f.write("Line 1\nLine 2\n")
        
        result = await ToolRegistry.execute(
            "read",
            ctx=tool_context,
            filePath=filepath,
            offset=1000  # Beyond file length
        )
        
        assert result.success


class TestWriteToolAdvanced:
    """Advanced tests for write tool"""
    
    @pytest.mark.asyncio
    async def test_write_unicode_content(self, tool_context, temp_dir):
        """Test writing unicode content"""
        filepath = os.path.join(temp_dir, "unicode.txt")
        content = "Hello 世界 🎉 مرحبا\n"
        
        result = await ToolRegistry.execute(
            "write",
            ctx=tool_context,
            filePath=filepath,
            content=content
        )
        
        assert result.success
        
        with open(filepath, 'r', encoding='utf-8') as f:
            assert f.read() == content
    
    @pytest.mark.asyncio
    async def test_write_preserves_content(self, tool_context, temp_dir):
        """Test that write preserves exact content"""
        filepath = os.path.join(temp_dir, "exact.txt")
        content = "  Leading spaces\n\tTabs\nTrailing  \n"
        
        result = await ToolRegistry.execute(
            "write",
            ctx=tool_context,
            filePath=filepath,
            content=content
        )
        
        assert result.success
        
        with open(filepath, 'r') as f:
            assert f.read() == content


class TestEditToolAdvanced:
    """Advanced tests for edit tool"""
    
    @pytest.mark.asyncio
    async def test_edit_not_found(self, tool_context, temp_dir):
        """Test edit when string not found"""
        filepath = os.path.join(temp_dir, "edit_notfound.txt")
        with open(filepath, 'w') as f:
            f.write("Hello World\n")
        
        result = await ToolRegistry.execute(
            "edit",
            ctx=tool_context,
            filePath=filepath,
            oldString="nonexistent",
            newString="replacement"
        )
        
        assert not result.success
        assert "could not find" in result.error.lower()
        assert "re-read the file" in result.error.lower()
        assert "slightly larger unique snippet" in result.error.lower()
    
    @pytest.mark.asyncio
    async def test_edit_create_new_file(self, tool_context, temp_dir):
        """Test edit creates new file with empty oldString"""
        filepath = os.path.join(temp_dir, "new_edit_file.txt")
        content = "New file content\n"
        
        result = await ToolRegistry.execute(
            "edit",
            ctx=tool_context,
            filePath=filepath,
            oldString="",
            newString=content
        )
        
        assert result.success
        assert os.path.exists(filepath)
        
        with open(filepath, 'r') as f:
            assert f.read() == content

    @pytest.mark.asyncio
    async def test_edit_multi_snapshot_semantics(self, tool_context, temp_dir):
        """Test edits[] are matched against the original file, not incrementally."""
        filepath = os.path.join(temp_dir, "edit_multi_snapshot.txt")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("foo\nbar\nbaz\n")

        result = await ToolRegistry.execute(
            "edit",
            ctx=tool_context,
            filePath=filepath,
            edits=[
                {"oldString": "foo\n", "newString": "foo bar\n"},
                {"oldString": "bar\n", "newString": "BAR\n"},
            ],
        )

        assert result.success
        with open(filepath, "r", encoding="utf-8") as f:
            assert f.read() == "foo bar\nBAR\nbaz\n"

    @pytest.mark.asyncio
    async def test_edit_multi_overlap_fails(self, tool_context, temp_dir):
        """Test overlapping edits are rejected."""
        filepath = os.path.join(temp_dir, "edit_overlap.txt")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("one\ntwo\nthree\n")

        result = await ToolRegistry.execute(
            "edit",
            ctx=tool_context,
            filePath=filepath,
            edits=[
                {"oldString": "one\ntwo\n", "newString": "ONE\nTWO\n"},
                {"oldString": "two\nthree\n", "newString": "TWO\nTHREE\n"},
            ],
        )

        assert not result.success
        assert "overlap" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_edit_multi_failure_is_not_partial(self, tool_context, temp_dir):
        """Test edit does not partially apply batch edits when one fails."""
        filepath = os.path.join(temp_dir, "edit_no_partial.txt")
        original = "alpha\nbeta\ngamma\n"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(original)

        result = await ToolRegistry.execute(
            "edit",
            ctx=tool_context,
            filePath=filepath,
            edits=[
                {"oldString": "alpha\n", "newString": "ALPHA\n"},
                {"oldString": "missing\n", "newString": "MISSING\n"},
            ],
        )

        assert not result.success
        with open(filepath, "r", encoding="utf-8") as f:
            assert f.read() == original

    @pytest.mark.asyncio
    async def test_edit_multi_preserves_bom_and_crlf(self, tool_context, temp_dir):
        """Test batch edit preserves UTF-8 BOM and CRLF line endings."""
        filepath = os.path.join(temp_dir, "edit_bom_crlf.txt")
        original = "\ufefffirst\r\nsecond\r\nthird\r\nfourth\r\n"
        with open(filepath, "w", encoding="utf-8", newline="") as f:
            f.write(original)

        result = await ToolRegistry.execute(
            "edit",
            ctx=tool_context,
            filePath=filepath,
            edits=[
                {"oldString": "second\n", "newString": "SECOND\n"},
                {"oldString": "fourth\n", "newString": "FOURTH\n"},
            ],
        )

        assert result.success
        with open(filepath, "r", encoding="utf-8", newline="") as f:
            assert f.read() == "\ufefffirst\r\nSECOND\r\nthird\r\nFOURTH\r\n"

    @pytest.mark.asyncio
    async def test_edit_fuzzy_match_preserves_unedited_unicode(self, tool_context, temp_dir):
        """Fuzzy edits must not normalize unrelated Unicode elsewhere in the file."""
        filepath = os.path.join(temp_dir, "edit_fuzzy_unicode.txt")
        original = 'title = "Don’t stop"\nother = "keep — dash"\n'
        with open(filepath, "w", encoding="utf-8", newline="") as f:
            f.write(original)

        result = await ToolRegistry.execute(
            "edit",
            ctx=tool_context,
            filePath=filepath,
            oldString="Don't stop",
            newString="Do not stop",
        )

        assert result.success
        with open(filepath, "r", encoding="utf-8", newline="") as f:
            assert f.read() == 'title = "Do not stop"\nother = "keep — dash"\n'


class TestBashToolAdvanced:
    """Advanced tests for bash tool"""
    
    @pytest.mark.asyncio
    async def test_bash_command_with_pipes(self, tool_context):
        """Test bash with pipe commands"""
        result = await ToolRegistry.execute(
            "bash",
            ctx=tool_context,
            command="echo 'hello world' | tr 'a-z' 'A-Z'"
        )
        
        assert result.success
        assert "HELLO WORLD" in result.output
    
    @pytest.mark.asyncio
    async def test_bash_environment_variables(self, tool_context):
        """Test bash environment variables"""
        result = await ToolRegistry.execute(
            "bash",
            ctx=tool_context,
            command="TEST_VAR=hello && echo $TEST_VAR"
        )
        
        assert result.success
        assert "hello" in result.output
    
    @pytest.mark.asyncio
    async def test_bash_with_description(self, tool_context):
        """Test bash with description parameter"""
        result = await ToolRegistry.execute(
            "bash",
            ctx=tool_context,
            command="echo test",
            description="Echo test message"
        )
        
        assert result.success
        assert result.title == "Echo test message"


class TestGrepToolAdvanced:
    """Advanced tests for grep tool"""
    
    @pytest.mark.asyncio
    async def test_grep_regex_pattern(self, tool_context, temp_dir, test_files):
        """Test grep with regex pattern"""
        result = await ToolRegistry.execute(
            "grep",
            ctx=tool_context,
            pattern=r"Line \d+",
            path=temp_dir
        )
        
        assert result.success
    
    @pytest.mark.asyncio
    async def test_grep_case_sensitivity(self, tool_context, temp_dir):
        """Test grep case sensitivity"""
        # Create a test file with mixed case
        filepath = os.path.join(temp_dir, "case_test.txt")
        with open(filepath, 'w') as f:
            f.write("HELLO\nhello\nHeLLo\n")
        
        result = await ToolRegistry.execute(
            "grep",
            ctx=tool_context,
            pattern="hello",
            path=temp_dir
        )
        
        assert result.success


class TestGlobToolAdvanced:
    """Advanced tests for glob tool"""
    
    @pytest.mark.asyncio
    async def test_glob_multiple_extensions(self, tool_context, temp_dir, test_files):
        """Test glob with multiple extensions"""
        result = await ToolRegistry.execute(
            "glob",
            ctx=tool_context,
            pattern="*.{txt,py}",
            path=temp_dir
        )
        
        assert result.success
    
    @pytest.mark.asyncio
    async def test_glob_hidden_files(self, tool_context, temp_dir):
        """Test glob with hidden files"""
        # Create a hidden file
        hidden_file = os.path.join(temp_dir, ".hidden")
        with open(hidden_file, 'w') as f:
            f.write("hidden content\n")
        
        result = await ToolRegistry.execute(
            "glob",
            ctx=tool_context,
            pattern=".*",
            path=temp_dir
        )
        
        assert result.success


@pytest.mark.usefixtures("clean_todo_storage")
class TestTodoToolsAdvanced:
    """Advanced tests for todo tools"""
    
    @pytest.mark.asyncio
    async def test_todo_status_transitions(self, tool_context):
        """Test todo status transitions"""
        # Create todos
        todos = [
            {"id": "1", "content": "Task 1", "status": "pending"},
        ]
        await ToolRegistry.execute("todo", ctx=tool_context, action="write", todos=todos)
        
        # Update status to in_progress
        todos[0]["status"] = "in_progress"
        result = await ToolRegistry.execute("todo", ctx=tool_context, action="write", todos=todos)
        assert result.success
        
        # Update status to completed
        todos[0]["status"] = "completed"
        result = await ToolRegistry.execute("todo", ctx=tool_context, action="write", todos=todos)
        assert result.success
    
    @pytest.mark.asyncio
    async def test_todo_multiple_items(self, tool_context):
        """Test managing multiple todo items"""
        todos = [
            {"id": "1", "content": "Task 1", "status": "completed"},
            {"id": "2", "content": "Task 2", "status": "in_progress"},
            {"id": "3", "content": "Task 3", "status": "pending"},
            {"id": "4", "content": "Task 4", "status": "pending"},
        ]
        
        result = await ToolRegistry.execute("todo", ctx=tool_context, action="write", todos=todos)
        assert result.success
        
        # Read and verify
        read_result = await ToolRegistry.execute("todo", ctx=tool_context, action="read")
        assert read_result.success
        assert "Task 1" in read_result.output
        assert "Task 4" in read_result.output


class TestToolCategorization:
    """Test tool categorization"""
    
    def test_file_category_tools(self):
        """Test that file category has expected tools"""
        file_tools = ToolRegistry.list_tools(category=ToolCategory.FILE)
        file_tool_names = [t.name for t in file_tools]
        
        assert "read" in file_tool_names
        assert "write" in file_tool_names
        assert "edit" in file_tool_names
    
    def test_terminal_category_tools(self):
        """Test that terminal category has expected tools"""
        terminal_tools = ToolRegistry.list_tools(category=ToolCategory.TERMINAL)
        terminal_tool_names = [t.name for t in terminal_tools]
        
        assert "bash" in terminal_tool_names
    
    def test_search_category_tools(self):
        """Test that search category has expected tools"""
        search_tools = ToolRegistry.list_tools(category=ToolCategory.SEARCH)
        search_tool_names = [t.name for t in search_tools]
        
        assert "grep" in search_tool_names
        assert "glob" in search_tool_names


class TestCustomToolRegistration:
    """Test custom tool registration"""
    
    def test_register_custom_tool(self):
        """Test registering a custom tool"""
        @ToolRegistry.register_function(
            name="test_custom_tool",
            description="A test custom tool",
            category=ToolCategory.CUSTOM,
            parameters=[
                ToolParameter(
                    name="input",
                    type=ParameterType.STRING,
                    description="Test input",
                    required=True
                )
            ]
        )
        async def test_custom_tool(ctx: ToolContext, input: str) -> ToolResult:
            return ToolResult(success=True, output=f"Received: {input}")
        
        # Verify registration
        tool = ToolRegistry.get("test_custom_tool")
        assert tool is not None
        assert tool.info.name == "test_custom_tool"
        assert tool.info.category == ToolCategory.CUSTOM
    
    @pytest.mark.asyncio
    async def test_execute_custom_tool(self, tool_context):
        """Test executing a custom registered tool"""
        result = await ToolRegistry.execute(
            "test_custom_tool",
            ctx=tool_context,
            input="Hello Custom"
        )
        
        assert result.success
        assert "Received: Hello Custom" in result.output


# =============================================================================
# Run Tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
