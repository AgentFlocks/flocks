"""Non-interactive execution policy, inherited by asyncio tasks and worker threads."""
from contextlib import contextmanager
from contextvars import ContextVar

_unattended = ContextVar('flocks_unattended', default=False)


def is_unattended() -> bool:
    return _unattended.get()


@contextmanager
def unattended_scope(enabled: bool = True):
    token = _unattended.set(enabled or is_unattended())
    try:
        yield
    finally:
        _unattended.reset(token)


async def require_interactive(session_id: str | None = None) -> None:
    blocked = is_unattended()
    if not blocked and session_id:
        from flocks.session.session import Session
        session = await Session.get_by_id(session_id)
        blocked = bool(session and (session.metadata or {}).get('interactionMode') == 'unattended')
    if blocked:
        raise PermissionError('Unattended execution cannot request questions or approvals')

_read_only = ContextVar('flocks_read_only_monitor', default=None)
_monitoring_call_scope = ContextVar('flocks_monitoring_call_scope', default=None)
_automatic_mark = ContextVar('flocks_automatic_mark', default=None)


@contextmanager
def monitoring_call_scope(tool: str, device: str, params: dict):
    """Pin a monitoring API call, including after hook input patches."""
    import copy
    token = _monitoring_call_scope.set(_monitoring_call_scope.get() or (tool, device, copy.deepcopy(params)))
    try:
        yield
    finally:
        _monitoring_call_scope.reset(token)


@contextmanager
def automatic_mark_scope(tool: str, device: str, event_id: str, target: int, session_id: str):
    """Internal grant for one persisted, rule-selected incident status write."""
    if type(target) is not int or target not in {10, 40, 60, 70}:
        raise PermissionError('Unsupported automatic status')
    grant = (tool, device, event_id, target, session_id)
    previous = _automatic_mark.get()
    if previous is not None and previous != grant:
        raise PermissionError('Automatic mark scope cannot be widened')
    token = _automatic_mark.set(grant)
    try:
        yield
    finally:
        _automatic_mark.reset(token)


@contextmanager
def monitoring_read_scope(tool: str, devices: list[str]):
    # A child may narrow its own inputs, but cannot replace its parent's scope.
    token = _read_only.set(_read_only.get() or {'tool': tool, 'devices': tuple(devices)})
    try:
        yield
    finally:
        _read_only.reset(token)


async def require_monitor_read(tool: str, params: dict, session_id: str | None = None, *, resolved_device=None):
    confirmed = _monitoring_call_scope.get()
    if confirmed:
        expected_tool, expected_device, expected_params = confirmed
        actual = {key: value for key, value in params.items() if key != 'device_id' and value is not None}
        if (tool != expected_tool or (resolved_device or params.get('device_id')) != expected_device
                or actual != expected_params):
            raise PermissionError('Confirmed monitoring operation cannot change target or parameters')
    policy = _read_only.get()
    if policy is None and session_id:
        from flocks.session.session import Session
        session = await Session.get_by_id(session_id)
        if session and (session.metadata or {}).get('monitorScope'):
            policy = (session.metadata or {}).get('readOnlyTools') or {'tool': '', 'devices': ()}
    if policy is None:
        return
    grant = _automatic_mark.get()
    automatic = (grant is not None and confirmed is not None
                 and grant[0] == tool and grant[1] == (resolved_device or params.get('device_id'))
                 and grant[4] == session_id and params.get('action') == 'update_status'
                 and params.get('uuids') == [grant[2]] and type(params.get('deal_status')) is int
                 and params.get('deal_status') == grant[3])
    if (tool != policy['tool'] or (params.get('action') not in {'list', 'get_entities', 'get_proof'} and not automatic)
            or (resolved_device or params.get('device_id')) not in policy['devices']):
        raise PermissionError('Monitoring execution permits only bound XDR read-only actions')
