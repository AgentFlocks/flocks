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
  it('窄屏下页签整个换行，页签文案不逐字换行', () => {
    render(<TaskPage />);

    const tab = screen.getByRole('button', { name: /tabs\.queued/ });
    const bar = tab.parentElement as HTMLElement;
    // 375px 时三个页签放不下：w-fit 的容器被压缩后「任务队列」会逐字竖排；
    // 改为 flex-wrap + whitespace-nowrap 让放不下的页签整个换到下一行
    expect(bar.className).toContain('flex-wrap');
    expect(bar.className).toContain('whitespace-nowrap');
    expect(bar.className).toContain('max-w-full');
  });
});
