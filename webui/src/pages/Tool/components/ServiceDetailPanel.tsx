import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Info, Wrench, FileText, Activity, Zap, RefreshCw, Power, PowerOff,
  CheckCircle, XCircle, Cloud, Database, AlertTriangle, Eye, EyeOff, Save, Trash2,
} from 'lucide-react';
import type { Tool } from '@/api/tool';
import type { MCPCatalogCategory, MCPCatalogEntry, MCPCredentials, MCPServer, MCPServerDetail } from '@/types';
import { mcpAPI } from '@/api/mcp';
import { providerAPI } from '@/api/provider';
import { toolAPI } from '@/api/tool';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { getCatalogDescription, getMetadataDescription } from '@/utils/mcpCatalog';
import { EnabledBadge } from './badges';
import { buildMCPConfigFromForm, buildMCPFormDataFromConfig, MCPFormFields } from '../ToolSheets';
import type { MCPFormData, ConnStatus as MCPConnStatus } from '../ToolSheets';
import type { APIServiceCredentialField, APIServiceMetadata, ProviderCredentials } from '@/types';

function KvRowValue({ value }: { value: string }) {
  const [showTooltip, setShowTooltip] = useState(false);

  return (
    <div className="min-w-0 flex-1 flex justify-end">
      <div
        className="relative max-w-full"
        onMouseEnter={() => setShowTooltip(true)}
        onMouseLeave={() => setShowTooltip(false)}
        onFocus={() => setShowTooltip(true)}
        onBlur={() => setShowTooltip(false)}
      >
        <span
          className="block max-w-full text-sm text-gray-900 truncate text-right cursor-help"
          tabIndex={0}
        >
          {value}
        </span>
        {showTooltip && (
          <div className="absolute right-0 top-full z-20 mt-2 w-max max-w-[360px] rounded-lg bg-gray-900 px-3 py-2 text-left text-xs leading-relaxed text-white shadow-lg break-words">
            {value}
          </div>
        )}
      </div>
    </div>
  );
}

function KvRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center py-2.5 px-4 gap-4">
      <span className="text-sm text-gray-500 shrink-0">{label}</span>
      <KvRowValue value={value} />
    </div>
  );
}

export function MCPServerDetailPanel({
  server,
  serverTools,
  onConnect,
  onDisconnect,
  onRefresh,
  onStatusChange,
  onRemove,
  onSelectTool,
}: {
  server: MCPServer;
  serverTools: Tool[];
  onConnect: () => void;
  onDisconnect: () => void;
  onRefresh: () => Promise<void>;
  onStatusChange?: () => Promise<void>;
  onRemove?: () => void;
  onSelectTool: (tool: Tool) => void;
}) {
  const { t } = useTranslation('tool');
  const [detailTab, setDetailTab] = useState<'overview' | 'tools' | 'resources'>('overview');
  const [serverDetail, setServerDetail] = useState<MCPServerDetail | null>(null);
  const [formData, setFormData] = useState<MCPFormData | null>(null);
  const [initialFormData, setInitialFormData] = useState<MCPFormData | null>(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [testingConnection, setTestingConnection] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string; latency?: number; tools_count?: number } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [savingConfig, setSavingConfig] = useState(false);

  const createFormData = useCallback((detail: MCPServerDetail | null): MCPFormData => (
    buildMCPFormDataFromConfig(server.name, detail?.config, server.url)
  ), [server.name, server.url]);

  const loadServerDetail = useCallback(async () => {
    try {
      setDetailLoading(true);
      const res = await mcpAPI.get(server.name);
      setServerDetail(res.data);
      const nextFormData = createFormData(res.data);
      setFormData(nextFormData);
      setInitialFormData(nextFormData);
    } catch {
      setServerDetail(null);
      setFormData(null);
      setInitialFormData(null);
    } finally {
      setDetailLoading(false);
    }
  }, [createFormData, server.name]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!cancelled) {
        await loadServerDetail();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadServerDetail, server.name, server.status]);

  const isDirty = !!(formData && initialFormData
    && JSON.stringify(buildMCPConfigFromForm(formData)) !== JSON.stringify(buildMCPConfigFromForm(initialFormData)));

  const handleFormChange = (fields: Partial<MCPFormData>) => {
    setFormData((prev) => (prev ? { ...prev, ...fields } : prev));
    setTestResult(null);
  };

  const handleTestConnection = async () => {
    if (!formData) return;
    setTestingConnection(true);
    setTestResult(null);
    const currentConfig = buildMCPConfigFromForm(formData);
    try {
      const res = await mcpAPI.testExisting(server.name, currentConfig);
      const data = res.data;
      setTestResult({
        success: data.success ?? false,
        message: data.message ?? (data.success ? t('alert.connectionOk') : t('detail.testFailed')),
        latency: data.latency_ms,
        tools_count: data.tools_count,
      });
    } catch (err: any) {
      setTestResult({
        success: false,
        message: err.response?.data?.detail ?? err.message ?? t('detail.testFailed'),
      });
    } finally {
      setSavingConfig(false);
      setTestingConnection(false);
    }
  };

  const handleSaveConfig = async () => {
    if (!formData || !isDirty) return;
    try {
      setSavingConfig(true);
      setTestResult(null);
      await mcpAPI.update(server.name, buildMCPConfigFromForm(formData));
      await loadServerDetail();
      await onStatusChange?.();
    } catch (err: any) {
      alert(t('alert.saveFailed', { error: err.response?.data?.detail ?? err.message ?? t('alert.unknownError') }));
    } finally {
      setSavingConfig(false);
    }
  };

  const handleResetConfig = () => {
    if (!initialFormData) return;
    setFormData(initialFormData);
    setTestResult(null);
  };

  const handleRefreshTools = async () => {
    setRefreshing(true);
    try {
      await onRefresh();
    } finally {
      setRefreshing(false);
    }
  };

  const handleToggleEnabled = async () => {
    try {
      setSavingConfig(true);
      setTestResult(null);
      await mcpAPI.update(server.name, { enabled: server.status === 'disabled' });
      await loadServerDetail();
      await onStatusChange?.();
    } catch (err: any) {
      alert(t('alert.saveFailed', { error: err.response?.data?.detail ?? err.message ?? t('alert.unknownError') }));
    } finally {
      setSavingConfig(false);
    }
  };

  return (
    <div className="bg-white" onClick={(e) => e.stopPropagation()}>
      <div className="flex items-center border-b border-gray-200 px-5">
        {([
          { key: 'overview' as const, label: t('detail.tabs.overview'), icon: <Info className="w-3.5 h-3.5" /> },
          { key: 'tools' as const, label: t('detail.tabs.tools', { count: serverTools.length }), icon: <Wrench className="w-3.5 h-3.5" /> },
          { key: 'resources' as const, label: t('detail.tabs.resources', { count: serverDetail?.resources?.length || 0 }), icon: <FileText className="w-3.5 h-3.5" /> },
        ]).map((tab) => (
          <button
            key={tab.key}
            onClick={() => setDetailTab(tab.key)}
            className={`flex items-center gap-1.5 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${detailTab === tab.key ? 'border-red-500 text-red-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      <div className="p-5">
        {detailLoading ? (
          <div className="flex justify-center py-8"><LoadingSpinner /></div>
        ) : detailTab === 'overview' ? (
          <div className="space-y-5">
            {formData && (
              <MCPFormFields
                formData={formData}
                onChange={handleFormChange}
                disabledFields={{ name: true, connType: true }}
                connStatus={savingConfig && !testingConnection
                  ? 'saving'
                  : testingConnection
                  ? 'testing'
                  : testResult?.success === false
                      ? 'failed'
                      : server.status === 'connected'
                        ? 'connected'
                        : testResult?.success === true
                          ? 'tested'
                          : 'idle'}
                testResult={testResult ? { success: testResult.success, message: testResult.message, tools_count: testResult.tools_count } : null}
                onTestConnection={handleTestConnection}
                isTesting={testingConnection}
              />
            )}

            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 flex items-center justify-between">
                <div>
                  <div className="text-xs text-gray-500">{t('detail.registeredTools')}</div>
                  <div className="text-lg font-semibold text-gray-900">{serverDetail?.status?.tools_count ?? serverTools.length}</div>
                </div>
                <button
                  onClick={handleRefreshTools}
                  disabled={refreshing || server.status !== 'connected'}
                  title={t('detail.refreshTools')}
                  className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-200 rounded-md disabled:opacity-40 transition-colors"
                >
                  <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
                </button>
              </div>
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
                <div className="text-xs text-gray-500">{t('detail.availableResources')}</div>
                <div className="text-lg font-semibold text-gray-900">{serverDetail?.status?.resources_count ?? serverDetail?.resources?.length ?? 0}</div>
              </div>
            </div>

            <div className="flex items-center justify-end gap-2">
              {isDirty && (
                <>
                  <button
                    onClick={handleResetConfig}
                    disabled={savingConfig}
                    className="inline-flex items-center gap-2 px-4 py-2.5 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 text-sm font-medium transition-colors disabled:opacity-50"
                  >
                    {t('button.cancel')}
                  </button>
                  <button
                    onClick={handleSaveConfig}
                    disabled={savingConfig}
                    className="inline-flex items-center gap-2 px-4 py-2.5 bg-red-600 text-white rounded-lg hover:bg-red-700 text-sm font-medium transition-colors disabled:opacity-50"
                  >
                    {savingConfig ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                    {savingConfig ? t('button.saving') : t('button.save')}
                  </button>
                </>
              )}
              {server.status === 'connected' ? (
                <button onClick={onDisconnect} className="inline-flex items-center gap-2 px-4 py-2.5 border border-red-300 text-red-700 rounded-lg hover:bg-red-50 text-sm font-medium transition-colors">
                  <PowerOff className="w-4 h-4" />{t('detail.disconnectConn')}
                </button>
              ) : server.status === 'disabled' ? (
                <button onClick={handleToggleEnabled} disabled={savingConfig} className="inline-flex items-center gap-2 px-4 py-2.5 bg-green-600 text-white rounded-lg hover:bg-green-700 text-sm font-medium transition-colors disabled:opacity-50">
                  <Power className="w-4 h-4" />{t('detail.enableServer')}
                </button>
              ) : (
                <button onClick={onConnect} className="inline-flex items-center gap-2 px-4 py-2.5 bg-red-600 text-white rounded-lg hover:bg-red-700 text-sm font-medium transition-colors">
                  <Power className="w-4 h-4" />{t('detail.connectConn')}
                </button>
              )}
              {server.status !== 'disabled' && (
                <button onClick={handleToggleEnabled} disabled={savingConfig} className="inline-flex items-center gap-2 px-4 py-2.5 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 text-sm font-medium transition-colors disabled:opacity-50">
                  <PowerOff className="w-4 h-4" />{t('detail.disableServer')}
                </button>
              )}
              {onRemove && (
                <button onClick={onRemove} className="inline-flex items-center gap-2 px-4 py-2.5 border border-red-300 text-red-700 rounded-lg hover:bg-red-50 text-sm font-medium transition-colors">
                  <Trash2 className="w-4 h-4" />{t('detail.removeServer')}
                </button>
              )}
            </div>
          </div>
        ) : detailTab === 'tools' ? (
          serverTools.length === 0 ? (
            <div className="text-center py-8 text-sm text-gray-500">{t('detail.noRegisteredTools')}</div>
          ) : (
            <div className="rounded-lg border border-gray-200 overflow-hidden">
              <table className="w-full table-fixed divide-y divide-gray-100">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="w-2/5 px-4 py-2.5 text-left text-xs font-medium text-gray-500 uppercase">{t('detail.tableToolName')}</th>
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-gray-500 uppercase">{t('detail.tableDesc')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {serverTools.map((tool) => (
                    <tr key={tool.name} onClick={() => onSelectTool(tool)} className="hover:bg-red-50 cursor-pointer transition-colors">
                      <td className="px-4 py-3"><span className="text-sm font-medium text-gray-900 font-mono break-all">{tool.name}</span></td>
                      <td className="px-4 py-3"><span className="text-sm text-gray-600 line-clamp-2 leading-relaxed">{tool.description}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : (!serverDetail?.resources || serverDetail.resources.length === 0) ? (
          <div className="text-center py-8 text-sm text-gray-500">{t('detail.noResources')}</div>
        ) : (
          <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-100 table-fixed">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="w-[25%] px-5 py-2.5 text-left text-xs font-medium text-gray-500 uppercase">{t('detail.tableResourceName')}</th>
                    <th className="w-[35%] px-5 py-2.5 text-left text-xs font-medium text-gray-500 uppercase">URI</th>
                    <th className="px-5 py-2.5 text-left text-xs font-medium text-gray-500 uppercase">{t('detail.tableDesc')}</th>
                    <th className="w-[100px] px-5 py-2.5 text-left text-xs font-medium text-gray-500 uppercase whitespace-nowrap">{t('detail.tableType')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {serverDetail.resources.map((res) => (
                    <tr key={res.uri} className="hover:bg-gray-50">
                      <td className="px-5 py-2.5 truncate"><span className="text-sm font-medium text-gray-900">{res.name}</span></td>
                      <td className="px-5 py-2.5 truncate"><span className="text-sm text-gray-600 font-mono">{res.uri}</span></td>
                      <td className="px-5 py-2.5"><span className="text-sm text-gray-600 line-clamp-1">{res.description || '-'}</span></td>
                      <td className="px-5 py-2.5 whitespace-nowrap"><span className="text-xs text-gray-500 font-mono">{res.mime_type || '-'}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function CatalogAPIDetailPanel({
  entry,
  catalogCategories,
}: {
  entry: MCPCatalogEntry;
  catalogCategories: Record<string, MCPCatalogCategory>;
}) {
  const { t, i18n } = useTranslation('tool');
  const [testing, setTesting] = useState(false);
  const [testElapsed, setTestElapsed] = useState(0);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string; latency_ms?: number; tool_tested?: string } | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => {
    if (timerRef.current) clearInterval(timerRef.current);
  }, []);

  const handleTestConnect = async () => {
    setTesting(true);
    setTestResult(null);
    setTestElapsed(0);
    const start = Date.now();
    timerRef.current = setInterval(() => setTestElapsed((Date.now() - start) / 1000), 100);
    try {
      const res = await mcpAPI.testCredentials(entry.id);
      setTestResult(res.data);
    } catch (err: any) {
      setTestResult({ success: false, message: t('alert.testFailed', { error: err.message || t('alert.unknownError') }) });
    } finally {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      setTesting(false);
    }
  };

  const handleConnect = async () => {
    try {
      await mcpAPI.connect(entry.id);
      setTestResult({ success: true, message: t('alert.connectionEstablished') });
    } catch (err: any) {
      setTestResult({ success: false, message: t('alert.connectFailed', { error: err.message || t('alert.unknownError') }) });
    }
  };

  return (
    <div className="bg-white" onClick={(e) => e.stopPropagation()}>
      <div className="p-5">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="space-y-5">
            <div className="bg-white rounded-lg border border-gray-200 p-4">
              <h4 className="text-sm font-semibold text-gray-900 mb-3 flex items-center">
                <Activity className="w-4 h-4 mr-2 text-purple-500" />
                {t('serviceInfo.title')}
              </h4>
              <div className="space-y-3">
                <div className="flex items-center justify-between"><span className="text-sm text-gray-500">{t('serviceInfo.name')}</span><span className="text-sm text-gray-900 font-medium">{entry.name}</span></div>
                <div className="flex items-center justify-between"><span className="text-sm text-gray-500">{t('serviceInfo.type')}</span><span className="px-2 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-800">{t('serviceInfo.apiType')}</span></div>
                <div className="flex items-center justify-between"><span className="text-sm text-gray-500">{t('serviceInfo.category')}</span><span className="text-sm text-gray-900">{catalogCategories[entry.category]?.label || entry.category}</span></div>
                <div className="flex items-center justify-between"><span className="text-sm text-gray-500">{t('serviceInfo.language')}</span><span className="text-sm text-gray-900">{entry.language}</span></div>
                <div className="pt-2"><span className="text-sm text-gray-500 block mb-1">{t('serviceInfo.description')}</span><p className="text-sm text-gray-900">{getCatalogDescription(entry, i18n.language)}</p></div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-gray-500">{t('serviceInfo.github')}</span>
                  <a href={`https://github.com/${entry.github}`} target="_blank" rel="noopener noreferrer" className="text-sm text-red-600 hover:text-red-800">{entry.github}</a>
                </div>
                {entry.requires_auth && (
                  <div className="pt-2 border-t border-gray-100">
                    <span className="text-sm text-gray-500 block mb-2">{t('serviceInfo.requiredKeys')}</span>
                    {Object.entries(entry.env_vars).filter(([, spec]) => spec.secret).map(([key, spec]) => (
                      <div key={key} className="flex items-center justify-between py-1">
                        <code className="text-xs bg-gray-100 px-1.5 py-0.5 rounded">{key}</code>
                        <span className="text-xs text-gray-500">{spec.description}</span>
                      </div>
                    ))}
                  </div>
                )}
                {entry.tags && entry.tags.length > 0 && (
                  <div className="pt-2 border-t border-gray-100">
                    <span className="text-sm text-gray-500 block mb-2">{t('serviceInfo.tags')}</span>
                    <div className="flex flex-wrap gap-1.5">
                      {entry.tags.map((tag) => (
                        <span key={tag} className="px-2 py-0.5 bg-gray-100 text-gray-700 text-xs rounded">#{tag}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="space-y-5">
            <div className="bg-white rounded-lg border border-gray-200 p-4">
              <h4 className="text-sm font-semibold text-gray-900 mb-3 flex items-center">
                <Zap className="w-4 h-4 mr-2 text-amber-500" />
                {t('detail.quickActions')}
              </h4>
              <div className="space-y-2.5">
                <button
                  onClick={handleTestConnect}
                  disabled={testing}
                  className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 border rounded-lg text-sm font-medium transition-colors ${testing ? 'border-red-300 bg-red-50 text-red-600 cursor-not-allowed' : 'bg-white border-gray-300 text-gray-700 hover:bg-gray-50'}`}
                >
                  {testing ? <><RefreshCw className="w-4 h-4 animate-spin" />{t('button.testing')}</> : <><Activity className="w-4 h-4" />{t('button.testConnectivity')}</>}
                </button>
                <button onClick={handleConnect} className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-red-600 text-white rounded-lg text-sm font-medium hover:bg-red-700 transition-colors">
                  <Power className="w-4 h-4" />{t('detail.connectConn')}
                </button>
              </div>
            </div>

            {testing && (
              <div className="p-3 rounded-lg border border-red-200 bg-red-50 text-red-700">
                <div className="flex items-center">
                  <div className="relative mr-3">
                    <div className="w-8 h-8 rounded-full border-2 border-red-200 border-t-red-600 animate-spin" />
                  </div>
                  <div className="flex-1">
                    <div className="text-sm font-medium">{t('testing.connectivityTitle')}</div>
                    <div className="text-xs mt-0.5 text-red-600">
                      {t('testing.connectivityDesc')}
                      <span className="ml-2 font-mono tabular-nums">{testElapsed.toFixed(1)}s</span>
                    </div>
                  </div>
                </div>
                <div className="mt-2.5 h-1 bg-red-100 rounded-full overflow-hidden">
                  <div className="h-full bg-red-500 rounded-full animate-pulse" style={{ width: `${Math.min(95, testElapsed * 8)}%`, transition: 'width 0.3s ease-out' }} />
                </div>
              </div>
            )}

            {!testing && testResult && (
              <div className={`p-3 rounded-lg border ${testResult.success ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
                <div className="flex items-start">
                  {testResult.success ? <CheckCircle className="w-4 h-4 text-green-600 mr-2 mt-0.5 flex-shrink-0" /> : <XCircle className="w-4 h-4 text-red-600 mr-2 mt-0.5 flex-shrink-0" />}
                  <div className="flex-1">
                    <div className={`text-sm font-medium ${testResult.success ? 'text-green-800' : 'text-red-800'}`}>
                      {testResult.success ? t('testing.testSuccess') : t('testing.testFailed')}
                    </div>
                    <div className={`text-xs mt-0.5 ${testResult.success ? 'text-green-700' : 'text-red-700'}`}>{testResult.message}</div>
                    {testResult.latency_ms != null && (
                      <div className={`text-xs mt-1 ${testResult.success ? 'text-green-600' : 'text-red-600'} opacity-75`}>{t('testing.responseLatency', { latency: testResult.latency_ms })}</div>
                    )}
                    {testResult.tool_tested && (
                      <div className={`text-xs mt-0.5 ${testResult.success ? 'text-green-600' : 'text-red-600'} opacity-75`}>{t('testing.testToolLabel', { tool: testResult.tool_tested })}</div>
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function getCredentialFieldLabel(field: APIServiceCredentialField, t: (key: string) => string): string {
  const fallbackLabels: Record<string, string> = {
    api_key: 'API Key',
    base_url: t('serviceInfo.apiUrl'),
    secret: t('serviceInfo.secret'),
    username: t('serviceInfo.username'),
    password: t('serviceInfo.password'),
  };
  return fallbackLabels[field.key] || field.label || field.key;
}

function getCredentialFieldPlaceholder(field: APIServiceCredentialField, t: (key: string, opts?: Record<string, unknown>) => string): string {
  const placeholders: Record<string, string> = {
    api_key: t('serviceInfo.enterApiKey'),
    base_url: t('serviceInfo.enterBaseUrl'),
    secret: t('serviceInfo.enterSecret'),
    username: t('serviceInfo.enterUsername'),
    password: t('serviceInfo.enterPassword'),
  };
  return placeholders[field.key] || t('credentials.enterField', { field: getCredentialFieldLabel(field, t) });
}

function getLegacyCredentialValue(credentials: ProviderCredentials | null, key: string): string {
  if (!credentials) return '';
  switch (key) {
    case 'api_key':
      return credentials.api_key || '';
    case 'secret':
      return credentials.secret || '';
    case 'base_url':
      return credentials.base_url || '';
    case 'username':
      return credentials.username || '';
    default:
      return '';
  }
}

function buildFallbackCredentialSchema(metadata: APIServiceMetadata | null): APIServiceCredentialField[] {
  if (!metadata) return [];
  const schema = metadata.credential_schema;
  if (Array.isArray(schema) && schema.length > 0) {
    return schema;
  }

  const auth = metadata.authentication;
  const fallback: APIServiceCredentialField[] = [];
  if (auth && typeof auth === 'object' && (auth.secret_key || auth.api_key_secret || auth.secret)) {
    fallback.push({
      key: 'api_key',
      label: 'API Key',
      storage: 'secret',
      sensitive: true,
      required: false,
      input_type: 'password',
      config_key: 'apiKey',
      secret_id: auth.secret_key || auth.api_key_secret || auth.secret,
    });
  }
  if (auth && typeof auth === 'object' && auth.secret_secret) {
    fallback.push({
      key: 'secret',
      label: 'Secret',
      storage: 'secret',
      sensitive: true,
      required: false,
      input_type: 'password',
      config_key: 'secret',
      secret_id: auth.secret_secret,
    });
  }
  if (metadata.base_url) {
    fallback.push({
      key: 'base_url',
      label: 'Base URL',
      storage: 'config',
      sensitive: false,
      required: false,
      input_type: 'url',
      config_key: 'base_url',
      default_value: metadata.base_url,
    });
  }
  return fallback;
}

export function APIServiceDetailPanel({
  serviceName,
  serviceTools,
  onSelectTool,
  onTestingStart,
  onTestingEnd,
  initialStatus,
  onTestResult,
  enabled,
  onToggleEnabled,
  onDelete,
  builtin,
  verifySsl,
  onToggleVerifySsl,
}: {
  serviceName: string;
  serviceTools: Tool[];
  onSelectTool: (tool: Tool) => void;
  onTestingStart?: () => void;
  onTestingEnd?: () => void;
  initialStatus?: { status: string; latency_ms?: number };
  onTestResult?: (name: string, result: { status: string; latency_ms?: number }) => void;
  enabled?: boolean;
  onToggleEnabled?: (enabled: boolean) => Promise<void> | void;
  onDelete?: () => Promise<void> | void;
  builtin?: boolean;
  verifySsl?: boolean;
  onToggleVerifySsl?: (verifySsl: boolean) => Promise<void> | void;
}) {
  const { t, i18n } = useTranslation('tool');
  const [detailTab, setDetailTab] = useState<'overview' | 'tools'>('overview');
  const [metadata, setMetadata] = useState<APIServiceMetadata | null>(null);
  const [metadataLoading, setMetadataLoading] = useState(true);
  const [credentials, setCredentials] = useState<ProviderCredentials | null>(null);
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [fieldVisibility, setFieldVisibility] = useState<Record<string, boolean>>({});
  const [apiKeySaving, setApiKeySaving] = useState(false);
  const formValuesOrigRef = useRef<Record<string, string>>({});
  const [quickTesting, setQuickTesting] = useState(false);
  const [quickTestResult, setQuickTestResult] = useState<{ success: boolean; message: string; latency_ms?: number; tool_tested?: string } | null>(null);
  const quickTestTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const credentialSchema = useMemo(
    () => buildFallbackCredentialSchema(metadata),
    [metadata],
  );

  useEffect(() => () => {
    if (quickTestTimerRef.current) clearInterval(quickTestTimerRef.current);
  }, []);

  const applyCredentialState = useCallback((
    nextMetadata: APIServiceMetadata | null,
    nextCredentials: ProviderCredentials | null,
  ) => {
    const schema = buildFallbackCredentialSchema(nextMetadata);
    const nextValues: Record<string, string> = {};

    schema.forEach((field) => {
      const dynamicValue = nextCredentials?.fields?.[field.key];
      const value = dynamicValue ?? getLegacyCredentialValue(nextCredentials, field.key) ?? '';
      const effectiveDefault = field.default_value || (field.key === 'base_url' ? nextMetadata?.base_url : undefined);
      nextValues[field.key] = (effectiveDefault && value === effectiveDefault) ? '' : (value || '');
    });

    setFormValues(nextValues);
    formValuesOrigRef.current = nextValues;
  }, []);

  const handleQuickTestConnectivity = async () => {
    setQuickTesting(true);
    setQuickTestResult(null);
    onTestingStart?.();
    try {
      const res = await providerAPI.testCredentials(serviceName);
      setQuickTestResult(res.data);
      onTestResult?.(serviceName, {
        status: res.data?.success ? 'connected' : 'error',
        latency_ms: res.data?.latency_ms,
      });
    } catch (err: any) {
      const msg = t('alert.testFailed', { error: err.message || t('alert.unknownError') });
      setQuickTestResult({ success: false, message: msg });
      onTestResult?.(serviceName, { status: 'error' });
    } finally {
      if (quickTestTimerRef.current) {
        clearInterval(quickTestTimerRef.current);
        quickTestTimerRef.current = null;
      }
      setQuickTesting(false);
      onTestingEnd?.();
    }
  };

  const loadData = useCallback(async () => {
    try {
      setMetadataLoading(true);
      const [metaRes, credRes] = await Promise.allSettled([
        providerAPI.getMetadata(serviceName),
        providerAPI.getServiceCredentials(serviceName),
      ]);
      const meta = metaRes.status === 'fulfilled' ? metaRes.value.data : null;
      const cred = credRes.status === 'fulfilled' ? credRes.value.data : null;
      setMetadata(meta);
      setCredentials(cred);
      applyCredentialState(meta, cred);
    } catch (err) {
      console.error('Failed to load API service data:', err);
      setMetadata(null);
      setCredentials(null);
      setFormValues({});
      formValuesOrigRef.current = {};
    } finally {
      setMetadataLoading(false);
    }
  }, [applyCredentialState, serviceName]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const hasCredentialChanges = credentialSchema.some(
    (field) => (formValues[field.key] || '') !== (formValuesOrigRef.current[field.key] || ''),
  );

  const handleSaveCredentials = async () => {
    if (!hasCredentialChanges) return;
    setApiKeySaving(true);
    try {
      const updatedFields: Record<string, string | undefined> = {};
      for (const field of credentialSchema) {
        const nextValue = formValues[field.key] || '';
        const previousValue = formValuesOrigRef.current[field.key] || '';
        if (nextValue !== previousValue) {
          updatedFields[field.key] = nextValue;
        }
      }

      const payload: Record<string, any> = { fields: updatedFields };
      if (Object.prototype.hasOwnProperty.call(updatedFields, 'api_key')) payload.api_key = updatedFields.api_key;
      if (Object.prototype.hasOwnProperty.call(updatedFields, 'secret')) payload.secret = updatedFields.secret;
      if (Object.prototype.hasOwnProperty.call(updatedFields, 'base_url')) payload.base_url = updatedFields.base_url ?? '';
      if (Object.prototype.hasOwnProperty.call(updatedFields, 'username')) payload.username = updatedFields.username ?? '';

      await providerAPI.setServiceCredentials(serviceName, payload);
      const credRes = await providerAPI.getServiceCredentials(serviceName);
      const cred = credRes.data;
      setCredentials(cred);
      applyCredentialState(metadata, cred);
    } catch (err: any) {
      alert(t('alert.saveFailed', { error: err.message || t('alert.unknownError') }));
    } finally {
      setApiKeySaving(false);
    }
  };

  return (
    <div className="bg-white" onClick={(e) => e.stopPropagation()}>
      <div className="flex items-center border-b border-gray-200 px-5">
        {([
          { key: 'overview' as const, label: t('detail.tabs.overview'), icon: <Info className="w-3.5 h-3.5" /> },
          { key: 'tools' as const, label: t('detail.tabs.tools', { count: serviceTools.length }), icon: <Wrench className="w-3.5 h-3.5" /> },
        ]).map((tab) => (
          <button
            key={tab.key}
            onClick={() => setDetailTab(tab.key)}
            className={`flex items-center gap-1.5 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${detailTab === tab.key ? 'border-purple-500 text-purple-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      <div className="p-5">
        {metadataLoading ? (
          <div className="flex justify-center py-8"><LoadingSpinner /></div>
        ) : detailTab === 'overview' ? (
          <div className="space-y-5">
            <div className="rounded-lg border border-gray-200 divide-y divide-gray-100">
              {(() => {
                const status = quickTesting
                  ? 'testing'
                  : quickTestResult != null
                    ? (quickTestResult.success ? 'connected' : 'error')
                    : (initialStatus?.status || 'unknown');
                const cfg: Record<string, { label: string; cls: string; dot: string }> = {
                  connected: { label: t('statusBadge.connected'), cls: 'bg-green-100 text-green-800', dot: 'bg-green-500' },
                  healthy: { label: t('statusBadge.connected'), cls: 'bg-green-100 text-green-800', dot: 'bg-green-500' },
                  testing: { label: t('button.testing'), cls: 'bg-red-100 text-red-800', dot: 'bg-red-500 animate-pulse' },
                  error: { label: t('statusBadge.error'), cls: 'bg-red-100 text-red-800', dot: 'bg-red-500' },
                  unknown: { label: t('statusBadge.unknown'), cls: 'bg-gray-100 text-gray-600', dot: 'bg-gray-400' },
                };
                const c = cfg[status] || cfg.unknown;
                return (
                  <div className="flex justify-between items-center py-2.5 px-4 gap-4">
                    <span className="text-sm text-gray-500 shrink-0">{t('detail.connectionStatus')}</span>
                    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${c.cls}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />
                      {c.label}
                      {quickTestResult?.success && quickTestResult.latency_ms != null && <span className="opacity-75">· {quickTestResult.latency_ms}ms</span>}
                    </span>
                  </div>
                );
              })()}
              <KvRow label={t('serviceInfo.name')} value={metadata?.name || serviceName} />
              {metadata?.version && <KvRow label={t('serviceInfo.version')} value={metadata.version} />}
              {getMetadataDescription(metadata as any, i18n.language) && (
                <KvRow label={t('serviceInfo.description')} value={getMetadataDescription(metadata as any, i18n.language)} />
              )}
              <KvRow label={t('serviceInfo.type')} value={t('serviceInfo.apiType')} />
              {metadata?.category && <KvRow label={t('serviceInfo.category')} value={metadata.category} />}
              {metadata?.docs_url && (
                <div className="flex justify-between items-center py-2.5 px-4 gap-4">
                  <span className="text-sm text-gray-500 shrink-0">{t('serviceInfo.documentation')}</span>
                  <a href={metadata.docs_url} target="_blank" rel="noopener noreferrer" className="text-sm text-red-600 hover:text-red-800">{t('serviceInfo.viewDocs')} →</a>
                </div>
              )}
              {credentials?.secret_id && (
                <div className="flex justify-between items-center py-2.5 px-4 gap-4">
                  <span className="text-sm text-gray-500 shrink-0">Secret ID</span>
                  <code className="text-sm font-mono bg-gray-100 px-2 py-1 rounded truncate">{credentials.secret_id}</code>
                </div>
              )}
              {credentials !== null && credentialSchema.length > 0 && (
                <div className="flex flex-col py-2 px-4 gap-3">
                  {credentialSchema.map((field) => {
                    const label = getCredentialFieldLabel(field, t);
                    const placeholder = getCredentialFieldPlaceholder(field, t);
                    const value = formValues[field.key] || '';
                    const visible = !!fieldVisibility[field.key];
                    const isSensitive = field.sensitive || field.input_type === 'password';

                    return (
                      <div key={field.key} className="flex items-center gap-3">
                        <span className="text-sm text-gray-500 shrink-0 w-20">{label}</span>
                        <div className="flex-1 flex items-center gap-1.5 min-w-0">
                          <input
                            type={isSensitive && !visible ? 'password' : 'text'}
                            value={value}
                            onChange={(e) => setFormValues((prev) => ({ ...prev, [field.key]: e.target.value }))}
                            placeholder={placeholder}
                            className="flex-1 min-w-0 px-2 py-1 border border-gray-200 rounded text-sm bg-gray-50 focus:outline-none focus:ring-1 focus:ring-red-400 focus:bg-white focus:border-red-400 transition-colors"
                          />
                          {isSensitive && (
                            <button
                              type="button"
                              onClick={() => setFieldVisibility((prev) => ({ ...prev, [field.key]: !prev[field.key] }))}
                              className="shrink-0 text-gray-400 hover:text-gray-600 p-1 rounded transition-colors"
                              title={visible ? t('detail.hide') : t('detail.show')}
                            >
                              {visible ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                  {hasCredentialChanges && (
                    <div className="flex justify-end gap-1.5">
                      <button
                        type="button"
                        onClick={handleSaveCredentials}
                        disabled={apiKeySaving}
                        className="shrink-0 inline-flex items-center gap-1 px-2 py-1 bg-red-600 text-white rounded text-xs font-medium hover:bg-red-700 disabled:opacity-50 transition-colors"
                      >
                        {apiKeySaving ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                        {t('button.save')}
                      </button>
                      <button
                        type="button"
                        onClick={() => setFormValues({ ...formValuesOrigRef.current })}
                        className="shrink-0 px-2 py-1 border border-gray-300 text-gray-600 rounded text-xs hover:bg-gray-50 transition-colors"
                      >
                        {t('button.cancel')}
                      </button>
                    </div>
                  )}
                </div>
              )}
              <KvRow label={t('serviceInfo.toolCount')} value={String(serviceTools.length)} />
              <div className="flex justify-between items-center py-2.5 px-4 gap-4">
                <div>
                  <span className="text-sm text-gray-500 shrink-0">{t('serviceInfo.sslVerify', { defaultValue: 'SSL 验证' })}</span>
                  <p className="text-xs text-gray-400 mt-0.5">{t('serviceInfo.sslVerifyDesc', { defaultValue: '关闭可访问内网 IP 部署的服务' })}</p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={!!verifySsl}
                  onClick={() => onToggleVerifySsl?.(!verifySsl)}
                  className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer items-center rounded-full transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-purple-500 focus:ring-offset-1 ${verifySsl ? 'bg-purple-600' : 'bg-gray-300'}`}
                >
                  <span
                    className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform duration-200 ${verifySsl ? 'translate-x-4' : 'translate-x-1'}`}
                  />
                </button>
              </div>
            </div>

            <div className="flex flex-col gap-2">
              <button onClick={handleQuickTestConnectivity} disabled={quickTesting} className="inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-white border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 disabled:opacity-50 text-sm font-medium transition-colors">
                {quickTesting ? <><RefreshCw className="w-4 h-4 animate-spin" />{t('button.testing')}</> : <><Activity className="w-4 h-4" />{t('button.testConnectivity')}</>}
              </button>
              <div className="flex gap-2">
                <button
                  onClick={() => onToggleEnabled?.(!enabled)}
                  className={`flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-colors ${enabled ? 'border border-gray-300 text-gray-700 bg-white hover:bg-gray-50' : 'bg-green-600 text-white hover:bg-green-700'}`}
                >
                  {enabled ? <PowerOff className="w-4 h-4" /> : <Power className="w-4 h-4" />}
                  {enabled ? t('detail.disableServer') : t('detail.enableServer')}
                </button>
                <button
                  onClick={() => { if (!builtin) onDelete?.(); }}
                  disabled={!!builtin}
                  className={`inline-flex items-center justify-center gap-2 px-4 py-2.5 border rounded-lg text-sm font-medium transition-colors ${builtin ? 'border-gray-200 text-gray-400 bg-gray-50 cursor-not-allowed' : 'border-red-200 text-red-600 bg-white hover:bg-red-50'}`}
                  title={builtin ? t('api.builtinCannotDelete') : t('button.delete')}
                >
                  <Trash2 className="w-4 h-4" />
                  {t('button.delete')}
                </button>
              </div>
              {!quickTesting && quickTestResult && (
                <div className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${quickTestResult.success ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800'}`}>
                  {quickTestResult.success ? <CheckCircle className="w-4 h-4 mt-0.5 flex-shrink-0" /> : <XCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />}
                  <span>{quickTestResult.message}</span>
                  {quickTestResult.latency_ms != null && <span className="text-xs opacity-90"> · {quickTestResult.latency_ms}ms</span>}
                </div>
              )}
            </div>
          </div>
        ) : serviceTools.length === 0 ? (
          <div className="text-center py-8 text-sm text-gray-500">{t('serviceInfo.noServiceTools')}</div>
        ) : (
          <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-100 table-fixed">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="w-[30%] px-5 py-2.5 text-left text-xs font-medium text-gray-500 uppercase">{t('detail.tableToolName')}</th>
                    <th className="px-5 py-2.5 text-left text-xs font-medium text-gray-500 uppercase">{t('detail.tableDesc')}</th>
                    <th className="w-[100px] px-5 py-2.5 text-left text-xs font-medium text-gray-500 uppercase whitespace-nowrap">{t('detail.tableStatus')}</th>
                    <th className="w-[120px] px-5 py-2.5 text-left text-xs font-medium text-gray-500 uppercase whitespace-nowrap">{t('detail.tableActions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {serviceTools.map((tool) => (
                    <tr key={tool.name} className="hover:bg-gray-50">
                      <td className="px-5 py-2.5 truncate"><span className="text-sm font-medium text-gray-900 font-mono">{tool.name}</span></td>
                      <td className="px-5 py-2.5"><span className="text-sm text-gray-600 line-clamp-1">{tool.description}</span></td>
                      <td className="px-5 py-2.5 whitespace-nowrap"><EnabledBadge enabled={tool.enabled} /></td>
                      <td className="px-5 py-2.5 whitespace-nowrap"><button onClick={() => onSelectTool(tool)} className="text-sm text-red-600 hover:text-red-800">{t('detail.testDetail')}</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
