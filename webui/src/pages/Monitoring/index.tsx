import { useState, useEffect, useCallback, useRef } from 'react';
import { IconChartBar, IconActivity, IconClock, IconAlertCircle, IconTrendingUp, IconBolt } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { monitoringAPI, SystemStatus, MetricsSnapshot, PerformanceData } from '@/api/monitoring';

export default function MonitoringPage() {
  const { t } = useTranslation('monitoring');
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [metrics, setMetrics] = useState<MetricsSnapshot | null>(null);
  const [llmPerformance, setLlmPerformance] = useState<PerformanceData[]>([]);
  const [toolPerformance, setToolPerformance] = useState<PerformanceData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const latestRequestIdRef = useRef(0);
  const inFlightRef = useRef<Promise<void> | null>(null);
  const inFlightRequestIdRef = useRef<number | null>(null);
  const mountedRef = useRef(false);

  const fetchData = useCallback((): Promise<void> => {
    if (inFlightRef.current) return inFlightRef.current;

    const requestId = ++latestRequestIdRef.current;
    const request = (async () => {
      try {
        setLoading(true);
        setError(null);

        const [statusResult, metricsResult, llmPerfResult, toolPerfResult] = await Promise.allSettled([
          monitoringAPI.getStatus(),
          monitoringAPI.getMetrics(),
          monitoringAPI.getLLMPerformance(),
          monitoringAPI.getToolPerformance(),
        ]);

        const failedResult = [statusResult, metricsResult, llmPerfResult, toolPerfResult]
          .find((result) => result.status === 'rejected');
        if (failedResult?.status === 'rejected') {
          throw failedResult.reason;
        }
        if (
          statusResult.status !== 'fulfilled'
          || metricsResult.status !== 'fulfilled'
          || llmPerfResult.status !== 'fulfilled'
          || toolPerfResult.status !== 'fulfilled'
        ) return;

        if (!mountedRef.current || latestRequestIdRef.current !== requestId) return;
        setStatus(statusResult.value.data);
        setMetrics(metricsResult.value.data);
        setLlmPerformance(llmPerfResult.value.data);
        setToolPerformance(toolPerfResult.value.data);
      } catch (err: any) {
        if (mountedRef.current && latestRequestIdRef.current === requestId) {
          setError(err.message);
        }
      } finally {
        if (mountedRef.current && latestRequestIdRef.current === requestId) {
          setLoading(false);
        }
        if (inFlightRequestIdRef.current === requestId) {
          inFlightRef.current = null;
          inFlightRequestIdRef.current = null;
        }
      }
    })();

    inFlightRef.current = request;
    inFlightRequestIdRef.current = requestId;
    return request;
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timerId: number | undefined;
    mountedRef.current = true;

    const poll = async () => {
      await fetchData();
      if (!cancelled) {
        timerId = window.setTimeout(poll, 5000);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      mountedRef.current = false;
      if (timerId !== undefined) {
        window.clearTimeout(timerId);
      }
    };
  }, [fetchData]);

  if (loading && !status) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner delayMs={180} />
      </div>
    );
  }

  if (error && !status) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-danger mb-4">{error}</p>
          <button
            onClick={fetchData}
            className="px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover"
          >
            {t('common:button.retry')}
          </button>
        </div>
      </div>
    );
  }

  const formatUptime = (seconds: number) => {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);

    if (days > 0) return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  };

  const formatRate = (value: number | null | undefined) => (
    value == null ? t('unavailable') : `${value.toFixed(1)}/min`
  );

  return (
    <div className="h-full flex flex-col overflow-y-auto">
      <PageHeader
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<IconChartBar aria-hidden="true" className="w-8 h-8" />}
      />

      {/* System Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-6">
        <div className="bg-surface rounded-card border border-line p-4">
          <div className="flex items-center justify-between mb-2">
            <IconActivity aria-hidden="true" className="w-8 h-8 text-success" />
            <span
              className={`px-2 py-1 rounded-pill text-xs font-medium ${
                status?.status === 'healthy'
                  ? 'bg-success-soft text-success'
                  : status?.status === 'degraded'
                  ? 'bg-warning-soft text-warning'
                  : 'bg-danger-soft text-danger'
              }`}
            >
              {status?.status === 'healthy' && t('status.healthy')}
              {status?.status === 'degraded' && t('status.degraded')}
              {status?.status === 'down' && t('status.down')}
            </span>
          </div>
          <div className="text-3xl tabular-nums font-bold text-fg">{t('systemStatus')}</div>
          <div className="text-sm text-fg-secondary mt-1">
            {t('uptime')}: {status ? formatUptime(status.uptime) : '-'}
          </div>
        </div>

        <div className="bg-surface rounded-card border border-line p-4">
          <div className="flex items-center justify-between mb-2">
            <IconActivity aria-hidden="true" className="w-8 h-8 text-danger" />
          </div>
          <div className="text-3xl tabular-nums font-bold text-fg">
            {status?.activeSessions ?? t('unavailable')}
          </div>
          <div className="text-sm text-fg-secondary mt-1">{t('activeSessions')}</div>
        </div>

        <div className="bg-surface rounded-card border border-line p-4">
          <div className="flex items-center justify-between mb-2">
            <IconBolt aria-hidden="true" className="w-8 h-8 text-ai" />
          </div>
          <div className="text-3xl tabular-nums font-bold text-fg">
            {status?.activeAgents ?? t('unavailable')}
          </div>
          <div className="text-sm text-fg-secondary mt-1">{t('activeAgents')}</div>
        </div>

        <div className="bg-surface rounded-card border border-line p-4">
          <div className="flex items-center justify-between mb-2">
            <IconTrendingUp aria-hidden="true" className="w-8 h-8 text-fg-muted" />
          </div>
          <div className="text-3xl tabular-nums font-bold text-fg">
            {metrics?.messageRate == null ? t('unavailable') : metrics.messageRate.toFixed(1)}
          </div>
          <div className="text-sm text-fg-secondary mt-1">{t('messagesPerMin')}</div>
        </div>
      </div>

      {/* Real-time Metrics */}
      {metrics && (
        <div className="bg-surface rounded-card border border-line p-4 mb-6">
          <h3 className="text-title-card font-semibold text-fg mb-4">{t('realTimeMetrics')}</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-6 gap-6">
            <div>
              <div className="text-sm text-fg-secondary mb-1">{t('metrics.messageRate')}</div>
              <div className="text-3xl tabular-nums font-bold text-fg">
                {formatRate(metrics.messageRate)}
              </div>
            </div>
            <div>
              <div className="text-sm text-fg-secondary mb-1">{t('metrics.toolCallRate')}</div>
              <div className="text-3xl tabular-nums font-bold text-fg">
                {formatRate(metrics.toolCallRate)}
              </div>
            </div>
            <div>
              <div className="text-sm text-fg-secondary mb-1">{t('metrics.errorRate')}</div>
              <div className="text-3xl tabular-nums font-bold text-danger">
                {formatRate(metrics.errorRate)}
              </div>
            </div>
            <div>
              <div className="text-sm text-fg-secondary mb-1">{t('metrics.toolParseFailureRate')}</div>
              <div className="text-3xl tabular-nums font-bold text-danger">
                {formatRate(metrics.toolParseFailureRate)}
              </div>
            </div>
            <div>
              <div className="text-sm text-fg-secondary mb-1">{t('metrics.avgResponse')}</div>
              <div className="text-3xl tabular-nums font-bold text-fg">
                {metrics.avgResponseTime == null ? t('unavailable') : `${metrics.avgResponseTime.toFixed(0)}ms`}
              </div>
            </div>
            <div>
              <div className="text-sm text-fg-secondary mb-1">{t('metrics.activeRequests')}</div>
              <div className="text-3xl tabular-nums font-bold text-fg">
                {metrics.activeRequests ?? t('unavailable')}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Performance Data */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        {/* LLM Performance */}
        <div className="bg-surface rounded-card border border-line p-4">
          <h3 className="text-title-card font-semibold text-fg mb-4 flex items-center gap-2">
            <IconClock aria-hidden="true" className="w-5 h-5" />
            {t('llmPerformance')}
          </h3>
          {llmPerformance.length === 0 ? (
            <p className="text-sm text-fg-secondary">{t('noData')}</p>
          ) : (
            <div className="space-y-3">
              {llmPerformance.slice(0, 10).map((perf, index) => (
                <div key={index} className="flex items-center justify-between">
                  <div className="flex-1">
                    <div className="text-sm font-medium text-fg">{perf.name}</div>
                    <div className="text-xs text-fg-secondary">
                      {perf.count} {t('calls')} · {perf.errors} {t('errors')}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-bold text-fg">
                      {perf.avgDuration.toFixed(0)}ms
                    </div>
                    {perf.p95 && (
                      <div className="text-xs text-fg-secondary">
                        P95: {perf.p95.toFixed(0)}ms
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Tool Performance */}
        <div className="bg-surface rounded-card border border-line p-4">
          <h3 className="text-title-card font-semibold text-fg mb-4 flex items-center gap-2">
            <IconBolt aria-hidden="true" className="w-5 h-5" />
            {t('toolPerformance')}
          </h3>
          {toolPerformance.length === 0 ? (
            <p className="text-sm text-fg-secondary">{t('noData')}</p>
          ) : (
            <div className="space-y-3">
              {toolPerformance.slice(0, 10).map((perf, index) => (
                <div key={index} className="flex items-center justify-between">
                  <div className="flex-1">
                    <div className="text-sm font-medium text-fg">{perf.name}</div>
                    <div className="text-xs text-fg-secondary">
                      {perf.count} {t('calls')} · {perf.errors} {t('errors')}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-bold text-fg">
                      {perf.avgDuration.toFixed(0)}ms
                    </div>
                    {perf.p95 && (
                      <div className="text-xs text-fg-secondary">
                        P95: {perf.p95.toFixed(0)}ms
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Chart Placeholder */}
      <div className="bg-surface rounded-card border border-line p-4">
        <h3 className="text-title-card font-semibold text-fg mb-4">{t('performanceTrend')}</h3>
        <div className="h-64 flex items-center justify-center border-2 border-dashed border-line-control rounded-control">
          <div className="text-center text-fg-muted">
            <IconChartBar aria-hidden="true" className="w-8 h-8 mx-auto mb-2 text-fg-muted" />
            <p>{t('chartHint')}</p>
            <p className="text-sm mt-1">{t('chartInstall')}</p>
          </div>
        </div>
      </div>
    </div>
  );
}
