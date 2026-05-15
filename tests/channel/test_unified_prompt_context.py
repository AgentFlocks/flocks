"""
Regression tests for the WebUI / IM-channel prompt-context unification.

These cover the three knobs introduced to make Rex see the same system
prompt regardless of whether the user message arrived through the Web UI
or through an IM channel (Feishu / WeCom / DingTalk):

1. ``session.directory`` — channel sessions now follow the same priority
   chain as the WebUI ``Session.create`` route:
   ``ChannelConfig.workspaceDir`` → ``Instance.get_directory()`` →
   ``os.getcwd()``.
2. ``default_agent`` — when the channel config does not specify an agent,
   the dispatcher falls back to ``Agent.default_agent()`` (which honours
   the global ``defaultAgent`` config and finally returns ``rex``) instead
   of the bogus literal ``"default"`` that previously slipped through.
3. ``model`` — the channel dispatcher now resolves the provider/model via
   ``SessionLoop._resolve_model`` and persists it on the user message,
   matching what the WebUI ``_process_session_message`` route does. This
   is what keeps title generation and the ``SystemPrompt.provider``
   template selection consistent on the first turn.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.channel.base import ChatType, InboundMessage
from flocks.channel.inbound import session_binding as binding_mod
from flocks.channel.inbound.dispatcher import (
    InboundDispatcher,
    _resolve_session_model,
)
from flocks.channel.inbound.session_binding import (
    SessionBindingService,
    _resolve_session_directory,
)
from flocks.config.config import ChannelConfig


@pytest.fixture(autouse=True)
def _reset_cwd_warn_flag(monkeypatch):
    """Reset the once-per-process warn flag so each test gets a clean slate."""
    monkeypatch.setattr(binding_mod, "_CWD_FALLBACK_WARNED", False)
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(**overrides) -> InboundMessage:
    base = dict(
        channel_id="wecom",
        account_id="default",
        message_id="m1",
        sender_id="user_a",
        sender_name="Alice",
        chat_id="user_a",
        chat_type=ChatType.DIRECT,
        text="hello",
    )
    base.update(overrides)
    return InboundMessage(**base)


# ---------------------------------------------------------------------------
# 1. session.directory unification
# ---------------------------------------------------------------------------

class TestResolveSessionDirectory:
    def test_explicit_wins(self):
        assert _resolve_session_directory("/srv/proj") == "/srv/proj"

    def test_falls_back_to_instance_directory(self):
        with patch(
            "flocks.project.instance.Instance.get_directory",
            return_value="/workspace/foo",
        ):
            assert _resolve_session_directory(None) == "/workspace/foo"

    def test_falls_back_to_cwd_when_instance_unavailable(self):
        with patch(
            "flocks.project.instance.Instance.get_directory",
            return_value=None,
        ):
            assert _resolve_session_directory(None) == os.getcwd()

    def test_swallows_instance_lookup_errors(self):
        with patch(
            "flocks.project.instance.Instance.get_directory",
            side_effect=RuntimeError("no instance"),
        ):
            assert _resolve_session_directory(None) == os.getcwd()

    def test_cwd_fallback_logs_once_per_process(self):
        """Operators must see the divergence warning, but not on every msg."""
        warn_calls = []

        def _spy(message=None, extra=None):
            warn_calls.append((message, extra))

        with patch(
            "flocks.project.instance.Instance.get_directory",
            return_value=None,
        ), patch.object(binding_mod.log, "warning", side_effect=_spy):
            _resolve_session_directory(None)
            _resolve_session_directory(None)
            _resolve_session_directory(None)

        cwd_warns = [
            (m, e) for (m, e) in warn_calls
            if m == "channel.session_directory.cwd_fallback"
        ]
        assert len(cwd_warns) == 1, (
            f"expected exactly one cwd_fallback warning, got {len(cwd_warns)}"
        )
        assert cwd_warns[0][1] is not None
        assert "workspaceDir" in cwd_warns[0][1]["hint"]

    def test_explicit_value_does_not_trigger_warning(self):
        warn_calls = []

        def _spy(message=None, extra=None):
            warn_calls.append(message)

        with patch.object(binding_mod.log, "warning", side_effect=_spy):
            _resolve_session_directory("/explicit/dir")

        assert "channel.session_directory.cwd_fallback" not in warn_calls

    def test_instance_directory_does_not_trigger_warning(self):
        warn_calls = []

        def _spy(message=None, extra=None):
            warn_calls.append(message)

        with patch(
            "flocks.project.instance.Instance.get_directory",
            return_value="/instance/dir",
        ), patch.object(binding_mod.log, "warning", side_effect=_spy):
            _resolve_session_directory(None)

        assert "channel.session_directory.cwd_fallback" not in warn_calls


class TestContextVarBoundary:
    """Document the runtime contract: ``Instance.get_directory()`` is empty
    inside a bare ``asyncio.create_task`` because the channel gateway
    spawns its message loops outside the HTTP middleware that populates
    the ContextVar. This is exactly why ``ChannelConfig.workspaceDir``
    must be set explicitly to align channel sessions with WebUI sessions.
    """

    @pytest.mark.asyncio
    async def test_instance_is_unset_in_detached_task(self):
        import asyncio
        from flocks.project.instance import Instance

        async def _from_detached_task():
            return Instance.get_directory()

        result = await asyncio.create_task(_from_detached_task())
        assert result is None


class TestChannelConfigWorkspaceDir:
    def test_workspace_dir_default_is_none(self):
        cfg = ChannelConfig()
        assert cfg.workspace_dir is None

    def test_workspace_dir_alias(self):
        cfg = ChannelConfig(workspaceDir="/data/proj")
        assert cfg.workspace_dir == "/data/proj"

    def test_workspace_dir_snake_case(self):
        cfg = ChannelConfig(workspace_dir="/data/proj")
        assert cfg.workspace_dir == "/data/proj"


class TestSessionBindingDirectoryPropagation:
    @pytest.mark.asyncio
    async def test_create_session_uses_explicit_directory(self):
        captured = {}

        class _StubSession:
            id = "ses_1"

        async def _fake_create(**kwargs):
            captured.update(kwargs)
            return _StubSession()

        with patch("flocks.session.session.Session.create", new=_fake_create):
            sid = await SessionBindingService._create_session(
                _msg(),
                default_agent="rex",
                directory="/explicit/dir",
            )

        assert sid == "ses_1"
        assert captured["directory"] == "/explicit/dir"
        assert captured["project_id"] == "channel"
        assert captured["agent"] == "rex"

    @pytest.mark.asyncio
    async def test_create_session_falls_back_to_instance(self):
        captured = {}

        class _StubSession:
            id = "ses_2"

        async def _fake_create(**kwargs):
            captured.update(kwargs)
            return _StubSession()

        with patch("flocks.session.session.Session.create", new=_fake_create), \
             patch(
                 "flocks.project.instance.Instance.get_directory",
                 return_value="/instance/dir",
             ):
            await SessionBindingService._create_session(
                _msg(), default_agent="rex", directory=None,
            )

        assert captured["directory"] == "/instance/dir"


# ---------------------------------------------------------------------------
# 2. default_agent unification
# ---------------------------------------------------------------------------

class TestDispatcherDefaultAgentResolution:
    """The dispatcher logic that turns a missing channel config into the
    same default agent that the WebUI uses."""

    @pytest.mark.asyncio
    async def test_channel_config_default_agent_takes_precedence(self):
        # When the channel config explicitly names an agent, we MUST honour
        # it instead of consulting Agent.default_agent().
        cfg = ChannelConfig(defaultAgent="security_helper")

        with patch(
            "flocks.agent.registry.Agent.default_agent",
            new=AsyncMock(return_value="rex"),
        ) as mock_default:
            chosen = cfg.default_agent or await _call_default_agent_fallback()
            assert chosen == "security_helper"
            mock_default.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_agent_default_agent(self):
        cfg = ChannelConfig()  # no defaultAgent

        with patch(
            "flocks.agent.registry.Agent.default_agent",
            new=AsyncMock(return_value="custom_default"),
        ):
            chosen = cfg.default_agent or await _call_default_agent_fallback()
            assert chosen == "custom_default"

    @pytest.mark.asyncio
    async def test_falls_back_to_rex_when_resolver_raises(self):
        cfg = ChannelConfig()

        with patch(
            "flocks.agent.registry.Agent.default_agent",
            new=AsyncMock(side_effect=ValueError("boom")),
        ):
            chosen = cfg.default_agent or await _call_default_agent_fallback()
            assert chosen == "rex"


async def _call_default_agent_fallback() -> str:
    """Mirror the resolver branch inside InboundDispatcher.dispatch."""
    try:
        from flocks.agent.registry import Agent as _Agent
        return await _Agent.default_agent()
    except Exception:
        return "rex"


# ---------------------------------------------------------------------------
# 3. model unification
# ---------------------------------------------------------------------------

class TestResolveSessionModel:
    @pytest.mark.asyncio
    async def test_returns_provider_model_dict(self):
        fake_session = SimpleNamespace(
            id="ses_x", provider=None, model=None, agent="rex",
            parent_id=None, project_id="channel",
        )

        async def _fake_get(_sid):
            return fake_session

        async def _fake_resolve(_session, _p, _m):
            return ("anthropic", "claude-sonnet-4-5")

        with patch("flocks.session.session.Session.get_by_id", new=_fake_get), \
             patch(
                 "flocks.session.session_loop.SessionLoop._resolve_model",
                 new=_fake_resolve,
             ):
            resolved = await _resolve_session_model("ses_x")

        assert resolved == {
            "providerID": "anthropic",
            "modelID": "claude-sonnet-4-5",
        }

    @pytest.mark.asyncio
    async def test_returns_none_when_session_missing(self):
        async def _missing(_sid):
            return None

        with patch("flocks.session.session.Session.get_by_id", new=_missing):
            assert await _resolve_session_model("ghost") is None

    @pytest.mark.asyncio
    async def test_swallows_resolution_errors(self):
        async def _fake_get(_sid):
            return SimpleNamespace(id="ses_y")

        async def _boom(_session, _p, _m):
            raise RuntimeError("provider not configured")

        with patch("flocks.session.session.Session.get_by_id", new=_fake_get), \
             patch(
                 "flocks.session.session_loop.SessionLoop._resolve_model",
                 new=_boom,
             ):
            assert await _resolve_session_model("ses_y") is None


class TestAppendUserMessagePersistsModel:
    @pytest.mark.asyncio
    async def test_model_is_passed_to_message_create(self):
        captured = {}

        class _StubMessage:
            id = "msg_1"

        async def _fake_create(**kwargs):
            captured.update(kwargs)
            return _StubMessage()

        with patch("flocks.session.message.Message.create", new=_fake_create):
            await InboundDispatcher._append_user_message(
                "ses_1",
                "hi",
                _msg(),
                channel_config=None,
                model={"providerID": "anthropic", "modelID": "claude-sonnet-4-5"},
            )

        assert captured["model"] == {
            "providerID": "anthropic",
            "modelID": "claude-sonnet-4-5",
        }
        assert captured["content"] == "hi"
        # part_metadata must still be propagated (audit fields)
        assert captured["part_metadata"]["source"] == "channel"
        assert captured["part_metadata"]["channel_id"] == "wecom"

    @pytest.mark.asyncio
    async def test_omits_model_when_none(self):
        captured = {}

        class _StubMessage:
            id = "msg_2"

        async def _fake_create(**kwargs):
            captured.update(kwargs)
            return _StubMessage()

        with patch("flocks.session.message.Message.create", new=_fake_create):
            await InboundDispatcher._append_user_message(
                "ses_2", "hi", _msg(), channel_config=None,
            )

        assert "model" not in captured

    @pytest.mark.asyncio
    async def test_agent_is_passed_to_message_create(self):
        # Without this, last_user.agent silently defaults to "rex" inside
        # Message.create (line 713), which then propagates as the wrong
        # parent agent for any subagent task spawned from this turn.
        captured = {}

        class _StubMessage:
            id = "msg_3"

        async def _fake_create(**kwargs):
            captured.update(kwargs)
            return _StubMessage()

        with patch("flocks.session.message.Message.create", new=_fake_create):
            await InboundDispatcher._append_user_message(
                "ses_3", "hi", _msg(), channel_config=None,
                agent="security_helper",
            )

        assert captured["agent"] == "security_helper"

    @pytest.mark.asyncio
    async def test_omits_agent_when_none_or_empty(self):
        captured = {}

        class _StubMessage:
            id = "msg_4"

        async def _fake_create(**kwargs):
            captured.update(kwargs)
            return _StubMessage()

        with patch("flocks.session.message.Message.create", new=_fake_create):
            await InboundDispatcher._append_user_message(
                "ses_4", "hi", _msg(), channel_config=None, agent=None,
            )

        # When omitted, Message.create's own default ("rex") kicks in —
        # we don't override it here so the historical behaviour is kept
        # for sessions whose binding has no agent recorded.
        assert "agent" not in captured


# ---------------------------------------------------------------------------
# 4. Legacy channel_bindings.agent_id one-shot normalisation
# ---------------------------------------------------------------------------

class TestLegacyBindingAgentIdMigration:
    """Older builds wrote the literal ``"default"`` string into
    ``channel_bindings.agent_id`` whenever ``ChannelConfig.defaultAgent``
    was unset. Now that the dispatcher trusts ``binding.agent_id`` for
    both the user-message ``agent`` field and ``SessionLoop.run``'s
    ``agent_name``, those rows would silently propagate a non-existent
    agent name through every inbound message until the runner's
    ``Agent.get(...) or Agent.get("rex")`` fallback masks it. We rewrite
    them to ``NULL`` once at startup so the dispatcher resolves the
    proper default chain instead.
    """

    @pytest.fixture
    async def db(self, tmp_path):
        import aiosqlite

        path = tmp_path / "channel_bindings.db"
        conn = await aiosqlite.connect(str(path))
        conn.row_factory = aiosqlite.Row
        await conn.executescript(binding_mod._DDL)
        try:
            yield conn
        finally:
            await conn.close()

    @staticmethod
    async def _insert(conn, *, agent_id, chat_id, session_id):
        import time as _time

        now = _time.time()
        await conn.execute(
            "INSERT INTO channel_bindings "
            "(id, channel_id, account_id, chat_id, chat_type, thread_id, "
            " session_id, agent_id, created_at, last_message_at) "
            "VALUES (?, 'wecom', 'default', ?, 'direct', NULL, ?, ?, ?, ?)",
            (f"chbind_{session_id}", chat_id, session_id, agent_id, now, now),
        )
        await conn.commit()

    @pytest.mark.asyncio
    async def test_rewrites_default_and_empty_to_null(self, db):
        await self._insert(db, agent_id="default", chat_id="c1", session_id="s1")
        await self._insert(db, agent_id="", chat_id="c2", session_id="s2")
        await self._insert(db, agent_id="rex", chat_id="c3", session_id="s3")
        await self._insert(db, agent_id="security", chat_id="c4", session_id="s4")
        await self._insert(db, agent_id=None, chat_id="c5", session_id="s5")

        with patch.object(binding_mod.log, "info") as info_log:
            await binding_mod._migrate_legacy_binding_agent_ids(db)

        # Targeted rows are now NULL.
        cursor = await db.execute(
            "SELECT chat_id, agent_id FROM channel_bindings ORDER BY chat_id",
        )
        rows = {r["chat_id"]: r["agent_id"] for r in await cursor.fetchall()}
        assert rows == {
            "c1": None,
            "c2": None,
            "c3": "rex",
            "c4": "security",
            "c5": None,
        }

        # Exactly one INFO log with the rewritten count for the legacy rows.
        assert info_log.call_count == 1
        event, payload = info_log.call_args.args
        assert event == "channel.binding.legacy_agent_id_normalised"
        assert payload["rows"] == 2

    @pytest.mark.asyncio
    async def test_is_idempotent_on_second_pass(self, db):
        await self._insert(db, agent_id="default", chat_id="c1", session_id="s1")
        await binding_mod._migrate_legacy_binding_agent_ids(db)

        with patch.object(binding_mod.log, "info") as info_log:
            await binding_mod._migrate_legacy_binding_agent_ids(db)

        # Second pass finds no legacy rows, must not emit a noisy log.
        assert info_log.call_count == 0

    @pytest.mark.asyncio
    async def test_is_noop_when_no_legacy_rows(self, db):
        await self._insert(db, agent_id="rex", chat_id="c1", session_id="s1")
        await self._insert(db, agent_id=None, chat_id="c2", session_id="s2")

        with patch.object(binding_mod.log, "info") as info_log:
            await binding_mod._migrate_legacy_binding_agent_ids(db)

        # Nothing rewritten → no INFO log; existing rows untouched.
        assert info_log.call_count == 0
        cursor = await db.execute(
            "SELECT chat_id, agent_id FROM channel_bindings ORDER BY chat_id",
        )
        rows = {r["chat_id"]: r["agent_id"] for r in await cursor.fetchall()}
        assert rows == {"c1": "rex", "c2": None}

    @pytest.mark.asyncio
    async def test_swallows_db_errors_without_raising(self):
        # Closed connection → execute raises; the migration must catch it
        # and only log a warning, never bubble out into _get_db().
        import aiosqlite

        broken = await aiosqlite.connect(":memory:")
        await broken.close()

        with patch.object(binding_mod.log, "warning") as warn_log:
            # Should not raise.
            await binding_mod._migrate_legacy_binding_agent_ids(broken)

        assert warn_log.call_count == 1
        event, _payload = warn_log.call_args.args
        assert event == "channel.binding.legacy_agent_id_migration_failed"

    @pytest.mark.asyncio
    async def test_get_db_runs_migration_on_init(self, tmp_path, monkeypatch):
        """End-to-end: ``_get_db()`` must invoke the migration after DDL."""
        # Reset module-level singleton state so _get_db() really initialises.
        monkeypatch.setattr(binding_mod, "_db_conn", None)
        monkeypatch.setattr(binding_mod, "_db_ready", False)

        # Point Storage.get_db_path() at a fresh sqlite file.
        from flocks.storage.storage import Storage

        db_path = tmp_path / "flocks.db"
        monkeypatch.setattr(Storage, "get_db_path", staticmethod(lambda: db_path))

        # Pre-create the schema and insert a legacy row, mimicking an upgrade
        # from an older build that already populated the table.
        import aiosqlite

        seed = await aiosqlite.connect(str(db_path))
        await seed.executescript(binding_mod._DDL)
        await seed.execute(
            "INSERT INTO channel_bindings "
            "(id, channel_id, account_id, chat_id, chat_type, thread_id, "
            " session_id, agent_id, created_at, last_message_at) "
            "VALUES ('chbind_legacy', 'wecom', 'default', 'c1', 'direct', "
            "NULL, 's1', 'default', 0, 0)",
        )
        await seed.commit()
        await seed.close()

        try:
            conn = await binding_mod._get_db()
            cursor = await conn.execute(
                "SELECT agent_id FROM channel_bindings WHERE id = 'chbind_legacy'",
            )
            row = await cursor.fetchone()
            assert row["agent_id"] is None
        finally:
            await binding_mod.close_binding_db()
