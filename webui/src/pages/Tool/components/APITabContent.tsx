import { pageActionLabel } from '../../pageActionLabel';
import PageDialog from '../../PageDialog';
import { useState, useMemo, useCallback, useEffect } from 'react';
import {
  IconCloud,
  IconTool,
  IconSettings,
  IconX,
  IconDownload,
  IconExternalLink,
  IconStar,
  IconPower,
  IconPlugOff,
  IconTrash,
} from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { mcpAPI } from '@/api/mcp';
import { providerAPI } from '@/api/provider';
import { listAllToolPages, type Tool } from '@/api/tool';
import type { APIServiceSummary, MCPCatalogCategory, MCPCatalogEntry } from '@/types';
import EmptyState from '@/components/common/EmptyState';
import { useToast } from '@/components/common/Toast';
import PluginGroupButton from '@/components/plugin-groups/PluginGroupButton';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { getCatalogDescription } from '@/utils/mcpCatalog';
import { APIServiceDetailPanel } from './ServiceDetailPanel';
import {
  SERVICE_TAB_ACTIONS_CELL,
  SERVICE_TAB_BADGE_CELL,
  SERVICE_TAB_CARD_CLASS,
  SERVICE_TAB_CARD_GRID_COLS,
  SERVICE_TAB_GRID_COLS,
  SERVICE_TAB_NAME_CELL,
  SERVICE_TAB_ROW_CLASS,
  SERVICE_TAB_STATS_CELL,
} from './gridLayout';
import GroupNav, { useGroupDrag } from '@/components/plugin-groups/GroupNav';
import { deriveGroupNav, matchesGroup, saveGroupItems, type GroupSelection } from '@/components/plugin-groups/groupView';
import { useAuth } from '@/contexts/AuthContext';
import { extractErrorMessage } from '@/utils/error';

const DETAIL_DRAWER_WIDTH = 480;
const LANG_COLORS: Record<string, string> = {
  python: 'bg-subtle text-fg-secondary',
  typescript: 'bg-subtle text-fg-secondary',
  go: 'bg-subtle text-fg-secondary',
  rust: 'bg-subtle text-fg-secondary',
  java: 'bg-subtle text-fg-secondary',
  c: 'bg-subtle text-fg-secondary',
};
const INSTALL_BUTTON_CLASS = 'flex-1 flex items-center justify-center gap-1 py-1 px-2 bg-success-soft text-success rounded-control text-xs font-medium hover:bg-success-soft disabled:bg-disabled disabled:text-fg-disabled transition-colors';
const INSTALL_CONFIRM_BUTTON_CLASS = 'px-4 py-2 text-sm text-success bg-success-soft rounded-control hover:bg-success-soft disabled:bg-disabled disabled:text-fg-disabled transition-colors';

interface APITabContentProps {
  tools: Tool[];
  searchQuery?: string;
  /** Complete source_name facet for the current query, not the loaded tool page. */
  matchingToolServices?: Readonly<Record<string, number>>;
  toolSearchPending?: boolean;
  onSelectTool: (tool: Tool) => void;
  onRefreshTools: () => Promise<void>;
  onServiceCountChange?: (count: number) => void;
  refreshKey?: number;
  viewMode?: 'list' | 'cards';
  catalogEntries: MCPCatalogEntry[];
  catalogCategories: Record<string, MCPCatalogCategory>;
  catalogLoading: boolean;
  configuredIds: Set<string>;
  onConfiguredChange: (id: string) => void;
}

export default function APITabContent({
  tools,
  searchQuery = '',
  matchingToolServices,
  toolSearchPending = false,
  onSelectTool,
  onRefreshTools,
  onServiceCountChange,
  refreshKey,
  viewMode = 'list',
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
  const [servicesError, setServicesError] = useState<unknown>(null);
  const [selectedServiceId, setSelectedServiceId] = useState<string | null>(null);
  const [testingServices, setTestingServices] = useState<Set<string>>(new Set());
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [installing, setInstalling] = useState<string | null>(null);
  const [credModalEntry, setCredModalEntry] = useState<MCPCatalogEntry | null>(null);
  const [credValues, setCredValues] = useState<Record<string, string>>({});
  const [serviceToolCache, setServiceToolCache] = useState<Record<string, Tool[]>>({});

  const fetchServices = useCallback(async (force = false) => {
    try {
      setServicesLoading(true);
      const res = await providerAPI.listApiServices(force ? { force: true } : undefined);
      // Exclude security device APIs — they live on the Device Integration page
      setServices((res.data || []).filter((s) => s.integration_type !== 'device'));
      setServicesError(null);
    } catch (error) {
      if (force) throw error;
      // Keep the last native rows and group selection when a reload fails.
      setServicesError(error);
    } finally {
      setServicesLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchServices();
  }, [fetchServices, refreshKey]);

  const selectedService = useMemo(
    () => (selectedServiceId ? services.find((service) => service.id === selectedServiceId) ?? null : null),
    [selectedServiceId, services],
  );
  const selectedModuleTools = selectedServiceId
    ? (serviceToolCache[selectedServiceId] ?? toolsByModule[selectedServiceId] ?? [])
    : [];
  const isConfigured = useCallback((entryId: string) => configuredIds.has(entryId), [configuredIds]);
  const PRIORITY_IDS = useMemo(() => new Set(['virustotal_mcp', 'urlhaus']), []);

  useEffect(() => {
    setServiceToolCache({});
  }, [tools]);

  useEffect(() => {
    if (!selectedServiceId || serviceToolCache[selectedServiceId]) return;
    let cancelled = false;
    void listAllToolPages({
      source: 'api',
      sourceName: selectedServiceId,
      sortBy: 'name',
      sortDir: 'asc',
    }).then((serviceTools) => {
      if (!cancelled) {
        setServiceToolCache((current) => ({ ...current, [selectedServiceId]: serviceTools }));
      }
    }).catch(() => {
      // Keep the current page slice visible; reopening retries the complete service list.
    });
    return () => {
      cancelled = true;
    };
  }, [selectedServiceId, serviceToolCache]);

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

  const { user } = useAuth();
  const [groupSelection, setGroupSelection] = useState<GroupSelection>(null);
  // The server distinguishes definition-only rows from configured instances;
  // builtin, credentials and enabled state do not establish group ownership.
  const asServiceGroupItem = (service: APIServiceSummary) => ({
    key: service.id, name: service.name, group: service.group,
    readOnlyReason: service.group_readonly ? t('pluginGroups:readOnly.system')
      : user?.role === 'admin' ? undefined : t('pluginGroups:readOnly.admin'),
  });
  const asCatalogGroupItem = (entry: MCPCatalogEntry) => ({
    key: `catalog:${entry.id}`, name: entry.name, group: entry.group,
    readOnlyReason: t(entry.group_readonly ? 'pluginGroups:readOnly.system' : 'pluginGroups:readOnly.catalog'),
  });
  const groupInventory = (nativeServices: APIServiceSummary[]) => [
    ...nativeServices.map(asServiceGroupItem),
    ...catalogEntries.filter((entry) => !nativeServices.some((service) => service.id === entry.id)).map(asCatalogGroupItem),
  ];
  const groupItems = groupInventory(services);
  useEffect(() => {
    if (!servicesLoading && servicesError === null) onServiceCountChange?.(groupItems.length);
  }, [groupItems.length, servicesLoading, servicesError, onServiceCountChange]);
  const { warning } = useToast();
  const groupDrag = useGroupDrag(groupItems, warning);
  const reloadGroupData = () => fetchServices(true);
  const saveGroup = (id: string, group: string | null) => providerAPI.updateApiService(id, { group });
  const loadGroupItems = async () => {
    const { data } = await providerAPI.listApiServices({ force: true });
    return groupInventory(data.filter((service) => service.integration_type !== 'device'));
  };
  const moveGroup = async (key: string, group: string | null) => {
    const inventory = await loadGroupItems();
    await saveGroupItems(inventory.filter((item) => item.key === key), group, saveGroup, reloadGroupData, t);
  };
  const createGroup = async (key: string, group: string) => {
    const inventory = await loadGroupItems();
    if (inventory.some((item) => matchesGroup(item.group, group))) throw new Error(t('pluginGroups:validation.duplicate'));
    await saveGroupItems(inventory.filter((item) => item.key === key), group, saveGroup, reloadGroupData, t);
  };
  const changeGroup = async (from: string, to: string | null) => {
    const inventory = await loadGroupItems();
    if (to !== null && inventory.some((item) => matchesGroup(item.group, to))) throw new Error(t('pluginGroups:validation.duplicate'));
    const members = inventory.filter((item) => matchesGroup(item.group, from));
    await saveGroupItems(members, to, saveGroup, reloadGroupData, t, true);
  };
  const query = searchQuery.trim().toLowerCase();
  const matchesQuery = (...values: (string | undefined)[]) => !query || values.some((value) => value?.toLowerCase().includes(query));
  const visibleServices = services.filter((service) => matchesGroup(service.group, groupSelection)
    && (matchesQuery(service.id, service.name, service.description, service.description_cn)
      || (!toolSearchPending && (matchingToolServices?.[service.id] ?? 0) > 0)));
  const visibleCatalog = filteredCatalog.filter((entry) => matchesGroup(entry.group, groupSelection)
    && matchesQuery(entry.id, entry.name, entry.description, entry.description_cn, ...entry.tags));

  return (
    <div className="flex flex-col gap-4 md:flex-row" aria-busy={!!query && toolSearchPending}>
      <GroupNav inventoryComplete={!servicesLoading && servicesError === null && !catalogLoading} preferenceKey="tools.api" {...deriveGroupNav(groupItems)} items={groupItems} selection={groupSelection} onSelect={setGroupSelection} onMove={moveGroup} onCreate={createGroup} onRename={changeGroup} onDelete={(name) => changeGroup(name, null)} {...groupDrag} />
      <div className="min-w-0 flex-1 space-y-4">
      {servicesError !== null && <div role="alert" className="flex items-center justify-between gap-3 rounded-lg border border-danger bg-danger-soft px-4 py-3 text-sm text-danger">
        <span>{extractErrorMessage(servicesError, t('alert.refreshFailedTitle'))}</span>
        <button type="button" onClick={() => void fetchServices()} disabled={servicesLoading} className="shrink-0 rounded border px-3 py-1.5 disabled:opacity-50">{t('button.retry')}</button>
      </div>}
      <div className="flex items-center gap-1.5 flex-wrap">
        <button
          onClick={() => setSelectedCategory('all')}
          className={`px-2.5 py-1 rounded-pill text-xs font-medium transition-colors ${selectedCategory === 'all' ? 'bg-ai-soft text-ai' : 'bg-subtle text-fg-secondary hover:bg-hover'}`}
          >
            {t('api.filterAll')}
          </button>
        {Object.entries(catalogCategories).map(([id, cat]) =>
          catalogCategoryCounts[id] ? (
            <button
              key={id}
              onClick={() => setSelectedCategory(id)}
              className={`px-2.5 py-1 rounded-pill text-xs font-medium transition-colors ${selectedCategory === id ? 'bg-ai-soft text-ai' : 'bg-subtle text-fg-secondary hover:bg-hover'}`}
            >
              {cat.label} ({catalogCategoryCounts[id]})
            </button>
          ) : null
        )}
      </div>

      {(servicesLoading && services.length === 0 && filteredCatalog.length === 0)
        || (query && toolSearchPending && visibleServices.length === 0 && visibleCatalog.length === 0) ? (
        <div className="flex min-h-[360px] items-center justify-center rounded-control border border-line bg-surface">
          <LoadingSpinner delayMs={180} />
        </div>
      ) : visibleServices.length === 0 && visibleCatalog.length === 0 && !catalogLoading ? (
        <EmptyState icon={<IconCloud aria-hidden="true" className="w-8 h-8" />} title={t('api.noTools')} description={t('api.noToolsDesc')} />
      ) : (
        <div className={viewMode === 'list' ? 'bg-surface border border-line rounded-control overflow-x-auto divide-y divide-line' : 'grid gap-3 sm:grid-cols-2 xl:grid-cols-3'}>
          {visibleServices.map((service) => {
            const isSelected = selectedServiceId === service.id;
            const rowDescription = getServiceDescription(service) || `${service.name} API service`;

            return (
              <div
                key={service.id}
                {...groupDrag.dragProps(service.id)}
                className={`${viewMode === 'list' ? SERVICE_TAB_ROW_CLASS : SERVICE_TAB_CARD_CLASS} transition-colors ${isSelected ? 'bg-ai-soft' : `${viewMode === 'cards' ? 'bg-surface ' : ''}hover:bg-hover`}`}
                style={{ gridTemplateColumns: viewMode === 'list' ? SERVICE_TAB_GRID_COLS : SERVICE_TAB_CARD_GRID_COLS }}
              >
                {/* Icon */}
                <div className={`w-8 h-8 flex items-center justify-center rounded-control ${service.enabled ? 'bg-ai-soft' : 'bg-subtle'}`}>
                  <IconCloud aria-hidden="true" className={`w-4 h-4 ${service.enabled ? 'text-ai' : 'text-fg-muted'}`} />
                </div>

                {/* Name + description.
                    Whole block is the click target (mirrors Hub catalog rows
                    where name+description live inside one `<button>`).  Toggle
                    is `isSelected ? null : id` so a second click collapses
                    the panel, matching the "manage" button's behaviour. */}
                <button
                  type="button"
                  onClick={() => setSelectedServiceId(isSelected ? null : service.id)}
                  className={`${SERVICE_TAB_NAME_CELL} text-left group/name `}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm font-semibold text-fg truncate transition-colors group-hover/name:text-ai group-focus-visible/name:underline">{service.name}</span>
                    {service.version && (
                      <span className="px-1.5 py-0.5 bg-info-soft text-info border border-info text-xs font-medium rounded-tag shrink-0" title={t('serviceInfo.version')}>
                        v{service.version.replace(/^v/i, '')}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-fg-muted truncate mt-0.5">{rowDescription}</p>
                </button>

                {/* Type column */}
                <div className={SERVICE_TAB_BADGE_CELL}>
                  <span className="px-1.5 py-0.5 bg-ai-soft text-ai text-xs font-medium rounded-tag">API</span>
                </div>

                {/* Status column */}
                <div className={SERVICE_TAB_BADGE_CELL}>
                  <span className={`px-1.5 py-0.5 text-xs font-medium rounded-tag ${service.enabled ? 'bg-success-soft text-success' : 'bg-subtle text-fg-muted'}`}>
                    {service.enabled ? t('enabledBadge.enabled') : t('enabledBadge.disabled')}
                  </span>
                </div>

                {/* Stats column */}
                <div className={`${SERVICE_TAB_STATS_CELL} gap-3 text-xs text-fg-muted`}>
                  <span className="flex items-center gap-1"><IconTool aria-hidden="true" className="w-4 h-4" />{service.tool_count}</span>
                  {service.latency_ms != null && <span>{service.latency_ms}ms</span>}
                </div>

                {/* Actions column */}
                <div className={`${SERVICE_TAB_ACTIONS_CELL} flex-wrap gap-1.5`}>
                  <PluginGroupButton grouping={groupDrag} itemKey={service.id} iconOnly className="border border-line hover:border-line-control" />
                  {service.enabled ? (
                    <>
                      <button
                        onClick={() => setSelectedServiceId(isSelected ? null : service.id)}
                        className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-control border text-xs font-medium transition-colors ${isSelected ? 'border-ai text-ai bg-ai-soft' : 'border-line text-fg-secondary hover:bg-hover'}`}
                      >
                        <IconSettings aria-hidden="true" className="w-4 h-4" />{t('mcp.manage')}
                      </button>
                      <button
                        onClick={(e) => handleToggleEnabled(service.id, false, e)}
                        disabled={installing === service.id}
                        className="ui-btn-icon inline-flex items-center justify-center p-1.5 rounded-control border border-danger text-danger hover:bg-danger-soft transition-colors disabled:bg-disabled disabled:text-fg-disabled"
                        title={t('detail.disableServer')}
                      >
                        <IconPlugOff aria-hidden="true" className="w-4 h-4" />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={(e) => handleToggleEnabled(service.id, true, e)}
                        disabled={installing === service.id}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-control bg-action text-action-fg text-xs font-medium hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled transition-colors"
                      >
                        <IconPower aria-hidden="true" className="w-4 h-4" />{installing === service.id ? t('mcp.configuring') : t('detail.enableServer')}
                      </button>
                      <button
                        onClick={() => setSelectedServiceId(isSelected ? null : service.id)}
                        className="ui-btn-icon inline-flex items-center justify-center p-1.5 rounded-control border border-line text-fg-muted hover:bg-hover transition-colors"
                        title={t('mcp.manage')}
                      >
                        <IconSettings aria-hidden="true" className="w-4 h-4" />
                      </button>
                    </>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); if (!service.builtin) handleDeleteService(service.id); }}
                    disabled={!!service.builtin || installing === service.id}
                    className={`ui-btn-icon inline-flex items-center justify-center p-1.5 rounded-control border transition-colors ${service.builtin ? 'border-line text-fg-placeholder cursor-not-allowed' : 'border-danger text-danger hover:bg-danger-soft'}`}
                    title={service.builtin ? t('api.builtinCannotDelete') : t('button.delete')}
                  >
                    <IconTrash aria-hidden="true" className="w-4 h-4" />
                  </button>
                </div>
              </div>
            );
          })}

          {visibleCatalog.map((entry) => (
            <div
              key={`catalog-${entry.id}`}
              {...groupDrag.dragProps(`catalog:${entry.id}`)}
              className={`${viewMode === 'list' ? SERVICE_TAB_ROW_CLASS : `${SERVICE_TAB_CARD_CLASS} bg-surface`} cursor-default hover:bg-hover transition-colors`}
              style={{ gridTemplateColumns: viewMode === 'list' ? SERVICE_TAB_GRID_COLS : SERVICE_TAB_CARD_GRID_COLS }}
            >
              {/* Icon */}
              <div className="w-8 h-8 flex items-center justify-center rounded-control bg-subtle">
                <IconCloud aria-hidden="true" className="w-4 h-4 text-fg-muted" />
              </div>

              {/* Name + description */}
              <div className={SERVICE_TAB_NAME_CELL}>
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-semibold text-fg truncate">{entry.name}</span>
                  {PRIORITY_IDS.has(entry.id) && (
                    <span className="px-1.5 py-0.5 bg-warning-soft text-warning text-xs font-medium rounded-tag border border-warning shrink-0">{t('api.recommended')}</span>
                  )}
                </div>
                <p className="text-xs text-fg-muted truncate mt-0.5">{getCatalogDescription(entry, i18n.language)}</p>
              </div>

              {/* Type column */}
              <div className={SERVICE_TAB_BADGE_CELL}>
                <span className="px-1.5 py-0.5 bg-ai-soft text-ai text-xs font-medium rounded-tag">API</span>
              </div>

              {/* Status column */}
              <div className={SERVICE_TAB_BADGE_CELL}>
                <span className="px-1.5 py-0.5 bg-subtle text-fg-muted text-xs font-medium rounded-tag">{t('statusBadge.inactive')}</span>
              </div>

              {/* Stats column */}
              <div className={`${SERVICE_TAB_STATS_CELL} gap-2 text-xs text-fg-muted`}>
                <span className={`px-1.5 py-0.5 rounded-tag text-xs font-medium ${LANG_COLORS[entry.language] || 'bg-subtle text-fg-secondary'}`}>{entry.language}</span>
                <span className="flex items-center gap-1"><IconStar aria-hidden="true" className="w-4 h-4" />{entry.stars}</span>
              </div>

              {/* Actions column */}
              <div className={`${SERVICE_TAB_ACTIONS_CELL} flex-wrap gap-1.5`}>
                <PluginGroupButton grouping={groupDrag} itemKey={`catalog:${entry.id}`} iconOnly className="border border-line hover:border-line-control" />
                <button
                  onClick={() => {
                    if (entry.requires_auth && !isConfigured(entry.id)) {
                      openCredModal(entry);
                    } else {
                      handleInstallNoAuth(entry);
                    }
                  }}
                  disabled={installing === entry.id}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-control bg-action text-action-fg text-xs font-medium hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled transition-colors"
                >
                  <IconDownload aria-hidden="true" className="w-4 h-4" />{installing === entry.id ? t('api.configuring') : t('button.install')}
                </button>
                {entry.github ? (
                  <a
                    href={`https://github.com/${entry.github}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="p-1.5 rounded-control border border-line text-fg-muted hover:bg-hover transition-colors"
                    title="GitHub"
                  >
                    <IconExternalLink aria-hidden="true" className="w-4 h-4" />
                  </a>
                ) : (
                  <span className="w-7 h-7" aria-hidden="true" />
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      </div>

      {credModalEntry && (
        <>
          <div className="fixed inset-0 bg-overlay-backdrop z-40" onClick={() => setCredModalEntry(null)} />
          <PageDialog onClose={() => setCredModalEntry(null)} className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="bg-surface rounded-card shadow-overlay w-full max-w-md" onClick={(e) => e.stopPropagation()}>
              <div className="px-6 py-4 border-b border-line">
                <h3 className="text-title-card font-semibold text-fg">{credModalEntry.name}</h3>
                <p className="text-sm text-fg-muted mt-1">{t('alert.credDescHint')}</p>
              </div>
              <div className="px-6 py-4 space-y-4">
                {Object.entries(credModalEntry.env_vars).filter(([, spec]) => spec.secret).map(([key, spec]) => (
                  <div key={key}>
                    <label className="block text-sm font-medium text-fg-secondary mb-1">{key}</label>
                    <p className="text-xs text-fg-muted mb-1.5">{spec.description}</p>
                    <input
                      type="password"
                      value={credValues[key] || ''}
                      onChange={(e) => setCredValues((prev) => ({ ...prev, [key]: e.target.value }))}
                      placeholder={t('credentials.enterField', { field: key })}
                      className="ui-input w-full px-3  text-sm border border-line-control rounded-control focus:border-transparent"
                    />
                  </div>
                ))}
              </div>
              <div className="px-6 py-4 border-t border-line flex gap-3 justify-end">
                <button onClick={() => setCredModalEntry(null)} className="px-4 py-2 text-sm text-fg-secondary border border-line-control rounded-control hover:bg-hover transition-colors">
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
          </PageDialog>
        </>
      )}

      {selectedServiceId && selectedService && (() => {
        const catalogEntry = catalogEntries.find((entry) => entry.id === selectedServiceId);
        const displayName = catalogEntry?.name || selectedService.name || selectedServiceId;
        return (
          <PageDialog variant="panels" className="contents" onClose={() => setSelectedServiceId(null)}>
            <div className="fixed inset-0 bg-overlay-backdrop z-40" onClick={() => setSelectedServiceId(null)} />
            <div data-ui-overlay="drawer" className="fixed right-0 top-0 bottom-0 z-50 flex min-h-0 flex-col w-full bg-surface shadow-overlay" style={{ maxWidth: DETAIL_DRAWER_WIDTH }} onClick={(e) => e.stopPropagation()}>
              <div className="flex-shrink-0 border-b border-line">
                <div className="flex items-center gap-3 px-6 py-4">
                  <div className="w-10 h-10 rounded-control flex items-center justify-center flex-shrink-0 bg-ai-soft">
                    <IconCloud aria-hidden="true" className="w-5 h-5 text-ai" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h2 className="text-title-section font-semibold text-fg">{displayName}</h2>
                    <p className="text-sm text-fg-muted">{t('api.serviceConfig')}</p>
                  </div>
                  <button aria-label={pageActionLabel('close')} onClick={() => setSelectedServiceId(null)} className="ui-btn-icon inline-flex items-center justify-center text-fg-muted hover:text-fg-secondary p-2 rounded-control hover:bg-hover flex-shrink-0">
                    <IconX aria-hidden="true" className="w-5 h-5" />
                  </button>
                </div>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                {catalogEntry && (
                  <div className="px-6 py-4 bg-subtle border-b border-line">
                    <p className="text-sm text-fg-secondary whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{getCatalogDescription(catalogEntry, i18n.language)}</p>
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
          </PageDialog>
        );
      })()}
    </div>
  );
}
