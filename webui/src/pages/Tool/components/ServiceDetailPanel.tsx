import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  IconInfoCircle,
  IconTool,
  IconFileText,
  IconActivity,
  IconBolt,
  IconRefresh,
  IconPower,
  IconPlugOff,
  IconCircleCheck,
  IconCircleX,
  IconCloud,
  IconDatabase,
  IconAlertTriangle,
  IconEye,
  IconEyeOff,
  IconDeviceFloppy,
  IconTrash,
} from '@tabler/icons-react';
import type { Tool } from '@/api/tool';
import type { MCPCatalogCategory, MCPCatalogEntry, MCPCredentials, MCPServer, MCPServerDetail } from '@/types';
import { mcpAPI } from '@/api/mcp';
import { providerAPI } from '@/api/provider';
import { toolAPI } from '@/api/tool';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { getCatalogDescription, getMetadataDescription } from '@/utils/mcpCatalog';
import { EnabledBadge } from './badges';
import { buildMCPConfigFromForm, buildMCPFormDataFromConfig, getMCPFormError, MCPFormFields } from '../ToolSheets';
import type { MCPFormData, ConnStatus as MCPConnStatus } from '../ToolSheets';
import type { APIServiceCredentialField, APIServiceMetadata, ProviderCredentials } from '@/types';
import ThreatBookMCPConfigPanel from './ThreatBookMCPConfigPanel';
import ThreatBookAPIConfigPanel from './ThreatBookAPIConfigPanel';

function isThreatBookMCPServer(serverName: string): boolean {
  return serverName.trim().toLowerCase() === 'threatbook_mcp';
}

function isThreatBookAPIService(serviceName: string): boolean {
  return ['threatbook-cn', 'threatbook-io'].includes(serviceName.trim().toLowerCase());
}

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
          className="block max-w-full text-sm text-fg truncate text-right cursor-help"
          tabIndex={0}
        >
          {value}
        </span>
        {showTooltip && (
          <div className="absolute right-0 top-full z-20 mt-2 w-max max-w-[360px] rounded-control bg-subtle px-3 py-2 text-left text-xs text-fg shadow-lg break-words">
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
      <span className="text-sm text-fg-muted shrink-0">{label}</span>
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
  const isThreatBookServer = isThreatBookMCPServer(server.name);

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
    if (getMCPFormError(formData) === 'invalidHeaders') {
      alert(t('alert.invalidHeaders'));
      return;
    }
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
    if (getMCPFormError(formData) === 'invalidHeaders') {
      alert(t('alert.invalidHeaders'));
      return;
    }
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
    <div className="bg-surface" onClick={(e) => e.stopPropagation()}>
      <div className="flex items-center border-b border-line px-5">
        {([
          { key: 'overview' as const, label: t('detail.tabs.overview'), icon: <IconInfoCircle aria-hidden="true" className="w-4 h-4" /> },
          { key: 'tools' as const, label: t('detail.tabs.tools', { count: serverTools.length }), icon: <IconTool aria-hidden="true" className="w-4 h-4" /> },
          { key: 'resources' as const, label: t('detail.tabs.resources', { count: serverDetail?.resources?.length || 0 }), icon: <IconFileText aria-hidden="true" className="w-4 h-4" /> },
        ]).map((tab) => (
          <button
            key={tab.key}
            onClick={() => setDetailTab(tab.key)}
            className={`flex items-center gap-1.5 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${detailTab === tab.key ? 'border-danger text-danger' : 'border-transparent text-fg-muted hover:text-fg-secondary'}`}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>

      <div className="p-5 min-h-0">
        {detailLoading ? (
          <div className="flex justify-center py-8"><LoadingSpinner /></div>
        ) : detailTab === 'overview' ? (
          <div className="space-y-5">
            {isThreatBookServer ? (
              <ThreatBookMCPConfigPanel
                serverName={server.name}
                serverStatus={server.status}
                configUrl={serverDetail?.config?.url || server.url}
                onConfigured={async () => {
                  await loadServerDetail();
                  await onStatusChange?.();
                }}
              />
            ) : formData && (
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
              <div className="rounded-control border border-line bg-subtle px-3 py-2 flex items-center justify-between">
                <div>
                  <div className="text-xs text-fg-muted">{t('detail.registeredTools')}</div>
                  <div className="text-lg font-semibold text-fg">{serverDetail?.status?.tools_count ?? serverTools.length}</div>
                </div>
                <button
                  onClick={handleRefreshTools}
                  disabled={refreshing || server.status !== 'connected'}
                  title={t('detail.refreshTools')}
                  className="ui-btn-icon inline-flex items-center justify-center p-1.5 text-fg-muted hover:text-fg-secondary hover:bg-hover rounded-control disabled:bg-disabled disabled:text-fg-disabled transition-colors"
                >
                  <IconRefresh aria-hidden="true" className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
                </button>
              </div>
              <div className="rounded-control border border-line bg-subtle px-3 py-2">
                <div className="text-xs text-fg-muted">{t('detail.availableResources')}</div>
                <div className="text-lg font-semibold text-fg">{serverDetail?.status?.resources_count ?? serverDetail?.resources?.length ?? 0}</div>
              </div>
            </div>

            <div className="flex items-center justify-end gap-2">
              {isDirty && (
                <>
                  <button
                    onClick={handleResetConfig}
                    disabled={savingConfig}
                    className="inline-flex items-center gap-2 px-4 py-2.5 border border-line-control text-fg-secondary rounded-control hover:bg-hover text-sm font-medium transition-colors disabled:bg-disabled disabled:text-fg-disabled"
                  >
                    {t('button.cancel')}
                  </button>
                  <button
                    onClick={handleSaveConfig}
                    disabled={savingConfig}
                    className="inline-flex items-center gap-2 px-4 py-2.5 bg-action text-action-fg rounded-control hover:bg-action-hover text-sm font-medium transition-colors disabled:bg-disabled disabled:text-fg-disabled"
                  >
                    {savingConfig ? <IconRefresh aria-hidden="true" className="w-4 h-4 animate-spin" /> : <IconDeviceFloppy aria-hidden="true" className="w-4 h-4" />}
                    {savingConfig ? t('button.saving') : t('button.save')}
                  </button>
                </>
              )}
              {server.status === 'connected' ? (
                <button onClick={onDisconnect} className="inline-flex items-center gap-2 px-4 py-2.5 border border-danger text-danger rounded-control hover:bg-danger-soft text-sm font-medium transition-colors">
                  <IconPlugOff aria-hidden="true" className="w-4 h-4" />{t('detail.disconnectConn')}
                </button>
              ) : server.status === 'disabled' ? (
                <button onClick={handleToggleEnabled} disabled={savingConfig} className="inline-flex items-center gap-2 px-4 py-2.5 bg-action text-action-fg rounded-control hover:bg-action-hover text-sm font-medium transition-colors disabled:bg-disabled disabled:text-fg-disabled">
                  <IconPower aria-hidden="true" className="w-4 h-4" />{t('detail.enableServer')}
                </button>
              ) : (
                <button onClick={onConnect} className="inline-flex items-center gap-2 px-4 py-2.5 bg-action text-action-fg rounded-control hover:bg-action-hover text-sm font-medium transition-colors">
                  <IconPower aria-hidden="true" className="w-4 h-4" />{t('detail.connectConn')}
                </button>
              )}
              {server.status !== 'disabled' && (
                <button onClick={handleToggleEnabled} disabled={savingConfig} className="inline-flex items-center gap-2 px-4 py-2.5 border border-line-control text-fg-secondary rounded-control hover:bg-hover text-sm font-medium transition-colors disabled:bg-disabled disabled:text-fg-disabled">
                  <IconPlugOff aria-hidden="true" className="w-4 h-4" />{t('detail.disableServer')}
                </button>
              )}
              {onRemove && (
                <button onClick={onRemove} className="inline-flex items-center gap-2 px-4 py-2.5 border border-danger text-danger rounded-control hover:bg-danger-soft text-sm font-medium transition-colors">
                  <IconTrash aria-hidden="true" className="w-4 h-4" />{t('detail.removeServer')}
                </button>
              )}
            </div>
          </div>
        ) : detailTab === 'tools' ? (
          serverTools.length === 0 ? (
            <div className="text-center py-8 text-sm text-fg-muted">{t('detail.noRegisteredTools')}</div>
          ) : (
            <div className="rounded-control border border-line overflow-hidden">
              <table className="w-full table-fixed divide-y divide-line">
                <thead className="bg-subtle">
                  <tr>
                    <th className="w-2/5 px-4 py-2.5 text-left text-xs font-medium text-fg-muted uppercase">{t('detail.tableToolName')}</th>
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-fg-muted uppercase">{t('detail.tableDesc')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {serverTools.map((tool) => (
                    <tr key={tool.name} onClick={() => onSelectTool(tool)} className="hover:bg-danger-soft cursor-pointer transition-colors">
                      <td className="px-4 py-3"><span className="text-sm font-medium text-fg font-mono break-all">{tool.name}</span></td>
                      <td className="px-4 py-3"><span className="text-sm text-fg-secondary line-clamp-2 ">{tool.description}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : (!serverDetail?.resources || serverDetail.resources.length === 0) ? (
          <div className="text-center py-8 text-sm text-fg-muted">{t('detail.noResources')}</div>
        ) : (
          <div className="bg-surface rounded-control border border-line overflow-hidden">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-line table-fixed">
                <thead className="bg-subtle">
                  <tr>
                    <th className="w-[25%] px-5 py-2.5 text-left text-xs font-medium text-fg-muted uppercase">{t('detail.tableResourceName')}</th>
                    <th className="w-[35%] px-5 py-2.5 text-left text-xs font-medium text-fg-muted uppercase">URI</th>
                    <th className="px-5 py-2.5 text-left text-xs font-medium text-fg-muted uppercase">{t('detail.tableDesc')}</th>
                    <th className="w-[100px] px-5 py-2.5 text-left text-xs font-medium text-fg-muted uppercase whitespace-nowrap">{t('detail.tableType')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {serverDetail.resources.map((res) => (
                    <tr key={res.uri} className="hover:bg-hover">
                      <td className="px-5 py-2.5 truncate"><span className="text-sm font-medium text-fg">{res.name}</span></td>
                      <td className="px-5 py-2.5 truncate"><span className="text-sm text-fg-secondary font-mono">{res.uri}</span></td>
                      <td className="px-5 py-2.5"><span className="text-sm text-fg-secondary line-clamp-1">{res.description || '-'}</span></td>
                      <td className="px-5 py-2.5 whitespace-nowrap"><span className="text-xs text-fg-muted font-mono">{res.mime_type || '-'}</span></td>
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
    <div className="bg-surface" onClick={(e) => e.stopPropagation()}>
      <div className="p-5">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="space-y-5">
            <div className="bg-surface rounded-control border border-line p-4">
              <h4 className="text-sm font-semibold text-fg mb-3 flex items-center">
                <IconActivity aria-hidden="true" className="w-4 h-4 mr-2 text-ai" />
                {t('serviceInfo.title')}
              </h4>
              <div className="space-y-3">
                <div className="flex items-center justify-between"><span className="text-sm text-fg-muted">{t('serviceInfo.name')}</span><span className="text-sm text-fg font-medium">{entry.name}</span></div>
                <div className="flex items-center justify-between"><span className="text-sm text-fg-muted">{t('serviceInfo.type')}</span><span className="px-2 py-0.5 rounded-pill text-xs font-medium bg-ai-soft text-ai">{t('serviceInfo.apiType')}</span></div>
                <div className="flex items-center justify-between"><span className="text-sm text-fg-muted">{t('serviceInfo.category')}</span><span className="text-sm text-fg">{catalogCategories[entry.category]?.label || entry.category}</span></div>
                <div className="flex items-center justify-between"><span className="text-sm text-fg-muted">{t('serviceInfo.language')}</span><span className="text-sm text-fg">{entry.language}</span></div>
                <div className="pt-2"><span className="text-sm text-fg-muted block mb-1">{t('serviceInfo.description')}</span><p className="text-sm text-fg">{getCatalogDescription(entry, i18n.language)}</p></div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-fg-muted">{t('serviceInfo.github')}</span>
                  <a href={`https://github.com/${entry.github}`} target="_blank" rel="noopener noreferrer" className="text-sm text-danger hover:text-danger">{entry.github}</a>
                </div>
                {entry.requires_auth && (
                  <div className="pt-2 border-t border-line">
                    <span className="text-sm text-fg-muted block mb-2">{t('serviceInfo.requiredKeys')}</span>
                    {Object.entries(entry.env_vars).filter(([, spec]) => spec.secret).map(([key, spec]) => (
                      <div key={key} className="flex items-center justify-between py-1">
                        <code className="text-xs bg-subtle px-1.5 py-0.5 rounded-tag">{key}</code>
                        <span className="text-xs text-fg-muted">{spec.description}</span>
                      </div>
                    ))}
                  </div>
                )}
                {entry.tags && entry.tags.length > 0 && (
                  <div className="pt-2 border-t border-line">
                    <span className="text-sm text-fg-muted block mb-2">{t('serviceInfo.tags')}</span>
                    <div className="flex flex-wrap gap-1.5">
                      {entry.tags.map((tag) => (
                        <span key={tag} className="px-2 py-0.5 bg-subtle text-fg-secondary text-xs rounded-tag">#{tag}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="space-y-5">
            <div className="bg-surface rounded-control border border-line p-4">
              <h4 className="text-sm font-semibold text-fg mb-3 flex items-center">
                <IconBolt aria-hidden="true" className="w-4 h-4 mr-2 text-warning" />
                {t('detail.quickActions')}
              </h4>
              <div className="space-y-2.5">
                <button
                  onClick={handleTestConnect}
                  disabled={testing}
                  className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 border rounded-control text-sm font-medium transition-colors ${testing ? 'border-danger bg-danger-soft text-danger cursor-not-allowed' : 'bg-surface border-line-control text-fg-secondary hover:bg-hover'}`}
                >
                  {testing ? <><IconRefresh aria-hidden="true" className="w-4 h-4 animate-spin" />{t('button.testing')}</> : <><IconActivity aria-hidden="true" className="w-4 h-4" />{t('button.testConnectivity')}</>}
                </button>
                <button onClick={handleConnect} className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-action text-action-fg rounded-control text-sm font-medium hover:bg-action-hover transition-colors">
                  <IconPower aria-hidden="true" className="w-4 h-4" />{t('detail.connectConn')}
                </button>
              </div>
            </div>

            {testing && (
              <div className="p-3 rounded-control border border-danger bg-danger-soft text-danger">
                <div className="flex items-center">
                  <div className="relative mr-3">
                    <div className="w-8 h-8 rounded-pill border-2 border-danger border-t-danger animate-spin" />
                  </div>
                  <div className="flex-1">
                    <div className="text-sm font-medium">{t('testing.connectivityTitle')}</div>
                    <div className="text-xs mt-0.5 text-danger">
                      {t('testing.connectivityDesc')}
                      <span className="ml-2 font-mono tabular-nums">{testElapsed.toFixed(1)}s</span>
                    </div>
                  </div>
                </div>
                <div className="mt-2.5 h-1 bg-danger-soft rounded-pill overflow-hidden">
                  <div className="h-full bg-danger rounded-pill animate-pulse" style={{ width: `${Math.min(95, testElapsed * 8)}%`, transition: 'width 0.3s ease-out' }} />
                </div>
              </div>
            )}

            {!testing && testResult && (
              <div className={`p-3 rounded-control border ${testResult.success ? 'bg-success-soft border-success' : 'bg-danger-soft border-danger'}`}>
                <div className="flex items-start">
                  {testResult.success ? <IconCircleCheck aria-hidden="true" className="w-4 h-4 text-success mr-2 mt-0.5 flex-shrink-0" /> : <IconCircleX aria-hidden="true" className="w-4 h-4 text-danger mr-2 mt-0.5 flex-shrink-0" />}
                  <div className="flex-1">
                    <div className={`text-sm font-medium ${testResult.success ? 'text-success' : 'text-danger'}`}>
                      {testResult.success ? t('testing.testSuccess') : t('testing.testFailed')}
                    </div>
                    <div className={`text-xs mt-0.5 ${testResult.success ? 'text-success' : 'text-danger'}`}>{testResult.message}</div>
                    {testResult.latency_ms != null && (
                      <div className={`text-xs mt-1 ${testResult.success ? 'text-success' : 'text-danger'} text-fg-muted`}>{t('testing.responseLatency', { latency: testResult.latency_ms })}</div>
                    )}
                    {testResult.tool_tested && (
                      <div className={`text-xs mt-0.5 ${testResult.success ? 'text-success' : 'text-danger'} text-fg-muted`}>{t('testing.testToolLabel', { tool: testResult.tool_tested })}</div>
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

    // Always show whatever the backend reports in `fields`. Do NOT compare with
    // schema.default_value to decide visibility: the metadata endpoint reuses
    // the persisted base_url as default_value for some providers (e.g. onesig),
    // and that previously caused the saved URL to be cleared from the form.
    schema.forEach((field) => {
      const dynamicValue = nextCredentials?.fields?.[field.key];
      const legacyValue = getLegacyCredentialValue(nextCredentials, field.key);
      nextValues[field.key] = dynamicValue ?? legacyValue ?? '';
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
    <div className="bg-surface" onClick={(e) => e.stopPropagation()}>
      <div className="flex items-center border-b border-line px-5">
        {([
          { key: 'overview' as const, label: t('detail.tabs.overview'), icon: <IconInfoCircle aria-hidden="true" className="w-4 h-4" /> },
          { key: 'tools' as const, label: t('detail.tabs.tools', { count: serviceTools.length }), icon: <IconTool aria-hidden="true" className="w-4 h-4" /> },
        ]).map((tab) => (
          <button
            key={tab.key}
            onClick={() => setDetailTab(tab.key)}
            className={`flex items-center gap-1.5 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${detailTab === tab.key ? 'border-ai text-ai' : 'border-transparent text-fg-muted hover:text-fg-secondary'}`}
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
            {isThreatBookAPIService(serviceName) ? (
              <ThreatBookAPIConfigPanel
                serviceName={serviceName}
                credentials={credentials}
                initialStatus={initialStatus}
                onConfigured={loadData}
                onTestResult={onTestResult}
              />
            ) : (
            <div className="rounded-control border border-line divide-y divide-line">
              {(() => {
                const status = quickTesting
                  ? 'testing'
                  : quickTestResult != null
                    ? (quickTestResult.success ? 'connected' : 'error')
                    : (initialStatus?.status || 'unknown');
                const cfg: Record<string, { label: string; cls: string; dot: string }> = {
                  connected: { label: t('statusBadge.connected'), cls: 'bg-success-soft text-success', dot: 'bg-success' },
                  healthy: { label: t('statusBadge.connected'), cls: 'bg-success-soft text-success', dot: 'bg-success' },
                  testing: { label: t('button.testing'), cls: 'bg-danger-soft text-danger', dot: 'bg-danger animate-pulse' },
                  error: { label: t('statusBadge.error'), cls: 'bg-danger-soft text-danger', dot: 'bg-danger' },
                  unknown: { label: t('statusBadge.unknown'), cls: 'bg-subtle text-fg-secondary', dot: 'bg-subtle' },
                };
                const c = cfg[status] || cfg.unknown;
                return (
                  <div className="flex justify-between items-center py-2.5 px-4 gap-4">
                    <span className="text-sm text-fg-muted shrink-0">{t('detail.connectionStatus')}</span>
                    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-pill text-xs font-medium ${c.cls}`}>
                      <span className={`w-1.5 h-1.5 rounded-pill ${c.dot}`} />
                      {c.label}
                      {quickTestResult?.success && quickTestResult.latency_ms != null && <span className="text-fg-muted">· {quickTestResult.latency_ms}ms</span>}
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
                  <span className="text-sm text-fg-muted shrink-0">{t('serviceInfo.documentation')}</span>
                  <a href={metadata.docs_url} target="_blank" rel="noopener noreferrer" className="text-sm text-danger hover:text-danger">{t('serviceInfo.viewDocs')} →</a>
                </div>
              )}
              {credentials?.secret_id && (
                <div className="flex justify-between items-center py-2.5 px-4 gap-4">
                  <span className="text-sm text-fg-muted shrink-0">Secret ID</span>
                  <code className="text-sm font-mono bg-subtle px-2 py-1 rounded-tag truncate">{credentials.secret_id}</code>
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
                        <span className="text-sm text-fg-muted shrink-0 w-20">{label}</span>
                        <div className="flex-1 flex items-center gap-1.5 min-w-0">
                          <input
                            type={isSensitive && !visible ? 'password' : 'text'}
                            value={value}
                            onChange={(e) => setFormValues((prev) => ({ ...prev, [field.key]: e.target.value }))}
                            placeholder={placeholder}
                            className="ui-input flex-1 min-w-0 px-2  border border-line rounded-tag text-sm bg-subtle focus:bg-surface focus:border-action transition-colors"
                          />
                          {isSensitive && (
                            <button
                              type="button"
                              onClick={() => setFieldVisibility((prev) => ({ ...prev, [field.key]: !prev[field.key] }))}
                              className="ui-btn-icon inline-flex items-center justify-center shrink-0 text-fg-muted hover:text-fg-secondary p-1 rounded-control transition-colors"
                              title={visible ? t('detail.hide') : t('detail.show')}
                            >
                              {visible ? <IconEyeOff aria-hidden="true" className="w-4 h-4" /> : <IconEye aria-hidden="true" className="w-4 h-4" />}
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
                        className="shrink-0 inline-flex items-center gap-1 px-2 py-1 bg-action text-action-fg rounded-tag text-xs font-medium hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled transition-colors"
                      >
                        {apiKeySaving ? <IconRefresh aria-hidden="true" className="w-4 h-4 animate-spin" /> : <IconDeviceFloppy aria-hidden="true" className="w-4 h-4" />}
                        {t('button.save')}
                      </button>
                      <button
                        type="button"
                        onClick={() => setFormValues({ ...formValuesOrigRef.current })}
                        className="shrink-0 px-2 py-1 border border-line-control text-fg-secondary rounded-tag text-xs hover:bg-hover transition-colors"
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
                  <span className="text-sm text-fg-muted shrink-0">{t('serviceInfo.sslVerify', { defaultValue: 'SSL 验证' })}</span>
                  <p className="text-xs text-fg-muted mt-0.5">{t('serviceInfo.sslVerifyDesc', { defaultValue: '关闭可访问内网 IP 部署的服务' })}</p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={!!verifySsl}
                  aria-label={t('serviceInfo.sslVerify', { defaultValue: 'SSL 验证' })}
                  onClick={() => onToggleVerifySsl?.(!verifySsl)}
                  className="ui-btn-icon inline-flex shrink-0 items-center justify-center rounded-control disabled:cursor-not-allowed">
<span className="ui-switch" aria-hidden="true" aria-checked={!!verifySsl} />
</button>
                <span className="text-xs text-fg-secondary">{t(verifySsl ? 'enabledBadge.enabled' : 'enabledBadge.disabled')}</span>
              </div>
            </div>
            )}

            <div className="flex flex-col gap-2">
              <button onClick={handleQuickTestConnectivity} disabled={quickTesting} className="inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-surface border border-line-control text-fg-secondary rounded-control hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled text-sm font-medium transition-colors">
                {quickTesting ? <><IconRefresh aria-hidden="true" className="w-4 h-4 animate-spin" />{t('button.testing')}</> : <><IconActivity aria-hidden="true" className="w-4 h-4" />{t('button.testConnectivity')}</>}
              </button>
              <div className="flex gap-2">
                <button
                  onClick={() => onToggleEnabled?.(!enabled)}
                  className={`flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-control text-sm font-medium transition-colors ${enabled ? 'border border-line-control text-fg-secondary bg-surface hover:bg-hover' : 'bg-action text-action-fg hover:bg-action-hover'}`}
                >
                  {enabled ? <IconPlugOff aria-hidden="true" className="w-4 h-4" /> : <IconPower aria-hidden="true" className="w-4 h-4" />}
                  {enabled ? t('detail.disableServer') : t('detail.enableServer')}
                </button>
                <button
                  onClick={() => { if (!builtin) onDelete?.(); }}
                  disabled={!!builtin}
                  className={`inline-flex items-center justify-center gap-2 px-4 py-2.5 border rounded-control text-sm font-medium transition-colors ${builtin ? 'border-line text-fg-muted bg-subtle cursor-not-allowed' : 'border-danger text-danger bg-surface hover:bg-danger-soft'}`}
                  title={builtin ? t('api.builtinCannotDelete') : t('button.delete')}
                >
                  <IconTrash aria-hidden="true" className="w-4 h-4" />
                  {t('button.delete')}
                </button>
              </div>
              {!quickTesting && quickTestResult && (
                <div className={`flex items-start gap-2 rounded-control border p-3 text-sm ${quickTestResult.success ? 'bg-success-soft border-success text-success' : 'bg-danger-soft border-danger text-danger'}`}>
                  {quickTestResult.success ? <IconCircleCheck aria-hidden="true" className="w-4 h-4 mt-0.5 flex-shrink-0" /> : <IconCircleX aria-hidden="true" className="w-4 h-4 mt-0.5 flex-shrink-0" />}
                  <span>{quickTestResult.message}</span>
                  {quickTestResult.latency_ms != null && <span className="text-xs text-fg-muted"> · {quickTestResult.latency_ms}ms</span>}
                </div>
              )}
            </div>
          </div>
        ) : serviceTools.length === 0 ? (
          <div className="text-center py-8 text-sm text-fg-muted">{t('serviceInfo.noServiceTools')}</div>
        ) : (
          <div className="bg-surface rounded-control border border-line overflow-hidden">
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-line table-fixed">
                <thead className="bg-subtle">
                  <tr>
                    <th className="w-[30%] px-5 py-2.5 text-left text-xs font-medium text-fg-muted uppercase">{t('detail.tableToolName')}</th>
                    <th className="px-5 py-2.5 text-left text-xs font-medium text-fg-muted uppercase">{t('detail.tableDesc')}</th>
                    <th className="w-[100px] px-5 py-2.5 text-left text-xs font-medium text-fg-muted uppercase whitespace-nowrap">{t('detail.tableStatus')}</th>
                    <th className="w-[120px] px-5 py-2.5 text-left text-xs font-medium text-fg-muted uppercase whitespace-nowrap">{t('detail.tableActions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {serviceTools.map((tool) => (
                    <tr key={tool.name} className="hover:bg-hover">
                      <td className="px-5 py-2.5 truncate"><span className="text-sm font-medium text-fg font-mono">{tool.name}</span></td>
                      <td className="px-5 py-2.5"><span className="text-sm text-fg-secondary line-clamp-1">{tool.description}</span></td>
                      <td className="px-5 py-2.5 whitespace-nowrap"><EnabledBadge enabled={tool.enabled} /></td>
                      <td className="px-5 py-2.5 whitespace-nowrap"><button onClick={() => onSelectTool(tool)} className="text-sm text-danger hover:text-danger">{t('detail.testDetail')}</button></td>
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
