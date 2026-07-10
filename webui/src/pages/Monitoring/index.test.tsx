import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const monitoringAPI = vi.hoisted(() => ({
  getStatus: vi.fn(),
  getMetrics: vi.fn(),
  getLLMPerformance: vi.fn(),
  getToolPerformance: vi.fn(),
}));

vi.mock('@/api/monitoring', () => ({ monitoringAPI }));
vi.mock('@/components/common/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));
vi.mock('@/components/common/LoadingSpinner', () => ({ default: () => <div>loading</div> }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => ({
      pageTitle: 'Monitoring',
      unavailable: 'No data',
      noData: 'No data',
    }[key] ?? key),
  }),
}));

import MonitoringPage from './index';

describe('MonitoringPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    monitoringAPI.getStatus.mockResolvedValue({
      data: {
        status: 'healthy',
        uptime: 60,
        activeSessions: null,
        activeAgents: null,
        mcpServers: {},
        timestamp: 1,
      },
    });
    monitoringAPI.getMetrics.mockResolvedValue({
      data: {
        timestamp: 1,
        messageRate: null,
        toolCallRate: null,
        errorRate: null,
        avgResponseTime: null,
        activeRequests: null,
      },
    });
    monitoringAPI.getLLMPerformance.mockResolvedValue({ data: [] });
    monitoringAPI.getToolPerformance.mockResolvedValue({ data: [] });
  });

  it('shows unavailable metrics instead of presenting fabricated zero rates', async () => {
    render(<MonitoringPage />);

    await waitFor(() => expect(screen.getByText('Monitoring')).toBeInTheDocument());
    expect(screen.getAllByText('No data').length).toBeGreaterThanOrEqual(5);
    expect(screen.queryByText('0.0/min')).not.toBeInTheDocument();
  });
});
