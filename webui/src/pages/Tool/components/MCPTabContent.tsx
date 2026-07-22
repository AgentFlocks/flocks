import { useState, useMemo, useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Server, Database, Wrench, FileText, Clock, Settings, PowerOff, RefreshCw, X,
  Download, ExternalLink, Star, TestTube, Play, Info, CheckCircle, XCircle, AlertTriangle, Power,
} from 'lucide-react';
import { mcpAPI } from '@/api/mcp';
import { listAllToolPages, toolAPI } from '@/api/tool';
import type { Tool } from '@/api/tool';
import type { MCPCatalogCategory, MCPCatalogEntry, MCPServer } from '@/types';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { getCatalogDescription, getMetadataDescription } from '@/utils/mcpCatalog';
import { MCPServerDetailPanel } from './ServiceDetailPanel';
import { SERVICE_TAB_GRID_COLS } from './gridLayout';

const DETAIL_DRAWER_WIDTH = 560;
const TOOL_PANEL_WIDTH = 720;
const LANG_COLORS: Record<string, string> = {
  python: 'bg-red-100 text-red-700',
  typescript: 'bg-sky-100 text-sky-700',
  go: 'bg-cyan-100 text-cyan-700',
  rust: 'bg-orange-100 text-orange-700',
  java: 'bg-red-100 text-red-700',
  c: 'bg-gray-100 text-gray-700',
};
const INSTALL_BUTTON_CLASS = 'flex-1 flex items-center justify-center gap-1 py-1 px-2 bg-green-600 text-white rounded-lg text-xs font-medium hover:bg-green-700 disabled:opacity-50 transition-colors';

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
          className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${selectedCategory === 'all' ? 'bg-slate-100 text-slate-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
        >
          {t('catalog.all')}
        </button>
        {Object.entries(catalogCategories).map(([id, cat]) =>
          catalogCategoryCounts[id] ? (
            <button
              key={id}
              onClick={() => setSelectedCategory(id)}
              className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${selectedCategory === id ? 'bg-slate-100 text-slate-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
            >
              {cat.label} ({catalogCategoryCounts[id]})
            </button>
          ) : null
        )}
      </div>

      {serversLoading && catalogLoading ? (
        <div className="flex min-h-[360px] items-center justify-center rounded-lg border border-gray-200 bg-white">
          <LoadingSpinner delayMs={180} />
        </div>
      ) : unifiedCards.length === 0 ? (
        <EmptyState icon={<Server className="w-16 h-16" />} title={t('mcp.noServers')} description={t('mcp.noServersDesc')} />
      ) : (
        <div className="bg-white border border-gray-200 rounded-lg overflow-hidden divide-y divide-gray-100">
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
              ? 'bg-green-100 text-green-700'
              : isError
              ? 'bg-red-100 text-red-700'
              : 'bg-gray-100 text-gray-500';

            return (
              <div
                key={id}
                className={`grid items-center gap-3 px-4 py-3 transition-colors ${isSelected ? 'bg-red-50' : 'hover:bg-gray-50'}`}
                style={{ gridTemplateColumns: SERVICE_TAB_GRID_COLS }}
              >
                {/* Icon */}
                <div className={`w-8 h-8 flex items-center justify-center rounded-lg ${isActive ? 'bg-green-50' : isError ? 'bg-red-50' : 'bg-gray-50'}`}>
                  <Server className={`w-4 h-4 ${isActive ? 'text-green-600' : isError ? 'text-red-500' : 'text-gray-400'}`} />
                </div>

                {/* Name + description.
                    Whole block is the click target — mirrors the Hub catalog
                    pattern (``Hub/index.tsx`` HubTable row) so the description
                    line is just as clickable as the name itself. */}
                <button
                  type="button"
                  onClick={() => setSelectedCard(isSelected ? null : id)}
                  className="min-w-0 text-left group/name focus:outline-none"
                >
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm font-semibold text-gray-900 truncate transition-colors group-hover/name:text-slate-700 group-focus-visible/name:underline">{displayName}</span>
                    {entry && PRIORITY_IDS.has(entry.id) && (
                      <span className="px-1.5 py-0.5 bg-amber-50 text-amber-700 text-[10px] font-medium rounded border border-amber-200 shrink-0">{t('mcp.recommended')}</span>
                    )}
                  </div>
                  <p className="text-xs text-gray-500 truncate mt-0.5">{rowDescription}</p>
                </button>

                {/* Type column */}
                <div className="text-center">
                  <span className="px-1.5 py-0.5 bg-slate-100 text-slate-600 text-[10px] font-medium rounded">MCP</span>
                </div>

                {/* Status column */}
                <div className="text-center">
                  <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded ${statusClass}`}>
                    {statusLabel}
                  </span>
                </div>

                {/* Stats column */}
                <div className="flex items-center justify-end gap-3 text-xs text-gray-400">
                  <span className="flex items-center gap-1"><Wrench className="w-3 h-3" />{server.tools_count ?? serverTools.length}</span>
                  {server.connected_at ? (
                    <span className="flex items-center gap-1"><Clock className="w-3 h-3" />{formatDuration(server.connected_at, t)}</span>
                  ) : entry ? (
                    <span className="flex items-center gap-1"><Star className="w-3 h-3" />{entry.stars}</span>
                  ) : null}
                </div>

                {/* Actions column */}
                <div className="flex items-center justify-end gap-1.5">
                  {isActive ? (
                    <>
                      <button
                        onClick={() => setSelectedCard(isSelected ? null : id)}
                        className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors ${isSelected ? 'border-red-300 text-red-700 bg-red-50' : 'border-gray-200 text-gray-600 hover:bg-gray-100'}`}
                      >
                        <Settings className="w-3 h-3" />{t('mcp.manage')}
                      </button>
                      <button onClick={(e) => handleDisconnect(id, e)} className="p-1.5 rounded-lg border border-red-200 text-red-400 hover:bg-red-50 transition-colors" title={t('mcp.disconnectTitle')}>
                        <PowerOff className="w-3.5 h-3.5" />
                      </button>
                    </>
                  ) : isDisabled ? (
                    <>
                      <button
                        onClick={(e) => handleToggleEnabled(id, true, e)}
                        disabled={installing === id}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-green-600 text-white text-xs font-medium hover:bg-green-700 disabled:opacity-50 transition-colors"
                      >
                        <Power className="w-3 h-3" />{installing === id ? t('mcp.configuring') : t('detail.enableServer')}
                      </button>
                      <button
                        onClick={() => setSelectedCard(isSelected ? null : id)}
                        className="p-1.5 rounded-lg border border-gray-200 text-gray-400 hover:bg-gray-100 transition-colors"
                        title={t('mcp.manage')}
                      >
                        <Settings className="w-3.5 h-3.5" />
                      </button>
                    </>
                  ) : configured ? (
                    <>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleConnect(id, e); }}
                        disabled={installing === id}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-green-600 text-white text-xs font-medium hover:bg-green-700 disabled:opacity-50 transition-colors"
                      >
                        <Power className="w-3 h-3" />{installing === id ? t('mcp.configuring') : t('mcp.reconnect')}
                      </button>
                      <button
                        onClick={() => setSelectedCard(isSelected ? null : id)}
                        className="p-1.5 rounded-lg border border-gray-200 text-gray-400 hover:bg-gray-100 transition-colors"
                        title={t('mcp.manage')}
                      >
                        <Settings className="w-3.5 h-3.5" />
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
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-green-600 text-white text-xs font-medium hover:bg-green-700 disabled:opacity-50 transition-colors"
                      >
                        <Download className="w-3 h-3" />{installing === id ? t('mcp.configuring') : t('button.install')}
                      </button>
                      {entry?.github ? (
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
          <div className="fixed inset-0 bg-black/40 z-40" onClick={() => setSetupEntry(null)} />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div
              className="w-full max-w-lg rounded-2xl bg-white shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="border-b border-gray-200 px-6 py-4">
                <h3 className="text-lg font-semibold text-gray-900">{setupEntry.name}</h3>
                <p className="mt-1 text-sm text-gray-500">{t('credentials.configNote')}</p>
              </div>
              <div className="max-h-[70vh] space-y-4 overflow-y-auto px-6 py-4">
                {Object.entries(setupEntry.env_vars || {}).map(([key, spec]) => {
                  const value = spec.secret ? setupCredentials[key] || '' : setupEnvOverrides[key] || '';
                  return (
                    <div key={key}>
                      <label className="mb-1 block text-sm font-medium text-gray-700">
                        {key}{spec.required ? <span className="text-red-500"> *</span> : null}
                      </label>
                      <p className="mb-1.5 text-xs text-gray-500">{spec.description || key}</p>
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
                        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-red-500"
                      />
                    </div>
                  );
                })}
              </div>
              <div className="flex justify-end gap-3 border-t border-gray-200 px-6 py-4">
                <button
                  onClick={() => setSetupEntry(null)}
                  className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 transition-colors hover:bg-gray-50"
                >
                  {t('button.cancel')}
                </button>
                <button
                  onClick={handleSetupSubmit}
                  disabled={installing === setupEntry.id}
                  className="rounded-lg bg-green-600 px-4 py-2 text-sm text-white transition-colors hover:bg-green-700 disabled:opacity-50"
                >
                  {installing === setupEntry.id ? t('mcp.configuring') : t('button.confirmConfig')}
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {selectedServer && (selectedServerData || selectedServerObj) && (() => {
        const drawerServer = selectedServerData || selectedServerObj!;
        const catEntry = catalogEntries.find((entry) => entry.id === selectedServer);
        const displayName = catEntry?.name || drawerServer.name;
        return (
          <>
            <div className="fixed inset-0 bg-black/40 z-40" onClick={() => { setSelectedServer(null); setSelectedServerData(null); setSelectedToolFromMCP(null); }} />

            {selectedToolFromMCP && (
              <MCPToolDetailPanel tool={selectedToolFromMCP} onClose={() => setSelectedToolFromMCP(null)} />
            )}

            <div className="fixed right-0 top-0 bottom-0 z-50 flex flex-col w-full bg-white shadow-2xl" style={{ maxWidth: DETAIL_DRAWER_WIDTH }} onClick={(e) => e.stopPropagation()}>
              <div className="flex-shrink-0 border-b border-gray-200">
                <div className="flex items-center gap-3 px-6 py-4">
                  <div className={`w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0 ${drawerServer.status === 'connected' ? 'bg-green-50' : drawerServer.status === 'error' || drawerServer.status === 'failed' ? 'bg-red-50' : 'bg-gray-50'}`}>
                    <Database className={`w-5 h-5 ${drawerServer.status === 'connected' ? 'text-green-600' : drawerServer.status === 'error' || drawerServer.status === 'failed' ? 'text-red-600' : 'text-gray-400'}`} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h2 className="text-lg font-semibold text-gray-900">{displayName}</h2>
                    <p className="text-sm text-gray-500">{t('mcp.serverConfig')}</p>
                  </div>
                  <button onClick={() => { setSelectedServer(null); setSelectedServerData(null); setSelectedToolFromMCP(null); }} className="text-gray-400 hover:text-gray-600 p-2 rounded-lg hover:bg-gray-100 flex-shrink-0">
                    <X className="w-5 h-5" />
                  </button>
                </div>
              </div>
              <div className="flex-1 overflow-y-auto">
                {catEntry && (
                  <div className="px-6 py-4 bg-gray-50 border-b border-gray-200">
                    <p className="text-sm text-gray-600">{getCatalogDescription(catEntry, i18n.language)}</p>
                    <div className="flex items-center gap-3 mt-2 text-xs text-gray-500">
                      <span className={`px-1.5 py-0.5 rounded font-medium ${LANG_COLORS[catEntry.language] || 'bg-gray-100 text-gray-600'}`}>{catEntry.language}</span>
                      <span className="flex items-center gap-0.5"><Star className="w-3 h-3" />{catEntry.stars}</span>
                      {catEntry.github ? (
                        <a href={`https://github.com/${catEntry.github}`} target="_blank" rel="noopener noreferrer" className="text-red-600 hover:text-red-800 flex items-center gap-0.5">
                          <ExternalLink className="w-3 h-3" /> GitHub
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
          </>
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
    <div className="fixed top-0 bottom-0 z-50 flex flex-col bg-white shadow-2xl border-r border-gray-200" style={{ right: DETAIL_DRAWER_WIDTH, width: TOOL_PANEL_WIDTH }} onClick={(e) => e.stopPropagation()}>
      <div className="flex-shrink-0 border-b border-gray-200">
        <div className="flex items-center gap-3 px-6 py-4">
          <div className="w-8 h-8 bg-gray-100 rounded-lg flex items-center justify-center flex-shrink-0">
            <Wrench className="w-4 h-4 text-gray-600" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-semibold text-gray-900 font-mono truncate">{tool.name}</h2>
            {tool.source_name && <p className="text-xs text-gray-500 mt-0.5">{tool.source_name}</p>}
          </div>
          <button onClick={onClose} className="flex-shrink-0 p-1 rounded hover:bg-gray-100 transition-colors">
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>
        <div className="flex px-6">
          {(['info', 'test'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setSection(tab)}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${section === tab ? 'border-red-600 text-red-600' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'}`}
            >
              {tab === 'info' ? <><Info className="w-3.5 h-3.5" />{t('detail.info')}</> : <><TestTube className="w-3.5 h-3.5" />{t('detail.test')}</>}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {section === 'info' ? (
          <div className="space-y-5">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('detail.tableDesc')}</label>
              <p className="text-sm text-gray-600 leading-relaxed">{tool.description || t('detail.noDescription')}</p>
            </div>
            {tool.parameters && tool.parameters.length > 0 && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">
                  {t('detail.parameters')} <span className="text-gray-400 font-normal">({tool.parameters.length})</span>
                </label>
                <div className="rounded-lg border border-gray-200 overflow-hidden">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">{t('detail.paramName')}</th>
                        <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">{t('detail.tableType')}</th>
                        <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">{t('detail.paramRequired')}</th>
                        <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">{t('detail.tableDesc')}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100 bg-white">
                      {tool.parameters.map((param: any, idx: number) => (
                        <tr key={idx}>
                          <td className="px-4 py-2.5"><code className="text-xs font-mono text-gray-900 bg-gray-100 px-1.5 py-0.5 rounded">{param.name}</code></td>
                          <td className="px-4 py-2.5 text-xs text-gray-500">{param.type}</td>
                          <td className="px-4 py-2.5">
                            {param.required ? <span className="text-xs text-red-600 font-medium">{t('detail.yes')}</span> : <span className="text-xs text-gray-400">{t('detail.no')}</span>}
                          </td>
                          <td className="px-4 py-2.5 text-xs text-gray-600">{param.description}</td>
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
              <label className="block text-sm font-medium text-gray-700 mb-2">{t('detail.testParamsJson')}</label>
              <textarea
                value={testParams}
                onChange={(e) => setTestParams(e.target.value)}
                placeholder='{"param": "value"}'
                rows={6}
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 font-mono text-sm bg-gray-50 resize-none"
              />
            </div>
            <button
              onClick={handleTest}
              disabled={testing || !tool.enabled}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed font-medium text-sm transition-colors"
            >
              {testing ? <><RefreshCw className="w-4 h-4 animate-spin" />{t('detail.executing')}</> : <><Play className="w-4 h-4" />{t('detail.runTest')}</>}
            </button>
            {!tool.enabled && <p className="text-xs text-amber-600 text-center">{t('detail.toolDisabledNoTest')}</p>}
            {testResult && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">{t('detail.testResults')}</label>
                <div className={`rounded-lg border p-4 ${testResult.success ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
                  <div className="flex items-center mb-2">
                    {testResult.success ? <CheckCircle className="w-4 h-4 text-green-600 mr-2" /> : <XCircle className="w-4 h-4 text-red-600 mr-2" />}
                    <span className={`text-sm font-medium ${testResult.success ? 'text-green-800' : 'text-red-800'}`}>
                      {testResult.success ? t('detail.testSuccess') : t('detail.testFailed')}
                    </span>
                  </div>
                  <pre className="text-xs overflow-auto max-h-48 whitespace-pre-wrap break-all text-gray-700 bg-white/60 rounded p-2">
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
