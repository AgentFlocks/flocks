import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import TaskPage from './index';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const healthState = vi.hoisted(() => ({ health: { status: 'healthy', pending_results: 0 } as { status: string; pending_results: number }, error: null as string | null }));

vi.mock('@/hooks/useTasks', () => ({
  useTaskDashboard: () => ({ counts: { queued: 2, scheduled_active: 1 }, refetch: vi.fn() }),
  useTaskSystemNotice: () => ({ notice: null }),
  useTaskSystemHealth: () => healthState,
}));

vi.mock('./components', () => ({ DashboardCards: () => <div>dashboard</div> }));
vi.mock('./QueuedSection', () => ({ default: () => <div>queued-section</div> }));
vi.mock('./ScheduledSection', () => ({ default: () => <div>scheduled-section</div> }));
vi.mock('./ServicesSection', () => ({ default: () => <div>services-section</div> }));
vi.mock('./TaskSheet', () => ({ default: () => null }));

describe('TaskPage tabs', () => {
  it('shows the queue first and switches sections from the tab strip', async () => {
    const { default: userEvent } = await import('@testing-library/user-event');
    const user = userEvent.setup();
    render(<TaskPage />);
    expect(screen.getByText('queued-section')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /tabs\.scheduled/ }));
    expect(screen.getByText('scheduled-section')).toBeInTheDocument();
    expect(screen.queryByText('queued-section')).not.toBeInTheDocument();
  });
});


describe('TaskPage health', () => {
  it('reports storage degradation and pending results, then clears after recovery', () => {
    healthState.health = { status: 'degraded', pending_results: 2 };
    const view = render(<TaskPage />);
    expect(screen.getByRole('status')).toHaveTextContent('health.degraded');
    expect(screen.getByRole('status')).toHaveTextContent('health.pendingResults');
    healthState.health = { status: 'healthy', pending_results: 0 };
    view.rerender(<TaskPage />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
  it('does not display stale healthy state when the health request fails', () => {
    healthState.error = 'offline';
    const view = render(<TaskPage />);
    expect(screen.getByRole('status')).toHaveTextContent('health.unknown');
    healthState.error = null;
    view.unmount();
  });
});
