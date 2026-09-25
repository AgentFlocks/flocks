import { pageActionLabel } from '../../pageActionLabel';
import PageDialog from '../../PageDialog';
import { useState, useMemo, useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  IconServer,
  IconDatabase,
  IconTool,
  IconFileText,
  IconClock,
  IconSettings,
  IconPlugOff,
  IconRefresh,
  IconX,
  IconDownload,
  IconExternalLink,
  IconStar,
  IconTestPipe,
  IconPlayerPlay,
  IconInfoCircle,
  IconCircleCheck,
  IconCircleX,
  IconAlertTriangle,
  IconPower,
} from '@tabler/icons-react';
import { mcpAPI } from '@/api/mcp';
import { listAllToolPages, toolAPI } from '@/api/tool';
import type { Tool } from '@/api/tool';
import type { MCPCatalogCategory, MCPCatalogEntry, MCPServer } from '@/types';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { getCatalogDescription, getMetadataDescription } from '@/utils/mcpCatalog';
import { MCPServerDetailPanel } from './ServiceDetailPanel';
import {
  SERVICE_TAB_ACTIONS_CELL,
  SERVICE_TAB_BADGE_CELL,
  SERVICE_TAB_GRID_COLS,
  SERVICE_TAB_NAME_CELL,
  SERVICE_TAB_ROW_CLASS,
  SERVICE_TAB_STATS_CELL,
} from './gridLayout';

const DETAIL_DRAWER_WIDTH = 480;
const TOOL_PANEL_WIDTH = 720;
const LANG_COLORS: Record<string, string> = {
  python: 'bg-subtle text-fg-secondary',
  typescript: 'bg-subtle text-fg-secondary',
  go: 'bg-subtle text-fg-secondary',
  rust: 'bg-subtle text-fg-secondary',
  java: 'bg-subtle text-fg-secondary',
  c: 'bg-subtle text-fg-secondary',
};
const INSTALL_BUTTON_CLASS = 'flex-1 flex items-center justify-center gap-1 py-1 px-2 bg-success-soft text-success rounded-control text-xs font-medium hover:bg-success-soft disabled:bg-disabled disabled:text-fg-disabled transition-colors';

function formatDuration(connectedAt: number, t: (key: string, options?: Record<string, unknown>) => string): string {
  const now = Date.now() / 1000;
  const diff = now - connectedAt;
  if (diff < 60) return t('duration.seconds', { count: Math.floor(diff) });
  if (diff < 3600) return t('duration.minutes', { count: Math.floor(diff / 60) });
  if (diff < 86400) return t('duration.hours', { count: Math.floor(diff / 3600) });
  return t('duration.days', { count: Math.floor(diff / 86400) });
}

interface MCPTabContentProps {
  tools: Tool[];
  searchQuery: string;
  onSelectTool: (tool: Tool) => void;
  onRefreshTools: () => Promise<void>;
  catalogEntries: MCPCatalogEntry[];
  catalogCategories: Record<string, MCPCatalogCategory>;
  catalogLoading: boolean;
  configuredIds: Set<string>;
  onConfiguredChange: (id: string) => void;
  onConfiguredRemove?: (id: string) => void;
  refreshKey?: number;
}

export default function MCPTabContent({
  tools,
  searchQuery,
  onSelectTool,
  onRefreshTools,
  catalogEntries,
  catalogCategories,
  catalogLoading,
  configuredIds,
  onConfiguredChange,
  onConfiguredRemove,
  refreshKey,
}: MCPTabContentProps) {
  const { t, i18n } = useTranslation('tool');
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [serversLoading, setServersLoading] = useState(true);
  const [selectedServer, setSelectedServer] = useState<string | null>(null);
  const [selectedServerData, setSelectedServerData] = useState<MCPServer | null>(null);
  const [selectedToolFromMCP, setSelectedToolFromMCP] = useState<Tool | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [installing, setInstalling] = useState<string | null>(null);
  const [setupEntry, setSetupEntry] = useState<MCPCatalogEntry | null>(null);
  const [setupCredentials, setSetupCredentials] = useState<Record<string, string>>({});
  const [setupEnvOverrides, setSetupEnvOverrides] = useState<Record<string, string>>({});
  const [serverToolCache, setServerToolCache] = useState<Record<string, Tool[]>>({});

  const fetchServers = useCallback(async () => {
    try {
      setServersLoading(true);
      const response = await mcpAPI.list();
      const data = response.data;
      let newServers: MCPServer[] = [];
      if (Array.isArray(data)) {
        newServers = data;
      } else if (data && typeof data === 'object') {
        newServers = Object.entries(data).map(([name, info]: [string, any]) => ({
          name,
          status: info.status === 'failed' ? 'error' as const : (info.status || 'disconnected') as MCPServer['status'],
          url: info.url || info.metadata?.url,
          tools: info.tools || [],
          resources: info.resources || [],
          error: info.error,
          connected_at: info.connected_at,
          tools_count: info.tools_count || 0,
          resources_count: info.resources_count || 0,
          metadata: info.metadata,
        }));
      }
      setServers(newServers);
      setSelectedServerData((prev) => {
        if (!prev) return prev;
        const updated = newServers.find((server) => server.name === prev.name);
        return updated ?? prev;
      });
    } catch {
      setServers([]);
    } finally {
      setServersLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchServers();
  }, [fetchServers]);

  useEffect(() => {
    if (refreshKey && refreshKey > 0) {
      fetchServers();
    }
  }, [refreshKey, fetchServers]);

  const handleConnect = async (name: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try {
      await mcpAPI.connect(name);
      await fetchServers();
      await onRefreshTools();
    } catch (err: any) {
      alert(t('alert.connectFailed', { error: err.message }));
    }
  };

  const handleDisconnect = async (name: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try {
      await mcpAPI.disconnect(name);
      await fetchServers();
      await onRefreshTools();
    } catch (err: any) {
      alert(t('alert.disconnectFailed', { error: err.message }));
    }
  };

  const handleRemove = async (name: string) => {
    if (!window.confirm(t('alert.confirmRemoveMCP', { name }))) return;
    try {
      await mcpAPI.remove(name);
      setSelectedServer(null);
      setSelectedServerData(null);
      onConfiguredRemove?.(name);
      await fetchServers();
      await onRefreshTools();
    } catch (err: any) {
      alert(t('alert.removeFailed', { error: err.response?.data?.detail ?? err.message }));
    }
  };

  const toolsByServer = useMemo(() => {
    const map: Record<string, Tool[]> = {};
    tools.filter((tool) => tool.source === 'mcp').forEach((tool) => {
      const key = tool.source_name || 'unknown';
      if (!map[key]) map[key] = [];
      map[key].push(tool);
    });
    return map;
  }, [tools]);

  useEffect(() => {
    setServerToolCache({});
  }, [tools]);

  useEffect(() => {
    if (!selectedServer || serverToolCache[selectedServer]) return;
    let cancelled = false;
    void listAllToolPages({
      source: 'mcp',
      sourceName: selectedServer,
      sortBy: 'name',
      sortDir: 'asc',
    }).then((serverTools) => {
      if (!cancelled) {
        setServerToolCache((current) => ({ ...current, [selectedServer]: serverTools }));
      }
    }).catch(() => {
      // Keep the current page slice visible; reopening retries the complete server list.
    });
    return () => {
      cancelled = true;
    };
  }, [selectedServer, serverToolCache]);

  const setSelectedCard = useCallback((name: string | null) => {
    setSelectedServer(name);
    setSelectedServerData(name ? (servers.find((server) => server.name === name) ?? null) : null);
  }, [servers]);

  const selectMcpTool = useCallback((tool: Tool) => {
    setSelectedToolFromMCP(tool);
    toolAPI.get(tool.name)
      .then((response) => {
        setSelectedToolFromMCP((current) => (
          current?.name === tool.name ? response.data : current
        ));
      })
      .catch(() => {
        // Keep the summary row visible; reopening retries the detail request.
      });
  }, []);

  const selectedServerObj = useMemo(() => {
    if (!selectedServer) return undefined;
    const fromServers = servers.find((server) => server.name === selectedServer);
    if (fromServers) return fromServers;
    const fromCatalog = catalogEntries.find((entry) => entry.id === selectedServer);
    if (fromCatalog) {
      return {
        name: fromCatalog.id,
        status: 'disconnected' as const,
        tools: [],
        resources: [],
        tools_count: 0,
        resources_count: 0,
      };
    }
    return undefined;
  }, [selectedServer, servers, catalogEntries]);

  const isConfigured = useCallback((entryId: string) => configuredIds.has(entryId), [configuredIds]);
  const PRIORITY_IDS = useMemo(() => new Set(['virustotal_mcp', 'urlhaus']), []);

  const filteredCatalogEntries = useMemo(() => {
    let result = [...catalogEntries];
    if (selectedCategory !== 'all') {
      result = result.filter((entry) => entry.category === selectedCategory);
    }
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      result = result.filter((entry) =>
        entry.name.toLowerCase().includes(q) ||
        entry.description.toLowerCase().includes(q) ||
        (entry.description_cn || '').toLowerCase().includes(q) ||
        entry.id.toLowerCase().includes(q) ||
        entry.tags.some((tag) => tag.toLowerCase().includes(q)) ||
        (toolsByServer[entry.id] || []).some(
          (tool) => tool.name.toLowerCase().includes(q) || tool.description.toLowerCase().includes(q),
        )
      );
    }
    result.sort((a, b) => {
      const aActive = servers.find((s) => s.name === a.id)?.status === 'connected';
      const bActive = servers.find((s) => s.name === b.id)?.status === 'connected';
      if (aActive !== bActive) return aActive ? -1 : 1;
      const nameA = (a.name || a.id || '').toLowerCase();
      const nameB = (b.name || b.id || '').toLowerCase();
      return nameA.localeCompare(nameB);
    });
    return result;
  }, [catalogEntries, selectedCategory, searchQuery, servers, toolsByServer]);

  const catalogCategoryCounts = useMemo(() => {
    const counts: Record<string, number> = { all: catalogEntries.length };
    for (const entry of catalogEntries) {
      counts[entry.category] = (counts[entry.category] || 0) + 1;
    }
    return counts;
  }, [catalogEntries]);

  const requiresSetupBeforeInstall = useCallback((entry: MCPCatalogEntry) => (
    Object.values(entry.env_vars || {}).some((spec) => spec.secret || spec.required)
  ), []);

  const openSetupModal = useCallback((entry: MCPCatalogEntry) => {
    const nextCredentials: Record<string, string> = {};
    const nextEnvOverrides: Record<string, string> = {};
    for (const [key, spec] of Object.entries(entry.env_vars || {})) {
      if (spec.secret) {
        nextCredentials[key] = '';
      } else {
        nextEnvOverrides[key] = spec.default || '';
      }
    }
    setSetupCredentials(nextCredentials);
    setSetupEnvOverrides(nextEnvOverrides);
    setSetupEntry(entry);
  }, []);

  const installCatalogEntry = useCallback(async (
    entry: MCPCatalogEntry,
    options?: {
      credentials?: Record<string, string>;
      env_overrides?: Record<string, string>;
    },
  ) => {
    let shouldConnect = false;
    let installRes: Awaited<ReturnType<typeof mcpAPI.catalogInstall>> | null = null;
    try {
      setInstalling(entry.id);
      installRes = await mcpAPI.catalogInstall(entry.id, options);
      shouldConnect = installRes.data?.config?.enabled !== false;
      onConfiguredChange(entry.id);
    } catch (err: any) {
      alert(t('alert.configFailed', { error: err.response?.data?.detail || err.message }));
      setInstalling(null);
      return false;
    }
    try {
      if (shouldConnect) {
        await mcpAPI.connect(entry.id);
      }
      await fetchServers();
      await onRefreshTools();
      setSelectedCard(entry.id);
      if (!shouldConnect) {
        alert(t('alert.mcpConfiguredDisabled', { name: entry.name }));
      }
    } catch (err: any) {
      alert(t('alert.connectFailed', { error: err.response?.data?.detail || err.message }));
    } finally {
      setInstalling(null);
    }
    return true;
  }, [fetchServers, onConfiguredChange, onRefreshTools, setSelectedCard, t]);

  const handleInstallNoAuth = async (entry: MCPCatalogEntry, e?: React.MouseEvent) => {
    e?.stopPropagation();
    return installCatalogEntry(entry);
  };

  const handleSetupSubmit = async () => {
    if (!setupEntry) return;

    const missingRequired = Object.entries(setupEntry.env_vars || {}).some(([key, spec]) => {
      const value = spec.secret ? setupCredentials[key] : setupEnvOverrides[key];
      return spec.required && !String(value || '').trim();
    });
    if (missingRequired) {
      alert(t('alert.fillAllRequired'));
      return;
    }

    const credentials = Object.fromEntries(
      Object.entries(setupCredentials).filter(([, value]) => String(value || '').trim()),
    );
    const envOverrides = Object.fromEntries(
      Object.entries(setupEnvOverrides).filter(([, value]) => String(value || '').trim()),
    );

    const success = await installCatalogEntry(setupEntry, {
      credentials: Object.keys(credentials).length ? credentials : undefined,
      env_overrides: Object.keys(envOverrides).length ? envOverrides : undefined,
    });
    if (success) {
      setSetupEntry(null);
    }
  };

  const handleToggleEnabled = async (name: string, enabled: boolean, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try {
      setInstalling(name);
      await mcpAPI.update(name, { enabled });
      if (!enabled) {
        setSelectedCard(null);
      }
      await fetchServers();
      await onRefreshTools();
    } catch (err: any) {
      alert(t('alert.saveFailed', { error: err.response?.data?.detail || err.message }));
    } finally {
      setInstalling(null);
    }
  };

  const filteredServers = useMemo(() => {
    if (!searchQuery) return servers;
    const q = searchQuery.toLowerCase();
    return servers.filter(
      (server) =>
        server.name.toLowerCase().includes(q) ||
        (server.url && server.url.toLowerCase().includes(q)) ||
        (toolsByServer[server.name] || []).some(
          (tool) => tool.name.toLowerCase().includes(q) || tool.description.toLowerCase().includes(q)
        )
    );
  }, [servers, searchQuery, toolsByServer]);

  const catalogEntryById = useMemo(
    () => new Map(catalogEntries.map((entry) => [entry.id, entry])),
    [catalogEntries],
  );

  const unifiedCards = useMemo(() => {
    const cardMap = new Map<string, {
      id: string;
      entry?: MCPCatalogEntry;
      runtimeServer?: MCPServer;
      server: MCPServer;
      configured: boolean;
    }>();

    for (const server of filteredServers) {
      const entry = catalogEntryById.get(server.name);
      cardMap.set(server.name, {
        id: server.name,
        entry,
        runtimeServer: server,
        server,
        configured: true,
      });
    }

    for (const entry of filteredCatalogEntries) {
      const existing = cardMap.get(entry.id);
      const runtimeServer = existing?.runtimeServer ?? servers.find((server) => server.name === entry.id);
      cardMap.set(entry.id, {
        id: entry.id,
        entry,
        runtimeServer,
        server: existing?.server ?? runtimeServer ?? {
          name: entry.id,
          status: 'disconnected',
          tools: [],
          resources: [],
          tools_count: 0,
          resources_count: 0,
        },
        configured: existing?.configured ?? (isConfigured(entry.id) || !!runtimeServer),
      });
    }

    return [...cardMap.values()].sort((a, b) => {
      const aActive = a.server.status === 'connected';
      const bActive = b.server.status === 'connected';
      if (aActive !== bActive) return aActive ? -1 : 1;
      return (a.entry?.name || a.server.name || '').toLowerCase().localeCompare(
        (b.entry?.name || b.server.name || '').toLowerCase(),
      );
    });
  }, [filteredServers, filteredCatalogEntries, servers, isConfigured, catalogEntryById]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1.5 flex-wrap">
        <button
          onClick={() => setSelectedCategory('all')}
          className={`px-2.5 py-1 rounded-pill text-xs font-medium transition-colors ${selectedCategory === 'all' ? 'bg-subtle text-fg-secondary' : 'bg-subtle text-fg-secondary hover:bg-hover'}`}
        >
          {t('catalog.all')}
        </button>
        {Object.entries(catalogCategories).map(([id, cat]) =>
          catalogCategoryCounts[id] ? (
            <button
              key={id}
              onClick={() => setSelectedCategory(id)}
              className={`px-2.5 py-1 rounded-pill text-xs font-medium transition-colors ${selectedCategory === id ? 'bg-subtle text-fg-secondary' : 'bg-subtle text-fg-secondary hover:bg-hover'}`}
            >
              {cat.label} ({catalogCategoryCounts[id]})
            </button>
          ) : null
        )}
      </div>

      {serversLoading && catalogLoading ? (
        <div className="flex min-h-[360px] items-center justify-center rounded-control border border-line bg-surface">
          <LoadingSpinner delayMs={180} />
        </div>
      ) : unifiedCards.length === 0 ? (
        <EmptyState icon={<IconServer aria-hidden="true" className="w-8 h-8" />} title={t('mcp.noServers')} description={t('mcp.noServersDesc')} />
      ) : (
        <div className="bg-surface border border-line rounded-control overflow-x-auto divide-y divide-line">
          {unifiedCards.map(({ id, entry, server, configured }) => {
            const displayName = entry?.name || server.name;
            const metadataDescription = getMetadataDescription(server.metadata, i18n.language);
            const rowDescription = getCatalogDescription(entry, i18n.language)
              || metadataDescription
              || `${displayName} MCP server`;
            const serverTools = toolsByServer[id] || [];
            const isSelected = selectedServer === id;
            const isActive = server.status === 'connected';
            const isError = server.status === 'error';
            const isDisabled = server.status === 'disabled';
            const isActionable = isActive || configured;
            const statusLabel = isActive
              ? t('statusBadge.active')
              : isError
              ? t('statusBadge.error')
              : isDisabled
              ? t('statusBadge.disabled')
              : t('statusBadge.inactive');
            const statusClass = isActive
              ? 'bg-success-soft text-success'
              : isError
              ? 'bg-danger-soft text-danger'
              : 'bg-subtle text-fg-muted';

            return (
              <div
                key={id}
                className={`${SERVICE_TAB_ROW_CLASS} transition-colors ${isSelected ? 'bg-danger-soft' : 'hover:bg-hover'}`}
                style={{ gridTemplateColumns: SERVICE_TAB_GRID_COLS }}
              >
                {/* Icon */}
                <div className={`w-8 h-8 flex items-center justify-center rounded-control ${isActive ? 'bg-success-soft' : isError ? 'bg-danger-soft' : 'bg-subtle'}`}>
                  <IconServer aria-hidden="true" className={`w-4 h-4 ${isActive ? 'text-success' : isError ? 'text-danger' : 'text-fg-muted'}`} />
                </div>

                {/* Name + description.
                    Whole block is the click target — mirrors the Hub catalog
                    pattern (``Hub/index.tsx`` HubTable row) so the description
                    line is just as clickable as the name itself. */}
                <button
                  type="button"
                  onClick={() => setSelectedCard(isSelected ? null : id)}
                  className={`${SERVICE_TAB_NAME_CELL} text-left group/name `}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm font-semibold text-fg truncate transition-colors group-hover/name:text-fg-secondary group-focus-visible/name:underline">{displayName}</span>
                    {entry && PRIORITY_IDS.has(entry.id) && (
                      <span className="px-1.5 py-0.5 bg-warning-soft text-warning text-xs font-medium rounded-tag border border-warning shrink-0">{t('mcp.recommended')}</span>
                    )}
                  </div>
                  <p className="text-xs text-fg-muted truncate mt-0.5">{rowDescription}</p>
                </button>

                {/* Type column */}
                <div className={SERVICE_TAB_BADGE_CELL}>
                  <span className="px-1.5 py-0.5 bg-subtle text-fg-secondary text-xs font-medium rounded-tag">MCP</span>
                </div>

                {/* Status column */}
                <div className={SERVICE_TAB_BADGE_CELL}>
                  <span className={`px-1.5 py-0.5 text-xs font-medium rounded-tag ${statusClass}`}>
                    {statusLabel}
                  </span>
                </div>

                {/* Stats column */}
                <div className={`${SERVICE_TAB_STATS_CELL} gap-3 text-xs text-fg-muted`}>
                  <span className="flex items-center gap-1"><IconTool aria-hidden="true" className="w-4 h-4" />{server.tools_count ?? serverTools.length}</span>
                  {server.connected_at ? (
                    <span className="flex items-center gap-1"><IconClock aria-hidden="true" className="w-4 h-4" />{formatDuration(server.connected_at, t)}</span>
                  ) : entry ? (
                    <span className="flex items-center gap-1"><IconStar aria-hidden="true" className="w-4 h-4" />{entry.stars}</span>
                  ) : null}
                </div>

                {/* Actions column */}
                <div className={`${SERVICE_TAB_ACTIONS_CELL} gap-1.5`}>
                  {isActive ? (
                    <>
                      <button
                        onClick={() => setSelectedCard(isSelected ? null : id)}
                        className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-control border text-xs font-medium transition-colors ${isSelected ? 'border-danger text-danger bg-danger-soft' : 'border-line text-fg-secondary hover:bg-hover'}`}
                      >
                        <IconSettings aria-hidden="true" className="w-4 h-4" />{t('mcp.manage')}
                      </button>
                      <button onClick={(e) => handleDisconnect(id, e)} className="ui-btn-icon inline-flex items-center justify-center p-1.5 rounded-control border border-danger text-danger hover:bg-danger-soft transition-colors" title={t('mcp.disconnectTitle')}>
                        <IconPlugOff aria-hidden="true" className="w-4 h-4" />
                      </button>
                    </>
                  ) : isDisabled ? (
                    <>
                      <button
                        onClick={(e) => handleToggleEnabled(id, true, e)}
                        disabled={installing === id}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-control bg-action text-action-fg text-xs font-medium hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled transition-colors"
                      >
                        <IconPower aria-hidden="true" className="w-4 h-4" />{installing === id ? t('mcp.configuring') : t('detail.enableServer')}
                      </button>
                      <button
                        onClick={() => setSelectedCard(isSelected ? null : id)}
                        className="ui-btn-icon inline-flex items-center justify-center p-1.5 rounded-control border border-line text-fg-muted hover:bg-hover transition-colors"
                        title={t('mcp.manage')}
                      >
                        <IconSettings aria-hidden="true" className="w-4 h-4" />
                      </button>
                    </>
                  ) : configured ? (
                    <>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleConnect(id, e); }}
                        disabled={installing === id}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-control bg-action text-action-fg text-xs font-medium hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled transition-colors"
                      >
                        <IconPower aria-hidden="true" className="w-4 h-4" />{installing === id ? t('mcp.configuring') : t('mcp.reconnect')}
                      </button>
                      <button
                        onClick={() => setSelectedCard(isSelected ? null : id)}
                        className="ui-btn-icon inline-flex items-center justify-center p-1.5 rounded-control border border-line text-fg-muted hover:bg-hover transition-colors"
                        title={t('mcp.manage')}
                      >
                        <IconSettings aria-hidden="true" className="w-4 h-4" />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          if (entry) {
                            if (requiresSetupBeforeInstall(entry)) {
                              openSetupModal(entry);
                            } else {
                              handleInstallNoAuth(entry, e);
                            }
                          }
                        }}
                        disabled={!entry || installing === id}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-control bg-action text-action-fg text-xs font-medium hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled transition-colors"
                      >
                        <IconDownload aria-hidden="true" className="w-4 h-4" />{installing === id ? t('mcp.configuring') : t('button.install')}
                      </button>
                      {entry?.github ? (
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
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {setupEntry && (
        <>
          <div className="fixed inset-0 bg-overlay-backdrop z-40" onClick={() => setSetupEntry(null)} />
          <PageDialog onClose={() => setSetupEntry(null)} className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div
              className="w-full max-w-lg rounded-card bg-surface shadow-overlay"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="border-b border-line px-6 py-4">
                <h3 className="text-title-card font-semibold text-fg">{setupEntry.name}</h3>
                <p className="mt-1 text-sm text-fg-muted">{t('credentials.configNote')}</p>
              </div>
              <div className="max-h-[70vh] space-y-4 overflow-y-auto px-6 py-4">
                {Object.entries(setupEntry.env_vars || {}).map(([key, spec]) => {
                  const value = spec.secret ? setupCredentials[key] || '' : setupEnvOverrides[key] || '';
                  return (
                    <div key={key}>
                      <label className="mb-1 block text-sm font-medium text-fg-secondary">
                        {key}{spec.required ? <span className="text-danger"> *</span> : null}
                      </label>
                      <p className="mb-1.5 text-xs text-fg-muted">{spec.description || key}</p>
                      <input
                        type={spec.secret ? 'password' : 'text'}
                        value={value}
                        onChange={(e) => {
                          const nextValue = e.target.value;
                          if (spec.secret) {
                            setSetupCredentials((prev) => ({ ...prev, [key]: nextValue }));
                          } else {
                            setSetupEnvOverrides((prev) => ({ ...prev, [key]: nextValue }));
                          }
                        }}
                        placeholder={spec.default || t('credentials.enterField', { field: key })}
                        className="ui-input w-full rounded-control border border-line-control px-3  text-sm focus:border-transparent"
                      />
                    </div>
                  );
                })}
              </div>
              <div className="flex justify-end gap-3 border-t border-line px-6 py-4">
                <button
                  onClick={() => setSetupEntry(null)}
                  className="rounded-control border border-line-control px-4 py-2 text-sm text-fg-secondary transition-colors hover:bg-hover"
                >
                  {t('button.cancel')}
                </button>
                <button
                  onClick={handleSetupSubmit}
                  disabled={installing === setupEntry.id}
                  className="rounded-control bg-action px-4 py-2 text-sm text-action-fg transition-colors hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled"
                >
                  {installing === setupEntry.id ? t('mcp.configuring') : t('button.confirmConfig')}
                </button>
              </div>
            </div>
          </PageDialog>
        </>
      )}

      {selectedServer && (selectedServerData || selectedServerObj) && (() => {
        const drawerServer = selectedServerData || selectedServerObj!;
        const catEntry = catalogEntries.find((entry) => entry.id === selectedServer);
        const displayName = catEntry?.name || drawerServer.name;
        return (
          <PageDialog variant="panels" className="contents" onClose={() => { setSelectedServer(null); setSelectedServerData(null); setSelectedToolFromMCP(null); }}>
            <div className="fixed inset-0 bg-overlay-backdrop z-40" onClick={() => { setSelectedServer(null); setSelectedServerData(null); setSelectedToolFromMCP(null); }} />

            {selectedToolFromMCP && (
              <MCPToolDetailPanel tool={selectedToolFromMCP} onClose={() => setSelectedToolFromMCP(null)} />
            )}

            <div data-ui-overlay="drawer" className="fixed right-0 top-0 bottom-0 z-50 flex flex-col w-full bg-surface shadow-overlay" style={{ maxWidth: DETAIL_DRAWER_WIDTH }} onClick={(e) => e.stopPropagation()}>
              <div className="flex-shrink-0 border-b border-line">
                <div className="flex items-center gap-3 px-6 py-4">
                  <div className={`w-10 h-10 rounded-control flex items-center justify-center flex-shrink-0 ${drawerServer.status === 'connected' ? 'bg-success-soft' : drawerServer.status === 'error' || drawerServer.status === 'failed' ? 'bg-danger-soft' : 'bg-subtle'}`}>
                    <IconDatabase aria-hidden="true" className={`w-5 h-5 ${drawerServer.status === 'connected' ? 'text-success' : drawerServer.status === 'error' || drawerServer.status === 'failed' ? 'text-danger' : 'text-fg-muted'}`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h2 className="text-title-section font-semibold text-fg">{displayName}</h2>
                    <p className="text-sm text-fg-muted">{t('mcp.serverConfig')}</p>
                  </div>
                  <button aria-label={pageActionLabel('close')} onClick={() => { setSelectedServer(null); setSelectedServerData(null); setSelectedToolFromMCP(null); }} className="ui-btn-icon inline-flex items-center justify-center text-fg-muted hover:text-fg-secondary p-2 rounded-control hover:bg-hover flex-shrink-0">
                    <IconX aria-hidden="true" className="w-5 h-5" />
                  </button>
                </div>
              </div>
              <div className="flex-1 overflow-y-auto">
                {catEntry && (
                  <div className="px-6 py-4 bg-subtle border-b border-line">
                    <p className="text-sm text-fg-secondary">{getCatalogDescription(catEntry, i18n.language)}</p>
                    <div className="flex items-center gap-3 mt-2 text-xs text-fg-muted">
                      <span className={`px-1.5 py-0.5 rounded-tag font-medium ${LANG_COLORS[catEntry.language] || 'bg-subtle text-fg-secondary'}`}>{catEntry.language}</span>
                      <span className="flex items-center gap-0.5"><IconStar aria-hidden="true" className="w-4 h-4" />{catEntry.stars}</span>
                      {catEntry.github ? (
                        <a href={`https://github.com/${catEntry.github}`} target="_blank" rel="noopener noreferrer" className="text-danger hover:text-danger flex items-center gap-0.5">
                          <IconExternalLink aria-hidden="true" className="w-4 h-4" /> GitHub
                        </a>
                      ) : null}
                    </div>
                  </div>
                )}
                <MCPServerDetailPanel
                  server={drawerServer}
                  serverTools={serverToolCache[selectedServer] ?? toolsByServer[selectedServer] ?? []}
                  onConnect={() => handleConnect(selectedServer)}
                  onDisconnect={() => handleDisconnect(selectedServer)}
                  onRefresh={async () => { await mcpAPI.refresh(selectedServer); await fetchServers(); await onRefreshTools(); }}
                  onStatusChange={async () => { await fetchServers(); await onRefreshTools(); }}
                  onRemove={() => handleRemove(selectedServer)}
                  onSelectTool={selectMcpTool}
                />
              </div>
            </div>
          </PageDialog>
        );
      })()}
    </div>
  );
}

function MCPToolDetailPanel({ tool, onClose }: { tool: Tool; onClose: () => void }) {
  const { t } = useTranslation('tool');
  const [section, setSection] = useState<'info' | 'test'>('info');
  const [testParams, setTestParams] = useState('{}');
  const [testResult, setTestResult] = useState<any>(null);
  const [testing, setTesting] = useState(false);

  const handleTest = async () => {
    try {
      setTesting(true);
      setTestResult(null);
      const params = JSON.parse(testParams);
      const res = await toolAPI.test(tool.name, params);
      setTestResult({ success: true, data: res.data });
    } catch (err: any) {
      setTestResult({ success: false, error: err.response?.data?.detail ?? err.message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="fixed top-0 bottom-0 z-50 flex flex-col bg-surface shadow-overlay border-r border-line" style={{ right: DETAIL_DRAWER_WIDTH, width: TOOL_PANEL_WIDTH }} onClick={(e) => e.stopPropagation()}>
      <div className="flex-shrink-0 border-b border-line">
        <div className="flex items-center gap-3 px-6 py-4">
          <div className="w-8 h-8 bg-subtle rounded-control flex items-center justify-center flex-shrink-0">
            <IconTool aria-hidden="true" className="w-4 h-4 text-fg-secondary" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-title-section font-semibold text-fg font-mono truncate">{tool.name}</h2>
            {tool.source_name && <p className="text-xs text-fg-muted mt-0.5">{tool.source_name}</p>}
          </div>
          <button aria-label={pageActionLabel('close')} onClick={onClose} className="ui-btn-icon inline-flex items-center justify-center flex-shrink-0 p-1 rounded-control hover:bg-hover transition-colors">
            <IconX aria-hidden="true" className="w-5 h-5 text-fg-muted" />
          </button>
        </div>
        <div className="flex px-6">
          {(['info', 'test'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setSection(tab)}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${section === tab ? 'border-danger text-danger' : 'border-transparent text-fg-muted hover:text-fg-secondary hover:border-line-control'}`}
            >
              {tab === 'info' ? <><IconInfoCircle aria-hidden="true" className="w-4 h-4" />{t('detail.info')}</> : <><IconTestPipe aria-hidden="true" className="w-4 h-4" />{t('detail.test')}</>}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {section === 'info' ? (
          <div className="space-y-5">
            <div>
              <label className="block text-sm font-medium text-fg-secondary mb-1.5">{t('detail.tableDesc')}</label>
              <p className="text-sm text-fg-secondary ">{tool.description || t('detail.noDescription')}</p>
            </div>
            {tool.parameters && tool.parameters.length > 0 && (
              <div>
                <label className="block text-sm font-medium text-fg-secondary mb-2">
                  {t('detail.parameters')} <span className="text-fg-muted font-normal">({tool.parameters.length})</span>
                </label>
                <div className="rounded-control border border-line overflow-hidden">
                  <table className="min-w-full divide-y divide-line">
                    <thead className="bg-subtle">
                      <tr>
                        <th className="px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('detail.paramName')}</th>
                        <th className="px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('detail.tableType')}</th>
                        <th className="px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('detail.paramRequired')}</th>
                        <th className="px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('detail.tableDesc')}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line bg-surface">
                      {tool.parameters.map((param: any, idx: number) => (
                        <tr key={idx}>
                          <td className="px-4 py-2.5"><code className="text-xs font-mono text-fg bg-subtle px-1.5 py-0.5 rounded-tag">{param.name}</code></td>
                          <td className="px-4 py-2.5 text-xs text-fg-muted">{param.type}</td>
                          <td className="px-4 py-2.5">
                            {param.required ? <span className="text-xs text-danger font-medium">{t('detail.yes')}</span> : <span className="text-xs text-fg-muted">{t('detail.no')}</span>}
                          </td>
                          <td className="px-4 py-2.5 text-xs text-fg-secondary">{param.description}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-fg-secondary mb-2">{t('detail.testParamsJson')}</label>
              <textarea
                value={testParams}
                onChange={(e) => setTestParams(e.target.value)}
                placeholder='{"param": "value"}'
                rows={6}
                className="ui-textarea w-full px-4  border border-line-control rounded-control font-mono text-sm bg-subtle resize-none"
              />
            </div>
            <button
              onClick={handleTest}
              disabled={testing || !tool.enabled}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-action text-action-fg rounded-control hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed font-medium text-sm transition-colors"
            >
              {testing ? <><IconRefresh aria-hidden="true" className="w-4 h-4 animate-spin" />{t('detail.executing')}</> : <><IconPlayerPlay aria-hidden="true" className="w-4 h-4" />{t('detail.runTest')}</>}
            </button>
            {!tool.enabled && <p className="text-xs text-warning text-center">{t('detail.toolDisabledNoTest')}</p>}
            {testResult && (
              <div>
                <label className="block text-sm font-medium text-fg-secondary mb-2">{t('detail.testResults')}</label>
                <div className={`rounded-control border p-4 ${testResult.success ? 'bg-success-soft border-success' : 'bg-danger-soft border-danger'}`}>
                  <div className="flex items-center mb-2">
                    {testResult.success ? <IconCircleCheck aria-hidden="true" className="w-4 h-4 text-success mr-2" /> : <IconCircleX aria-hidden="true" className="w-4 h-4 text-danger mr-2" />}
                    <span className={`text-sm font-medium ${testResult.success ? 'text-success' : 'text-danger'}`}>
                      {testResult.success ? t('detail.testSuccess') : t('detail.testFailed')}
                    </span>
                  </div>
                  <pre className="text-xs overflow-auto max-h-48 whitespace-pre-wrap break-all text-fg-secondary bg-surface rounded-tag p-2">
                    {testResult.success ? JSON.stringify(testResult.data, null, 2) : testResult.error}
                  </pre>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
