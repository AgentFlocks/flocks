import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { Activity, ArrowLeft, ExternalLink, FlaskConical, Play, Plus, RefreshCw, Trash2, Workflow } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useToast } from '@/components/common/Toast';
import { n8nAPI, type N8nBuildRun, type N8nWorkflowRecord } from '@/api/n8n';
import { extractErrorMessage } from '@/utils/error';
import N8nBuildPanel from '@/pages/WorkflowCreate/N8nBuildPanel';

type Mode = 'list' | 'new' | 'detail';

const STATUS_STYLE: Record<string, string> = {
  active: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  test_passed: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  inactive: 'border-gray-200 bg-gray-50 text-gray-600',
  unknown: 'border-gray-200 bg-gray-50 text-gray-600',
  not_tested: 'border-gray-200 bg-gray-50 text-gray-600',
  cleaned: 'border-amber-200 bg-amber-50 text-amber-700',
  missing: 'border-amber-200 bg-amber-50 text-amber-700',
  failed: 'border-red-200 bg-red-50 text-red-700',
  test_failed: 'border-red-200 bg-red-50 text-red-700',
  test_error: 'border-red-200 bg-red-50 text-red-700',
  auth_error: 'border-red-200 bg-red-50 text-red-700',
  sync_error: 'border-red-200 bg-red-50 text-red-700',
  lint_failed: 'border-red-200 bg-red-50 text-red-700',
};

function statusClass(status?: string | null): string {
  return STATUS_STYLE[status || 'unknown'] || STATUS_STYLE.unknown;
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  if (value === undefined || value === null) return null;
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
      <pre className="max-h-72 overflow-auto rounded-lg bg-gray-950 p-3 font-mono text-xs leading-relaxed text-gray-100">
        {JSON.stringify(value, null, 2)}
      </pre>
    </section>
  );
}

function isFlocksManagedRecord(record: N8nWorkflowRecord): boolean {
  return record.source !== 'external';
}

export default function N8nWorkflowPage() {
  const params = useParams();
  const location = useLocation();
  const mode: Mode = params.recordId ? 'detail' : location.pathname.endsWith('/new') ? 'new' : 'list';
  if (mode === 'new') return <N8nWorkflowCreate />;
  if (mode === 'detail' && params.recordId) return <N8nWorkflowDetail recordId={params.recordId} />;
  return <N8nWorkflowList />;
}

function N8nWorkflowList() {
  const { t } = useTranslation('workflow');
  const navigate = useNavigate();
  const toast = useToast();
  const [records, setRecords] = useState<N8nWorkflowRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadRecords = useCallback(async () => {
    try {
      const response = await n8nAPI.listWorkflowRecords(500);
      setRecords(response.data);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRecords();
  }, [loadRecords]);

  const managedRecords = useMemo(() => records.filter(isFlocksManagedRecord), [records]);

  const handleSyncAll = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      const response = await n8nAPI.syncWorkflowRecords({
        includeExternal: false,
      });
      setRecords(response.data.records);
      toast.success(t('n8n.syncSuccess'), t('n8n.syncSummary', {
        created: response.data.created,
        updated: response.data.updated,
        missing: response.data.missing,
        failed: response.data.connectionsFailed,
      }));
      setError(null);
    } catch (err) {
      const message = extractErrorMessage(err);
      setError(message);
      toast.error(t('n8n.syncFailed'), message);
    } finally {
      setRefreshing(false);
    }
  };

  const handleCleanup = async (record: N8nWorkflowRecord) => {
    if (!window.confirm(t('n8n.confirmCleanup', { name: record.name }))) return;
    try {
      const response = await n8nAPI.cleanupWorkflowRecord(record.id);
      setRecords((current) => current.map((item) => (item.id === record.id ? response.data : item)));
      toast.success(t('n8n.cleanupSuccess'));
    } catch (err) {
      toast.error(t('n8n.cleanupFailed'), extractErrorMessage(err));
    }
  };

  if (loading) {
    return <div className="flex h-full items-center justify-center"><LoadingSpinner delayMs={180} /></div>;
  }

  return (
    <div className="flex h-full flex-col bg-gray-50">
      <PageHeader
        title={t('n8n.centerTitle')}
        description={t('n8n.centerDescription')}
        icon={<Workflow className="h-8 w-8" />}
      />
      <div className="flex items-center gap-2 border-b border-gray-100 bg-white px-4 py-2">
        <Link to="/workflows" className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50">
          <ArrowLeft className="h-4 w-4" />
          {t('n8n.backToFlocks')}
        </Link>
        <button
          type="button"
          onClick={() => void handleSyncAll()}
          disabled={refreshing}
          className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
          {t('n8n.syncAll')}
        </button>
        <button
          type="button"
          onClick={() => navigate('/workflows/n8n/new')}
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700"
        >
          <Plus className="h-4 w-4" />
          {t('n8n.create')}
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {error ? (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>
        ) : managedRecords.length === 0 ? (
          <EmptyState
            icon={<Workflow className="h-16 w-16" />}
            title={t('n8n.emptyTitle')}
            description={t('n8n.emptyDescription')}
            action={(
              <button
                type="button"
                onClick={() => navigate('/workflows/n8n/new')}
                className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-white hover:bg-red-700"
              >
                <Plus className="h-5 w-5" />
                {t('n8n.create')}
              </button>
            )}
          />
        ) : (
          <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
            <table className="min-w-full divide-y divide-gray-100">
              <thead className="bg-gray-50 text-left text-xs font-semibold text-gray-500">
                <tr>
                  <th className="px-4 py-3">{t('n8n.name')}</th>
                  <th className="px-4 py-3">{t('n8n.connection')}</th>
                  <th className="px-4 py-3">{t('n8n.remoteStatus')}</th>
                  <th className="px-4 py-3">{t('n8n.testStatus')}</th>
                  <th className="px-4 py-3">{t('n8n.webhookUrl')}</th>
                  <th className="px-4 py-3 text-right">{t('n8n.actions')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 text-sm">
                {managedRecords.map((record) => (
                  <tr key={record.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3">
                      <button type="button" onClick={() => navigate(`/workflows/n8n/${record.id}`)} className="text-left">
                        <span className="block font-semibold text-gray-900">{record.name}</span>
                        <span className="block font-mono text-xs text-gray-400">{record.n8nWorkflowId}</span>
                      </button>
                    </td>
                    <td className="max-w-[220px] truncate px-4 py-3">
                      <span className="block text-gray-700">{record.connectionName || record.connectionId}</span>
                      <span className="block truncate font-mono text-xs text-gray-400">{record.n8nBaseUrl}</span>
                    </td>
                    <td className="px-4 py-3"><span className={`rounded border px-2 py-1 text-xs ${statusClass(record.remoteStatus)}`}>{record.remoteStatus}</span></td>
                    <td className="px-4 py-3"><span className={`rounded border px-2 py-1 text-xs ${statusClass(record.testStatus)}`}>{record.testStatus}</span></td>
                    <td className="max-w-[360px] truncate px-4 py-3 font-mono text-xs text-gray-500">{record.webhookUrl || '-'}</td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        <button type="button" onClick={() => navigate(`/workflows/n8n/${record.id}`)} className="rounded border border-gray-200 px-2 py-1 text-xs text-gray-600 hover:bg-white">
                          {t('n8n.viewDetail')}
                        </button>
                        <a href={record.workflowUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded border border-gray-200 px-2 py-1 text-xs text-gray-600 hover:bg-white">
                          <ExternalLink className="h-3 w-3" />
                          {t('n8n.openN8n')}
                        </a>
                        {record.ownership === 'managed' && record.source !== 'external' && (
                          <button type="button" onClick={() => void handleCleanup(record)} className="inline-flex items-center gap-1 rounded border border-gray-200 px-2 py-1 text-xs text-gray-600 hover:bg-white">
                            <Trash2 className="h-3 w-3" />
                            {t('n8n.cleanup')}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function N8nWorkflowCreate() {
  const { t } = useTranslation('workflow');
  const navigate = useNavigate();
  const [latestRun, setLatestRun] = useState<N8nBuildRun | null>(null);

  useEffect(() => {
    if (latestRun?.recordId) {
      navigate(`/workflows/n8n/${latestRun.recordId}`);
    }
  }, [latestRun?.recordId, navigate]);

  const handleGuidePrompt = useCallback((prompt: string, displayLabel: string) => {
    navigate('/workflows/new', {
      state: {
        freshCreate: true,
        chatLaunchRequest: {
          id: Date.now(),
          prompt,
          displayLabel,
        },
      },
    });
  }, [navigate]);

  return (
    <div className="flex h-full flex-col bg-gray-50">
      <PageHeader
        title={t('n8n.createTitle')}
        description={t('n8n.createDescription')}
        icon={<Workflow className="h-8 w-8" />}
      />
      <div className="flex items-center border-b border-gray-100 bg-white px-4 py-2">
        <Link to="/workflows/n8n" className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50">
          <ArrowLeft className="h-4 w-4" />
          {t('n8n.backToCenter')}
        </Link>
      </div>
      <div className="min-h-0 flex-1 overflow-hidden p-4">
        <div className="mx-auto flex h-full max-w-5xl overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
          <N8nBuildPanel onGuidePrompt={handleGuidePrompt} onBuildRunCreated={setLatestRun} />
        </div>
      </div>
    </div>
  );
}

function N8nWorkflowDetail({ recordId }: { recordId: string }) {
  const { t } = useTranslation('workflow');
  const navigate = useNavigate();
  const toast = useToast();
  const [record, setRecord] = useState<N8nWorkflowRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadRecord = useCallback(async () => {
    try {
      const response = await n8nAPI.getWorkflowRecord(recordId);
      setRecord(response.data);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [recordId]);

  useEffect(() => {
    void loadRecord();
  }, [loadRecord]);

  const applyAction = async (action: 'sync' | 'retry' | 'cleanup') => {
    if (!record) return;
    if (action === 'cleanup' && !window.confirm(t('n8n.confirmCleanup', { name: record.name }))) return;
    setBusy(action);
    try {
      const response = action === 'sync'
        ? await n8nAPI.syncWorkflowRecord(record.id)
        : action === 'retry'
          ? await n8nAPI.retryWorkflowRecordTests(record.id)
          : await n8nAPI.cleanupWorkflowRecord(record.id);
      setRecord(response.data);
      toast.success(t(`n8n.${action}Success`));
    } catch (err) {
      toast.error(t(`n8n.${action}Failed`), extractErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const handleRun = async () => {
    if (!record) return;
    const rawPayload = window.prompt(t('n8n.runPayloadPrompt'), '{}');
    if (rawPayload === null) return;
    let payload: unknown;
    try {
      payload = rawPayload.trim() ? JSON.parse(rawPayload) : {};
    } catch {
      toast.error(t('n8n.runFailed'), t('n8n.invalidRunPayload'));
      return;
    }
    setBusy('run');
    try {
      const response = await n8nAPI.runWorkflowRecord(record.id, {
        payload,
        waitForExecution: true,
      });
      setRecord(response.data);
      const result = response.data.latestRunResult;
      toast.success(
        t('n8n.runSuccess'),
        result?.status ? t('n8n.runSummary', { status: String(result.status), executionId: String(response.data.latestExecutionId || '-') }) : undefined,
      );
    } catch (err) {
      toast.error(t('n8n.runFailed'), extractErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  if (loading) {
    return <div className="flex h-full items-center justify-center"><LoadingSpinner delayMs={180} /></div>;
  }
  if (error || !record) {
    return <div className="flex h-full items-center justify-center text-sm text-red-600">{error || t('n8n.notFound')}</div>;
  }

  const facts = [
    [t('n8n.recordId'), record.id],
    [t('n8n.connection'), record.connectionName || record.connectionId],
    [t('n8n.n8nBaseUrl'), record.n8nBaseUrl],
    [t('n8n.n8nWorkflowId'), record.n8nWorkflowId],
    [t('n8n.source'), record.source],
    [t('n8n.ownership'), record.ownership],
    [t('n8n.webhookMethod'), record.webhookMethod || '-'],
    [t('n8n.latestExecutionId'), record.latestExecutionId || '-'],
    [t('n8n.lastSyncedAt'), record.lastSyncedAt || '-'],
    [t('n8n.lastTestedAt'), record.lastTestedAt || '-'],
    [t('n8n.lastRunAt'), record.lastRunAt || '-'],
  ];

  const canManageRemote = record.ownership === 'managed' && record.source !== 'external';
  const canRunRemote = record.source !== 'external' && Boolean(record.webhookPath || record.webhookUrl);

  return (
    <div className="flex h-full flex-col bg-gray-50">
      <PageHeader title={record.name} description={t('n8n.detailDescription')} icon={<Workflow className="h-8 w-8" />} />
      <div className="flex items-center gap-2 border-b border-gray-100 bg-white px-4 py-2">
        <button type="button" onClick={() => navigate('/workflows/n8n')} className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50">
          <ArrowLeft className="h-4 w-4" />
          {t('n8n.backToCenter')}
        </button>
        <button type="button" disabled={busy !== null} onClick={() => void applyAction('sync')} className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50">
          <Activity className="h-4 w-4" />
          {t('n8n.sync')}
        </button>
        {canRunRemote && (
          <button type="button" disabled={busy !== null} onClick={() => void handleRun()} className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50">
            <Play className="h-4 w-4" />
            {t('n8n.run')}
          </button>
        )}
        {canManageRemote && (
          <button type="button" disabled={busy !== null} onClick={() => void applyAction('retry')} className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50">
            <FlaskConical className="h-4 w-4" />
            {t('n8n.retryTest')}
          </button>
        )}
        <a href={record.workflowUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50">
          <ExternalLink className="h-4 w-4" />
          {t('n8n.openN8n')}
        </a>
        {canManageRemote && (
          <button type="button" disabled={busy !== null} onClick={() => void applyAction('cleanup')} className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-red-200 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50">
            <Trash2 className="h-4 w-4" />
            {t('n8n.cleanup')}
          </button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto grid max-w-6xl gap-4 lg:grid-cols-[360px,1fr]">
          <div className="space-y-4">
            <section className="rounded-xl border border-gray-200 bg-white p-4">
              <div className="flex gap-2">
                <span className={`rounded border px-2 py-1 text-xs ${statusClass(record.remoteStatus)}`}>{record.remoteStatus}</span>
                <span className={`rounded border px-2 py-1 text-xs ${statusClass(record.testStatus)}`}>{record.testStatus}</span>
                <span className={`rounded border px-2 py-1 text-xs ${statusClass(record.buildStatus)}`}>{record.buildStatus}</span>
              </div>
              <dl className="mt-4 space-y-2 text-sm">
                {facts.map(([label, value]) => (
                  <div key={label} className="grid grid-cols-[120px,1fr] gap-2">
                    <dt className="text-gray-500">{label}</dt>
                    <dd className="min-w-0 truncate font-mono text-xs text-gray-800">{value}</dd>
                  </div>
                ))}
              </dl>
              {record.webhookUrl && (
                <div className="mt-4 rounded-lg bg-gray-50 p-3">
                  <p className="text-xs font-semibold text-gray-500">{t('n8n.webhookUrl')}</p>
                  <p className="mt-1 break-all font-mono text-xs text-gray-700">{record.webhookUrl}</p>
                </div>
              )}
              {record.error && <p className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">{record.error}</p>}
            </section>
          </div>
          <div className="space-y-4 rounded-xl border border-gray-200 bg-white p-4">
            <JsonBlock title={t('n8n.userRequest')} value={record.userRequest} />
            <JsonBlock title="IR" value={record.ir} />
            <JsonBlock title={t('n8n.nativeWorkflowJson')} value={record.workflowJson} />
            <JsonBlock title={t('n8n.lintIssues')} value={record.lintIssues} />
            <JsonBlock title={t('n8n.testCases')} value={record.testCases} />
            <JsonBlock title={t('n8n.testResults')} value={record.testResults} />
            <JsonBlock title={t('n8n.latestRunResult')} value={record.latestRunResult} />
          </div>
        </div>
      </div>
    </div>
  );
}
