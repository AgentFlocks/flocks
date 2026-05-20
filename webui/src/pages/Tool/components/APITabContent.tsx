import { useState, useMemo, useCallback, useEffect } from 'react';
import {
  Cloud, Wrench, Settings, X, Download, ExternalLink, Star, Power, PowerOff, Trash2,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { mcpAPI } from '@/api/mcp';
import { providerAPI } from '@/api/provider';
import type { Tool } from '@/api/tool';
import type { APIServiceSummary, MCPCatalogCategory, MCPCatalogEntry } from '@/types';
import EmptyState from '@/components/common/EmptyState';
import { getCatalogDescription } from '@/utils/mcpCatalog';
import { APIServiceDetailPanel } from './ServiceDetailPanel';
import { SERVICE_TAB_GRID_COLS } from './gridLayout';

const DETAIL_DRAWER_WIDTH = 560;
const LANG_COLORS: Record<string, string> = {
  python: 'bg-red-100 text-red-700',
  typescript: 'bg-sky-100 text-sky-700',
  go: 'bg-cyan-100 text-cyan-700',
  rust: 'bg-orange-100 text-orange-700',
  java: 'bg-red-100 text-red-700',
  c: 'bg-gray-100 text-gray-700',
};
const INSTALL_BUTTON_CLASS = 'flex-1 flex items-center justify-center gap-1 py-1 px-2 bg-green-600 text-white rounded-lg text-xs font-medium hover:bg-green-700 disabled:opacity-50 transition-colors';
const INSTALL_CONFIRM_BUTTON_CLASS = 'px-4 py-2 text-sm text-white bg-green-600 rounded-lg hover:bg-green-700 disabled:opacity-50 transition-colors';

interface APITabContentProps {
  tools: Tool[];
  onSelectTool: (tool: Tool) => void;
  onRefreshTools: () => Promise<void>;
  catalogEntries: MCPCatalogEntry[];
  catalogCategories: Record<string, MCPCatalogCategory>;
  catalogLoading: boolean;
  configuredIds: Set<string>;
  onConfiguredChange: (id: string) => void;
}

export default function APITabContent({
  tools,
  onSelectTool,
  onRefreshTools,
  catalogEntries,
  catalogCategories,
  catalogLoading,
  configuredIds,
  onConfiguredChange,
}: APITabContentProps) {
  const { t, i18n } = useTranslation('tool');
  const toolsByModule = useMemo(() => {
    const map: Record<string, Tool[]> = {};
    tools.filter((t) => t.source === 'api').forEach((t) => {
      const key = t.source_name || 'other';
      if (!map[key]) map[key] = [];
      map[key].push(t);
    });
    return map;
  }, [tools]);

  const [services, setServices] = useState<APIServiceSummary[]>([]);
  const [servicesLoading, setServicesLoading] = useState(true);
  const [selectedServiceId, setSelectedServiceId] = useState<string | null>(null);
  const [testingServices, setTestingServices] = useState<Set<string>>(new Set());
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [installing, setInstalling] = useState<string | null>(null);
  const [credModalEntry, setCredModalEntry] = useState<MCPCatalogEntry | null>(null);
  const [credValues, setCredValues] = useState<Record<string, string>>({});

  const fetchServices = useCallback(async () => {
    try {
      setServicesLoading(true);
      const res = await providerAPI.listApiServices();
      // Exclude security device APIs — they live on the Device Integration page
      setServices((res.data || []).filter((s) => s.integration_type !== 'device'));
    } catch {
      setServices([]);
    } finally {
      setServicesLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchServices();
  }, [fetchServices]);

  const selectedService = useMemo(
    () => (selectedServiceId ? services.find((service) => service.id === selectedServiceId) ?? null : null),
    [selectedServiceId, services],
  );
  const selectedModuleTools = selectedServiceId ? (toolsByModule[selectedServiceId] || []) : [];
  const isConfigured = useCallback((entryId: string) => configuredIds.has(entryId), [configuredIds]);
  const PRIORITY_IDS = useMemo(() => new Set(['virustotal_mcp', 'urlhaus']), []);

  const inactiveCatalogEntries = useMemo(() => {
    const activeModuleIds = new Set(services.map((service) => service.id));
    return catalogEntries.filter((entry) => !activeModuleIds.has(entry.id));
  }, [catalogEntries, services]);

  const filteredCatalog = useMemo(() => {
    const result = [...inactiveCatalogEntries];
    if (selectedCategory !== 'all') {
      return result
        .filter((entry) => entry.category === selectedCategory)
        .sort((a, b) => {
          const pa = PRIORITY_IDS.has(a.id) ? 0 : 1;
          const pb = PRIORITY_IDS.has(b.id) ? 0 : 1;
          if (pa !== pb) return pa - pb;
          const ca = isConfigured(a.id) ? 0 : 1;
          const cb = isConfigured(b.id) ? 0 : 1;
          if (ca !== cb) return ca - cb;
          return b.stars - a.stars;
        });
    }
    return result.sort((a, b) => {
      const pa = PRIORITY_IDS.has(a.id) ? 0 : 1;
      const pb = PRIORITY_IDS.has(b.id) ? 0 : 1;
      if (pa !== pb) return pa - pb;
      const ca = isConfigured(a.id) ? 0 : 1;
      const cb = isConfigured(b.id) ? 0 : 1;
      if (ca !== cb) return ca - cb;
      return b.stars - a.stars;
    });
  }, [inactiveCatalogEntries, selectedCategory, PRIORITY_IDS, isConfigured]);

  const catalogCategoryCounts = useMemo(() => {
    const counts: Record<string, number> = { all: inactiveCatalogEntries.length };
    for (const entry of inactiveCatalogEntries) {
      counts[entry.category] = (counts[entry.category] || 0) + 1;
    }
    return counts;
  }, [inactiveCatalogEntries]);

  const handleTestingStart = useCallback((serviceName: string) => {
    setTestingServices((prev) => new Set(prev).add(serviceName));
  }, []);

  const handleTestingEnd = useCallback((serviceName: string) => {
    setTestingServices((prev) => {
      const next = new Set(prev);
      next.delete(serviceName);
      return next;
    });
    fetchServices();
  }, [fetchServices]);

  const handleTestResult = useCallback((serviceName: string, result: { status: string; latency_ms?: number }) => {
    setServices((prev) => prev.map((service) => (
      service.id === serviceName
        ? { ...service, status: result.status, latency_ms: result.latency_ms }
        : service
    )));
  }, []);

  const openCredModal = (entry: MCPCatalogEntry) => {
    const initial: Record<string, string> = {};
    for (const [key, spec] of Object.entries(entry.env_vars)) {
      if (spec.secret) initial[key] = '';
    }
    setCredValues(initial);
    setCredModalEntry(entry);
  };

  const handleCredSubmit = async () => {
    if (!credModalEntry) return;
    const hasEmpty = Object.entries(credValues).some(([, value]) => !value.trim());
    if (hasEmpty) {
      alert(t('alert.fillAllRequired'));
      return;
    }
    const entryId = credModalEntry.id;
    const entryName = credModalEntry.name;
    let shouldConnect = false;
    let installRes: Awaited<ReturnType<typeof mcpAPI.catalogInstall>> | null = null;
    try {
      setInstalling(entryId);
      installRes = await mcpAPI.catalogInstall(entryId, { credentials: credValues });
      shouldConnect = installRes.data?.config?.enabled !== false;
      onConfiguredChange(entryId);
      setCredModalEntry(null);
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
      setInstalling(null);
      return;
    }
    try {
      if (shouldConnect) {
        await mcpAPI.connect(entryId);
      }
      await onRefreshTools();
      await fetchServices();
      if (!shouldConnect) {
        alert(t('alert.mcpConfiguredDisabled', { name: entryName }));
      }
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
    } finally {
      setInstalling(null);
    }
  };

  const handleInstallNoAuth = async (entry: MCPCatalogEntry) => {
    let shouldConnect = false;
    let installRes: Awaited<ReturnType<typeof mcpAPI.catalogInstall>> | null = null;
    try {
      setInstalling(entry.id);
      installRes = await mcpAPI.catalogInstall(entry.id);
      shouldConnect = installRes.data?.config?.enabled !== false;
      onConfiguredChange(entry.id);
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
      setInstalling(null);
      return;
    }
    try {
      if (shouldConnect) {
        await mcpAPI.connect(entry.id);
      }
      await onRefreshTools();
      await fetchServices();
      if (!shouldConnect) {
        alert(t('alert.mcpConfiguredDisabled', { name: entry.name }));
      }
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
    } finally {
      setInstalling(null);
    }
  };

  const handleToggleEnabled = async (serviceId: string, enabled: boolean, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try {
      setInstalling(serviceId);
      await providerAPI.updateApiService(serviceId, { enabled });
      await onRefreshTools();
      await fetchServices();
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
    } finally {
      setInstalling(null);
    }
  };

  const handleToggleSsl = async (serviceId: string, verifySsl: boolean) => {
    const service = services.find((s) => s.id === serviceId);
    if (!service) return;
    try {
      const res = await providerAPI.updateApiService(serviceId, {
        enabled: service.enabled,
        verify_ssl: verifySsl,
      });
      setServices((prev) => prev.map((s) => (s.id === serviceId ? res.data : s)));
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
    }
  };

  const handleDeleteService = async (serviceId: string) => {
    if (!window.confirm(t('alert.confirmRemoveApiService', { name: serviceId }))) return;
    try {
      setInstalling(serviceId);
      await providerAPI.deleteApiService(serviceId);
      if (selectedServiceId === serviceId) {
        setSelectedServiceId(null);
      }
      await onRefreshTools();
      await fetchServices();
    } catch (err: any) {
      alert(t('alert.deleteFailed', { error: err.response?.data?.detail || err.message }));
    } finally {
      setInstalling(null);
    }
  };

  const getServiceDescription = useCallback((service: APIServiceSummary) => {
    const englishDescription = service.description?.trim() || '';
    const chineseDescription = service.description_cn?.trim() || '';
    return i18n.language.toLowerCase().replace('_', '-').startsWith('zh')
      ? (chineseDescription || englishDescription)
      : (englishDescription || chineseDescription);
  }, [i18n.language]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1.5 flex-wrap">
        <button
          onClick={() => setSelectedCategory('all')}
          className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${selectedCategory === 'all' ? 'bg-purple-50 text-purple-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
          >
            {t('api.filterAll')}
          </button>
        {Object.entries(catalogCategories).map(([id, cat]) =>
          catalogCategoryCounts[id] ? (
            <button
              key={id}
              onClick={() => setSelectedCategory(id)}
              className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${selectedCategory === id ? 'bg-purple-50 text-purple-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
            >
              {cat.label} ({catalogCategoryCounts[id]})
            </button>
          ) : null
        )}
      </div>

      {services.length === 0 && filteredCatalog.length === 0 && !catalogLoading && !servicesLoading ? (
        <EmptyState icon={<Cloud className="w-16 h-16" />} title={t('api.noTools')} description={t('api.noToolsDesc')} />
      ) : (
        <div className="bg-white border border-gray-200 rounded-lg overflow-hidden divide-y divide-gray-100">
          {services.map((service) => {
            const serviceTools = toolsByModule[service.id] || [];
            const isSelected = selectedServiceId === service.id;
            const rowDescription = getServiceDescription(service) || `${service.name} API service`;

            return (
              <div
                key={service.id}
                className={`grid items-center gap-3 px-4 py-3 transition-colors ${isSelected ? 'bg-purple-50' : 'hover:bg-gray-50'}`}
                style={{ gridTemplateColumns: SERVICE_TAB_GRID_COLS }}
              >
                {/* Icon */}
                <div className={`w-8 h-8 flex items-center justify-center rounded-lg ${service.enabled ? 'bg-purple-50' : 'bg-gray-50'}`}>
                  <Cloud className={`w-4 h-4 ${service.enabled ? 'text-purple-600' : 'text-gray-400'}`} />
                </div>

                {/* Name + description.
                    Whole block is the click target (mirrors Hub catalog rows
                    where name+description live inside one `<button>`).  Toggle
                    is `isSelected ? null : id` so a second click collapses
                    the panel, matching the "manage" button's behaviour. */}
                <button
                  type="button"
                  onClick={() => setSelectedServiceId(isSelected ? null : service.id)}
                  className="min-w-0 text-left group/name focus:outline-none"
                >
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm font-semibold text-gray-900 truncate transition-colors group-hover/name:text-purple-700 group-focus-visible/name:underline">{service.name}</span>
                    {service.version && (
                      <span className="px-1.5 py-0.5 bg-sky-50 text-sky-700 border border-sky-100 text-[10px] font-medium rounded shrink-0" title={t('serviceInfo.version')}>
                        v{service.version.replace(/^v/i, '')}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-gray-500 truncate mt-0.5">{rowDescription}</p>
                </button>

                {/* Type column */}
                <div className="text-center">
                  <span className="px-1.5 py-0.5 bg-purple-100 text-purple-700 text-[10px] font-medium rounded">API</span>
                </div>

                {/* Status column */}
                <div className="text-center">
                  <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded ${service.enabled ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                    {service.enabled ? t('enabledBadge.enabled') : t('enabledBadge.disabled')}
                  </span>
                </div>

                {/* Stats column */}
                <div className="flex items-center justify-end gap-3 text-xs text-gray-400">
                  <span className="flex items-center gap-1"><Wrench className="w-3 h-3" />{serviceTools.length}</span>
                  {service.latency_ms != null && <span>{service.latency_ms}ms</span>}
                </div>

                {/* Actions column */}
                <div className="flex items-center justify-end gap-1.5">
                  {service.enabled ? (
                    <>
                      <button
                        onClick={() => setSelectedServiceId(isSelected ? null : service.id)}
                        className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors ${isSelected ? 'border-purple-300 text-purple-700 bg-purple-50' : 'border-gray-200 text-gray-600 hover:bg-gray-100'}`}
                      >
                        <Settings className="w-3 h-3" />{t('mcp.manage')}
                      </button>
                      <button
                        onClick={(e) => handleToggleEnabled(service.id, false, e)}
                        disabled={installing === service.id}
                        className="p-1.5 rounded-lg border border-red-200 text-red-400 hover:bg-red-50 transition-colors disabled:opacity-50"
                        title={t('detail.disableServer')}
                      >
                        <PowerOff className="w-3.5 h-3.5" />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={(e) => handleToggleEnabled(service.id, true, e)}
                        disabled={installing === service.id}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-green-600 text-white text-xs font-medium hover:bg-green-700 disabled:opacity-50 transition-colors"
                      >
                        <Power className="w-3 h-3" />{installing === service.id ? t('mcp.configuring') : t('detail.enableServer')}
                      </button>
                      <button
                        onClick={() => setSelectedServiceId(isSelected ? null : service.id)}
                        className="p-1.5 rounded-lg border border-gray-200 text-gray-400 hover:bg-gray-100 transition-colors"
                        title={t('mcp.manage')}
                      >
                        <Settings className="w-3.5 h-3.5" />
                      </button>
                    </>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); if (!service.builtin) handleDeleteService(service.id); }}
                    disabled={!!service.builtin || installing === service.id}
                    className={`p-1.5 rounded-lg border transition-colors ${service.builtin ? 'border-gray-100 text-gray-300 cursor-not-allowed' : 'border-red-100 text-red-400 hover:bg-red-50'}`}
                    title={service.builtin ? t('api.builtinCannotDelete') : t('button.delete')}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            );
          })}

          {filteredCatalog.map((entry) => (
            <div
              key={`catalog-${entry.id}`}
              className="grid items-center gap-3 px-4 py-3 cursor-default hover:bg-gray-50 transition-colors"
              style={{ gridTemplateColumns: SERVICE_TAB_GRID_COLS }}
            >
              {/* Icon */}
              <div className="w-8 h-8 flex items-center justify-center rounded-lg bg-gray-50">
                <Cloud className="w-4 h-4 text-gray-400" />
              </div>

              {/* Name + description */}
              <div className="min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-semibold text-gray-900 truncate">{entry.name}</span>
                  {PRIORITY_IDS.has(entry.id) && (
                    <span className="px-1.5 py-0.5 bg-amber-50 text-amber-700 text-[10px] font-medium rounded border border-amber-200 shrink-0">{t('api.recommended')}</span>
                  )}
                </div>
                <p className="text-xs text-gray-500 truncate mt-0.5">{getCatalogDescription(entry, i18n.language)}</p>
              </div>

              {/* Type column */}
              <div className="text-center">
                <span className="px-1.5 py-0.5 bg-purple-100 text-purple-700 text-[10px] font-medium rounded">API</span>
              </div>

              {/* Status column */}
              <div className="text-center">
                <span className="px-1.5 py-0.5 bg-gray-100 text-gray-500 text-[10px] font-medium rounded">{t('statusBadge.inactive')}</span>
              </div>

              {/* Stats column */}
              <div className="flex items-center justify-end gap-2 text-xs text-gray-400">
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${LANG_COLORS[entry.language] || 'bg-gray-100 text-gray-600'}`}>{entry.language}</span>
                <span className="flex items-center gap-1"><Star className="w-3 h-3" />{entry.stars}</span>
              </div>

              {/* Actions column */}
              <div className="flex items-center justify-end gap-1.5">
                <button
                  onClick={() => {
                    if (entry.requires_auth && !isConfigured(entry.id)) {
                      openCredModal(entry);
                    } else {
                      handleInstallNoAuth(entry);
                    }
                  }}
                  disabled={installing === entry.id}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-green-600 text-white text-xs font-medium hover:bg-green-700 disabled:opacity-50 transition-colors"
                >
                  <Download className="w-3 h-3" />{installing === entry.id ? t('api.configuring') : t('button.install')}
                </button>
                {entry.github ? (
                  <a
                    href={`https://github.com/${entry.github}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="p-1.5 rounded-lg border border-gray-200 text-gray-400 hover:bg-gray-100 transition-colors"
                    title="GitHub"
                  >
                    <ExternalLink className="w-3.5 h-3.5" />
                  </a>
                ) : (
                  <span className="w-7 h-7" aria-hidden="true" />
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {credModalEntry && (
        <>
          <div className="fixed inset-0 bg-black/40 z-40" onClick={() => setCredModalEntry(null)} />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md" onClick={(e) => e.stopPropagation()}>
              <div className="px-6 py-4 border-b border-gray-200">
                <h3 className="text-lg font-semibold text-gray-900">{credModalEntry.name}</h3>
                <p className="text-sm text-gray-500 mt-1">{t('alert.credDescHint')}</p>
              </div>
              <div className="px-6 py-4 space-y-4">
                {Object.entries(credModalEntry.env_vars).filter(([, spec]) => spec.secret).map(([key, spec]) => (
                  <div key={key}>
                    <label className="block text-sm font-medium text-gray-700 mb-1">{key}</label>
                    <p className="text-xs text-gray-500 mb-1.5">{spec.description}</p>
                    <input
                      type="password"
                      value={credValues[key] || ''}
                      onChange={(e) => setCredValues((prev) => ({ ...prev, [key]: e.target.value }))}
                      placeholder={t('credentials.enterField', { field: key })}
                      className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                    />
                  </div>
                ))}
              </div>
              <div className="px-6 py-4 border-t border-gray-200 flex gap-3 justify-end">
                <button onClick={() => setCredModalEntry(null)} className="px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors">
                  {t('button.cancel')}
                </button>
                <button
                  onClick={handleCredSubmit}
                  disabled={installing === credModalEntry.id}
                  className={INSTALL_CONFIRM_BUTTON_CLASS}
                >
                  {installing === credModalEntry.id ? t('api.configuring') : t('button.confirmConfig')}
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {selectedServiceId && selectedService && (() => {
        const catalogEntry = catalogEntries.find((entry) => entry.id === selectedServiceId);
        const displayName = catalogEntry?.name || selectedService.name || selectedServiceId;
        return (
          <>
            <div className="fixed inset-0 bg-black/40 z-40" onClick={() => setSelectedServiceId(null)} />
            <div className="fixed right-0 top-0 bottom-0 z-50 flex min-h-0 flex-col w-full bg-white shadow-2xl" style={{ maxWidth: DETAIL_DRAWER_WIDTH }} onClick={(e) => e.stopPropagation()}>
              <div className="flex-shrink-0 border-b border-gray-200">
                <div className="flex items-center gap-3 px-6 py-4">
                  <div className="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0 bg-purple-50">
                    <Cloud className="w-5 h-5 text-purple-600" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h2 className="text-lg font-semibold text-gray-900">{displayName}</h2>
                    <p className="text-sm text-gray-500">{t('api.serviceConfig')}</p>
                  </div>
                  <button onClick={() => setSelectedServiceId(null)} className="text-gray-400 hover:text-gray-600 p-2 rounded-lg hover:bg-gray-100 flex-shrink-0">
                    <X className="w-5 h-5" />
                  </button>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                {catalogEntry && (
                  <div className="px-6 py-4 bg-gray-50 border-b border-gray-200">
                    <p className="text-sm text-gray-600 whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{getCatalogDescription(catalogEntry, i18n.language)}</p>
                  </div>
                )}
                <APIServiceDetailPanel
                  serviceName={selectedServiceId}
                  serviceTools={selectedModuleTools}
                  onSelectTool={onSelectTool}
                  onTestingStart={() => handleTestingStart(selectedServiceId)}
                  onTestingEnd={() => handleTestingEnd(selectedServiceId)}
                  onTestResult={handleTestResult}
                  enabled={selectedService.enabled}
                  onToggleEnabled={async (enabled) => handleToggleEnabled(selectedServiceId, enabled)}
                  onDelete={selectedService.builtin ? undefined : async () => handleDeleteService(selectedServiceId)}
                  builtin={selectedService.builtin}
                  initialStatus={{
                    status: selectedService.enabled ? selectedService.status : 'disabled',
                    latency_ms: selectedService.latency_ms,
                  }}
                  verifySsl={selectedService.verify_ssl}
                  onToggleVerifySsl={async (verifySsl) => handleToggleSsl(selectedServiceId, verifySsl)}
                />
              </div>
            </div>
          </>
        );
      })()}
    </div>
  );
}
