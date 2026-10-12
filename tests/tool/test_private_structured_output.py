"""Private machine results must never reach generic output files or previews."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from flocks.hooks.pipeline import HookPipeline
from flocks.tool import structured_output, truncation
from flocks.tool.registry import Tool, ToolContext, ToolInfo, ToolRegistry, ToolResult


@pytest.fixture
def registered(monkeypatch, tmp_path):
    state = SimpleNamespace(payload={'proof': 'small'}, error=None, raises=False)

    async def handler(ctx):
        if state.raises:
            raise RuntimeError(state.error)
        return ToolResult(success=state.error is None, output=state.payload, error=state.error,
                          metadata={'raw_header': 'Authorization: secret'},
                          title='secret response', attachments=[{'content': 'secret body'}])

    name = 'private_evidence_fixture'
    tool = Tool(ToolInfo(name=name, description='Synthetic evidence'), handler)
    monkeypatch.setattr(ToolRegistry, '_tools', {name: tool})
    monkeypatch.setattr(ToolRegistry, '_initialized', True)
    monkeypatch.setattr(ToolRegistry, '_sync_configured_enabled_states', lambda: None)
    monkeypatch.setattr(ToolRegistry, '_failure_auto_disable_enabled', AsyncMock(return_value=False))
    monkeypatch.setattr(ToolRegistry, '_reset_failure_state', lambda name: None)
    monkeypatch.setattr('flocks.session.tool_execution.build_session_tool_execution_payload',
                        AsyncMock(return_value={}))
    monkeypatch.setattr(HookPipeline, 'run_tool_before', AsyncMock(return_value=SimpleNamespace(
        output={}, execution_stop_requested=False)))
    state.after = AsyncMock()
    monkeypatch.setattr(HookPipeline, 'run_tool_after', state.after)
    monkeypatch.setattr(truncation, '_OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(truncation, '_last_cleanup_ts', 0)
    state.ctx = ToolContext(session_id='', message_id='fixture')
    state.name = name
    return state


async def test_private_large_packet_never_reaches_truncation_or_files(registered, monkeypatch, tmp_path):
    registered.payload = {'requestHead': 'Authorization: Bearer secret', 'requestBody': 'x' * 200_000}
    capture = structured_output.OutputCapture(registered.name, private=True)
    registered.ctx._output_capture = capture
    truncate = Mock(side_effect=AssertionError('Private evidence reached display truncation'))
    monkeypatch.setattr(truncation, 'truncate_output', truncate)

    result = await ToolRegistry.execute(registered.name, registered.ctx)

    assert result.success and not result.truncated
    assert result.output == structured_output.PRIVATE_OUTPUT
    assert result.metadata == {'private_output': True}
    assert result.title is None and result.attachments is None
    raw = capture.take(result)
    assert raw == registered.payload and raw is not registered.payload
    assert capture.result is None and capture.value is None
    truncate.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('raises', [False, True])
async def test_private_failure_hides_error_body_and_hook_outcome(registered, raises):
    registered.payload = {'requestHead': 'Authorization: Bearer secret'}
    registered.error = 'upstream returned Authorization: Bearer secret'
    registered.raises = raises
    capture = structured_output.OutputCapture(registered.name, private=True)
    registered.ctx._output_capture = capture

    result = await ToolRegistry.execute(registered.name, registered.ctx)

    assert not result.success
    assert result.error == structured_output.PRIVATE_ERROR
    assert 'secret' not in str(result.model_dump())
    assert registered.after.await_args.args[0]['outcome']['error_message'] == structured_output.PRIVATE_ERROR
    with pytest.raises(structured_output.StructuredOutputError, match='tool_failed'):
        capture.take(result)


async def test_private_capture_budget_failure_remains_failure(registered, monkeypatch, tmp_path):
    registered.payload = {'body': 'x' * 1000}
    monkeypatch.setattr(structured_output, 'MAX_BYTES', 100)
    capture = structured_output.OutputCapture(registered.name, private=True)
    registered.ctx._output_capture = capture

    result = await ToolRegistry.execute(registered.name, registered.ctx)

    assert result.success and not result.truncated
    with pytest.raises(structured_output.StructuredOutputError, match='limit'):
        capture.take(result)
    assert not list(tmp_path.iterdir())


async def test_private_pretruncated_result_cannot_become_complete(registered, monkeypatch):
    async def truncated_handler(ctx):
        return ToolResult(success=True, output='secret fragment', truncated=True,
                          metadata={'output_path': '/private/secret'})
    monkeypatch.setattr(ToolRegistry.get(registered.name), 'handler', truncated_handler)
    capture = structured_output.OutputCapture(registered.name, private=True)
    registered.ctx._output_capture = capture

    result = await ToolRegistry.execute(registered.name, registered.ctx)

    assert result.success and result.truncated
    assert result.output == structured_output.PRIVATE_OUTPUT
    assert 'output_path' not in result.metadata
    with pytest.raises(structured_output.StructuredOutputError, match='already_truncated'):
        capture.take(result)


@pytest.mark.parametrize('with_capture', [False, True])
async def test_default_display_truncation_and_full_capture_are_compatible(registered, with_capture, tmp_path):
    registered.payload = {'proof': 'ordinary_data' * 20_000}
    capture = structured_output.OutputCapture(registered.name)
    if with_capture:
        registered.ctx._output_capture = capture

    result = await ToolRegistry.execute(registered.name, registered.ctx)

    assert result.success and result.truncated
    saved = list(tmp_path.iterdir())
    assert len(saved) == 1 and 'ordinary_data' in saved[0].read_text()
    assert result.metadata['output_path'] == str(saved[0])
    if with_capture:
        assert capture.take(result) == registered.payload


async def test_private_capture_cannot_hide_a_different_tools_output(registered):
    capture = structured_output.OutputCapture('other_tool', private=True)
    registered.ctx._output_capture = capture

    result = await ToolRegistry.execute(registered.name, registered.ctx)

    assert result.success and 'small' in result.output
    assert 'private_output' not in result.metadata
    with pytest.raises(structured_output.StructuredOutputError, match='unavailable'):
        capture.take(result)
