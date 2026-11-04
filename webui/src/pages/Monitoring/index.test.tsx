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
import { appendTrendPoint, TREND_MAX_POINTS, TREND_WINDOW_MS, type TrendPoint } from './PerformanceTrend';

function snapshot(timestamp: number, toolCallRate: number | null) {
  return {
    timestamp,
    messageRate: null,
    toolCallRate,
    errorRate: null,
    toolParseFailureRate: 0.5,
    avgResponseTime: null,
    activeRequests: null,
  };
}

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

  it('shows a sampled trend instead of the development placeholder', async () => {
    vi.useFakeTimers();
    let timestamp = 1_000;
    monitoringAPI.getMetrics.mockImplementation(async () => ({ data: snapshot((timestamp += 5_000), 3) }));

    const { unmount } = render(<MonitoringPage />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText('performanceTrend')).toBeInTheDocument();
    expect(screen.queryByText(/recharts/)).not.toBeInTheDocument();
    expect(screen.getByText('trendCollecting')).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.getByRole('img', { name: 'trendAriaLabel' })).toBeInTheDocument();
    expect(screen.queryByText('trendCollecting')).not.toBeInTheDocument();
    unmount();
  });

  it('keeps sampling through the first rate window, then says the metrics are unavailable', async () => {
    vi.useFakeTimers();
    let timestamp = 1_000;
    // The server's clock: each poll here stands for a minute of real time.
    monitoringAPI.getMetrics.mockImplementation(async () => ({
      data: { ...snapshot((timestamp += 60_000), null), toolParseFailureRate: null },
    }));

    const { unmount } = render(<MonitoringPage />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.getByText('trendCollecting')).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.getByText('trendUnavailable')).toBeInTheDocument();
    expect(screen.queryByRole('img', { name: 'trendAriaLabel' })).not.toBeInTheDocument();
    unmount();
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

describe('appendTrendPoint', () => {
  it('ignores repeated or older samples and keeps the last ten minutes', () => {
    let points: TrendPoint[] = appendTrendPoint([], snapshot(10, 1));
    expect(appendTrendPoint(points, snapshot(10, 2))).toBe(points);
    expect(appendTrendPoint(points, snapshot(5, 2))).toBe(points);
    for (let at = 11; at < 200; at += 1) points = appendTrendPoint(points, snapshot(at, at));
    expect(points).toHaveLength(TREND_MAX_POINTS);
    expect(points[0].timestamp).toBe(200 - TREND_MAX_POINTS);
    expect(points[points.length - 1]).toEqual({ timestamp: 199, toolCallRate: 199, toolParseFailureRate: 0.5 });
  });
});

describe('appendTrendPoint window', () => {
  it('drops samples older than ten minutes even when the tab polled slowly', () => {
    let points: TrendPoint[] = [];
    // A background tab: one sample a minute for an hour.
    for (let minute = 0; minute <= 60; minute += 1) {
      points = appendTrendPoint(points, snapshot(minute * 60_000, minute));
    }
    expect(points[points.length - 1].timestamp - points[0].timestamp).toBeLessThanOrEqual(TREND_WINDOW_MS);
    expect(points.map((point) => point.toolCallRate)).toEqual([50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60]);
  });
});
