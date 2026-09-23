import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMonitorNavigation } from './useMonitorNavigation';
const mocks = vi.hoisted(() => ({ navigate: vi.fn(), state: vi.fn(), subscription: null as any, location: { pathname: '/hub', search: '' } }));
vi.mock('react-router-dom', () => ({ useNavigate: () => mocks.navigate, useLocation: () => mocks.location }));
vi.mock('@/api/securityMonitoring', () => ({ monitoringApi: { state: mocks.state } }));
vi.mock('./useSSE', () => ({ useSSE: (options: any) => { mocks.subscription = options; } }));
const state = (n: number | null) => ({ data: { owner: 'owner', latest: n === null ? null : { sequence: n, session_id: `session-${n}`, message_id: 'message', business_date: '2026-09-23' } } });
describe('monitor navigation', () => {
  beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear(); mocks.location = { pathname: '/hub', search: '' }; });
  it('waits for confirmed start', async () => {
    mocks.state.mockResolvedValue(state(null)); renderHook(() => useMonitorNavigation('owner'));
    await waitFor(() => expect(mocks.state).toHaveBeenCalled()); expect(mocks.navigate).not.toHaveBeenCalled();
    mocks.state.mockResolvedValue(state(1)); await act(async () => mocks.subscription.onEvent({ type: 'monitor.execution.started' }));
    expect(mocks.navigate).toHaveBeenCalledWith('/sessions?session=session-1', { replace: true });
  });
  it('deduplicates stale snapshots and restores on reconnect', async () => {
    mocks.state.mockResolvedValue(state(3)); renderHook(() => useMonitorNavigation('owner'));
    await waitFor(() => expect(mocks.navigate).toHaveBeenCalledTimes(1));
    for (const n of [2, 3]) { mocks.state.mockResolvedValue(state(n)); await act(async () => mocks.subscription.onReconnect()); }
    expect(mocks.navigate).toHaveBeenCalledTimes(1);
    mocks.state.mockResolvedValue(state(4)); await act(async () => mocks.subscription.onReconnect()); expect(mocks.navigate).toHaveBeenCalledTimes(2);
  });
  it('preserves current session and does not repeat after refresh', async () => {
    mocks.location = { pathname: '/sessions', search: '?session=session-1' }; mocks.state.mockResolvedValue(state(1));
    const hook = renderHook(() => useMonitorNavigation('owner'));
    await waitFor(() => expect(sessionStorage.getItem('flocks:monitor-nav:owner')).toBe('1')); expect(mocks.navigate).not.toHaveBeenCalled();
    hook.unmount(); mocks.location = { pathname: '/hub', search: '' }; renderHook(() => useMonitorNavigation('owner'));
    await act(async () => mocks.subscription.onReconnect()); expect(mocks.navigate).not.toHaveBeenCalled();
  });
  it('preserves native canonical session routes', async () => {
    mocks.location={pathname:'/sessions/session-1',search:'?focusMessage=original'};mocks.state.mockResolvedValue(state(1));
    renderHook(() => useMonitorNavigation('owner'));
    await waitFor(() => expect(sessionStorage.getItem('flocks:monitor-nav:owner')).toBe('1'));
    expect(mocks.navigate).not.toHaveBeenCalled();
  });
  it('rejects a delayed snapshot from another account', async () => {
    mocks.state.mockResolvedValue({data:{...state(1).data,owner:'other'}});renderHook(() => useMonitorNavigation('owner'));
    await act(async () => mocks.subscription.onReconnect()); expect(mocks.navigate).not.toHaveBeenCalled();
  });
  it('does not navigate on unauthorized state', async () => {
    mocks.state.mockRejectedValue(new Error('403')); renderHook(() => useMonitorNavigation('owner'));
    await act(async () => mocks.subscription.onReconnect()); expect(mocks.navigate).not.toHaveBeenCalled();
  });
});
