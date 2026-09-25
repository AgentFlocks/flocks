import { useState, useEffect, useMemo } from 'react';
import {
  IconServer,
  IconRefresh,
  IconPower,
  IconPlugOff,
  IconTool,
  IconFileText,
  IconDownload,
  IconSearch,
  IconExternalLink,
  IconStar,
  IconShield,
  IconTag,
} from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { mcpAPI, MCPServer } from '@/api/mcp';
import type { MCPCatalogEntry, MCPCatalogCategory } from '@/types';
import { getCatalogDescription } from '@/utils/mcpCatalog';

type TabType = 'servers' | 'catalog';

const LANG_COLORS: Record<string, string> = {
  python: 'bg-subtle text-fg-secondary',
  typescript: 'bg-subtle text-fg-secondary',
  go: 'bg-subtle text-fg-secondary',
  rust: 'bg-subtle text-fg-secondary',
  java: 'bg-subtle text-fg-secondary',
  c: 'bg-subtle text-fg-secondary',
};

export default function MCPPage() {
  const { t, i18n } = useTranslation('mcp');
  const [tab, setTab] = useState<TabType>('servers');

  return (
    <div className="h-full flex flex-col">
      <PageHeader tone="data"
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<IconServer aria-hidden="true" className="w-8 h-8" />}
      />

      <div className="flex gap-1 mb-6 border-b border-line">
        <button
          onClick={() => setTab('servers')}
          className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            tab === 'servers'
              ? 'border-danger text-danger'
              : 'border-transparent text-fg-muted hover:text-fg-secondary'
          }`}
        >
          <div className="flex items-center gap-2">
            <IconServer aria-hidden="true" className="w-4 h-4" />
            {t('tabs.myServers')}
          </div>
        </button>
        <button
          onClick={() => setTab('catalog')}
          className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            tab === 'catalog'
              ? 'border-danger text-danger'
              : 'border-transparent text-fg-muted hover:text-fg-secondary'
          }`}
        >
          <div className="flex items-center gap-2">
            <IconDownload aria-hidden="true" className="w-4 h-4" />
            {t('tabs.catalog')}
          </div>
        </button>
      </div>

      {tab === 'servers' ? <ServersTab /> : <CatalogTab />}
    </div>
  );
}

/* ==================== Servers Tab ==================== */

function ServersTab() {
  const { t } = useTranslation('mcp');
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedServer, setSelectedServer] = useState<MCPServer | null>(null);

  useEffect(() => { fetchServers(); }, []);

  const fetchServers = async () => {
    try {
      setLoading(true);
      const response = await mcpAPI.list();
      const data = response.data;
      if (Array.isArray(data)) {
        setServers(data);
      } else if (data && typeof data === 'object') {
        const arr = Object.entries(data).map(([name, info]: [string, any]) => ({
          name,
          status: info.status === 'failed' ? 'error' : (info.status || 'disconnected'),
          url: info.url,
          tools: info.tools || [],
          resources: info.resources || [],
          error: info.error,
        }));
        setServers(arr);
      } else {
        setServers([]);
      }
    } catch (err: any) {
      setError(err.message);
      setServers([]);
    } finally {
      setLoading(false);
    }
  };

  const handleConnect = async (name: string) => {
    try { await mcpAPI.connect(name); fetchServers(); } catch (err: any) { alert(`${t('servers.connectFailed')}: ${err.message}`); }
  };

  const handleDisconnect = async (name: string) => {
    try { await mcpAPI.disconnect(name); fetchServers(); } catch (err: any) { alert(`${t('servers.disconnectFailed')}: ${err.message}`); }
  };

  const handleSelectServer = async (server: MCPServer) => {
    setSelectedServer(server);
  };

  if (loading) return <div className="flex items-center justify-center h-64"><LoadingSpinner delayMs={180} /></div>;
  if (error) return (
    <div className="flex items-center justify-center h-64">
      <div className="text-center">
        <p className="text-danger mb-4">{error}</p>
        <button onClick={fetchServers} className="px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover">Retry</button>
      </div>
    </div>
  );

  return (
    <>
      <div className="flex items-center justify-between mb-4">
        <div className="text-sm text-fg-secondary">{t('servers.count', { count: servers.length })}</div>
        <button onClick={fetchServers} className="flex items-center gap-2 px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover">
          <IconRefresh aria-hidden="true" className="w-4 h-4" /> {t('servers.refresh')}
        </button>
      </div>

      <div className="flex gap-6 flex-1 overflow-hidden">
        <div className="w-96 flex-col overflow-y-auto space-y-3">
          {servers.length === 0 ? (
            <EmptyState icon={<IconServer aria-hidden="true" className="w-8 h-8" />} title={t('servers.empty')} description={t('servers.emptyHint')} />
          ) : (
            servers.map((s) => (
              <div key={s.name} onClick={() => handleSelectServer(s)}
                className={`bg-surface rounded-control shadow-sm border-2 p-4 cursor-pointer transition-all ${selectedServer?.name === s.name ? 'border-danger ring-2 ring-focus' : 'border-line hover:border-line-control'}`}>
                <div className="flex items-start justify-between mb-2">
                  <h3 className="font-semibold text-fg">{s.name}</h3>
                  <span className={`px-2 py-1 rounded-pill text-xs font-medium ${s.status === 'connected' ? 'bg-success-soft text-success' : s.status === 'error' ? 'bg-danger-soft text-danger' : 'bg-subtle text-fg-secondary'}`}>
                    {s.status === 'connected' ? t('servers.connected') : s.status === 'error' ? t('servers.error') : t('servers.disconnected')}
                  </span>
                </div>
                {s.url && <p className="text-sm text-fg-secondary mb-3 font-mono">{s.url}</p>}
                <div className="flex items-center gap-4 text-sm text-fg-secondary mb-3">
                  <span>{s.tools.length} {t('servers.tools')}</span>
                  <span>{s.resources.length} {t('servers.resources')}</span>
                </div>
                <div className="flex items-center gap-2">
                  {s.status === 'connected' ? (
                    <button onClick={(e) => { e.stopPropagation(); handleDisconnect(s.name); }}
                      className="flex-1 flex items-center justify-center gap-2 px-3 py-2 border border-line-control text-fg-secondary rounded-control hover:bg-hover">
                      <IconPlugOff aria-hidden="true" className="w-4 h-4" /> {t('servers.disconnect')}
                    </button>
                  ) : (
                    <button onClick={(e) => { e.stopPropagation(); handleConnect(s.name); }}
                      className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover">
                      <IconPower aria-hidden="true" className="w-4 h-4" /> {t('servers.connect')}
                    </button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        {selectedServer && (
          <div className="flex-1 bg-surface rounded-control shadow-sm border border-line overflow-hidden">
            <div className="px-6 py-4 border-b border-line">
              <h2 className="text-xl font-semibold text-fg">{selectedServer.name}</h2>
              <p className="text-sm text-fg-secondary mt-1">{selectedServer.url}</p>
            </div>
            <div className="p-6 overflow-y-auto">
              <div className="mb-6">
                <div className="flex items-center gap-2 mb-3">
                  <IconTool aria-hidden="true" className="w-5 h-5 text-fg-secondary" />
                  <h3 className="text-title-card font-semibold text-fg">{t('servers.tools')} ({selectedServer.tools.length})</h3>
                </div>
                {selectedServer.tools.length === 0 ? <p className="text-sm text-fg-secondary">{t('servers.noTools')}</p> : (
                  <div className="grid grid-cols-2 gap-2">
                    {selectedServer.tools.map((tool) => <div key={tool} className="px-3 py-2 bg-subtle border border-line rounded-control text-sm">{tool}</div>)}
                  </div>
                )}
              </div>
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <IconFileText aria-hidden="true" className="w-5 h-5 text-fg-secondary" />
                  <h3 className="text-title-card font-semibold text-fg">{t('servers.resources')} ({selectedServer.resources.length})</h3>
                </div>
                {selectedServer.resources.length === 0 ? <p className="text-sm text-fg-secondary">{t('servers.noResources')}</p> : (
                  <div className="grid grid-cols-2 gap-2">
                    {selectedServer.resources.map((r) => <div key={r} className="px-3 py-2 bg-subtle border border-line rounded-control text-sm">{r}</div>)}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

/* ==================== Catalog Tab ==================== */

function CatalogTab() {
  const { t, i18n } = useTranslation('mcp');
  const [entries, setEntries] = useState<MCPCatalogEntry[]>([]);
  const [categories, setCategories] = useState<Record<string, MCPCatalogCategory>>({});
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [selectedEntry, setSelectedEntry] = useState<MCPCatalogEntry | null>(null);
  const [installing, setInstalling] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true);
        const [entriesRes, catsRes] = await Promise.all([
          mcpAPI.catalogList(),
          mcpAPI.catalogCategories(),
        ]);
        setEntries(entriesRes.data);
        setCategories(catsRes.data);
      } catch (err) {
        console.error('Failed to load catalog:', err);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  const filtered = useMemo(() => {
    let result = entries;
    if (selectedCategory !== 'all') {
      result = result.filter(e => e.category === selectedCategory);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(e =>
        e.name.toLowerCase().includes(q) ||
        e.description.toLowerCase().includes(q) ||
        (e.description_cn || '').toLowerCase().includes(q) ||
        e.id.toLowerCase().includes(q) ||
        e.tags.some(t => t.toLowerCase().includes(q))
      );
    }
    return result;
  }, [entries, selectedCategory, searchQuery]);

  const categoryCounts = useMemo(() => {
    const counts: Record<string, number> = { all: entries.length };
    for (const e of entries) {
      counts[e.category] = (counts[e.category] || 0) + 1;
    }
    return counts;
  }, [entries]);

  const handleInstall = async (entry: MCPCatalogEntry) => {
    try {
      setInstalling(entry.id);
      const res = await mcpAPI.catalogInstall(entry.id);
      const data = res.data;
      const installMessage = data.config?.enabled
        ? t('alert.mcpConfiguredEnabled', { name: entry.name })
        : t('alert.mcpConfiguredDisabled', { name: entry.name });
      if (data.requires_env && data.requires_env.length > 0) {
        const envNames = data.requires_env.map((e: any) => `${e.name}: ${e.description}`).join('\n');
        alert(`${installMessage}\n\n${t('catalog.requiresEnvVars')}:\n${envNames}\n\n${t('catalog.configureEnvHint')}`);
      } else {
        alert(installMessage);
      }
    } catch (err: any) {
      alert(`${t('catalog.installFailed')}: ${err.response?.data?.detail || err.message}`);
    } finally {
      setInstalling(null);
    }
  };

  if (loading) return <div className="flex items-center justify-center h-64"><LoadingSpinner delayMs={180} /></div>;

  return (
    <div className="flex gap-6 flex-1 overflow-hidden">
      {/* Left sidebar: categories */}
      <div className="w-56 flex-shrink-0 overflow-y-auto">
        <div className="space-y-1">
          <button
            onClick={() => setSelectedCategory('all')}
            className={`w-full text-left px-3 py-2 rounded-control text-sm transition-colors ${selectedCategory === 'all' ? 'bg-danger-soft text-danger font-medium' : 'text-fg-secondary hover:bg-hover'}`}
          >
            {t('catalog.all')} <span className="text-fg-muted ml-1">({categoryCounts.all || 0})</span>
          </button>
          {Object.entries(categories).map(([id, cat]) => (
            <button
              key={id}
              onClick={() => setSelectedCategory(id)}
              className={`w-full text-left px-3 py-2 rounded-control text-sm transition-colors ${selectedCategory === id ? 'bg-danger-soft text-danger font-medium' : 'text-fg-secondary hover:bg-hover'}`}
            >
              {t(`categories.${id}`, { defaultValue: cat.label })} <span className="text-fg-muted ml-1">({categoryCounts[id] || 0})</span>
            </button>
          ))}
        </div>
      </div>

      {/* Center: list */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Search */}
        <div className="relative mb-4">
          <IconSearch aria-hidden="true" className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-fg-muted" />
          <input
            type="text"
            placeholder={t('catalog.searchPlaceholder')}
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="ui-input w-full pl-10 pr-4  border border-line rounded-control text-sm focus:border-transparent"
          />
        </div>

        <div className="text-xs text-fg-muted mb-3">
          {t('catalog.toolCount', { count: filtered.length })}
          {selectedCategory !== 'all' && categories[selectedCategory] && (
            <span> · {t(`categories.${selectedCategory}`, { defaultValue: categories[selectedCategory].label })}</span>
          )}
        </div>

        {/* Grid */}
        <div className="flex-1 overflow-y-auto">
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
            {filtered.map(entry => (
              <div
                key={entry.id}
                onClick={() => setSelectedEntry(entry)}
                className={`bg-surface rounded-control border-2 p-4 cursor-pointer transition-all ${selectedEntry?.id === entry.id ? 'border-danger ring-2 ring-focus' : 'border-line hover:border-line-control'}`}
              >
                <div className="flex items-start justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold text-fg text-sm">{entry.name}</h3>
                    {entry.official && <span className="px-1.5 py-0.5 bg-warning-soft text-warning rounded-tag text-xs font-medium">{t('catalog.official')}</span>}
                  </div>
                  <div className="flex items-center gap-1 text-xs text-fg-muted">
                    <IconStar aria-hidden="true" className="w-4 h-4" />
                    {entry.stars}
                  </div>
                </div>
                <p className="text-xs text-fg-secondary mb-3 line-clamp-2">{getCatalogDescription(entry, i18n.language)}</p>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className={`px-2 py-0.5 rounded-tag text-xs font-medium ${LANG_COLORS[entry.language] || 'bg-subtle text-fg-secondary'}`}>
                      {entry.language}
                    </span>
                    {Object.values(entry.env_vars).some(v => v.secret) && (
                      <span className="flex items-center gap-0.5 text-xs text-warning">
                        <IconShield aria-hidden="true" className="w-4 h-4" /> {t('catalog.requiresSecret')}
                      </span>
                    )}
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleInstall(entry); }}
                    disabled={installing === entry.id}
                    className="flex items-center gap-1 px-3 py-1.5 bg-action text-action-fg rounded-control text-xs hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled"
                  >
                    <IconDownload aria-hidden="true" className="w-4 h-4" />
                    {installing === entry.id ? t('catalog.installing') : t('catalog.install')}
                  </button>
                </div>
              </div>
            ))}
          </div>
          {filtered.length === 0 && (
            <EmptyState icon={<IconSearch aria-hidden="true" className="w-8 h-8" />} title={t('catalog.noResults')} description={t('catalog.noResultsHint')} />
          )}
        </div>
      </div>

      {/* Right: detail panel */}
      {selectedEntry && (
        <div className="w-80 flex-shrink-0 bg-surface rounded-control border border-line overflow-hidden flex flex-col">
          <div className="px-5 py-4 border-b border-line">
            <div className="flex items-center gap-2">
              <h2 className="text-title-section font-semibold text-fg">{selectedEntry.name}</h2>
              {selectedEntry.official && <span className="px-1.5 py-0.5 bg-warning-soft text-warning rounded-tag text-xs font-medium">{t('catalog.official')}</span>}
            </div>
            <p className="text-sm text-fg-secondary mt-1">{getCatalogDescription(selectedEntry, i18n.language)}</p>
          </div>

          <div className="flex-1 overflow-y-auto p-5 space-y-5">
            {/* Meta */}
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <span className="text-fg-muted text-xs">{t('catalog.language')}</span>
                <p className="font-medium text-fg-secondary">{selectedEntry.language}</p>
              </div>
              <div>
                <span className="text-fg-muted text-xs">{t('catalog.license')}</span>
                <p className="font-medium text-fg-secondary">{selectedEntry.license}</p>
              </div>
              <div>
                <span className="text-fg-muted text-xs">{t('catalog.transport')}</span>
                <p className="font-medium text-fg-secondary">{selectedEntry.transport}</p>
              </div>
              <div>
                <span className="text-fg-muted text-xs">Stars</span>
                <p className="font-medium text-fg-secondary">{selectedEntry.stars}</p>
              </div>
            </div>

            {/* Tags */}
            <div>
                <div className="flex items-center gap-1 text-xs text-fg-muted mb-2">
                <IconTag aria-hidden="true" className="w-4 h-4" /> {t('catalog.tags')}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {selectedEntry.tags.map(tag => (
                  <span key={tag} className="px-2 py-0.5 bg-subtle text-fg-secondary rounded-tag text-xs">{tag}</span>
                ))}
              </div>
            </div>

            {/* Env Vars */}
            {Object.keys(selectedEntry.env_vars).length > 0 && (
              <div>
                <div className="flex items-center gap-1 text-xs text-fg-muted mb-2">
                  <IconShield aria-hidden="true" className="w-4 h-4" /> {t('catalog.envVars')}
                </div>
                <div className="space-y-2">
                  {Object.entries(selectedEntry.env_vars).map(([key, spec]) => (
                    <div key={key} className="text-xs">
                      <div className="flex items-center gap-1">
                        <code className="font-mono text-fg bg-subtle px-1.5 py-0.5 rounded-tag">{key}</code>
                        {spec.required && <span className="text-danger">*</span>}
                        {spec.secret && <IconShield aria-hidden="true" className="w-4 h-4 text-warning" />}
                      </div>
                      <p className="text-fg-muted mt-0.5">{spec.description}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* System deps */}
            {selectedEntry.system_deps.length > 0 && (
              <div>
                <div className="text-xs text-fg-muted mb-2">{t('catalog.systemDeps')}</div>
                <div className="flex flex-wrap gap-1.5">
                  {selectedEntry.system_deps.map(dep => (
                    <span key={dep} className="px-2 py-0.5 bg-warning-soft text-warning rounded-tag text-xs">{dep}</span>
                  ))}
                </div>
              </div>
            )}

            {/* Install command */}
            {selectedEntry.install.local_command && (
              <div>
                <div className="text-xs text-fg-muted mb-2">{t('catalog.startCommand')}</div>
                <code className="block text-xs bg-subtle text-success rounded-control p-3 font-mono overflow-x-auto">
                  {selectedEntry.install.local_command.join(' ')}
                </code>
              </div>
            )}

            {/* GitHub link */}
            <a
              href={`https://github.com/${selectedEntry.github}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 text-sm text-danger hover:text-danger"
            >
              <IconExternalLink aria-hidden="true" className="w-4 h-4" />
              {t('catalog.githubRepo')}
            </a>
          </div>

          {/* Install button */}
          <div className="p-4 border-t border-line">
            <button
              onClick={() => handleInstall(selectedEntry)}
              disabled={installing === selectedEntry.id}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-action text-action-fg rounded-control hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled font-medium"
            >
              <IconDownload aria-hidden="true" className="w-4 h-4" />
              {installing === selectedEntry.id ? t('catalog.installing') : t('catalog.addToMyServers')}
            </button>
          </div>
        </div>
      )}
    </div>
  );

}
