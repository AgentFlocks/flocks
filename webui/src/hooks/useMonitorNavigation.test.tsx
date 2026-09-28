import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useMonitorNavigation } from './useMonitorNavigation';
const mocks = vi.hoisted(() => ({ navigate: vi.fn(), state: vi.fn(), subscribe: vi.fn() }));
vi.mock('react-router-dom', () => ({ useNavigate: () => mocks.navigate, useLocation: () => ({ pathname: '/soc', search: '' }) }));
vi.mock('@/api/securityMonitoring', () => ({ monitoringApi: { state: mocks.state } }));
vi.mock('./useSSE', () => ({ useSSE: mocks.subscribe }));
describe('background monitoring never takes over navigation', () => {
  afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });
  it.each(['owner', undefined])('does not subscribe or poll for redirects on login (%s)', owner => {
    vi.useFakeTimers();
    const { rerender, unmount } = renderHook(({ id }) => useMonitorNavigation(id), { initialProps: { id: owner } });
    rerender({ id: 'another-owner' });
    act(() => vi.advanceTimersByTime(600_000));
    unmount();
    expect(mocks.navigate).not.toHaveBeenCalled();
    expect(mocks.state).not.toHaveBeenCalled();
    expect(mocks.subscribe).not.toHaveBeenCalled();
  });
});
