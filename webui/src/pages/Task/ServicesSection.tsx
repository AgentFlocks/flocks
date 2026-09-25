import PageSelectionGroup from '../PageSelectionGroup';
import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { IconWorld, IconPlayerStop, IconLoader2, IconRefresh, IconExternalLink } from '@tabler/icons-react';
import { resolveWorkflowServicePort, workflowAPI, WorkflowService } from '@/api/workflow';
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
    <div className="bg-surface border border-line rounded-card p-5 space-y-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-8 h-8 rounded-control bg-subtle flex items-center justify-center flex-shrink-0">
            <IconWorld aria-hidden="true" className="w-4 h-4 text-fg-secondary" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-fg truncate">{service.workflowName}</p>
            <p className="text-xs text-fg-muted mt-0.5">{t('services.publishedAt', { date: publishedDate })}</p>
          </div>
        </div>
        <WorkflowStatusBadge status={service.status} variant="pill" />
      </div>

      <div className="flex items-center justify-between gap-2 rounded-control bg-subtle border border-line px-3 py-2">
        <span className="text-xs text-fg-muted">{t('services.serviceDriver')}</span>
        <span className="text-xs font-medium text-fg-secondary">{driverLabel}</span>
      </div>

      {/* Invoke URL */}
      <div>
        <label className="block text-xs text-fg-muted mb-1">Invoke URL</label>
        <div className="flex items-center gap-1 bg-subtle border border-line rounded-control px-2.5 py-2">
          <span className="text-xs font-mono text-fg-secondary flex-1 truncate">{service.invokeUrl}</span>
          <a
            href={service.invokeUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="p-1 rounded-tag hover:bg-hover text-fg-muted hover:text-fg-secondary transition-colors flex-shrink-0"
            title={t('services.openInNewTab')}
          >
            <IconExternalLink aria-hidden="true" className="w-4 h-4" />
          </a>
          <CopyButton text={service.invokeUrl} />
        </div>
      </div>

      {/* API Key */}
      <div>
        <label className="block text-xs text-fg-muted mb-1">API Key</label>
        <div className="flex items-center gap-1 bg-subtle border border-line rounded-control px-2.5 py-2">
          <span className="text-xs font-mono text-fg-secondary flex-1 truncate">{maskedKey}</span>
          <button
            onClick={() => setKeyVisible(v => !v)}
            className="text-xs text-fg-secondary hover:text-fg px-1 flex-shrink-0"
          >
            {keyVisible ? t('services.hideKey') : t('services.showKey')}
          </button>
          <CopyButton text={service.apiKey} />
        </div>
      </div>

      {/* Quick call example */}
      <div>
        <label className="block text-xs text-fg-muted mb-1">{t('services.quickCall')}</label>
        <div className="bg-subtle rounded-control px-3 py-2 relative overflow-x-auto">
          <pre className="text-xs text-fg-placeholder font-mono whitespace-pre">{`curl -X POST ${service.invokeUrl} \\
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
            className="flex items-center justify-center gap-2 py-2 border border-info text-info text-xs font-medium rounded-control hover:bg-info-soft disabled:bg-disabled disabled:text-fg-disabled transition-colors"
          >
            {restarting ? <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" /> : <IconRefresh aria-hidden="true" className="w-4 h-4" />}
            {restarting ? t('services.restarting') : t('services.restartService')}
          </button>
          {isRunning && (
            <button
              onClick={onStop}
              disabled={stopping || restarting}
              className="flex items-center justify-center gap-2 py-2 border border-line-control text-fg-secondary text-xs font-medium rounded-control hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled transition-colors"
            >
              {stopping ? <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" /> : <IconPlayerStop aria-hidden="true" className="w-4 h-4" />}
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
        ? { driver: service.driver, port: resolveWorkflowServicePort(service) }
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
        <PageSelectionGroup className="flex ga">
          {FILTERS.map(f => (
            <button aria-pressed={filter === f.key}
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`px-3 py-1.5 text-xs rounded-segment transition-colors ${
                filter === f.key
                  ? 'bg-selected text-fg font-medium'
                  : 'text-fg-secondary hover:text-fg'
              }`}
            >
              {f.label}
              {f.key !== 'all' && (
                <span className="ml-1 text-fg-muted">
                  ({services.filter(s => s.status === f.key).length})
                </span>
              )}
            </button>
          ))}
        </PageSelectionGroup>
        <button
          onClick={fetchServices}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-fg-secondary border border-line rounded-control hover:bg-hover transition-colors"
        >
          <IconRefresh aria-hidden="true" className="w-4 h-4" />
          {t('common:button.refresh')}
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16">
          <IconLoader2 aria-hidden="true" className="w-6 h-6 animate-spin text-fg-muted" />
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<IconWorld aria-hidden="true" className="w-8 h-8" />}
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
