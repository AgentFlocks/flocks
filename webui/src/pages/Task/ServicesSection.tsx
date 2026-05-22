import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Globe, StopCircle, Loader2, RefreshCw, ExternalLink } from 'lucide-react';
import { workflowAPI, WorkflowService } from '@/api/workflow';
import { useToast } from '@/components/common/Toast';
import EmptyState from '@/components/common/EmptyState';
import CopyButton from '@/components/common/CopyButton';
import WorkflowStatusBadge from '@/components/common/WorkflowStatusBadge';
import i18n from '@/i18n';

function ServiceCard({
  service,
  onRestart,
  onStop,
  restarting,
  stopping,
}: {
  service: WorkflowService;
  onRestart: () => void;
  onStop: () => void;
  restarting: boolean;
  stopping: boolean;
}) {
  const { t } = useTranslation('task');
  const [keyVisible, setKeyVisible] = useState(false);
  const maskedKey = keyVisible
    ? service.apiKey
    : `${service.apiKey.slice(0, 6)}${'•'.repeat(Math.max(0, service.apiKey.length - 12))}${service.apiKey.slice(-6)}`;

  const locale = i18n.language ?? 'zh-CN';
  const publishedDate = new Date(service.publishedAt).toLocaleString(locale, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
  const isRunning = service.status === 'running';
  const canRestart = service.status !== 'publishing';
  const driverLabel = service.driver === 'docker'
    ? t('services.driverDocker')
    : t('services.driverLocal');

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center flex-shrink-0">
            <Globe className="w-4 h-4 text-slate-600" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-gray-900 truncate">{service.workflowName}</p>
            <p className="text-xs text-gray-400 mt-0.5">{t('services.publishedAt', { date: publishedDate })}</p>
          </div>
        </div>
        <WorkflowStatusBadge status={service.status} variant="pill" />
      </div>

      <div className="flex items-center justify-between gap-2 rounded-lg bg-gray-50 border border-gray-200 px-3 py-2">
        <span className="text-xs text-gray-500">{t('services.serviceDriver')}</span>
        <span className="text-xs font-medium text-gray-700">{driverLabel}</span>
      </div>

      {/* Invoke URL */}
      <div>
        <label className="block text-xs text-gray-400 mb-1">Invoke URL</label>
        <div className="flex items-center gap-1 bg-gray-50 border border-gray-200 rounded-lg px-2.5 py-2">
          <span className="text-xs font-mono text-gray-700 flex-1 truncate">{service.invokeUrl}</span>
          <a
            href={service.invokeUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="p-1 rounded hover:bg-gray-200 text-gray-400 hover:text-gray-600 transition-colors flex-shrink-0"
            title={t('services.openInNewTab')}
          >
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
          <CopyButton text={service.invokeUrl} />
        </div>
      </div>

      {/* API Key */}
      <div>
        <label className="block text-xs text-gray-400 mb-1">API Key</label>
        <div className="flex items-center gap-1 bg-gray-50 border border-gray-200 rounded-lg px-2.5 py-2">
          <span className="text-xs font-mono text-gray-700 flex-1 truncate">{maskedKey}</span>
          <button
            onClick={() => setKeyVisible(v => !v)}
            className="text-xs text-slate-600 hover:text-slate-800 px-1 flex-shrink-0"
          >
            {keyVisible ? t('services.hideKey') : t('services.showKey')}
          </button>
          <CopyButton text={service.apiKey} />
        </div>
      </div>

      {/* Quick call example */}
      <div>
        <label className="block text-xs text-gray-400 mb-1">{t('services.quickCall')}</label>
        <div className="bg-gray-900 rounded-lg px-3 py-2 relative overflow-x-auto">
          <pre className="text-xs text-gray-300 font-mono whitespace-pre">{`curl -X POST ${service.invokeUrl} \\
  -H "X-API-Key: ${service.apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"inputs": {}}'`}</pre>
          <div className="absolute top-1.5 right-1.5">
            <CopyButton text={`curl -X POST ${service.invokeUrl} \\\n  -H "X-API-Key: ${service.apiKey}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"inputs": {}}'`} />
          </div>
        </div>
      </div>

      {canRestart && (
        <div className={`grid gap-2 ${isRunning ? 'grid-cols-2' : 'grid-cols-1'}`}>
          <button
            onClick={onRestart}
            disabled={restarting || stopping}
            className="flex items-center justify-center gap-2 py-2 border border-blue-200 text-blue-600 text-xs font-medium rounded-lg hover:bg-blue-50 disabled:opacity-60 transition-colors"
          >
            {restarting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            {restarting ? t('services.restarting') : t('services.restartService')}
          </button>
          {isRunning && (
            <button
              onClick={onStop}
              disabled={stopping || restarting}
              className="flex items-center justify-center gap-2 py-2 border border-gray-300 text-gray-700 text-xs font-medium rounded-lg hover:bg-gray-50 disabled:opacity-60 transition-colors"
            >
              {stopping ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <StopCircle className="w-3.5 h-3.5" />}
              {stopping ? t('services.stopping') : t('services.stopService')}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function ServicesSection() {
  const { t } = useTranslation('task');
  const [services, setServices] = useState<WorkflowService[]>([]);
  const [loading, setLoading] = useState(true);
  const [restartingIds, setRestartingIds] = useState<Set<string>>(new Set());
  const [stoppingIds, setStoppingIds] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<'all' | 'running' | 'stopped'>('all');
  const toast = useToast();

  const fetchServices = useCallback(async () => {
    try {
      const res = await workflowAPI.listServices();
      setServices(res.data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(t('services.loadFailed'), msg);
    } finally {
      setLoading(false);
    }
  }, [toast, t]);

  useEffect(() => {
    fetchServices();
    const timer = setInterval(fetchServices, 10000);
    return () => clearInterval(timer);
  }, [fetchServices]);

  const handleRestart = async (service: WorkflowService) => {
    setRestartingIds(prev => new Set(prev).add(service.workflowId));
    try {
      const publishRequest = service.driver === 'local' || service.driver === 'docker'
        ? { driver: service.driver }
        : undefined;
      await workflowAPI.publish(service.workflowId, publishRequest);
      await fetchServices();
      toast.success(t('services.serviceRestarted'));
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(t('services.restartFailed'), msg);
    } finally {
      setRestartingIds(prev => {
        const s = new Set(prev);
        s.delete(service.workflowId);
        return s;
      });
    }
  };

  const handleStop = async (workflowId: string) => {
    setStoppingIds(prev => new Set(prev).add(workflowId));
    try {
      await workflowAPI.unpublish(workflowId);
      await fetchServices();
      toast.success(t('services.serviceStopped'));
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(t('services.stopFailed'), msg);
    } finally {
      setStoppingIds(prev => { const s = new Set(prev); s.delete(workflowId); return s; });
    }
  };

  const FILTERS: { key: typeof filter; label: string }[] = [
    { key: 'all',     label: t('services.filterAll') },
    { key: 'running', label: t('services.filterRunning') },
    { key: 'stopped', label: t('services.filterStopped') },
  ];

  const filtered = filter === 'all' ? services : services.filter(s => s.status === filter);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
          {FILTERS.map(f => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`px-3 py-1.5 text-xs rounded-md transition-colors ${
                filter === f.key
                  ? 'bg-white text-slate-800 shadow-sm font-medium'
                  : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              {f.label}
              {f.key !== 'all' && (
                <span className="ml-1 text-gray-400">
                  ({services.filter(s => s.status === f.key).length})
                </span>
              )}
            </button>
          ))}
        </div>
        <button
          onClick={fetchServices}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          {t('common:button.refresh')}
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="w-6 h-6 animate-spin text-gray-400" />
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<Globe className="w-10 h-10" />}
          title={t('services.emptyTitle')}
          description={t('services.emptyDescription')}
        />
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {filtered.map(service => (
            <ServiceCard
              key={service.workflowId}
              service={service}
              onRestart={() => handleRestart(service)}
              onStop={() => handleStop(service.workflowId)}
              restarting={restartingIds.has(service.workflowId)}
              stopping={stoppingIds.has(service.workflowId)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
