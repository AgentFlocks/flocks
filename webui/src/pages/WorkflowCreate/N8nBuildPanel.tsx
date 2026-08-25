import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, Bot, CheckCircle2, Clipboard, FlaskConical, Play, RefreshCw, Save, Trash2, Workflow } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { n8nAPI, type N8nBuildRun, type N8nConnection } from '@/api/n8n';
import { useToast } from '@/components/common/Toast';
import { extractErrorMessage } from '@/utils/error';
import type { CreateWorkflowChatLaunchRequest } from './CreateChatTab';

interface N8nBuildPanelProps {
  onGuidePrompt?: (prompt: string, label: string) => void;
  onBuildRunCreated?: (run: N8nBuildRun) => void;
}

const SAMPLE_IR = {
  name: 'flocks-test-hello',
  description: 'Webhook receives a name and returns a greeting.',
  trigger: {
    type: 'webhook',
    method: 'POST',
    path: 'flocks-test-hello',
    responseMode: 'responseNode',
  },
  steps: [
    {
      id: 'build_response',
      kind: 'code',
      name: 'Build Response',
      js_code: "const input = $input.first().json;\nconst body = input.body || input;\nreturn [{ json: { message: `Hello ${body.name || 'World'}` } }];",
      next: 'respond',
    },
    {
      id: 'respond',
      kind: 'respond_to_webhook',
      name: 'Respond',
      respond_with: 'json',
      response_body: '={{ $json }}',
    },
  ],
  tests: [
    {
      name: 'returns greeting',
      input: { name: 'Alice' },
      expect: { status: 200, jsonContains: { message: 'Hello Alice' } },
    },
  ],
};

const SAMPLE_KAFKA_IR = {
  name: 'flocks-kafka-alerts',
  description: 'Kafka Trigger consumes security alerts and normalizes each message.',
  trigger: {
    type: 'kafka',
    topic: 'security-alerts',
    groupId: 'flocks-security-alerts',
    credentialRef: { name: 'Kafka Production' },
    fromBeginning: false,
    batchSize: 1,
    resolveOffset: 'onCompletion',
  },
  steps: [
    {
      id: 'normalize',
      kind: 'code',
      name: 'Normalize',
      js_code: "return $input.all().map((item) => ({ json: { ...item.json, handledBy: 'n8n' } }));",
    },
  ],
  tests: [],
};

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function statusClass(status?: string | null): string {
  if (status === 'test_passed' || status === 'rendered' || status === 'cleaned' || status === 'ok') {
    return 'border-emerald-200 bg-emerald-50 text-emerald-700';
  }
  if (status === 'failed' || status === 'test_failed' || status === 'lint_failed' || status === 'error') {
    return 'border-red-200 bg-red-50 text-red-700';
  }
  return 'border-gray-200 bg-gray-50 text-gray-600';
}

function httpStatus(err: unknown): number | undefined {
  const status = (err as any)?.response?.status;
  return typeof status === 'number' ? status : undefined;
}

export default function N8nBuildPanel({ onGuidePrompt, onBuildRunCreated }: N8nBuildPanelProps) {
  const { t } = useTranslation('workflow');
  const toast = useToast();
  const [connections, setConnections] = useState<N8nConnection[]>([]);
  const [selectedConnectionId, setSelectedConnectionId] = useState<string>('new');
  const [connection, setConnection] = useState<N8nConnection | null>(null);
  const [connectionName, setConnectionName] = useState('Default n8n');
  const [isDefaultConnection, setIsDefaultConnection] = useState(true);
  const [baseUrl, setBaseUrl] = useState('http://localhost:5678');
  const [apiKeySecretRef, setApiKeySecretRef] = useState('N8N_API_KEY');
  const [apiKey, setApiKey] = useState('');
  const [userRequest, setUserRequest] = useState('');
  const [irText, setIrText] = useState(formatJson(SAMPLE_IR));
  const [runs, setRuns] = useState<N8nBuildRun[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const latestRun = runs[0] ?? null;
  const isNewConnection = selectedConnectionId === 'new';

  const loadState = useCallback(async () => {
    try {
      const [conn, runList] = await Promise.all([
        n8nAPI.listConnections(),
        n8nAPI.listBuildRuns(8),
      ]);
      const connectionList = conn.data;
      const activeConnection = connectionList.find((item) => item.isDefault) || connectionList[0] || null;
      setConnections(connectionList);
      setConnection(activeConnection);
      if (activeConnection) {
        setSelectedConnectionId(activeConnection.id);
        setConnectionName(activeConnection.name);
        setIsDefaultConnection(activeConnection.isDefault);
        setBaseUrl(activeConnection.baseUrl);
        setApiKeySecretRef(activeConnection.apiKeySecretRef);
      }
      setRuns(runList.data);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void loadState();
  }, [loadState]);

  useEffect(() => {
    if (selectedConnectionId === 'new') {
      setConnection(null);
      setConnectionName('n8n');
      setIsDefaultConnection(connections.length === 0);
      setBaseUrl('http://localhost:5678');
      setApiKeySecretRef('N8N_API_KEY');
      setApiKey('');
      return;
    }
    const next = connections.find((item) => item.id === selectedConnectionId) || null;
    if (!next) return;
    setConnection(next);
    setConnectionName(next.name);
    setIsDefaultConnection(next.isDefault);
    setBaseUrl(next.baseUrl);
    setApiKeySecretRef(next.apiKeySecretRef);
    setApiKey('');
  }, [connections, selectedConnectionId]);

  const parsedIr = useMemo(() => {
    try {
      return JSON.parse(irText) as Record<string, any>;
    } catch {
      return null;
    }
  }, [irText]);

  const saveConnectionDraft = useCallback(async () => {
    const payload = {
      name: connectionName,
      baseUrl,
      apiKeySecretRef,
      apiKey: apiKey.trim() || undefined,
      isDefault: isDefaultConnection,
    };

    if (isNewConnection) {
      try {
        return await n8nAPI.createConnection(payload);
      } catch (err) {
        if (httpStatus(err) !== 404) throw err;
        // Compatibility with deployments that have the legacy single-connection API.
        return await n8nAPI.updateConnection({ ...payload, isDefault: true });
      }
    }

    try {
      return await n8nAPI.updateConnectionById(selectedConnectionId, payload);
    } catch (err) {
      if (httpStatus(err) !== 404) throw err;
      try {
        return await n8nAPI.createConnection(payload);
      } catch (createErr) {
        if (httpStatus(createErr) !== 404) throw createErr;
        return await n8nAPI.updateConnection({ ...payload, isDefault: true });
      }
    }
  }, [apiKey, apiKeySecretRef, baseUrl, connectionName, isDefaultConnection, isNewConnection, selectedConnectionId]);

  const handleSave = async () => {
    setBusy('save');
    setError(null);
    try {
      const response = await saveConnectionDraft();
      setConnection(response.data);
      setSelectedConnectionId(response.data.id);
      setConnections((current) => {
        const existing = current.some((item) => item.id === response.data.id);
        const next = existing
          ? current.map((item) => (item.id === response.data.id ? response.data : item))
          : [response.data, ...current];
        return next.map((item) => ({ ...item, isDefault: response.data.isDefault ? item.id === response.data.id : item.isDefault }));
      });
      setApiKey('');
      toast.success(t('create.n8n.connectionSaved'));
    } catch (err) {
      const message = extractErrorMessage(err);
      setError(message);
      toast.error(t('create.n8n.connectionSaveFailed'), message);
    } finally {
      setBusy(null);
    }
  };

  const handleHealthCheck = async () => {
    setBusy('health');
    setError(null);
    try {
      let activeConnection = connection;
      if (
        apiKey.trim()
        || !connection
        || connection.baseUrl !== baseUrl
        || connection.apiKeySecretRef !== apiKeySecretRef
        || connection.name !== connectionName
      ) {
        const saved = await saveConnectionDraft();
        activeConnection = saved.data;
        setConnection(saved.data);
        setSelectedConnectionId(saved.data.id);
        setConnections((current) => {
          const existing = current.some((item) => item.id === saved.data.id);
          return existing ? current.map((item) => (item.id === saved.data.id ? saved.data : item)) : [saved.data, ...current];
        });
        setApiKey('');
      }
      const response = await n8nAPI.healthCheck({ connectionId: activeConnection?.id, baseUrl, apiKeySecretRef });
      setConnection(response.data.connection);
      setConnections((current) => current.map((item) => (item.id === response.data.connection.id ? response.data.connection : item)));
      setIsDefaultConnection(response.data.connection.isDefault);
      if (response.data.success) {
        toast.success(t('create.n8n.healthOk'));
      } else {
        setError(response.data.error || t('create.n8n.healthFailed'));
        toast.error(t('create.n8n.healthFailed'), response.data.error || '');
      }
    } catch (err) {
      const message = extractErrorMessage(err);
      setError(message);
      toast.error(t('create.n8n.healthFailed'), message);
    } finally {
      setBusy(null);
    }
  };

  const buildGuidePrompt = (): CreateWorkflowChatLaunchRequest => ({
    id: Date.now(),
    displayLabel: t('create.n8n.sendToWorkbench'),
    prompt: t('create.n8n.guidePrompt', {
      baseUrl,
      secretRef: apiKeySecretRef,
      request: userRequest.trim() || t('create.n8n.defaultUserRequest'),
    }),
  });

  const handleSendToWorkbench = () => {
    const launch = buildGuidePrompt();
    onGuidePrompt?.(launch.prompt, launch.displayLabel || t('create.n8n.sendToWorkbench'));
  };

  const handleBuildRun = async () => {
    if (!parsedIr) {
      setError(t('create.n8n.invalidIr'));
      return;
    }
    setBusy('build');
    setError(null);
    try {
      const response = await n8nAPI.createBuildRun({
        connectionId: connection?.id || (selectedConnectionId !== 'new' ? selectedConnectionId : undefined),
        userRequest,
        ir: parsedIr,
        baseUrl,
        apiKeySecretRef,
        publish: true,
        activate: true,
        cleanupOnSuccess: false,
        waitForExecution: true,
      });
      setRuns((current) => [response.data, ...current.filter((run) => run.runId !== response.data.runId)].slice(0, 8));
      onBuildRunCreated?.(response.data);
      if (['test_passed', 'published', 'rendered'].includes(response.data.status)) {
        toast.success(t('create.n8n.buildSucceeded'));
      } else {
        toast.warning(t('create.n8n.buildFinishedWithStatus', { status: response.data.status }));
      }
    } catch (err) {
      const message = extractErrorMessage(err);
      setError(message);
      toast.error(t('create.n8n.buildFailed'), message);
    } finally {
      setBusy(null);
    }
  };

  const handleDeleteConnection = async () => {
    if (isNewConnection || !connection) return;
    if (!window.confirm(t('create.n8n.confirmDeleteConnection', { name: connection.name }))) return;
    setBusy('deleteConnection');
    setError(null);
    try {
      await n8nAPI.deleteConnection(connection.id);
      const nextConnections = connections.filter((item) => item.id !== connection.id);
      setConnections(nextConnections);
      const next = nextConnections.find((item) => item.isDefault) || nextConnections[0] || null;
      if (next) {
        setSelectedConnectionId(next.id);
      } else {
        setSelectedConnectionId('new');
      }
      toast.success(t('create.n8n.connectionDeleted'));
    } catch (err) {
      const message = extractErrorMessage(err);
      setError(message);
      toast.error(t('create.n8n.connectionDeleteFailed'), message);
    } finally {
      setBusy(null);
    }
  };

  const handleRetry = async (runId: string) => {
    setBusy(`retry:${runId}`);
    try {
      const response = await n8nAPI.retryTests(runId);
      setRuns((current) => current.map((run) => (run.runId === runId ? response.data : run)));
    } catch (err) {
      toast.error(t('create.n8n.retryFailed'), extractErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const handleCleanup = async (runId: string) => {
    setBusy(`cleanup:${runId}`);
    try {
      const response = await n8nAPI.cleanup(runId);
      setRuns((current) => current.map((run) => (run.runId === runId ? response.data : run)));
    } catch (err) {
      toast.error(t('create.n8n.cleanupFailed'), extractErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto bg-white p-4">
      <div className="space-y-4">
        <section className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-gray-900">{t('create.n8n.title')}</h3>
              <p className="mt-1 text-xs leading-relaxed text-gray-500">{t('create.n8n.subtitle')}</p>
            </div>
            <span className={`shrink-0 rounded border px-2 py-1 text-[11px] font-medium ${statusClass(connection?.lastHealthStatus)}`}>
              {connection?.lastHealthStatus || t('create.n8n.unchecked')}
            </span>
          </div>

          <label className="block">
            <span className="text-xs font-medium text-gray-600">{t('create.n8n.connection')}</span>
            <select
              value={selectedConnectionId}
              onChange={(event) => setSelectedConnectionId(event.target.value)}
              className="mt-1 w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-red-300 focus:ring-2 focus:ring-red-100"
            >
              {connections.map((item) => (
                <option key={item.id} value={item.id}>{item.name} - {item.baseUrl}</option>
              ))}
              <option value="new">{t('create.n8n.newConnection')}</option>
            </select>
          </label>

          <label className="block">
            <span className="text-xs font-medium text-gray-600">{t('create.n8n.connectionName')}</span>
            <input
              value={connectionName}
              onChange={(event) => setConnectionName(event.target.value)}
              className="mt-1 w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-red-300 focus:ring-2 focus:ring-red-100"
              placeholder="Production n8n"
            />
          </label>

          <label className="block">
            <span className="text-xs font-medium text-gray-600">{t('create.n8n.baseUrl')}</span>
            <input
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              className="mt-1 w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-red-300 focus:ring-2 focus:ring-red-100"
              placeholder="http://localhost:5678"
            />
          </label>

          <label className="block">
            <span className="text-xs font-medium text-gray-600">{t('create.n8n.secretRef')}</span>
            <input
              value={apiKeySecretRef}
              onChange={(event) => setApiKeySecretRef(event.target.value)}
              className="mt-1 w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-red-300 focus:ring-2 focus:ring-red-100"
              placeholder="N8N_API_KEY"
            />
          </label>

          <label className="block">
            <span className="text-xs font-medium text-gray-600">{t('create.n8n.apiKey')}</span>
            <input
              value={apiKey}
              type="password"
              onChange={(event) => setApiKey(event.target.value)}
              className="mt-1 w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-red-300 focus:ring-2 focus:ring-red-100"
              placeholder={connection?.apiKeyConfigured ? t('create.n8n.apiKeyConfigured', { value: connection.apiKeyMasked || '***' }) : t('create.n8n.apiKeyPlaceholder')}
            />
          </label>

          <label className="inline-flex items-center gap-2 text-xs font-medium text-gray-600">
            <input
              type="checkbox"
              checked={isDefaultConnection}
              onChange={(event) => setIsDefaultConnection(event.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-red-600 focus:ring-red-500"
            />
            {t('create.n8n.setDefault')}
          </label>

          <div className="grid grid-cols-3 gap-2">
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={busy !== null}
              className="inline-flex items-center justify-center gap-1.5 rounded-md bg-gray-900 px-3 py-2 text-xs font-medium text-white disabled:opacity-50"
            >
              <Save className="h-3.5 w-3.5" />
              {busy === 'save' ? t('create.n8n.saving') : t('create.n8n.save')}
            </button>
            <button
              type="button"
              onClick={() => void handleHealthCheck()}
              disabled={busy !== null}
              className="inline-flex items-center justify-center gap-1.5 rounded-md border border-gray-200 px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              <Activity className="h-3.5 w-3.5" />
              {busy === 'health' ? t('create.n8n.checking') : t('create.n8n.check')}
            </button>
            <button
              type="button"
              onClick={() => void handleDeleteConnection()}
              disabled={busy !== null || isNewConnection}
              className="inline-flex items-center justify-center gap-1.5 rounded-md border border-red-200 px-3 py-2 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              {busy === 'deleteConnection' ? t('create.n8n.deleting') : t('create.n8n.deleteConnection')}
            </button>
          </div>
        </section>

        <section className="space-y-3 border-t border-gray-100 pt-4">
          <label className="block">
            <span className="text-xs font-medium text-gray-600">{t('create.n8n.userRequest')}</span>
            <textarea
              value={userRequest}
              onChange={(event) => setUserRequest(event.target.value)}
              className="mt-1 min-h-[86px] w-full resize-none rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-red-300 focus:ring-2 focus:ring-red-100"
              placeholder={t('create.n8n.userRequestPlaceholder')}
            />
          </label>
          <button
            type="button"
            onClick={handleSendToWorkbench}
            disabled={!onGuidePrompt}
            className="inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
          >
            <Bot className="h-3.5 w-3.5" />
            {t('create.n8n.sendToWorkbench')}
          </button>
        </section>

        <section className="space-y-3 border-t border-gray-100 pt-4">
          <div className="flex items-center justify-between gap-2">
            <div>
              <h4 className="text-xs font-semibold text-gray-700">{t('create.n8n.irTitle')}</h4>
              <p className="mt-1 text-[11px] leading-relaxed text-gray-500">{t('create.n8n.irHint')}</p>
            </div>
            <div className="flex shrink-0 gap-2">
              <button
                type="button"
                onClick={() => setIrText(formatJson(SAMPLE_IR))}
                className="inline-flex items-center gap-1 rounded-md border border-gray-200 px-2 py-1 text-[11px] text-gray-600 hover:bg-gray-50"
              >
                <Clipboard className="h-3 w-3" />
                {t('create.n8n.resetSample')}
              </button>
              <button
                type="button"
                onClick={() => setIrText(formatJson(SAMPLE_KAFKA_IR))}
                className="inline-flex items-center gap-1 rounded-md border border-gray-200 px-2 py-1 text-[11px] text-gray-600 hover:bg-gray-50"
              >
                <Clipboard className="h-3 w-3" />
                {t('create.n8n.kafkaSample')}
              </button>
            </div>
          </div>
          <textarea
            value={irText}
            onChange={(event) => setIrText(event.target.value)}
            spellCheck={false}
            className="h-56 w-full resize-y rounded-md border border-gray-200 bg-gray-950 px-3 py-2 font-mono text-xs leading-relaxed text-gray-100 outline-none focus:border-red-300 focus:ring-2 focus:ring-red-100"
          />
          <button
            type="button"
            onClick={() => void handleBuildRun()}
            disabled={busy !== null || !parsedIr}
            className="inline-flex w-full items-center justify-center gap-1.5 rounded-md bg-red-600 px-3 py-2 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
          >
            <Play className="h-3.5 w-3.5" />
            {busy === 'build' ? t('create.n8n.building') : t('create.n8n.publishAndTest')}
          </button>
          {!parsedIr && <p className="text-xs text-red-600">{t('create.n8n.invalidIr')}</p>}
        </section>

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs leading-relaxed text-red-700">
            {error}
          </div>
        )}

        {latestRun && (
          <section className="space-y-3 border-t border-gray-100 pt-4">
            <div className="flex items-center justify-between gap-2">
              <h4 className="text-xs font-semibold text-gray-700">{t('create.n8n.latestRun')}</h4>
              <button
                type="button"
                onClick={() => void loadState()}
                className="inline-flex items-center gap-1 rounded-md border border-gray-200 px-2 py-1 text-[11px] text-gray-600 hover:bg-gray-50"
              >
                <RefreshCw className="h-3 w-3" />
                {t('create.n8n.refresh')}
              </button>
            </div>
            <div className="space-y-2 rounded-md border border-gray-200 p-3">
              <div className="flex items-center justify-between gap-2">
                <span className={`rounded border px-2 py-1 text-[11px] font-medium ${statusClass(latestRun.status)}`}>
                  {latestRun.status}
                </span>
                <span className="truncate font-mono text-[11px] text-gray-500">{latestRun.runId}</span>
              </div>
              <div className="grid grid-cols-1 gap-1 text-xs text-gray-600">
                {latestRun.n8nWorkflowId && (
                  <div className="flex items-center gap-1.5">
                    <Workflow className="h-3.5 w-3.5" />
                    <span className="truncate">{latestRun.n8nWorkflowId}</span>
                  </div>
                )}
                {latestRun.webhookUrl && (
                  <a className="truncate text-red-600 hover:underline" href={latestRun.webhookUrl} target="_blank" rel="noreferrer">
                    {latestRun.webhookUrl}
                  </a>
                )}
                {latestRun.workflowUrl && (
                  <a className="truncate text-red-600 hover:underline" href={latestRun.workflowUrl} target="_blank" rel="noreferrer">
                    {t('create.n8n.openWorkflow')}
                  </a>
                )}
                {latestRun.reportPath && (
                  <span className="truncate font-mono text-[11px] text-gray-500">{latestRun.reportPath}</span>
                )}
              </div>
              {latestRun.lintIssues.length > 0 && (
                <div className="rounded border border-gray-100 bg-gray-50 p-2 text-[11px] leading-relaxed text-gray-600">
                  {t('create.n8n.lintIssues', { count: latestRun.lintIssues.length })}
                </div>
              )}
              {latestRun.testResults.length > 0 && (
                <div className="space-y-1">
                  {latestRun.testResults.map((result, index) => (
                    <div key={`${result.name || 'test'}-${index}`} className="flex items-center gap-1.5 text-xs text-gray-600">
                      {result.success ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" /> : <FlaskConical className="h-3.5 w-3.5 text-red-600" />}
                      <span className="truncate">{String(result.name || `test-${index + 1}`)}</span>
                    </div>
                  ))}
                </div>
              )}
              <div className="grid grid-cols-2 gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => void handleRetry(latestRun.runId)}
                  disabled={busy !== null || !latestRun.n8nWorkflowId}
                  className="inline-flex items-center justify-center gap-1 rounded-md border border-gray-200 px-2 py-1.5 text-[11px] text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                >
                  <FlaskConical className="h-3 w-3" />
                  {t('create.n8n.retryTest')}
                </button>
                <button
                  type="button"
                  onClick={() => void handleCleanup(latestRun.runId)}
                  disabled={busy !== null || !latestRun.n8nWorkflowId}
                  className="inline-flex items-center justify-center gap-1 rounded-md border border-gray-200 px-2 py-1.5 text-[11px] text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                >
                  <Trash2 className="h-3 w-3" />
                  {t('create.n8n.cleanup')}
                </button>
              </div>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
