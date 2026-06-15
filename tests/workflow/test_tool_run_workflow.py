"""
Test Run Workflow Tool - Test suite for run_workflow tool

Tests cover:
- Tool registration and discovery
- Error handling when flocks_workflow is not available
- Workflow execution with mocked dependencies
- Parameter validation
- Permission handling
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock
from typing import Dict, Any

# Import the tool system
from flocks.tool import (
    ParameterType,
    ToolCategory,
    ToolRegistry,
    ToolContext,
    ToolParameter,
    ToolResult,
)

import flocks.tool.task.run_workflow as run_workflow_module
from flocks.mcp.client import McpClient
from flocks.workflow.runner import RunWorkflowResult, run_workflow


class FakeRunWorkflowResult:
    """Minimal RunWorkflowResult stand-in for tool-layer unit tests."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _runtime_tuple(*, run_fn: Mock, installer_cls: Mock | None = None):
    """Return a tuple compatible with _get_workflow_runtime()."""

    installer = installer_cls or Mock(name="RequirementsInstaller")
    return installer, run_fn, FakeRunWorkflowResult


def _make_large_alerts(count: int) -> list[dict[str, Any]]:
    return [{"id": idx, "payload": "x" * 20} for idx in range(count)]


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def init_tool_registry():
    """Ensure ToolRegistry is initialized before each test"""
    ToolRegistry.init()
    yield
    # Cleanup if needed


@pytest.fixture
def tool_context():
    """Create a tool context for testing"""
    return ToolContext(
        session_id="test-session-workflow",
        message_id="test-message-workflow",
        agent="test",
    )


@pytest.fixture
def tool_context_with_permission():
    """Create a tool context with permission tracking"""
    permissions_requested = []
    
    async def track_permission(request):
        permissions_requested.append(request)
    
    ctx = ToolContext(
        session_id="test-session-workflow-perm",
        message_id="test-message-workflow-perm",
        agent="test",
        permission_callback=track_permission,
    )
    ctx._permissions_requested = permissions_requested
    return ctx


@pytest.fixture
def simple_workflow():
    """Create a simple workflow definition for testing"""
    return {
        "id": "test-workflow-001",
        "name": "Test Workflow",
        "start": "node-1",
        "metadata": {},
        "start": "node-1",
        "nodes": [
            {
                "id": "node-1",
                "type": "python",
                "code": "result = {'message': 'Hello from workflow!', 'value': 42}"
            }
        ],
        "edges": []
    }


@pytest.fixture
def workflow_with_requirements():
    """Create a workflow with requirements"""
    return {
        "id": "test-workflow-002",
        "name": "Test Workflow with Requirements",
        "start": "node-1",
        "metadata": {
            "requirements": ["requests>=2.31,<3"]
        },
        "start": "node-1",
        "nodes": [
            {
                "id": "node-1",
                "type": "python",
                "code": "result = {'status': 'ok'}"
            }
        ],
        "edges": []
    }


@pytest.fixture
def workflow_with_inputs():
    """Create a workflow that uses inputs"""
    return {
        "id": "test-workflow-003",
        "name": "Test Workflow with Inputs",
        "start": "node-1",
        "metadata": {},
        "start": "node-1",
        "nodes": [
            {
                "id": "node-1",
                "type": "python",
                "code": "greeting = f'Hello, {inputs.get(\"name\", \"World\")}!'; result = {'greeting': greeting}"
            }
        ],
        "edges": []
    }


# =============================================================================
# Test Tool Registration
# =============================================================================

class TestRunWorkflowToolRegistration:
    """Test run_workflow tool registration"""
    
    def test_run_workflow_tool_exists(self):
        """Test that run_workflow tool is registered"""
        ToolRegistry.init()
        tool = ToolRegistry.get("run_workflow")
        assert tool is not None, "run_workflow tool should be registered"
        assert tool.info.name == "run_workflow"
        assert tool.info.category.value == "system"
        assert tool.info.requires_confirmation is True
    
    def test_run_workflow_tool_schema(self):
        """Test run_workflow tool schema"""
        ToolRegistry.init()
        schema = ToolRegistry.get_schema("run_workflow")
        assert schema is not None
        assert "workflow" in schema.properties
        assert "inputs" in schema.properties
        assert "use_llm" in schema.properties
        assert "ensure_requirements" in schema.properties
        assert "timeout_s" in schema.properties
        assert "trace" in schema.properties


# =============================================================================
# Test Error Handling (flocks_workflow not available)
# =============================================================================

class TestRunWorkflowToolWithoutDependency:
    """Test run_workflow tool when flocks_workflow is not available"""
    
    @pytest.mark.anyio
    async def test_run_workflow_without_flocks_workflow(self, tool_context, simple_workflow):
        """Test that tool returns error when flocks_workflow is not available"""
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=(None, None, None)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context,
                workflow=simple_workflow,
            )
            assert result.success is False
            assert "flocks-workflow package is not available" in result.error


# =============================================================================
# Test Parameter Validation
# =============================================================================

class TestRunWorkflowToolValidation:
    """Test run_workflow tool parameter validation"""
    
    @pytest.mark.anyio
    async def test_run_workflow_missing_workflow(self, tool_context):
        """Test that missing workflow parameter returns error"""
        mock_run = Mock(name="run_workflow")
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context,
                workflow=None,
            )
            assert result.success is False
            assert "workflow parameter is required" in result.error
    
    @pytest.mark.anyio
    async def test_run_workflow_invalid_workflow_type(self, tool_context):
        """Test that invalid workflow type returns error"""
        mock_run = Mock(name="run_workflow")
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context,
                workflow=123,  # Invalid type
            )
            assert result.success is False
            assert "workflow must be a dictionary or string" in result.error
    
    @pytest.mark.anyio
    async def test_run_workflow_empty_workflow(self, tool_context):
        """Test that empty workflow returns error"""
        mock_run = Mock(name="run_workflow", side_effect=Exception("boom"))
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context,
                workflow={},
            )
            # Tool should not crash
            assert result is not None


# =============================================================================
# Test Workflow Execution (with mocked flocks_workflow)
# =============================================================================

class TestRunWorkflowToolExecution:
    """Test run_workflow tool execution with mocked dependencies"""
    
    @pytest.mark.anyio
    async def test_run_workflow_success(self, tool_context_with_permission, simple_workflow):
        """Test successful workflow execution"""
        fake = FakeRunWorkflowResult(**{
            "status": "SUCCEEDED",
            "run_id": "run-123",
            "steps": 1,
            "last_node_id": "node-1",
            "outputs": {"message": "Hello from workflow!", "value": 42},
            "history": [
                {"node_id": "node-1", "status": "SUCCEEDED", "outputs": {"message": "Hello from workflow!", "value": 42}}
            ],
            "error": None
        })
        mock_run = Mock(name="run_workflow", return_value=fake)
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow=simple_workflow,
                inputs={}
            )
            
            assert result.success is True
            assert "SUCCEEDED" in result.output
            assert "run-123" in result.output
            assert "Steps executed: 1" in result.output
            assert result.metadata["status"] == "success"
            assert result.metadata["steps"] == 1
            assert result.metadata["run_id"] == "run-123"
            
            # Check that permission was requested
            assert len(tool_context_with_permission._permissions_requested) > 0

    @pytest.mark.anyio
    async def test_run_workflow_registered_id_updates_execution_history(
        self,
        tool_context_with_permission,
        simple_workflow,
    ):
        metadata_updates: list[dict[str, Any]] = []
        tool_context_with_permission._metadata_callback = metadata_updates.append

        def run_side_effect(**kwargs):
            kwargs["on_step_start"]("run-registered", 1, MagicMock(id="node-1", type="python"), {})
            kwargs["on_step_complete"]({
                "node_id": "node-1",
                "node_type": "python",
                "outputs": {"message": "ok"},
            })
            return FakeRunWorkflowResult(
                status="SUCCEEDED",
                run_id="run-registered",
                steps=1,
                last_node_id="node-1",
                outputs={"message": "ok"},
                history=[{"node_id": "node-1", "node_type": "python", "outputs": {"message": "ok"}}],
                error=None,
            )

        mock_run = Mock(name="run_workflow", side_effect=run_side_effect)
        create_execution = AsyncMock(return_value={
            "id": "exec-registered",
            "workflowId": "test-workflow-001",
            "inputParams": {"name": "Flocks"},
            "status": "running",
            "startedAt": 1,
            "executionLog": [],
        })
        storage_read = AsyncMock(return_value={
            "id": "exec-registered",
            "workflowId": "test-workflow-001",
            "inputParams": {"name": "Flocks"},
            "status": "running",
            "startedAt": 1,
            "executionLog": [],
        })
        storage_write = AsyncMock(return_value=None)
        record_result = AsyncMock(return_value=None)

        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)), \
             patch.object(run_workflow_module, "read_workflow_from_fs", return_value={"id": "test-workflow-001", "workflowJson": simple_workflow}), \
             patch.object(run_workflow_module, "resolve_workflow_id_from_source", return_value="test-workflow-001"), \
             patch.object(run_workflow_module, "create_execution_record", create_execution), \
             patch.object(run_workflow_module.Storage, "read", storage_read), \
             patch.object(run_workflow_module.Storage, "write", storage_write), \
             patch.object(run_workflow_module, "record_execution_result", record_result):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow="test-workflow-001",
                inputs={"name": "Flocks"},
            )

        assert result.success is True
        assert result.metadata["workflow_execution_id"] == "exec-registered"
        create_execution.assert_awaited_once()
        record_result.assert_awaited_once()
        assert storage_write.await_count >= 1
        assert any(update.get("workflow_execution_id") == "exec-registered" for update in metadata_updates)

    @pytest.mark.anyio
    async def test_run_workflow_compacts_large_outputs_for_progress_and_final_record(
        self,
        tool_context_with_permission,
        simple_workflow,
    ):
        large_alerts = _make_large_alerts(150)
        metadata_updates: list[dict[str, Any]] = []
        tool_context_with_permission._metadata_callback = metadata_updates.append

        def run_side_effect(**kwargs):
            kwargs["on_step_start"]("run-compacted", 1, MagicMock(id="node-1", type="python"), {})
            kwargs["on_step_complete"]({
                "node_id": "node-1",
                "node_type": "python",
                "inputs": {"raw_alerts": large_alerts, "source": "syslog"},
                "outputs": {"raw_alerts": large_alerts, "message": "ok"},
            })
            return FakeRunWorkflowResult(
                status="SUCCEEDED",
                run_id="run-compacted",
                steps=1,
                last_node_id="node-1",
                outputs={"enriched_alerts": large_alerts, "message": "done"},
                history=[
                    {
                        "node_id": "node-1",
                        "node_type": "python",
                        "inputs": {"raw_alerts": large_alerts, "source": "syslog"},
                        "outputs": {"raw_alerts": large_alerts, "message": "ok"},
                    }
                ],
                error=None,
            )

        mock_run = Mock(name="run_workflow", side_effect=run_side_effect)
        create_execution = AsyncMock(return_value={
            "id": "exec-compacted",
            "workflowId": "test-workflow-001",
            "inputParams": {},
            "status": "running",
            "startedAt": 1,
            "executionLog": [],
        })
        storage_read = AsyncMock(return_value={
            "id": "exec-compacted",
            "workflowId": "test-workflow-001",
            "inputParams": {},
            "status": "running",
            "startedAt": 1,
            "executionLog": [],
        })
        storage_write = AsyncMock(return_value=None)
        record_result = AsyncMock(return_value=None)

        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)), \
             patch.object(run_workflow_module, "resolve_workflow_id_from_source", return_value="test-workflow-001"), \
             patch.object(run_workflow_module, "create_execution_record", create_execution), \
             patch.object(run_workflow_module.Storage, "read", storage_read), \
             patch.object(run_workflow_module.Storage, "write", storage_write), \
             patch.object(run_workflow_module, "record_execution_result", record_result), \
             patch.object(run_workflow_module, "_record_workflow_tool_result", AsyncMock(return_value=None)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow=simple_workflow,
                inputs={},
            )

        assert result.success is True
        step_write = storage_write.await_args_list[-1]
        assert step_write.args[0] == "workflow_execution_step/exec-compacted/00000001"
        step_payload = step_write.args[1]
        assert step_payload["inputs"] == {
            "_raw_alerts_count": 150,
            "source": "syslog",
        }
        assert step_payload["outputs"] == {
            "_raw_alerts_count": 150,
            "message": "ok",
        }
        assert result.metadata["outputs"] == {
            "_enriched_alerts_count": 150,
            "message": "done",
        }
        assert result.metadata["history"] == []
        assert result.metadata["history_count"] == 1

        final_exec_data = record_result.await_args.args[2]
        assert final_exec_data["outputResults"] == {
            "_enriched_alerts_count": 150,
            "message": "done",
        }
        assert final_exec_data["executionLog"] == []
        assert final_exec_data["stepCount"] == 1
        assert any(update.get("workflow_execution_id") == "exec-compacted" for update in metadata_updates)

    @pytest.mark.anyio
    async def test_run_workflow_uses_isolated_child_tool_context(
        self,
        tool_context_with_permission,
        simple_workflow,
    ):
        fake = FakeRunWorkflowResult(
            status="SUCCEEDED",
            run_id="run-isolated-ctx",
            steps=1,
            last_node_id="node-1",
            outputs={"ok": True},
            history=[],
            error=None,
        )
        mock_run = Mock(name="run_workflow", return_value=fake)

        with patch.object(
            run_workflow_module,
            "_get_workflow_runtime",
            return_value=_runtime_tuple(run_fn=mock_run),
        ):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow=simple_workflow,
                inputs={"name": "Flocks"},
            )

        assert result.success is True
        call_kwargs = mock_run.call_args.kwargs
        nested_ctx = call_kwargs["tool_context"]
        assert nested_ctx is not tool_context_with_permission
        assert nested_ctx.session_id == tool_context_with_permission.session_id
        assert nested_ctx.message_id == tool_context_with_permission.message_id
        assert nested_ctx.agent == tool_context_with_permission.agent
        assert nested_ctx.call_id == tool_context_with_permission.call_id
        assert nested_ctx.extra == tool_context_with_permission.extra
        assert nested_ctx.abort is tool_context_with_permission.abort
        assert nested_ctx.event_publish_callback == tool_context_with_permission.event_publish_callback
        assert nested_ctx._permission_callback == tool_context_with_permission._permission_callback
        assert nested_ctx._metadata_callback is None
    
    @pytest.mark.anyio
    async def test_run_workflow_with_inputs(self, tool_context_with_permission, workflow_with_inputs):
        """Test workflow execution with input parameters"""
        fake = FakeRunWorkflowResult(**{
            "status": "SUCCEEDED",
            "run_id": "run-456",
            "steps": 1,
            "last_node_id": "node-1",
            "outputs": {"greeting": "Hello, Flocks!"},
            "history": [
                {"node_id": "node-1", "status": "SUCCEEDED", "outputs": {"greeting": "Hello, Flocks!"}}
            ],
            "error": None
        })
        mock_run = Mock(name="run_workflow", return_value=fake)
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow=workflow_with_inputs,
                inputs={"name": "Flocks"}
            )
            
            assert result.success is True
            assert "SUCCEEDED" in result.output
    
    @pytest.mark.anyio
    async def test_run_workflow_with_requirements(self, tool_context_with_permission, workflow_with_requirements):
        """Test workflow execution with requirements installation"""
        fake = FakeRunWorkflowResult(**{
            "status": "SUCCEEDED",
            "run_id": "run-789",
            "steps": 1,
            "last_node_id": "node-1",
            "outputs": {"status": "ok"},
            "history": [
                {"node_id": "node-1", "status": "SUCCEEDED", "outputs": {"status": "ok"}}
            ],
            "error": None
        })
        mock_run = Mock(name="run_workflow", return_value=fake)
        installer_cls = Mock(name="RequirementsInstaller")
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run, installer_cls=installer_cls)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow=workflow_with_requirements,
                inputs={},
                ensure_requirements=True
            )
            
            assert result.success is True
            assert installer_cls.called is True
    
    @pytest.mark.anyio
    async def test_run_workflow_with_timeout(self, tool_context_with_permission, simple_workflow):
        """Test workflow execution with timeout"""
        fake = FakeRunWorkflowResult(**{
            "status": "SUCCEEDED",
            "run_id": "run-timeout",
            "steps": 1,
            "last_node_id": "node-1",
            "outputs": {},
            "history": [],
            "error": None
        })
        mock_run = Mock(name="run_workflow", return_value=fake)
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow=simple_workflow,
                inputs={},
                timeout_s=300.0
            )
            
            assert result.success is True
            # Verify timeout was passed to run_workflow
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("timeout_s") == 300.0
            assert call_kwargs.get("use_llm") is True
    
    @pytest.mark.anyio
    async def test_run_workflow_with_trace(self, tool_context_with_permission, simple_workflow):
        """Test workflow execution with tracing enabled"""
        fake = FakeRunWorkflowResult(**{
            "status": "SUCCEEDED",
            "run_id": "run-trace",
            "steps": 1,
            "last_node_id": "node-1",
            "outputs": {},
            "history": [],
            "error": None
        })
        mock_run = Mock(name="run_workflow", return_value=fake)
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow=simple_workflow,
                inputs={},
                trace=True
            )
            
            assert result.success is True
            # Verify trace was passed to run_workflow
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("trace") is True
            assert call_kwargs.get("use_llm") is True

    @pytest.mark.anyio
    async def test_run_workflow_passes_cancel_callback(self, tool_context_with_permission, simple_workflow):
        """Session abort should be forwarded to workflow runtime cancellation."""
        fake = FakeRunWorkflowResult(**{
            "status": "SUCCEEDED",
            "run_id": "run-cancel",
            "steps": 1,
            "last_node_id": "node-1",
            "outputs": {},
            "history": [],
            "error": None,
        })
        mock_run = Mock(name="run_workflow", return_value=fake)
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow=simple_workflow,
                inputs={},
            )

            assert result.success is True
            call_kwargs = mock_run.call_args[1]
            cancel = call_kwargs.get("cancel")
            assert callable(cancel)
            assert cancel() is False
            tool_context_with_permission.abort.set()
            assert cancel() is True

    @pytest.mark.anyio
    async def test_run_workflow_disable_llm(self, tool_context_with_permission, simple_workflow):
        """Test workflow execution with use_llm disabled"""
        fake = FakeRunWorkflowResult(**{
            "status": "SUCCEEDED",
            "run_id": "run-no-llm",
            "steps": 1,
            "last_node_id": "node-1",
            "outputs": {},
            "history": [],
            "error": None
        })
        mock_run = Mock(name="run_workflow", return_value=fake)
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow=simple_workflow,
                inputs={},
                use_llm=False,
            )

            assert result.success is True
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs.get("use_llm") is False
    
    @pytest.mark.anyio
    async def test_run_workflow_execution_failure(self, tool_context_with_permission, simple_workflow):
        """Test workflow execution failure handling"""
        # Mock execution failure
        mock_run = Mock(name="run_workflow", side_effect=Exception("Workflow execution failed"))
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow=simple_workflow,
                inputs={}
            )
            
            assert result.success is False
            assert "Workflow execution failed" in result.error
            assert result.metadata["status"] == "FAILED"
    
    @pytest.mark.anyio
    async def test_run_workflow_failed_status(self, tool_context_with_permission, simple_workflow):
        """Test workflow execution with FAILED status"""
        fake = FakeRunWorkflowResult(**{
            "status": "FAILED",
            "run_id": "run-failed",
            "steps": 0,
            "last_node_id": None,
            "outputs": {},
            "history": [],
            "error": "NodeExecutionError: Error in node 'node-1'"
        })
        mock_run = Mock(name="run_workflow", return_value=fake)
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow=simple_workflow,
                inputs={}
            )
            
            assert result.success is False
            assert "FAILED" in result.output
            assert result.metadata["status"] == "error"


# =============================================================================
# Test Result Formatting
# =============================================================================

class TestRunWorkflowToolResultFormatting:
    """Test run_workflow tool result formatting"""
    
    @pytest.mark.anyio
    async def test_run_workflow_result_formatting(self, tool_context_with_permission, simple_workflow):
        """Test that workflow results are properly formatted"""
        fake = FakeRunWorkflowResult(**{
            "status": "SUCCEEDED",
            "run_id": "run-format",
            "steps": 3,
            "last_node_id": "node-3",
            "outputs": {
                "result": "processed",
                "count": 42
            },
            "history": [
                {"node_id": "node-1", "status": "SUCCEEDED"},
                {"node_id": "node-2", "status": "SUCCEEDED"},
                {"node_id": "node-3", "status": "SUCCEEDED"},
            ],
            "error": None
        })
        mock_run = Mock(name="run_workflow", return_value=fake)
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow=simple_workflow,
                inputs={}
            )
            
            assert result.success is True
            output = result.output
            
            # Check that all key information is present
            assert "Status: SUCCEEDED" in output
            assert "Run ID: run-format" in output
            assert "Steps executed: 3" in output
            assert "Last node: node-3" in output
            assert "Final Outputs:" in output
            assert "Execution History" not in output
        assert result.metadata["history"] == []
        assert result.metadata["history_count"] == len(fake.history)
    
    @pytest.mark.anyio
    async def test_run_workflow_result_with_error(self, tool_context_with_permission, simple_workflow):
        """Test result formatting when workflow has error"""
        fake = FakeRunWorkflowResult(**{
            "status": "FAILED",
            "run_id": "run-error",
            "steps": 1,
            "last_node_id": "node-1",
            "outputs": {},
            "history": [],
            "error": "NodeExecutionError: Invalid code"
        })
        mock_run = Mock(name="run_workflow", return_value=fake)
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow=simple_workflow,
                inputs={}
            )
            
            assert result.success is False
            assert "Error:" in result.output
            assert "NodeExecutionError" in result.output


# =============================================================================
# Test Permission Handling
# =============================================================================

class TestRunWorkflowToolPermissions:
    """Test run_workflow tool permission handling"""
    
    @pytest.mark.anyio
    async def test_run_workflow_requests_permission(self, tool_context_with_permission, simple_workflow):
        """Test that workflow execution requests permission"""
        fake = FakeRunWorkflowResult(**{
            "status": "SUCCEEDED",
            "run_id": "run-perm",
            "steps": 1,
            "last_node_id": "node-1",
            "outputs": {},
            "history": [],
            "error": None
        })
        mock_run = Mock(name="run_workflow", return_value=fake)
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                workflow=simple_workflow,
                inputs={}
            )
            
            # Verify permission was requested
            assert len(tool_context_with_permission._permissions_requested) == 1
            perm_request = tool_context_with_permission._permissions_requested[0]
            assert perm_request.permission == "run_workflow"
            assert "test-workflow-001" in perm_request.patterns or "*" in perm_request.always
            assert perm_request.metadata["workflow_id"] == "test-workflow-001"
            assert perm_request.metadata["workflow_name"] == "Test Workflow"


# =============================================================================
# JSON Parsing Tests (simulating LLM-generated tool calls)
# =============================================================================

class TestRunWorkflowToolJSONParsing:
    """Test JSON parsing scenarios that occur when LLM generates tool calls."""
    
    @pytest.mark.anyio
    async def test_workflow_path_with_quotes_valid_json(self, tool_context_with_permission, tmp_path):
        """Test that workflow path with quotes is valid JSON and can be parsed."""
        import json
        
        # Create a workflow file
        workflow_path = str(tmp_path / "test_workflow.json")
        workflow_content = {
            "id": "test-json-parsing",
            "name": "Test JSON Parsing",
            "start": "node-1",
            "nodes": [
                {
                    "id": "node-1",
                    "type": "python",
                    "code": "outputs['result'] = 'success'"
                }
            ],
            "edges": []
        }
        with open(workflow_path, 'w') as f:
            json.dump(workflow_content, f)
        
        # Simulate LLM generating JSON with QUOTED path (correct)
        arguments_json_string = json.dumps({
            "workflow": workflow_path,  # This will be properly quoted in JSON
            "inputs": {}
        })
        
        # Verify it's valid JSON
        parsed_args = json.loads(arguments_json_string)
        assert parsed_args["workflow"] == workflow_path
        
        # Now execute with the parsed arguments
        fake = FakeRunWorkflowResult(**{
            "status": "SUCCEEDED",
            "run_id": "run-json-test",
            "steps": 1,
            "last_node_id": "node-1",
            "outputs": {"result": "success"},
            "history": []
        })
        
        mock_run = Mock(name="run_workflow", return_value=fake)
        with patch.object(run_workflow_module, "_get_workflow_runtime", return_value=_runtime_tuple(run_fn=mock_run)):
            result = await ToolRegistry.execute(
                "run_workflow",
                ctx=tool_context_with_permission,
                **parsed_args  # Unpack parsed arguments
            )
            
            assert result.success is True
    
    @pytest.mark.anyio
    async def test_workflow_path_without_quotes_invalid_json(self):
        """Test that workflow path without quotes is INVALID JSON and cannot be parsed."""
        import json
        
        # Simulate LLM generating JSON with UNQUOTED path (incorrect - this is the bug)
        invalid_json_string = '{"workflow": workflow/alert_triage/workflow.json, "inputs": {}}'
        
        # Verify it's INVALID JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(invalid_json_string)
        
        # This is exactly what causes "Failed to parse tool arguments" error in production


# =============================================================================
# Integration Test (if flocks_workflow is available)
# =============================================================================

class TestRunWorkflowToolIntegration:
    """Integration tests with the real in-repo workflow runtime."""
    
    @pytest.mark.anyio
    async def test_run_workflow_integration(self, tool_context_with_permission):
        """Integration test that exercises the tool end-to-end."""
        workflow = {
            "id": "integration-test",
            "name": "Integration Test Workflow",
            "metadata": {},
            "start": "node-1",
            "nodes": [
                {
                    "id": "node-1",
                    "type": "python",
                    "code": "outputs['result'] = {'test': 'integration', 'value': 100}"
                }
            ],
            "edges": []
        }
        
        result = await ToolRegistry.execute(
            "run_workflow",
            ctx=tool_context_with_permission,
            workflow=workflow,
            inputs={},
            ensure_requirements=False  # Skip requirements for test
        )
        
        assert result is not None
        assert result.success is True
        assert "Status: SUCCEEDED" in (result.output or "")

    @pytest.mark.asyncio
    async def test_run_workflow_integration_reuses_cross_loop_mcp_client(
        self,
        tool_context,
    ):
        tool_name = "test_fake_mcp_cross_loop_tool"
        client = McpClient(
            name="demo",
            server_type="remote",
            url="https://example.com/mcp",
        )
        owner_loop = asyncio.get_running_loop()
        client._connected = True
        client._owner_loop = owner_loop
        client._command_queue = asyncio.Queue()
        observed: dict[str, Any] = {}

        async def owner() -> None:
            while True:
                command = await client._command_queue.get()
                if command.action == "disconnect":
                    if command.response is not None and not command.response.done():
                        command.response.set_result(None)
                    return
                observed["action"] = command.action
                observed["payload"] = dict(command.payload)
                if command.response is not None and not command.response.done():
                    command.response.set_result("owner-loop-ok")

        client._owner_task = asyncio.create_task(owner())

        @ToolRegistry.register_function(
            name=tool_name,
            description="Cross-loop MCP-backed test tool",
            category=ToolCategory.SYSTEM,
            parameters=[
                ToolParameter(
                    name="value",
                    type=ParameterType.STRING,
                    description="Payload value",
                    required=True,
                )
            ],
        )
        async def _tool(_ctx: ToolContext, value: str) -> ToolResult:
            result = await client.call_tool("demo_tool", {"value": value})
            return ToolResult(success=True, output=result)

        workflow = {
            "id": "integration-mcp-cross-loop",
            "name": "Integration MCP Cross Loop Workflow",
            "metadata": {},
            "start": "tool-node",
            "nodes": [
                {
                    "id": "tool-node",
                    "type": "tool",
                    "tool_name": tool_name,
                    "tool_args": {"value": "demo"},
                    "output_key": "result",
                }
            ],
            "edges": [],
        }

        try:
            with patch.object(
                run_workflow_module,
                "_get_workflow_runtime",
                return_value=(Mock(name="RequirementsInstaller"), run_workflow, RunWorkflowResult),
            ):
                result = await ToolRegistry.execute(
                    "run_workflow",
                    ctx=tool_context,
                    workflow=workflow,
                    inputs={},
                    ensure_requirements=False,
                )
        finally:
            await client.disconnect()
            ToolRegistry.unregister(tool_name)
            ToolRegistry._enabled_defaults.pop(tool_name, None)

        assert result.success is True
        assert "owner-loop-ok" in (result.output or "")
        assert observed["action"] == "call_tool"
        assert observed["payload"] == {
            "name": "demo_tool",
            "arguments": {"value": "demo"},
        }
