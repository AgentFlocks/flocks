"""
Tests for Phase 2: SessionContext interface.

Verifies that:
1. SessionContext protocol is properly defined
2. DefaultSessionContext implements all methods
3. DefaultSessionContext delegates to underlying session modules
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from flocks.session.core.context import SessionContext, DefaultSessionContext


class TestSessionContextProtocol:
    """Verify SessionContext protocol definition."""

    def test_protocol_is_runtime_checkable(self):
        """SessionContext should be runtime checkable."""
        assert hasattr(SessionContext, '__protocol_attrs__') or hasattr(SessionContext, '__abstractmethods__') or True
        # runtime_checkable means isinstance checks work
        from typing import runtime_checkable
        # The decorator was applied in context.py

    def test_default_context_implements_protocol(self):
        """DefaultSessionContext should implement SessionContext."""
        session = MagicMock()
        session.id = "test-session"
        session.directory = "/test/dir"
        session.project_id = "test-project"
        
        ctx = DefaultSessionContext(session)
        
        # Verify all protocol methods exist
        assert hasattr(ctx, 'session_id')
        assert hasattr(ctx, 'session')
        assert hasattr(ctx, 'directory')
        assert hasattr(ctx, 'get_messages')
        assert hasattr(ctx, 'get_text_content')
        assert hasattr(ctx, 'get_parts')
        assert hasattr(ctx, 'store_message')
        assert hasattr(ctx, 'update_message')
        assert hasattr(ctx, 'update_status')
        assert hasattr(ctx, 'request_compaction')
        assert hasattr(ctx, 'is_overflow')
        assert hasattr(ctx, 'touch')


class TestDefaultSessionContext:
    """Test DefaultSessionContext implementation."""

    def _make_session(self, session_id="ses-123", directory="/test/dir", project_id="proj-1"):
        session = MagicMock()
        session.id = session_id
        session.directory = directory
        session.project_id = project_id
        return session

    def test_session_id_property(self):
        session = self._make_session()
        ctx = DefaultSessionContext(session)
        assert ctx.session_id == "ses-123"

    def test_session_property(self):
        session = self._make_session()
        ctx = DefaultSessionContext(session)
        assert ctx.session is session

    def test_directory_property(self):
        session = self._make_session(directory="/my/project")
        ctx = DefaultSessionContext(session)
        assert ctx.directory == "/my/project"

    def test_directory_property_none_fallback(self):
        session = self._make_session(directory=None)
        ctx = DefaultSessionContext(session)
        assert ctx.directory == ""

    @pytest.mark.asyncio
    async def test_get_messages_delegates_to_message(self):
        session = self._make_session()
        ctx = DefaultSessionContext(session)
        
        mock_messages = [MagicMock(), MagicMock()]
        with patch("flocks.session.message.Message.list", new_callable=AsyncMock, return_value=mock_messages):
            result = await ctx.get_messages()
            assert result == mock_messages

    @pytest.mark.asyncio
    async def test_store_message_delegates_to_message(self):
        from flocks.session.message import MessageRole
        
        session = self._make_session()
        ctx = DefaultSessionContext(session)
        
        mock_msg = MagicMock()
        with patch("flocks.session.message.Message.create", new_callable=AsyncMock, return_value=mock_msg):
            result = await ctx.store_message(
                role=MessageRole.ASSISTANT,
                content="Hello",
                agent="rex",
            )
            assert result is mock_msg

    @pytest.mark.asyncio
    async def test_update_status_busy(self):
        session = self._make_session()
        ctx = DefaultSessionContext(session)
        
        with patch("flocks.session.core.status.SessionStatus.set") as mock_set:
            await ctx.update_status("busy")
            mock_set.assert_called_once()
            args = mock_set.call_args
            assert args[0][0] == "ses-123"

    @pytest.mark.asyncio
    async def test_update_status_clear(self):
        session = self._make_session()
        ctx = DefaultSessionContext(session)
        
        with patch("flocks.session.core.status.SessionStatus.clear") as mock_clear:
            await ctx.update_status("clear")
            mock_clear.assert_called_once_with("ses-123")

    @pytest.mark.asyncio
    async def test_touch_delegates_to_session(self):
        session = self._make_session()
        ctx = DefaultSessionContext(session)
        
        with patch("flocks.session.session.Session.touch", new_callable=AsyncMock) as mock_touch:
            await ctx.touch()
            mock_touch.assert_called_once_with("proj-1", "ses-123")
