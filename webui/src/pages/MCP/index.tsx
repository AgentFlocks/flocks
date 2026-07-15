import { useState, useEffect, useMemo } from 'react';
import {
  Server, RefreshCw, Power, PowerOff, Wrench, FileText,
  Download, Search, ExternalLink, Star, Shield, Tag,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { mcpAPI, MCPServer } from '@/api/mcp';
import type { MCPCatalogEntry, MCPCatalogCategory } from '@/types';
import { getCatalogDescription } from '@/utils/mcpCatalog';

type TabType = 'servers' | 'catalog';

const LANG_COLORS: Record<string, string> = {
  python: 'bg-red-100 text-red-700',
  typescript: 'bg-sky-100 text-sky-700',
  go: 'bg-cyan-100 text-cyan-700',
  rust: 'bg-orange-100 text-orange-700',
  java: 'bg-red-100 text-red-700',
  c: 'bg-gray-100 text-gray-700',
};

export default function MCPPage() {
  const { t, i18n } = useTranslation('mcp');
  const [tab, setTab] = useState<TabType>('servers');

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<Server className="w-8 h-8" />}
      />

      <div className="flex gap-1 mb-6 border-b border-gray-200">
        <button
          onClick={() => setTab('servers')}
          className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            tab === 'servers'
              ? 'border-red-600 text-red-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          }`}
        >
          <div className="flex items-center gap-2">
            <Server className="w-4 h-4" />
            {t('tabs.myServers')}
          </div>
        </button>
        <button
          onClick={() => setTab('catalog')}
          className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
            tab === 'catalog'
              ? 'border-red-600 text-red-600'
              : 'border-transparent text-gray-500 hover:text-gray-700'
          }`}
        >
          <div className="flex items-center gap-2">
            <Download className="w-4 h-4" />
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

  if (loading) return <div className="flex items-center justify-center h-64"><LoadingSpinner /></div>;
  if (error) return (
    <div className="flex items-center justify-center h-64">
      <div className="text-center">
        <p className="text-red-600 mb-4">{error}</p>
        <button onClick={fetchServers} className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700">Retry</button>
      </div>
    </div>
  );

  return (
    <>
      <div className="flex items-center justify-between mb-4">
        <div className="text-sm text-gray-600">{t('servers.count', { count: servers.length })}</div>
        <button onClick={fetchServers} className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700">
          <RefreshCw className="w-4 h-4" /> {t('servers.refresh')}
        </button>
      </div>

      <div className="flex gap-6 flex-1 overflow-hidden">
        <div className="w-96 flex-col overflow-y-auto space-y-3">
          {servers.length === 0 ? (
            <EmptyState icon={<Server className="w-12 h-12" />} title={t('servers.empty')} description={t('servers.emptyHint')} />
          ) : (
            servers.map((s) => (
              <div key={s.name} onClick={() => handleSelectServer(s)}
                className={`bg-white rounded-lg shadow-sm border-2 p-4 cursor-pointer transition-all ${selectedServer?.name === s.name ? 'border-red-500 ring-2 ring-red-200' : 'border-gray-200 hover:border-gray-300'}`}>
                <div className="flex items-start justify-between mb-2">
                  <h3 className="font-semibold text-gray-900">{s.name}</h3>
                  <span className={`px-2 py-1 rounded-full text-xs font-medium ${s.status === 'connected' ? 'bg-green-100 text-green-800' : s.status === 'error' ? 'bg-red-100 text-red-800' : 'bg-gray-100 text-gray-600'}`}>
                    {s.status === 'connected' ? t('servers.connected') : s.status === 'error' ? t('servers.error') : t('servers.disconnected')}
                  </span>
                </div>
                {s.url && <p className="text-sm text-gray-600 mb-3 font-mono">{s.url}</p>}
                <div className="flex items-center gap-4 text-sm text-gray-600 mb-3">
                  <span>{s.tools.length} {t('servers.tools')}</span>
                  <span>{s.resources.length} {t('servers.resources')}</span>
                </div>
                <div className="flex items-center gap-2">
                  {s.status === 'connected' ? (
                    <button onClick={(e) => { e.stopPropagation(); handleDisconnect(s.name); }}
                      className="flex-1 flex items-center justify-center gap-2 px-3 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50">
                      <PowerOff className="w-4 h-4" /> {t('servers.disconnect')}
                    </button>
                  ) : (
                    <button onClick={(e) => { e.stopPropagation(); handleConnect(s.name); }}
                      className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700">
                      <Power className="w-4 h-4" /> {t('servers.connect')}
                    </button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        {selectedServer && (
          <div className="flex-1 bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-200">
              <h2 className="text-xl font-semibold text-gray-900">{selectedServer.name}</h2>
              <p className="text-sm text-gray-600 mt-1">{selectedServer.url}</p>
            </div>
            <div className="p-6 overflow-y-auto">
              <div className="mb-6">
                <div className="flex items-center gap-2 mb-3">
                  <Wrench className="w-5 h-5 text-gray-700" />
                  <h3 className="text-lg font-semibold text-gray-900">{t('servers.tools')} ({selectedServer.tools.length})</h3>
                </div>
                {selectedServer.tools.length === 0 ? <p className="text-sm text-gray-600">{t('servers.noTools')}</p> : (
                  <div className="grid grid-cols-2 gap-2">
                    {selectedServer.tools.map((tool) => <div key={tool} className="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm">{tool}</div>)}
                  </div>
                )}
              </div>
              <div>
                <div className="flex items-center gap-2 mb-3">
                  <FileText className="w-5 h-5 text-gray-700" />
                  <h3 className="text-lg font-semibold text-gray-900">{t('servers.resources')} ({selectedServer.resources.length})</h3>
                </div>
                {selectedServer.resources.length === 0 ? <p className="text-sm text-gray-600">{t('servers.noResources')}</p> : (
                  <div className="grid grid-cols-2 gap-2">
                    {selectedServer.resources.map((r) => <div key={r} className="px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg text-sm">{r}</div>)}
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

  if (loading) return <div className="flex items-center justify-center h-64"><LoadingSpinner /></div>;

  return (
    <div className="flex gap-6 flex-1 overflow-hidden">
      {/* Left sidebar: categories */}
      <div className="w-56 flex-shrink-0 overflow-y-auto">
        <div className="space-y-1">
          <button
            onClick={() => setSelectedCategory('all')}
            className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${selectedCategory === 'all' ? 'bg-red-50 text-red-700 font-medium' : 'text-gray-600 hover:bg-gray-50'}`}
          >
            {t('catalog.all')} <span className="text-gray-400 ml-1">({categoryCounts.all || 0})</span>
          </button>
          {Object.entries(categories).map(([id, cat]) => (
            <button
              key={id}
              onClick={() => setSelectedCategory(id)}
              className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${selectedCategory === id ? 'bg-red-50 text-red-700 font-medium' : 'text-gray-600 hover:bg-gray-50'}`}
            >
              {t(`categories.${id}`, { defaultValue: cat.label })} <span className="text-gray-400 ml-1">({categoryCounts[id] || 0})</span>
            </button>
          ))}
        </div>
      </div>

      {/* Center: list */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Search */}
        <div className="relative mb-4">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder={t('catalog.searchPlaceholder')}
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2.5 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-red-500 focus:border-transparent"
          />
        </div>

        <div className="text-xs text-gray-500 mb-3">
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
                className={`bg-white rounded-lg border-2 p-4 cursor-pointer transition-all hover:shadow-md ${selectedEntry?.id === entry.id ? 'border-red-500 ring-2 ring-red-200' : 'border-gray-200 hover:border-gray-300'}`}
              >
                <div className="flex items-start justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold text-gray-900 text-sm">{entry.name}</h3>
                    {entry.official && <span className="px-1.5 py-0.5 bg-yellow-100 text-yellow-700 rounded text-[10px] font-medium">{t('catalog.official')}</span>}
                  </div>
                  <div className="flex items-center gap-1 text-xs text-gray-400">
                    <Star className="w-3 h-3" />
                    {entry.stars}
                  </div>
                </div>
                <p className="text-xs text-gray-600 mb-3 line-clamp-2">{getCatalogDescription(entry, i18n.language)}</p>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${LANG_COLORS[entry.language] || 'bg-gray-100 text-gray-600'}`}>
                      {entry.language}
                    </span>
                    {Object.values(entry.env_vars).some(v => v.secret) && (
                      <span className="flex items-center gap-0.5 text-[10px] text-amber-600">
                        <Shield className="w-3 h-3" /> {t('catalog.requiresSecret')}
                      </span>
                    )}
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); handleInstall(entry); }}
                    disabled={installing === entry.id}
                    className="flex items-center gap-1 px-3 py-1.5 bg-red-600 text-white rounded-lg text-xs hover:bg-red-700 disabled:opacity-50"
                  >
                    <Download className="w-3 h-3" />
                    {installing === entry.id ? t('catalog.installing') : t('catalog.install')}
                  </button>
                </div>
              </div>
            ))}
          </div>
          {filtered.length === 0 && (
            <EmptyState icon={<Search className="w-12 h-12" />} title={t('catalog.noResults')} description={t('catalog.noResultsHint')} />
          )}
        </div>
      </div>

      {/* Right: detail panel */}
      {selectedEntry && (
        <div className="w-80 flex-shrink-0 bg-white rounded-lg border border-gray-200 overflow-hidden flex flex-col">
          <div className="px-5 py-4 border-b border-gray-200">
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-semibold text-gray-900">{selectedEntry.name}</h2>
              {selectedEntry.official && <span className="px-1.5 py-0.5 bg-yellow-100 text-yellow-700 rounded text-[10px] font-medium">{t('catalog.official')}</span>}
            </div>
            <p className="text-sm text-gray-600 mt-1">{getCatalogDescription(selectedEntry, i18n.language)}</p>
          </div>

          <div className="flex-1 overflow-y-auto p-5 space-y-5">
            {/* Meta */}
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <span className="text-gray-400 text-xs">{t('catalog.language')}</span>
                <p className="font-medium text-gray-700">{selectedEntry.language}</p>
              </div>
              <div>
                <span className="text-gray-400 text-xs">{t('catalog.license')}</span>
                <p className="font-medium text-gray-700">{selectedEntry.license}</p>
              </div>
              <div>
                <span className="text-gray-400 text-xs">{t('catalog.transport')}</span>
                <p className="font-medium text-gray-700">{selectedEntry.transport}</p>
              </div>
              <div>
                <span className="text-gray-400 text-xs">Stars</span>
                <p className="font-medium text-gray-700">{selectedEntry.stars}</p>
              </div>
            </div>

            {/* Tags */}
            <div>
                <div className="flex items-center gap-1 text-xs text-gray-400 mb-2">
                <Tag className="w-3 h-3" /> {t('catalog.tags')}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {selectedEntry.tags.map(tag => (
                  <span key={tag} className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-xs">{tag}</span>
                ))}
              </div>
            </div>

            {/* Env Vars */}
            {Object.keys(selectedEntry.env_vars).length > 0 && (
              <div>
                <div className="flex items-center gap-1 text-xs text-gray-400 mb-2">
                  <Shield className="w-3 h-3" /> {t('catalog.envVars')}
                </div>
                <div className="space-y-2">
                  {Object.entries(selectedEntry.env_vars).map(([key, spec]) => (
                    <div key={key} className="text-xs">
                      <div className="flex items-center gap-1">
                        <code className="font-mono text-gray-800 bg-gray-50 px-1.5 py-0.5 rounded">{key}</code>
                        {spec.required && <span className="text-red-500">*</span>}
                        {spec.secret && <Shield className="w-3 h-3 text-amber-500" />}
                      </div>
                      <p className="text-gray-500 mt-0.5">{spec.description}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* System deps */}
            {selectedEntry.system_deps.length > 0 && (
              <div>
                <div className="text-xs text-gray-400 mb-2">{t('catalog.systemDeps')}</div>
                <div className="flex flex-wrap gap-1.5">
                  {selectedEntry.system_deps.map(dep => (
                    <span key={dep} className="px-2 py-0.5 bg-orange-50 text-orange-600 rounded text-xs">{dep}</span>
                  ))}
                </div>
              </div>
            )}

            {/* Install command */}
            {selectedEntry.install.local_command && (
              <div>
                <div className="text-xs text-gray-400 mb-2">{t('catalog.startCommand')}</div>
                <code className="block text-xs bg-gray-900 text-green-400 rounded-lg p-3 font-mono overflow-x-auto">
                  {selectedEntry.install.local_command.join(' ')}
                </code>
              </div>
            )}

            {/* GitHub link */}
            <a
              href={`https://github.com/${selectedEntry.github}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 text-sm text-red-600 hover:text-red-700"
            >
              <ExternalLink className="w-4 h-4" />
              {t('catalog.githubRepo')}
            </a>
          </div>

          {/* Install button */}
          <div className="p-4 border-t border-gray-200">
            <button
              onClick={() => handleInstall(selectedEntry)}
              disabled={installing === selectedEntry.id}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50 font-medium"
            >
              <Download className="w-4 h-4" />
              {installing === selectedEntry.id ? t('catalog.installing') : t('catalog.addToMyServers')}
            </button>
          </div>
        </div>
      )}
    </div>
  );

}
