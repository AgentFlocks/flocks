import { StrictMode } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

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
        toolParseFailureRate: 2.5,
        avgResponseTime: null,
        activeRequests: null,
      },
    });
    monitoringAPI.getLLMPerformance.mockResolvedValue({ data: [] });
    monitoringAPI.getToolPerformance.mockResolvedValue({ data: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows unavailable metrics instead of presenting fabricated zero rates', async () => {
    render(<MonitoringPage />);

    await waitFor(() => expect(screen.getByText('Monitoring')).toBeInTheDocument());
    expect(screen.getAllByText('No data').length).toBeGreaterThanOrEqual(5);
    expect(screen.getByText('2.5/min')).toBeInTheDocument();
    expect(screen.queryByText('0.0/min')).not.toBeInTheDocument();
  });

  it('waits for the current polling batch to finish before scheduling another', async () => {
    vi.useFakeTimers();
    const never = new Promise(() => {});
    monitoringAPI.getStatus.mockReturnValue(never);
    monitoringAPI.getMetrics.mockReturnValue(never);
    monitoringAPI.getLLMPerformance.mockReturnValue(never);
    monitoringAPI.getToolPerformance.mockReturnValue(never);

    const { unmount } = render(<MonitoringPage />);
    expect(monitoringAPI.getStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(monitoringAPI.getStatus).toHaveBeenCalledTimes(1);
    expect(monitoringAPI.getMetrics).toHaveBeenCalledTimes(1);
    expect(monitoringAPI.getLLMPerformance).toHaveBeenCalledTimes(1);
    expect(monitoringAPI.getToolPerformance).toHaveBeenCalledTimes(1);
    unmount();
  });

  it('does not start another polling batch while slow requests remain after a fast failure', async () => {
    vi.useFakeTimers();
    let resolveMetrics: (value: unknown) => void = () => {};
    let resolveLLMPerformance: (value: unknown) => void = () => {};
    let resolveToolPerformance: (value: unknown) => void = () => {};
    monitoringAPI.getStatus.mockRejectedValueOnce(new Error('status failed'));
    monitoringAPI.getMetrics.mockImplementationOnce(() => new Promise((resolve) => {
      resolveMetrics = resolve;
    }));
    monitoringAPI.getLLMPerformance.mockImplementationOnce(() => new Promise((resolve) => {
      resolveLLMPerformance = resolve;
    }));
    monitoringAPI.getToolPerformance.mockImplementationOnce(() => new Promise((resolve) => {
      resolveToolPerformance = resolve;
    }));

    const { unmount } = render(<MonitoringPage />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    expect(monitoringAPI.getStatus).toHaveBeenCalledTimes(1);
    expect(monitoringAPI.getMetrics).toHaveBeenCalledTimes(1);
    expect(monitoringAPI.getLLMPerformance).toHaveBeenCalledTimes(1);
    expect(monitoringAPI.getToolPerformance).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveMetrics({ data: { timestamp: 1 } });
      resolveLLMPerformance({ data: [] });
      resolveToolPerformance({ data: [] });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_999);
    });
    expect(monitoringAPI.getStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(monitoringAPI.getStatus).toHaveBeenCalledTimes(2);
    unmount();
  });

  it('reuses the in-flight polling batch during StrictMode effect replay', async () => {
    const statusResolvers: Array<(value: unknown) => void> = [];
    const metricsResolvers: Array<(value: unknown) => void> = [];
    const llmResolvers: Array<(value: unknown) => void> = [];
    const toolResolvers: Array<(value: unknown) => void> = [];
    monitoringAPI.getStatus.mockImplementation(() => new Promise((resolve) => statusResolvers.push(resolve)));
    monitoringAPI.getMetrics.mockImplementation(() => new Promise((resolve) => metricsResolvers.push(resolve)));
    monitoringAPI.getLLMPerformance.mockImplementation(() => new Promise((resolve) => llmResolvers.push(resolve)));
    monitoringAPI.getToolPerformance.mockImplementation(() => new Promise((resolve) => toolResolvers.push(resolve)));

    const { unmount } = render(
      <StrictMode>
        <MonitoringPage />
      </StrictMode>,
    );

    expect(monitoringAPI.getStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      statusResolvers[0]({
        data: {
          status: 'healthy',
          uptime: 60,
          activeSessions: 22,
          activeAgents: null,
          mcpServers: {},
          timestamp: 2,
        },
      });
      metricsResolvers[0]({ data: { timestamp: 2 } });
      llmResolvers[0]({ data: [] });
      toolResolvers[0]({ data: [] });
    });
    expect(await screen.findByText('22')).toBeInTheDocument();
    expect(monitoringAPI.getStatus).toHaveBeenCalledTimes(1);
    unmount();
  });
});
