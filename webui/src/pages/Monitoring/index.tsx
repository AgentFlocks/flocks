import { useState, useEffect } from 'react';
import { BarChart3, Activity, Clock, AlertCircle, TrendingUp, Zap } from 'lucide-react';
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

  useEffect(() => {
    fetchData();
    
    // Auto refresh every 5 seconds
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, []);

  const fetchData = async () => {
    try {
      setLoading(true);
      setError(null);
      
      const [statusRes, metricsRes, llmPerfRes, toolPerfRes] = await Promise.all([
        monitoringAPI.getStatus(),
        monitoringAPI.getMetrics(),
        monitoringAPI.getLLMPerformance(),
        monitoringAPI.getToolPerformance(),
      ]);
      
      setStatus(statusRes.data);
      setMetrics(metricsRes.data);
      setLlmPerformance(llmPerfRes.data);
      setToolPerformance(toolPerfRes.data);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  if (loading && !status) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner />
      </div>
    );
  }

  if (error && !status) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-red-600 mb-4">{error}</p>
          <button
            onClick={fetchData}
            className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
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

  return (
    <div className="h-full flex flex-col overflow-y-auto">
      <PageHeader
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<BarChart3 className="w-8 h-8" />}
      />

      {/* System Status Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-6">
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <div className="flex items-center justify-between mb-2">
            <Activity className="w-8 h-8 text-green-600" />
            <span
              className={`px-2 py-1 rounded-full text-xs font-medium ${
                status?.status === 'healthy'
                  ? 'bg-green-100 text-green-800'
                  : status?.status === 'degraded'
                  ? 'bg-yellow-100 text-yellow-800'
                  : 'bg-red-100 text-red-800'
              }`}
            >
              {status?.status === 'healthy' && t('status.healthy')}
              {status?.status === 'degraded' && t('status.degraded')}
              {status?.status === 'down' && t('status.down')}
            </span>
          </div>
          <div className="text-xl font-bold text-gray-900">{t('systemStatus')}</div>
          <div className="text-sm text-gray-600 mt-1">
            {t('uptime')}: {status ? formatUptime(status.uptime) : '-'}
          </div>
        </div>

        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <div className="flex items-center justify-between mb-2">
            <Activity className="w-8 h-8 text-red-600" />
          </div>
          <div className="text-xl font-bold text-gray-900">
            {status?.activeSessions || 0}
          </div>
          <div className="text-sm text-gray-600 mt-1">{t('activeSessions')}</div>
        </div>

        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <div className="flex items-center justify-between mb-2">
            <Zap className="w-8 h-8 text-purple-600" />
          </div>
          <div className="text-xl font-bold text-gray-900">
            {status?.activeAgents || 0}
          </div>
          <div className="text-sm text-gray-600 mt-1">{t('activeAgents')}</div>
        </div>

        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <div className="flex items-center justify-between mb-2">
            <TrendingUp className="w-8 h-8 text-orange-600" />
          </div>
          <div className="text-xl font-bold text-gray-900">
            {metrics?.messageRate.toFixed(1) || '0'}
          </div>
          <div className="text-sm text-gray-600 mt-1">{t('messagesPerMin')}</div>
        </div>
      </div>

      {/* Real-time Metrics */}
      {metrics && (
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 mb-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">{t('realTimeMetrics')}</h3>
          <div className="grid grid-cols-1 md:grid-cols-5 gap-6">
            <div>
              <div className="text-sm text-gray-600 mb-1">{t('metrics.messageRate')}</div>
              <div className="text-xl font-bold text-gray-900">
                {metrics.messageRate.toFixed(1)}/min
              </div>
            </div>
            <div>
              <div className="text-sm text-gray-600 mb-1">{t('metrics.toolCallRate')}</div>
              <div className="text-xl font-bold text-gray-900">
                {metrics.toolCallRate.toFixed(1)}/min
              </div>
            </div>
            <div>
              <div className="text-sm text-gray-600 mb-1">{t('metrics.errorRate')}</div>
              <div className="text-xl font-bold text-red-600">
                {metrics.errorRate.toFixed(1)}/min
              </div>
            </div>
            <div>
              <div className="text-sm text-gray-600 mb-1">{t('metrics.avgResponse')}</div>
              <div className="text-xl font-bold text-gray-900">
                {metrics.avgResponseTime.toFixed(0)}ms
              </div>
            </div>
            <div>
              <div className="text-sm text-gray-600 mb-1">{t('metrics.activeRequests')}</div>
              <div className="text-xl font-bold text-gray-900">
                {metrics.activeRequests}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Performance Data */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        {/* LLM Performance */}
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
            <Clock className="w-5 h-5" />
            {t('llmPerformance')}
          </h3>
          {llmPerformance.length === 0 ? (
            <p className="text-sm text-gray-600">{t('noData')}</p>
          ) : (
            <div className="space-y-3">
              {llmPerformance.slice(0, 10).map((perf, index) => (
                <div key={index} className="flex items-center justify-between">
                  <div className="flex-1">
                    <div className="text-sm font-medium text-gray-900">{perf.name}</div>
                    <div className="text-xs text-gray-600">
                      {perf.count} {t('calls')} · {perf.errors} {t('errors')}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-bold text-gray-900">
                      {perf.avgDuration.toFixed(0)}ms
                    </div>
                    {perf.p95 && (
                      <div className="text-xs text-gray-600">
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
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h3 className="text-lg font-semibold text-gray-900 mb-4 flex items-center gap-2">
            <Zap className="w-5 h-5" />
            {t('toolPerformance')}
          </h3>
          {toolPerformance.length === 0 ? (
            <p className="text-sm text-gray-600">{t('noData')}</p>
          ) : (
            <div className="space-y-3">
              {toolPerformance.slice(0, 10).map((perf, index) => (
                <div key={index} className="flex items-center justify-between">
                  <div className="flex-1">
                    <div className="text-sm font-medium text-gray-900">{perf.name}</div>
                    <div className="text-xs text-gray-600">
                      {perf.count} {t('calls')} · {perf.errors} {t('errors')}
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-bold text-gray-900">
                      {perf.avgDuration.toFixed(0)}ms
                    </div>
                    {perf.p95 && (
                      <div className="text-xs text-gray-600">
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
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">{t('performanceTrend')}</h3>
        <div className="h-64 flex items-center justify-center border-2 border-dashed border-gray-300 rounded-lg">
          <div className="text-center text-gray-500">
            <BarChart3 className="w-12 h-12 mx-auto mb-2 text-gray-400" />
            <p>{t('chartHint')}</p>
            <p className="text-sm mt-1">{t('chartInstall')}</p>
          </div>
        </div>
      </div>
    </div>
  );
}
