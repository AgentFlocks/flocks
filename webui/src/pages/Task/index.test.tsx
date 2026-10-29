import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import TaskPage from './index';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock('@/hooks/useTasks', () => ({
  useTaskDashboard: () => ({ counts: { queued: 2, scheduled_active: 1 }, refetch: vi.fn() }),
  useTaskSystemNotice: () => ({ notice: null }),
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
