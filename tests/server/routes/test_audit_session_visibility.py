from types import SimpleNamespace

import pytest

from flocks.server.routes.session import _is_hidden_from_session_manager


@pytest.mark.parametrize('agent,metadata', [
    ('code-security', {}),  # Existing audit parent sessions predate visibility flags.
    ('threat_modeler', {'code_security_scan_id': 'scan-1'}),
    ('rex', {'session_scope': 'code-security'}),
    ('rex', {'hideFromSessionManager': True}),
])
def test_audit_sessions_are_hidden_from_workbench(agent, metadata):
    assert _is_hidden_from_session_manager(SimpleNamespace(agent=agent, metadata=metadata))


@pytest.mark.parametrize('metadata', [None, {}, {'code_security_scan_id': ''}])
def test_ordinary_sessions_remain_visible(metadata):
    assert not _is_hidden_from_session_manager(SimpleNamespace(agent='rex', metadata=metadata))


def test_audit_reader_is_listed_including_legacy_hidden_metadata():
    assert not _is_hidden_from_session_manager(SimpleNamespace(agent='code-security-reader', metadata={'code_security_chat_scan_id': 'scan-1', 'session_scope': 'code-security', 'hideFromSessionManager': True}))
