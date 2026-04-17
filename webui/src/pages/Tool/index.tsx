import { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Wrench,
  Search,
  RefreshCw,
  TestTube,
  Grid,
  Database,
  Cloud,
  X,
  CheckCircle,
  XCircle,
  ChevronLeft,
  ChevronRight,
  AlertTriangle,
  Play,
  Info,
  Power,
  PowerOff,
  FileText,
  ChevronDown,
  ChevronUp,
  Server,
  ArrowUp,
  ArrowDown,
  Filter,
  Settings,
  Activity,
  Clock,
  Shield,
  Zap,
  Plus,
  Code,
  Download,
  Star,
  ExternalLink,
  Tag,
  Trash2,
  Eye,
  EyeOff,
  Save,
} from 'lucide-react';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useTools } from '@/hooks/useTools';
import { toolAPI, Tool, ToolSource } from '@/api/tool';
import { mcpAPI, MCPServer } from '@/api/mcp';
import { providerAPI } from '@/api/provider';
import client from '@/api/client';
import type { MCPServerDetail, MCPCredentials, MCPCredentialInput, MCPCatalogEntry, MCPCatalogCategory } from '@/types';
import { MCPSheet, APISheet, GenerateToolSheet, MCPFormFields } from './ToolSheets';
import type { MCPFormData, ConnStatus as MCPConnStatus } from './ToolSheets';
import MCPTabContent from './components/MCPTabContent';
import APITabContent from './components/APITabContent';
import LocalTabContent from './components/LocalTabContent';
import { getToolTabCounts } from './tabCounts';
import { getCatalogDescription, getMetadataDescription } from '@/utils/mcpCatalog';
import { getLocalizedToolDescription } from './toolDisplay';
import LogViewerModal from '@/components/common/LogViewerModal';

// ============================================================================
// Constants & Config
// ============================================================================

type TabKey = 'all' | 'mcp' | 'api' | 'local';

interface TabConfig {
  key: TabKey;
  label: string;
  icon: React.ReactNode;
  sourceFilter?: ToolSource | ToolSource[];
}

/** 类别 badge (source) - labels for MCP/API are proper nouns; builtin/custom use i18n */
const SOURCE_BADGE: Record<string, { label: string; className: string }> = {
  mcp: { label: 'MCP', className: 'bg-slate-100 text-slate-800' },
  api: { label: 'API', className: 'bg-purple-100 text-purple-800' },
  plugin_py: { label: 'Local', className: 'bg-blue-100 text-blue-800' },
  plugin_yaml: { label: 'API Plugin', className: 'bg-violet-100 text-violet-800' },
  builtin: { label: 'Built-in', className: 'bg-green-100 text-green-800' },
  custom: { label: 'Custom', className: 'bg-orange-100 text-orange-800' },
};

/** 功能类 label key map (category) - maps category key to i18n key */
const CATEGORY_I18N_KEY: Record<string, string> = {
  file: 'category.file',
  terminal: 'category.terminal',
  browser: 'category.browser',
  code: 'category.code',
  search: 'category.search',
  system: 'category.system',
  custom: 'category.custom',
};

/** Default sort order for 类别 */
const SOURCE_SORT_ORDER: Record<string, number> = {
  mcp: 0,
  api: 1,
  plugin_py: 2,
  plugin_yaml: 3,
  builtin: 4,
  custom: 5,
};

const PAGE_SIZE = 20;

/** MCP/API 服务器详情抽屉宽度（px） */
const DETAIL_DRAWER_WIDTH = 560;
/** MCP 工具详情面板宽度（px），叠加在抽屉左侧 */
const TOOL_PANEL_WIDTH = 720;

// ============================================================================
// Sort & Filter types
// ============================================================================

type SortField = 'category' | 'source' | 'source_name' | 'enabled';
type SortDir = 'asc' | 'desc';

interface SortState {
  field: SortField;
  dir: SortDir;
}

interface ColumnFilters {
  category: Set<string>;   // 功能类
  source: Set<string>;     // 类别
  source_name: Set<string>; // 来源
  enabled: Set<string>;    // 状态
}

const EMPTY_FILTERS: ColumnFilters = {
  category: new Set(),
  source: new Set(),
  source_name: new Set(),
  enabled: new Set(),
};

// ============================================================================
// Main Page
// ============================================================================

export default function ToolPage() {
  const { t, i18n } = useTranslation('tool');

  const TABS: TabConfig[] = [
    { key: 'all', label: t('tabs.all'), icon: <Grid className="w-5 h-5" /> },
    { key: 'mcp', label: t('tabs.mcp'), icon: <Database className="w-5 h-5" />, sourceFilter: 'mcp' },
    { key: 'api', label: t('tabs.api'), icon: <Cloud className="w-5 h-5" />, sourceFilter: 'api' },
    { key: 'local', label: t('tabs.local'), icon: <Code className="w-5 h-5" />, sourceFilter: 'plugin_py' },
  ];

  const [activeTab, setActiveTab] = useState<TabKey>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedTool, setSelectedTool] = useState<Tool | null>(null);
  const [testParams, setTestParams] = useState('{}');
  const [testResult, setTestResult] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshDone, setRefreshDone] = useState(false);

  // Sheet state
  const [showMCPSheet, setShowMCPSheet] = useState(false);
  const [mcpRefreshKey, setMcpRefreshKey] = useState(0);
  const [showAPISheet, setShowAPISheet] = useState(false);
  const [showGenerateSheet, setShowGenerateSheet] = useState(false);
  const [showLogViewer, setShowLogViewer] = useState(false);
  // Sort: default by 类别 (source) MCP -> API -> 内置
  const [sort, setSort] = useState<SortState>({ field: 'source', dir: 'asc' });
  const [filters, setFilters] = useState<ColumnFilters>(EMPTY_FILTERS);

  const { tools, loading, error, refetch } = useTools();
  const [apiEnabledServicesCount, setApiEnabledServicesCount] = useState(0);

  // Catalog data (fetched once at top level, shared with MCP & API tabs)
  const [catalogEntries, setCatalogEntries] = useState<MCPCatalogEntry[]>([]);
  const [catalogCategories, setCatalogCategories] = useState<Record<string, MCPCatalogCategory>>({});
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [configuredIds, setConfiguredIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    const loadCatalog = async () => {
      try {
        setCatalogLoading(true);
        const [entriesRes, catsRes] = await Promise.all([
          mcpAPI.catalogList(),
          mcpAPI.catalogCategories(),
        ]);
        setCatalogEntries(entriesRes.data);
        setCatalogCategories(catsRes.data);
        // Get currently configured IDs (no auto-setup to avoid re-adding removed entries)
        try {
          const confRes = await mcpAPI.catalogConfigured();
          setConfiguredIds(new Set(confRes.data));
        } catch { /* ignore */ }
      } catch (err) {
        console.error('Failed to load catalog:', err);
      } finally {
        setCatalogLoading(false);
      }
    };
    loadCatalog();
  }, []);

  const onConfiguredChange = useCallback((id: string) => {
    setConfiguredIds(prev => new Set(prev).add(id));
  }, []);

  const onConfiguredRemove = useCallback((id: string) => {
    setConfiguredIds(prev => { const next = new Set(prev); next.delete(id); return next; });
  }, []);

  const fetchApiServicesCount = useCallback(async () => {
    try {
      const res = await providerAPI.listApiServices();
      const services = Array.isArray(res.data) ? res.data : [];
      setApiEnabledServicesCount(services.filter((service) => service.enabled).length);
    } catch {
      // Keep the previous count when the status request fails.
    }
  }, []);

  useEffect(() => {
    fetchApiServicesCount();
  }, [fetchApiServicesCount]);

  const refreshToolData = useCallback(async () => {
    await Promise.all([
      refetch(),
      fetchApiServicesCount(),
    ]);
  }, [refetch, fetchApiServicesCount]);

  // The backend still marks some valid MCP catalog entries as "api" based on category.
  // Until API catalog has its own rendering path, show all catalog entries in the MCP tab.
  const mcpCatalogEntries = useMemo(() => catalogEntries, [catalogEntries]);
  // API catalog not yet implemented — keep this empty so entries are not duplicated across tabs.
  const apiCatalogEntries = useMemo(() => [] as MCPCatalogEntry[], []);

  // Compute tab counts: all = active tools; mcp/api = unique active servers/modules
  const tabCounts = useMemo(
    () => getToolTabCounts(tools, apiEnabledServicesCount),
    [tools, apiEnabledServicesCount],
  );

  // Get unique values for filter options (active tools only)
  const filterOptions = useMemo(() => {
    const cats = new Set<string>();
    const sources = new Set<string>();
    const sourceNames = new Set<string>();
    tools.forEach((tool) => {
      cats.add(tool.category);
      sources.add(tool.source);
      sourceNames.add(tool.source_name || 'Flocks');
    });
    return {
      category: Array.from(cats).sort(),
      source: Array.from(sources).sort((a, b) => (SOURCE_SORT_ORDER[a] ?? 99) - (SOURCE_SORT_ORDER[b] ?? 99)),
      source_name: Array.from(sourceNames).sort(),
      enabled: ['true', 'false'],
    };
  }, [tools]);

  // Apply tab filter + search + column filters + sort (All tab shows active tools only)
  const processedTools = useMemo(() => {
    let result = [...tools];

    // Tab filter
    const tabConfig = TABS.find((tab) => tab.key === activeTab);
    if (tabConfig?.sourceFilter) {
      const sf = tabConfig.sourceFilter;
      const allowed = Array.isArray(sf) ? sf : [sf];
      result = result.filter((tool) => allowed.includes(tool.source));
    }

    // Search
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (tool) =>
          tool.name.toLowerCase().includes(q) ||
          tool.description.toLowerCase().includes(q) ||
          (tool.description_cn || '').toLowerCase().includes(q) ||
          (tool.source_name || '').toLowerCase().includes(q)
      );
    }

    // Column filters
    if (filters.category.size > 0) {
      result = result.filter((tool) => filters.category.has(tool.category));
    }
    if (filters.source.size > 0) {
      result = result.filter((tool) => filters.source.has(tool.source));
    }
    if (filters.source_name.size > 0) {
      result = result.filter((tool) => filters.source_name.has(tool.source_name || 'Flocks'));
    }
    if (filters.enabled.size > 0) {
      result = result.filter((tool) => filters.enabled.has(String(tool.enabled)));
    }

    // Sort
    result.sort((a, b) => {
      let cmp = 0;
      switch (sort.field) {
        case 'category': {
          const la = a.category;
          const lb = b.category;
          cmp = la.localeCompare(lb);
          break;
        }
        case 'source':
          cmp = (SOURCE_SORT_ORDER[a.source] ?? 99) - (SOURCE_SORT_ORDER[b.source] ?? 99);
          break;
        case 'source_name':
          cmp = (a.source_name || 'Flocks').localeCompare(b.source_name || 'Flocks', 'zh');
          break;
        case 'enabled':
          cmp = (a.enabled === b.enabled ? 0 : a.enabled ? -1 : 1);
          break;
      }
      return sort.dir === 'desc' ? -cmp : cmp;
    });

    return result;
  }, [tools, activeTab, searchQuery, filters, sort]);

  // Pagination
  const totalPages = Math.ceil(processedTools.length / PAGE_SIZE);
  const paginatedTools = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE;
    return processedTools.slice(start, start + PAGE_SIZE);
  }, [processedTools, currentPage]);

  const handleTabChange = (tab: TabKey) => {
    setActiveTab(tab);
    setCurrentPage(1);
    setSearchQuery('');
    setFilters(EMPTY_FILTERS);
    setSort({ field: 'source', dir: 'asc' });
  };

  const handleSearchChange = (value: string) => {
    setSearchQuery(value);
    setCurrentPage(1);
  };

  const handleRefresh = async () => {
    if (refreshing) return;
    try {
      setRefreshing(true);
      await Promise.all([
        toolAPI.refresh().then(() => refetch()),
        new Promise((r) => setTimeout(r, 600)),
      ]);
      setRefreshDone(true);
      setTimeout(() => setRefreshDone(false), 2000);
    } catch (err: any) {
      alert(t('alert.refreshFailed', { error: err.message }));
    } finally {
      setRefreshing(false);
    }
  };

  const handleTest = async () => {
    if (!selectedTool) return;
    try {
      setTesting(true);
      setTestResult(null);
      const params = JSON.parse(testParams);
      const response = await toolAPI.test(selectedTool.name, params);
      setTestResult(response.data);
    } catch (err: any) {
      setTestResult({ success: false, error: err.message });
    } finally {
      setTesting(false);
    }
  };

  const openDetail = (tool: Tool) => {
    setSelectedTool(tool);
    setTestParams('{}');
    setTestResult(null);
  };

  const toggleSort = (field: SortField) => {
    setSort((prev) =>
      prev.field === field
        ? { field, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
        : { field, dir: 'asc' }
    );
    setCurrentPage(1);
  };

  const toggleFilter = (column: keyof ColumnFilters, value: string) => {
    setFilters((prev) => {
      const next = new Set(prev[column]);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return { ...prev, [column]: next };
    });
    setCurrentPage(1);
  };

  const clearFilter = (column: keyof ColumnFilters) => {
    setFilters((prev) => ({ ...prev, [column]: new Set() }));
    setCurrentPage(1);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-red-600 mb-4">{error}</p>
          <button onClick={() => refetch()} className="px-4 py-2 bg-slate-700 text-white rounded-lg hover:bg-slate-800">
            {t('button.retry')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="text-red-600">
            <Wrench className="w-8 h-8" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-gray-900">{t('pageTitle')}</h1>
          <p className="mt-1 text-sm text-gray-500">
            <span className="font-semibold text-gray-700">{tools.length}</span> {t('statusBadge.active')}
            <span className="mx-1.5 text-gray-300">·</span>
            <span className="font-semibold text-gray-700">{catalogEntries.length}</span> {t('statusBadge.inactive')}
          </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowLogViewer(true)}
            className="inline-flex items-center px-3 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 transition-colors"
            title={t('logs.viewLogs')}
          >
            <FileText className="w-4 h-4" />
          </button>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            title={refreshDone ? t('button.refreshDone') : t('button.refreshList')}
            className={`inline-flex items-center px-3 py-2 border rounded-lg text-sm font-medium transition-all ${
              refreshDone
                ? 'border-green-300 text-green-600 bg-green-50'
                : 'border-gray-300 text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50'
            }`}
          >
            <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={() => setShowMCPSheet(true)}
            className="inline-flex items-center px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 transition-colors"
          >
            <Database className="w-4 h-4 mr-1.5" />
            {t('button.addMCP')}
          </button>
          <button
            onClick={() => setShowAPISheet(true)}
            className="inline-flex items-center px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 transition-colors"
          >
            <Cloud className="w-4 h-4 mr-1.5" />
            {t('button.addAPI')}
          </button>
          <button
            onClick={() => setShowGenerateSheet(true)}
            className="inline-flex items-center px-4 py-2 border border-transparent rounded-lg text-sm font-medium text-white bg-red-600 hover:bg-red-700 transition-colors shadow-sm"
          >
            <Plus className="w-4 h-4 mr-1.5" />
            {t('button.createTool')}
          </button>
        </div>
      </div>

      {/* Tabs row with search */}
      <div className="border-b border-gray-200">
        <div className="flex items-center justify-between">
          <nav className="-mb-px flex space-x-8">
            {TABS.map((tab) => (
              <button
                key={tab.key}
                onClick={() => handleTabChange(tab.key)}
                className={`py-4 px-1 border-b-2 font-medium text-sm whitespace-nowrap transition-colors ${
                  activeTab === tab.key
                    ? 'border-slate-600 text-slate-800'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
              >
                <div className="flex items-center">
                  <span className={activeTab === tab.key ? 'text-slate-700' : 'text-gray-400'}>
                    {tab.icon}
                  </span>
                  <span className="ml-2">{tab.label}</span>
                  <span className="ml-2 bg-gray-100 text-gray-900 py-0.5 px-2.5 rounded-full text-xs font-medium">
                    {tabCounts[tab.key] || 0}
                  </span>
                </div>
              </button>
            ))}
          </nav>

          {/* Search - right aligned, same row */}
          <div className="relative mb-px">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-gray-400" />
            <input
              type="text"
              placeholder={t('search.placeholder')}
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
              className="w-64 pl-9 pr-4 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 focus:border-transparent bg-white"
            />
          </div>
        </div>
      </div>

      {/* Tab Content */}
      {activeTab === 'mcp' ? (
        <MCPTabContent
          tools={processedTools}
          searchQuery={searchQuery}
          onSelectTool={openDetail}
          onRefreshTools={refreshToolData}
          catalogEntries={mcpCatalogEntries}
          catalogCategories={catalogCategories}
          catalogLoading={catalogLoading}
          configuredIds={configuredIds}
          onConfiguredChange={onConfiguredChange}
          onConfiguredRemove={onConfiguredRemove}
          refreshKey={mcpRefreshKey}
        />
      ) : activeTab === 'api' ? (
        <APITabContent
          tools={processedTools}
          onSelectTool={openDetail}
          onRefreshTools={refreshToolData}
          catalogEntries={apiCatalogEntries}
          catalogCategories={catalogCategories}
          catalogLoading={catalogLoading}
          configuredIds={configuredIds}
          onConfiguredChange={onConfiguredChange}
        />
      ) : activeTab === 'local' ? (
        <LocalTabContent
          tools={processedTools}
          searchQuery={searchQuery}
          selectedToolName={selectedTool?.name}
          onSelectTool={openDetail}
          onRefreshTools={refreshToolData}
        />
      ) : (
        /* All tab: active tools only */
        <div className="space-y-4">
          {/* Inactive services callout */}
          {(mcpCatalogEntries.length > 0 || apiCatalogEntries.length > 0) && !searchQuery && (
            <div className="flex items-center justify-between px-4 py-3 bg-gray-50 border border-gray-200 rounded-lg text-sm">
              <span className="text-gray-600">
                <span className="font-medium text-gray-800">{catalogEntries.length}</span> {t('summary.inactiveServicesAvailable')}
              </span>
              <div className="flex items-center gap-3">
                {mcpCatalogEntries.length > 0 && (
                  <button
                    onClick={() => handleTabChange('mcp')}
                    className="flex items-center gap-1 text-slate-700 hover:text-slate-900 font-medium"
                  >
                    MCP <ChevronRight className="w-4 h-4" />
                  </button>
                )}
                {apiCatalogEntries.length > 0 && (
                  <button
                    onClick={() => handleTabChange('api')}
                    className="flex items-center gap-1 text-purple-600 hover:text-purple-700 font-medium"
                  >
                    API <ChevronRight className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>
          )}
          {processedTools.length === 0 ? (
            <EmptyState
              icon={<Wrench className="w-16 h-16" />}
              title={t('empty.noTools')}
              description={searchQuery ? t('empty.tryOtherKeywords') : t('empty.noToolsInCategory')}
            />
          ) : (
            <ToolTable
              tools={paginatedTools}
              sort={sort}
              filters={filters}
              filterOptions={filterOptions}
              currentPage={currentPage}
              totalPages={totalPages}
              totalCount={processedTools.length}
              pageSize={PAGE_SIZE}
              onSort={toggleSort}
              onToggleFilter={toggleFilter}
              onClearFilter={clearFilter}
              onPageChange={setCurrentPage}
              onSelect={openDetail}
            />
          )}
        </div>
      )}

      {/* Tool Detail Drawer */}
      {selectedTool && (
        <ToolDetailDrawer
          tool={selectedTool}
          testParams={testParams}
          testResult={testResult}
          testing={testing}
          onClose={() => {
            setSelectedTool(null);
            setTestResult(null);
          }}
          onTestParamsChange={setTestParams}
          onTest={handleTest}
          onDelete={async (name) => {
            try {
              await toolAPI.delete(name);
              setSelectedTool(null);
              setTestResult(null);
              await handleRefresh();
            } catch (err: any) {
              alert(t('alert.deleteFailed', { error: err.response?.data?.detail || err.message }));
            }
          }}
          onEnabledChange={(name, newEnabled) => {
            setSelectedTool((prev) => prev ? { ...prev, enabled: newEnabled } : prev);
            handleRefresh();
          }}
        />
      )}

      {/* MCP Sheet */}
      {showMCPSheet && (
        <MCPSheet
          onClose={() => setShowMCPSheet(false)}
          onSaved={() => { setShowMCPSheet(false); handleRefresh(); setMcpRefreshKey(k => k + 1); }}
          onRefresh={handleRefresh}
        />
      )}

      {/* API Sheet */}
      {showAPISheet && (
        <APISheet
          onClose={() => setShowAPISheet(false)}
        />
      )}

      {/* Generate Tool Sheet */}
      {showGenerateSheet && (
        <GenerateToolSheet
          onClose={() => setShowGenerateSheet(false)}
        />
      )}

      <LogViewerModal open={showLogViewer} onClose={() => setShowLogViewer(false)} />

    </div>
  );
}

// ============================================================================
// (AddMCPDialog and AddAPIDialog removed - replaced by MCPSheet/APISheet)
// ============================================================================

// ============================================================================
// Sortable / Filterable Table Header Cell
// ============================================================================

function SortFilterHeader({
  label,
  field,
  sort,
  filterValues,
  activeFilters,
  onSort,
  onToggleFilter,
  onClearFilter,
  renderLabel,
}: {
  label: string;
  field: SortField;
  sort: SortState;
  filterValues: string[];
  activeFilters: Set<string>;
  onSort: (f: SortField) => void;
  onToggleFilter: (f: keyof ColumnFilters, v: string) => void;
  onClearFilter: (f: keyof ColumnFilters) => void;
  renderLabel?: (v: string) => string;
}) {
  const { t, i18n } = useTranslation('tool');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLTableHeaderCellElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    if (open) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  const isActive = sort.field === field;
  const hasFilter = activeFilters.size > 0;

  return (
    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider relative whitespace-nowrap" ref={ref}>
      <div className="flex items-center gap-1">
        {/* Sort button */}
        <button
          onClick={() => onSort(field)}
          className={`flex items-center gap-0.5 hover:text-gray-900 transition-colors ${isActive ? 'text-slate-700' : ''}`}
        >
          {label}
          {isActive && (sort.dir === 'asc' ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />)}
        </button>

        {/* Filter toggle */}
        <button
          onClick={() => setOpen((v) => !v)}
          className={`p-0.5 rounded hover:bg-gray-200 transition-colors ${hasFilter ? 'text-slate-600' : 'text-gray-400'}`}
        >
          <Filter className="w-3 h-3" />
        </button>
      </div>

      {/* Filter dropdown */}
      {open && (
        <div className="absolute left-0 top-full mt-1 z-20 bg-white rounded-lg shadow-lg border border-gray-200 py-2 min-w-[160px] max-h-60 overflow-y-auto">
          {hasFilter && (
            <button
              onClick={() => { onClearFilter(field); setOpen(false); }}
              className="w-full text-left px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
            >
              {t('button.clearFilter')}
            </button>
          )}
          {filterValues.map((v) => {
            const checked = activeFilters.has(v);
            const displayLabel = renderLabel ? renderLabel(v) : v;
            return (
              <label key={v} className="flex items-center px-3 py-1.5 hover:bg-gray-50 cursor-pointer">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => onToggleFilter(field, v)}
                  className="w-3.5 h-3.5 rounded border-gray-300 text-slate-600 focus:ring-slate-400 mr-2"
                />
                <span className="text-xs text-gray-700">{displayLabel}</span>
              </label>
            );
          })}
        </div>
      )}
    </th>
  );
}

// ============================================================================
// MCP Tab Content
// ============================================================================

function LegacyMCPTabContent({
  tools,
  searchQuery,
  onSelectTool,
  onRefreshTools,
  catalogEntries,
  catalogCategories,
  catalogLoading,
  configuredIds,
  onConfiguredChange,
  refreshKey,
}: {
  tools: Tool[];
  searchQuery: string;
  onSelectTool: (tool: Tool) => void;
  onRefreshTools: () => void;
  catalogEntries: MCPCatalogEntry[];
  catalogCategories: Record<string, MCPCatalogCategory>;
  catalogLoading: boolean;
  configuredIds: Set<string>;
  onConfiguredChange: (id: string) => void;
  refreshKey?: number;
}) {
  const { t, i18n } = useTranslation('tool');
  const [servers, setServers] = useState<MCPServer[]>([]);
  const [serversLoading, setServersLoading] = useState(true);
  const [selectedServer, setSelectedServer] = useState<string | null>(null);
  const [selectedServerData, setSelectedServerData] = useState<MCPServer | null>(null);
  const [selectedToolFromMCP, setSelectedToolFromMCP] = useState<Tool | null>(null);

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
      setSelectedServerData(prev => {
        if (!prev) return prev;
        const updated = newServers.find(s => s.name === prev.name);
        return updated ?? prev;
      });
    } catch {
      setServers([]);
    } finally {
      setServersLoading(false);
    }
  }, []);

  useEffect(() => { fetchServers(); }, [fetchServers]);

  useEffect(() => {
    if (refreshKey && refreshKey > 0) fetchServers();
  }, [refreshKey, fetchServers]);

  const handleConnect = async (name: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try { await mcpAPI.connect(name); await fetchServers(); onRefreshTools(); }
    catch (err: any) { alert(t('alert.connectFailed', { error: err.message })); }
  };

  const handleDisconnect = async (name: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try { await mcpAPI.disconnect(name); await fetchServers(); onRefreshTools(); }
    catch (err: any) { alert(t('alert.disconnectFailed', { error: err.message })); }
  };

  const handleRemove = async (name: string) => {
    if (!window.confirm(t('alert.confirmRemoveMCP', { name }))) return;
    try {
      await mcpAPI.remove(name);
      setSelectedServer(null);
      setSelectedServerData(null);
      await fetchServers();
      onRefreshTools();
    } catch (err: any) {
      alert(t('alert.removeFailed', { error: err.response?.data?.detail ?? err.message }));
    }
  };

  const toolsByServer = useMemo(() => {
    const map: Record<string, Tool[]> = {};
    tools.filter((t) => t.source === 'mcp').forEach((t) => {
      const key = t.source_name || 'unknown';
      if (!map[key]) map[key] = [];
      map[key].push(t);
    });
    return map;
  }, [tools]);

  const selectedServerObj = useMemo((): MCPServer | undefined => {
    if (!selectedServer) return undefined;
    const fromServers = servers.find((s) => s.name === selectedServer);
    if (fromServers) return fromServers;
    const fromCatalog = catalogEntries.find(e => e.id === selectedServer);
    if (fromCatalog) {
      return {
        name: fromCatalog.id,
        status: 'disconnected',
        tools: [],
        resources: [],
        tools_count: 0,
        resources_count: 0,
      };
    }
    return undefined;
  }, [selectedServer, servers, catalogEntries]);

  // Catalog filtering
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [installing, setInstalling] = useState<string | null>(null);

  // Credential modal state
  const [credModalEntry, setCredModalEntry] = useState<MCPCatalogEntry | null>(null);
  const [credValues, setCredValues] = useState<Record<string, string>>({});

  const isConfigured = useCallback((entryId: string) => {
    return configuredIds.has(entryId);
  }, [configuredIds]);

  // Priority IDs for sorting
  const PRIORITY_IDS = useMemo(() => new Set(['virustotal_mcp', 'urlhaus']), []);

  const filteredCatalog = useMemo(() => {
    let result = [...catalogEntries];
    if (selectedCategory !== 'all') {
      result = result.filter(e => e.category === selectedCategory);
    }
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      result = result.filter(e =>
        e.name.toLowerCase().includes(q) ||
        e.description.toLowerCase().includes(q) ||
        (e.description_cn || '').toLowerCase().includes(q) ||
        e.id.toLowerCase().includes(q) ||
        e.tags.some(tag => tag.toLowerCase().includes(q))
      );
    }
    // Sort: priority first, then configured, then by stars
    result.sort((a, b) => {
      const pa = PRIORITY_IDS.has(a.id) ? 0 : 1;
      const pb = PRIORITY_IDS.has(b.id) ? 0 : 1;
      if (pa !== pb) return pa - pb;
      const ca = isConfigured(a.id) ? 0 : 1;
      const cb = isConfigured(b.id) ? 0 : 1;
      if (ca !== cb) return ca - cb;
      return b.stars - a.stars;
    });
    return result;
  }, [catalogEntries, selectedCategory, searchQuery, PRIORITY_IDS, isConfigured]);

  const catalogCategoryCounts = useMemo(() => {
    const counts: Record<string, number> = { all: catalogEntries.length };
    for (const e of catalogEntries) {
      counts[e.category] = (counts[e.category] || 0) + 1;
    }
    return counts;
  }, [catalogEntries]);

  // Open credential modal for entries requiring auth
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
    const hasEmpty = Object.entries(credValues).some(([, v]) => !v.trim());
    if (hasEmpty) { alert(t('alert.fillAllRequired')); return; }
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
      alert(t('alert.configFailed', { error: err.response?.data?.detail || err.message }));
      setInstalling(null);
      return;
    }
    try {
      if (shouldConnect) {
        await mcpAPI.connect(entryId);
      }
      await fetchServers();
      onRefreshTools();
      if (!shouldConnect) {
        alert(t('alert.mcpConfiguredDisabled', { name: entryName }));
      }
    } catch (err: any) {
      alert(t('alert.connectFailed', { error: err.response?.data?.detail || err.message }));
    } finally {
      setInstalling(null);
    }
  };

  const handleInstallNoAuth = async (entry: MCPCatalogEntry, e?: React.MouseEvent) => {
    e?.stopPropagation();
    let shouldConnect = false;
    let installRes: Awaited<ReturnType<typeof mcpAPI.catalogInstall>> | null = null;
    try {
      setInstalling(entry.id);
      installRes = await mcpAPI.catalogInstall(entry.id);
      shouldConnect = installRes.data?.config?.enabled !== false;
      onConfiguredChange(entry.id);
    } catch (err: any) {
      alert(t('alert.configFailed', { error: err.response?.data?.detail || err.message }));
      setInstalling(null);
      return;
    }
    try {
      if (shouldConnect) {
        await mcpAPI.connect(entry.id);
      }
      await fetchServers();
      onRefreshTools();
      if (!shouldConnect) {
        alert(t('alert.mcpConfiguredDisabled', { name: entry.name }));
      }
    } catch (err: any) {
      alert(t('alert.connectFailed', { error: err.response?.data?.detail || err.message }));
    } finally {
      setInstalling(null);
    }
  };

  // Filter servers by search
  const filteredServers = useMemo(() => {
    if (!searchQuery) return servers;
    const q = searchQuery.toLowerCase();
    return servers.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        (s.url && s.url.toLowerCase().includes(q)) ||
        (toolsByServer[s.name] || []).some(
          (t) => t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q)
        )
    );
  }, [servers, searchQuery, toolsByServer]);

  // Only show servers that are NOT from catalog (user-added ones)
  const userServers = useMemo(() => {
    const catalogIds = new Set(catalogEntries.map(e => e.id));
    return filteredServers.filter(s => !catalogIds.has(s.name));
  }, [filteredServers, catalogEntries]);

  return (
    <div className="space-y-4">
      {/* Category pills + refresh */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-1.5 flex-wrap flex-1 min-w-0">
          <button
            onClick={() => setSelectedCategory('all')}
            className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${selectedCategory === 'all' ? 'bg-slate-100 text-slate-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
          >{t('catalog.all')}</button>
          {Object.entries(catalogCategories).map(([id, cat]) =>
            catalogCategoryCounts[id] ? (
              <button key={id} onClick={() => setSelectedCategory(id)} className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${selectedCategory === id ? 'bg-slate-100 text-slate-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>
                {cat.label} ({catalogCategoryCounts[id]})
              </button>
            ) : null
          )}
        </div>
        <button
          onClick={fetchServers}
          className="inline-flex items-center px-3 py-2 text-sm text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 flex-shrink-0"
        >
          <RefreshCw className="w-4 h-4 mr-1.5" />
          {t('mcp.refreshStatus')}
        </button>
      </div>

      {serversLoading && catalogLoading ? (
        <div className="flex justify-center py-12"><LoadingSpinner /></div>
      ) : userServers.length === 0 && filteredCatalog.length === 0 ? (
        <EmptyState icon={<Server className="w-16 h-16" />} title={t('mcp.noServers')} description={t('mcp.noServersDesc')} />
      ) : (
        <div className="grid gap-4 grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {/* User-added servers (not from catalog) */}
          {userServers.map((server) => {
            const serverTools = toolsByServer[server.name] || [];
            const isSelected = selectedServer === server.name;
            const isActive = server.status === 'connected';
            const isError = server.status === 'error';
            const borderColor = isActive ? '#10B981' : isError ? '#EF4444' : '#9CA3AF';
            return (
              <div
                key={server.name}
                onClick={() => {
                  if (isSelected) { setSelectedServer(null); setSelectedServerData(null); }
                  else { setSelectedServer(server.name); setSelectedServerData(server); }
                }}
                className={`relative bg-white rounded-xl border overflow-hidden cursor-pointer h-[180px] flex flex-col transition-all duration-150 ${isSelected ? 'border-slate-400 shadow-md ring-2 ring-slate-200' : 'border-gray-200 shadow-sm hover:shadow-md hover:border-gray-300'}`}
                style={{ borderLeftWidth: 4, borderLeftColor: borderColor }}
              >
                <div className="flex-1 px-4 pt-4 pb-2 min-h-0 flex flex-col gap-1.5">
                  <div className="flex items-start gap-1.5 flex-wrap">
                    <span className="text-sm font-semibold text-gray-900 truncate max-w-[120px]">{server.name}</span>
                    <span className={`px-1.5 py-0.5 text-xs font-medium rounded-full shrink-0 ${isActive ? 'bg-green-100 text-green-700' : isError ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-600'}`}>
                      {isActive ? t('statusBadge.active') : isError ? t('statusBadge.error') : t('statusBadge.inactive')}
                    </span>
                    <span className="px-1.5 py-0.5 bg-slate-100 text-slate-700 text-xs font-medium rounded-full shrink-0">MCP</span>
                  </div>
                  {server.url && <p className="text-xs text-gray-500 font-mono truncate">{server.url}</p>}
                  <div className="flex items-center gap-1 text-xs text-gray-500 mt-auto">
                    <Wrench className="w-3 h-3 shrink-0" /><span>{serverTools.length} {t('mcp.tools')}</span>
                    <span className="mx-1">·</span>
                    <FileText className="w-3 h-3 shrink-0" /><span>{server.resources?.length || 0} {t('mcp.resources')}</span>
                  </div>
                  {server.connected_at && (
                    <div className="flex items-center gap-1">
                      <Clock className="w-3 h-3 shrink-0" /><span>{t('mcp.connectedFor', { duration: formatDuration(server.connected_at, t) })}</span>
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          {/* Catalog entries */}
          {filteredCatalog.map((entry) => {
            const serverObj = servers.find(s => s.name === entry.id);
            const serverStatus = serverObj?.status;
            const isActive = serverStatus === 'connected';
            const isError = serverStatus === 'error';
            const borderColor = isActive ? '#10B981' : isError ? '#EF4444' : '#9CA3AF';
            const isSelected = selectedServer === entry.id;

            return (
              <div
                key={`catalog-${entry.id}`}
                onClick={() => isActive ? setSelectedServer(isSelected ? null : entry.id) : undefined}
                className={`relative bg-white rounded-xl border overflow-hidden h-[180px] flex flex-col transition-all duration-150 ${isActive ? 'cursor-pointer' : 'cursor-default'} ${isSelected ? 'border-slate-400 shadow-md ring-2 ring-slate-200' : 'border-gray-200 shadow-sm hover:shadow-md hover:border-gray-300'}`}
                style={{ borderLeftWidth: 4, borderLeftColor: borderColor }}
              >
                <div className="flex-1 px-4 pt-4 pb-2 min-h-0 flex flex-col gap-1.5">
                  <div className="flex items-start gap-1.5 flex-wrap">
                    <span className="text-sm font-semibold text-gray-900 truncate max-w-[120px]">{entry.name}</span>
                    <span className={`px-1.5 py-0.5 text-xs font-medium rounded-full shrink-0 ${isActive ? 'bg-green-100 text-green-700' : isError ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-600'}`}>
                      {isActive ? t('statusBadge.active') : isError ? t('statusBadge.error') : t('statusBadge.inactive')}
                    </span>
                    <span className="px-1.5 py-0.5 bg-slate-100 text-slate-700 text-xs font-medium rounded-full shrink-0">MCP</span>
                    {PRIORITY_IDS.has(entry.id) && (
                      <span className="px-1.5 py-0.5 bg-amber-100 text-amber-800 text-xs font-medium rounded-full shrink-0">{t('mcp.recommended')}</span>
                    )}
                  </div>
                  <p className="text-xs text-gray-500 line-clamp-2 leading-relaxed">{getCatalogDescription(entry, i18n.language)}</p>
                  <div className="flex items-center gap-2 text-xs text-gray-500 mt-auto">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${LANG_COLORS[entry.language] || 'bg-gray-100 text-gray-600'}`}>{entry.language}</span>
                    <span className="flex items-center gap-0.5"><Star className="w-3 h-3" />{entry.stars}</span>
                  </div>
                </div>
                <div className="border-t border-gray-100 px-4 py-2 flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                  {isActive ? (
                    <>
                      <button
                        onClick={() => setSelectedServer(isSelected ? null : entry.id)}
                        className={`flex-1 flex items-center justify-center gap-1 py-1 px-2 border rounded-lg text-xs font-medium transition-colors ${isSelected ? 'border-slate-300 text-slate-800 bg-slate-50' : 'border-gray-300 text-gray-700 hover:bg-gray-50'}`}
                      >
                        <Settings className="w-3 h-3" /> {t('mcp.manage')}
                      </button>
                      <button onClick={(e) => handleDisconnect(entry.id, e)} className="flex items-center justify-center w-7 h-7 border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-100 transition-colors" title={t('mcp.disconnectTitle')}>
                        <PowerOff className="w-3.5 h-3.5" />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          if (entry.requires_auth && !isConfigured(entry.id)) {
                            openCredModal(entry);
                          } else {
                            handleInstallNoAuth(entry, e);
                          }
                        }}
                        disabled={installing === entry.id}
                        className="flex-1 flex items-center justify-center gap-1 py-1 px-2 bg-slate-700 text-white rounded-lg text-xs font-medium hover:bg-slate-800 disabled:opacity-50 transition-colors"
                      >
                        <Download className="w-3 h-3" />
                        {installing === entry.id ? t('mcp.configuring') : t('button.install')}
                      </button>
                      <a
                        href={`https://github.com/${entry.github}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center justify-center w-7 h-7 border border-gray-300 text-gray-500 rounded-lg hover:bg-gray-50 transition-colors"
                        title="GitHub"
                      >
                        <ExternalLink className="w-3.5 h-3.5" />
                      </a>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Credential Input Modal */}
      {credModalEntry && (
        <>
          <div className="fixed inset-0 bg-black/40 z-40" onClick={() => setCredModalEntry(null)} />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md" onClick={(e) => e.stopPropagation()}>
              <div className="px-6 py-4 border-b border-gray-200">
                <h3 className="text-lg font-semibold text-gray-900">{credModalEntry.name}</h3>
                <p className="text-sm text-gray-500 mt-1">{t('credentials.configNote')}</p>
              </div>
              <div className="px-6 py-4 space-y-4">
                {Object.entries(credModalEntry.env_vars).filter(([, spec]) => spec.secret).map(([key, spec]) => (
                  <div key={key}>
                    <label className="block text-sm font-medium text-gray-700 mb-1">{key}</label>
                    <p className="text-xs text-gray-500 mb-1.5">{spec.description}</p>
                    <input
                      type="password"
                      value={credValues[key] || ''}
                      onChange={(e) => setCredValues(prev => ({ ...prev, [key]: e.target.value }))}
                      placeholder={t('credentials.enterField', { field: key })}
                      className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 focus:border-transparent"
                    />
                  </div>
                ))}
              </div>
              <div className="px-6 py-4 border-t border-gray-200 flex gap-3 justify-end">
                <button
                  onClick={() => setCredModalEntry(null)}
                  className="px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
                >{t('button.cancel')}</button>
                <button
                  onClick={handleCredSubmit}
                  disabled={installing === credModalEntry.id}
                  className="px-4 py-2 text-sm text-white bg-slate-700 rounded-lg hover:bg-slate-800 disabled:opacity-50 transition-colors"
                >
                  {installing === credModalEntry.id ? t('mcp.configuring') : t('button.confirmConfig')}
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {/* Server Detail Drawer */}
      {selectedServer && (selectedServerData || selectedServerObj) && (() => {
        const drawerServer = selectedServerData || selectedServerObj!;
        const catEntry = catalogEntries.find(e => e.id === selectedServer);
        const displayName = catEntry?.name || drawerServer.name;
        return (
          <>
            <div className="fixed inset-0 bg-black/40 z-40" onClick={() => { setSelectedServer(null); setSelectedServerData(null); setSelectedToolFromMCP(null); }} />

            {selectedToolFromMCP && (
              <MCPToolDetailPanel
                tool={selectedToolFromMCP}
                onClose={() => setSelectedToolFromMCP(null)}
              />
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
                      <a href={`https://github.com/${catEntry.github}`} target="_blank" rel="noopener noreferrer" className="text-sky-700 hover:text-sky-900 flex items-center gap-0.5">
                        <ExternalLink className="w-3 h-3" /> GitHub
                      </a>
                    </div>
                  </div>
                )}
                <MCPServerDetailPanel
                  server={drawerServer}
                  serverTools={toolsByServer[selectedServer] || []}
                  onConnect={() => handleConnect(selectedServer)}
                  onDisconnect={() => handleDisconnect(selectedServer)}
                  onRefresh={async () => { await mcpAPI.refresh(selectedServer); await fetchServers(); onRefreshTools(); }}
                  onRemove={() => handleRemove(selectedServer)}
                  onSelectTool={setSelectedToolFromMCP}
                />
              </div>
            </div>
          </>
        );
      })()}
    </div>
  );
}

// ============================================================================
// MCP Server Detail Panel (cjtools-style with MCPFormFields)
// ============================================================================

function MCPServerDetailPanel({
  server,
  serverTools,
  onConnect,
  onDisconnect,
  onRefresh,
  onRemove,
  onSelectTool,
}: {
  server: MCPServer;
  serverTools: Tool[];
  onConnect: () => void;
  onDisconnect: () => void;
  onRefresh: () => Promise<void>;
  onRemove?: () => void;
  onSelectTool: (tool: Tool) => void;
}) {
  const { t, i18n } = useTranslation('tool');
  const [detailTab, setDetailTab] = useState<'overview' | 'tools' | 'resources'>('overview');
  const [serverDetail, setServerDetail] = useState<MCPServerDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [testingConnection, setTestingConnection] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string; latency?: number; tools_count?: number } | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setDetailLoading(true);
        const res = await mcpAPI.get(server.name);
        if (!cancelled) setServerDetail(res.data);
      } catch {
        if (!cancelled) setServerDetail(null);
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [server.name, server.status]);

  const handleTestConnection = async () => {
    setTestingConnection(true);
    setTestResult(null);
    try {
      const res = await mcpAPI.testCredentials(server.name);
      const data = res.data;
      setTestResult({
        success: data.success ?? false,
        message: data.message ?? (data.success ? t('alert.connectionOk') : t('detail.testFailed')),
        latency: data.latency_ms,
        tools_count: data.tools_count,
      });
      if (data.success) {
        const detailRes = await mcpAPI.get(server.name);
        setServerDetail(detailRes.data);
        await onRefresh();
      }
    } catch (err: any) {
      setTestResult({
        success: false,
        message: err.response?.data?.detail ?? err.message ?? t('detail.testFailed'),
      });
    } finally {
      setTestingConnection(false);
    }
  };

  const handleRefreshTools = async () => {
    setRefreshing(true);
    try {
      await onRefresh();
    } finally {
      setRefreshing(false);
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
            className={`flex items-center gap-1.5 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
              detailTab === tab.key
                ? 'border-slate-600 text-slate-800'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
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
            {(() => {
              const connType = (serverDetail?.config?.type ?? (server.url ? 'sse' : 'stdio')) as 'stdio' | 'sse';
              const detailFormData: MCPFormData = {
                name: server.name,
                connType,
                command: Array.isArray(serverDetail?.config?.command)
                  ? serverDetail.config.command.join(' ')
                  : (serverDetail?.config?.command ?? ''),
                args: Array.isArray(serverDetail?.config?.args)
                  ? serverDetail.config.args.join('\n')
                  : (typeof serverDetail?.config?.args === 'string' ? serverDetail.config.args : ''),
                url: serverDetail?.config?.url ?? server.url ?? '',
              };
              const mcpConnStatus: MCPConnStatus = testingConnection
                ? 'testing'
                : testResult?.success === true
                  ? 'connected'
                  : testResult?.success === false
                    ? 'failed'
                    : server.status === 'connected'
                      ? 'connected'
                      : 'idle';
              return (
                <MCPFormFields
                  formData={detailFormData}
                  connStatus={mcpConnStatus}
                  testResult={testResult ? { success: testResult.success, message: testResult.message, tools_count: testResult.tools_count } : null}
                  onTestConnection={handleTestConnection}
                  isTesting={testingConnection}
                />
              );
            })()}

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
              {server.status === 'connected' ? (
                <button onClick={onDisconnect} className="inline-flex items-center gap-2 px-4 py-2.5 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 text-sm font-medium transition-colors">
                  <PowerOff className="w-4 h-4" />{t('detail.disconnectConn')}
                </button>
              ) : (
                <button onClick={onConnect} className="inline-flex items-center gap-2 px-4 py-2.5 bg-slate-700 text-white rounded-lg hover:bg-slate-800 text-sm font-medium transition-colors">
                  <Power className="w-4 h-4" />{t('detail.connectConn')}
                </button>
              )}
              {onRemove && (
                <button
                  onClick={onRemove}
                  className="inline-flex items-center gap-2 px-4 py-2.5 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 text-sm font-medium transition-colors"
                >
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
                    <tr
                      key={tool.name}
                      onClick={() => onSelectTool(tool)}
                      className="hover:bg-slate-50 cursor-pointer transition-colors"
                    >
                      <td className="px-4 py-3">
                        <span className="text-sm font-medium text-gray-900 font-mono break-all">{tool.name}</span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-sm text-gray-600 line-clamp-2 leading-relaxed">{tool.description}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : (
          (!serverDetail?.resources || serverDetail.resources.length === 0) ? (
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
          )
        )}
      </div>
    </div>
  );
}

// ============================================================================
// MCP Tool Detail Panel — tool detail overlay inside server drawer
// ============================================================================

function MCPToolDetailPanel({ tool, onClose }: { tool: Tool; onClose: () => void }) {
  const { t, i18n } = useTranslation('tool');
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
    <div
      className="fixed top-0 bottom-0 z-50 flex flex-col bg-white shadow-2xl border-r border-gray-200"
      style={{ right: DETAIL_DRAWER_WIDTH, width: TOOL_PANEL_WIDTH }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex-shrink-0 border-b border-gray-200">
        <div className="flex items-center gap-3 px-6 py-4">
          <div className="w-8 h-8 bg-gray-100 rounded-lg flex items-center justify-center flex-shrink-0">
            <Wrench className="w-4 h-4 text-gray-600" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-semibold text-gray-900 font-mono truncate">{tool.name}</h2>
            {tool.source_name && (
              <p className="text-xs text-gray-500 mt-0.5">{tool.source_name}</p>
            )}
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
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                section === tab
                  ? 'border-slate-700 text-slate-800'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
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
                          <td className="px-4 py-2.5">
                            <code className="text-xs font-mono text-gray-900 bg-gray-100 px-1.5 py-0.5 rounded">{param.name}</code>
                          </td>
                          <td className="px-4 py-2.5 text-xs text-gray-500">{param.type}</td>
                          <td className="px-4 py-2.5">
                            {param.required
                              ? <span className="text-xs text-slate-700 font-medium">{t('detail.yes')}</span>
                              : <span className="text-xs text-gray-400">{t('detail.no')}</span>}
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
                className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 font-mono text-sm bg-gray-50 resize-none"
              />
            </div>
            <button
              onClick={handleTest}
              disabled={testing || !tool.enabled}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-slate-700 text-white rounded-lg hover:bg-slate-800 disabled:opacity-50 disabled:cursor-not-allowed font-medium text-sm transition-colors"
            >
              {testing
                ? <><RefreshCw className="w-4 h-4 animate-spin" />{t('detail.executing')}</>
                : <><Play className="w-4 h-4" />{t('detail.runTest')}</>}
            </button>
            {!tool.enabled && (
              <p className="text-xs text-amber-600 text-center">{t('detail.toolDisabledNoTest')}</p>
            )}
            {testResult && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">{t('detail.testResults')}</label>
                <div className={`rounded-lg border p-4 ${testResult.success ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
                  <div className="flex items-center mb-2">
                    {testResult.success
                      ? <CheckCircle className="w-4 h-4 text-green-600 mr-2" />
                      : <XCircle className="w-4 h-4 text-red-600 mr-2" />}
                    <span className={`text-sm font-medium ${testResult.success ? 'text-green-800' : 'text-red-800'}`}>
                      {testResult.success ? t('detail.testSuccess') : t('detail.testFailed')}
                    </span>
                  </div>
                  <pre className="text-xs overflow-auto max-h-48 whitespace-pre-wrap break-all text-gray-700 bg-white/60 rounded p-2">
                    {testResult.success
                      ? JSON.stringify(testResult.data, null, 2)
                      : testResult.error}
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

// ============================================================================
// MCP Credentials Card Component
// ============================================================================

function MCPCredentialsCard({ serverName }: { serverName: string }) {
  const { t, i18n } = useTranslation('tool');
  const [credentials, setCredentials] = useState<MCPCredentials | null>(null);
  const [editing, setEditing] = useState(false);
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] = useState<{success: boolean; message: string; latency_ms?: number} | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadCredentials();
  }, [serverName]);

  const loadCredentials = async () => {
    try {
      setLoading(true);
      const res = await mcpAPI.getCredentials(serverName);
      setCredentials(res.data);
    } catch (err) {
      console.error('Failed to load credentials:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      if (!apiKey) {
        alert(t('alert.apiKeyRequired'));
        return;
      }

      await mcpAPI.setCredentials(serverName, { api_key: apiKey });
      alert(t('alert.credSaved'));
      setEditing(false);
      setApiKey('');
      await loadCredentials();
    } catch (err: any) {
      alert(t('alert.saveFailed', { error: err.message || t('alert.unknownError') }));
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    try {
      const res = await mcpAPI.testCredentials(serverName);
      setTestResult(res.data);
    } catch (err: any) {
      setTestResult({
        success: false,
        message: t('alert.testFailed', { error: err.message || t('alert.unknownError') })
      });
    }
  };

  const handleDelete = async () => {
    if (!confirm(t('alert.confirmDeleteCred'))) return;
    
    try {
      await mcpAPI.deleteCredentials(serverName);
      alert(t('alert.credDeleted'));
      await loadCredentials();
    } catch (err: any) {
      alert(t('alert.deleteFailed', { error: err.message || t('alert.unknownError') }));
    }
  };

  if (loading) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex justify-center py-4">
          <LoadingSpinner />
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold text-gray-900 flex items-center">
          <Shield className="w-4 h-4 mr-2 text-gray-500" />
          {t('credentials.title')}
        </h4>
        {credentials?.has_credential && !editing && (
          <button
            onClick={handleDelete}
            className="text-xs text-slate-600 hover:text-slate-800"
          >
            {t('credentials.delete')}
          </button>
        )}
      </div>

      {!editing ? (
        <div className="space-y-3">
          {credentials?.has_credential && credentials.secret_id && (
            <div className="flex items-center justify-between">
              <span className="text-sm text-gray-500">Secret ID</span>
              <code className="text-sm font-mono bg-gray-100 px-2 py-1 rounded">
                {credentials.secret_id}
              </code>
            </div>
          )}
          
          {credentials?.api_key_masked && (
            <div className="flex items-center justify-between">
              <span className="text-sm text-gray-500">API Key</span>
              <code className="text-sm font-mono bg-gray-100 px-2 py-1 rounded">
                {credentials.api_key_masked}
              </code>
            </div>
          )}

          <div className="flex gap-2 pt-2">
            <button
              onClick={() => setEditing(true)}
              className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-700 hover:bg-gray-50"
            >
              {credentials?.has_credential ? t('credentials.modify') : t('credentials.configure')}
            </button>
            {credentials?.has_credential && (
              <button
                onClick={handleTest}
                className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-700 hover:bg-gray-50"
              >
                {t('button.test')}
              </button>
            )}
          </div>

          {testResult && (
            <div className={`p-2.5 rounded text-xs ${
              testResult.success 
                ? 'bg-green-50 border border-green-200 text-green-700' 
                : 'bg-red-50 border border-red-200 text-red-700'
            }`}>
              <div className="flex items-start">
                {testResult.success ? (
                  <CheckCircle className="w-3.5 h-3.5 mr-1.5 mt-0.5 flex-shrink-0" />
                ) : (
                  <XCircle className="w-3.5 h-3.5 mr-1.5 mt-0.5 flex-shrink-0" />
                )}
                <div className="flex-1">
                  <div>{testResult.message}</div>
                  {testResult.latency_ms != null && (
                    <div className="mt-1 opacity-75">{t('credentials.latency', { latency: testResult.latency_ms })}</div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1.5">API Key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={t('credentials.enterKey')}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-slate-400"
            />
          </div>

          <div className="flex gap-2 pt-2">
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex-1 px-3 py-2 bg-slate-700 text-white rounded-lg text-sm disabled:opacity-50 hover:bg-slate-800"
            >
              {saving ? t('credentials.saving') : t('credentials.save')}
            </button>
            <button
              onClick={() => {
                setEditing(false);
                setApiKey('');
              }}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-700 hover:bg-gray-50"
            >
              {t('credentials.cancel')}
            </button>
          </div>

          <div className="p-2 bg-amber-50 border border-amber-200 rounded text-xs text-amber-700">
            <div className="flex items-start">
              <Info className="w-3.5 h-3.5 mr-1.5 mt-0.5 flex-shrink-0" />
              <div>{t('credentials.storageNote')}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Helper: format duration from timestamp
// ============================================================================

function formatDuration(connectedAt: number, t: (key: string, opts?: Record<string, unknown>) => string): string {
  const now = Date.now() / 1000;
  const diff = now - connectedAt;
  if (diff < 60) return t('duration.seconds', { count: Math.floor(diff) });
  if (diff < 3600) return t('duration.minutes', { count: Math.floor(diff / 60) });
  if (diff < 86400) return t('duration.hours', { count: Math.floor(diff / 3600) });
  return t('duration.days', { count: Math.floor(diff / 86400) });
}

// ============================================================================
// API Tab Content
// ============================================================================

function LegacyAPITabContent({
  tools,
  onSelectTool,
  onRefreshTools,
  catalogEntries,
  catalogCategories,
  catalogLoading,
  configuredIds,
  onConfiguredChange,
}: {
  tools: Tool[];
  onSelectTool: (tool: Tool) => void;
  onRefreshTools: () => Promise<void>;
  catalogEntries: MCPCatalogEntry[];
  catalogCategories: Record<string, MCPCatalogCategory>;
  catalogLoading: boolean;
  configuredIds: Set<string>;
  onConfiguredChange: (id: string) => void;
}) {
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

  const modules = Object.keys(toolsByModule).sort();
  const [selectedModule, setSelectedModule] = useState<string | null>(null);

  const [serviceStatuses, setServiceStatuses] = useState<Record<string, { status: string; message?: string; latency_ms?: number; checked_at?: number }>>({});

  const fetchStatuses = useCallback(async () => {
    try {
      const res = await providerAPI.getApiServiceStatuses();
      if (res.data) setServiceStatuses(res.data);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchStatuses(); }, [fetchStatuses]);

  const [apiStatuses, setApiStatuses] = useState<Record<string, { status: string; latency_ms?: number }>>({});

  const [testingServices, setTestingServices] = useState<Set<string>>(new Set());

  const handleTestingStart = useCallback((serviceName: string) => {
    setTestingServices((prev) => new Set(prev).add(serviceName));
  }, []);

  const handleTestingEnd = useCallback((serviceName: string) => {
    setTestingServices((prev) => { const next = new Set(prev); next.delete(serviceName); return next; });
    fetchStatuses();
  }, [fetchStatuses]);

  const refreshStatuses = useCallback(async () => {
    const newStatuses: Record<string, { status: string; latency_ms?: number }> = {};
    for (const mod of modules) {
      try {
        const res = await client.get(`/api/api-services/${mod}/status`);
        newStatuses[mod] = res.data;
      } catch {
        newStatuses[mod] = { status: 'unknown' };
      }
    }
    setApiStatuses(newStatuses);
  }, [modules]);

  useEffect(() => {
    if (modules.length > 0) refreshStatuses();
  }, [refreshStatuses]);

  const selectedModuleTools = selectedModule ? toolsByModule[selectedModule] : [];

  // Catalog filtering
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [installing, setInstalling] = useState<string | null>(null);

  // Credential modal state
  const [credModalEntry, setCredModalEntry] = useState<MCPCatalogEntry | null>(null);
  const [credValues, setCredValues] = useState<Record<string, string>>({});

  const isConfigured = useCallback((entryId: string) => configuredIds.has(entryId), [configuredIds]);

  const PRIORITY_IDS = useMemo(() => new Set(['virustotal_mcp', 'urlhaus']), []);

  // Catalog entries not yet active (not in modules list)
  const inactiveCatalogEntries = useMemo(() => {
    const activeModuleIds = new Set(modules);
    return catalogEntries.filter(e => !activeModuleIds.has(e.id));
  }, [catalogEntries, modules]);

  const filteredCatalog = useMemo(() => {
    let result = [...inactiveCatalogEntries];
    if (selectedCategory !== 'all') {
      result = result.filter(e => e.category === selectedCategory);
    }
    result.sort((a, b) => {
      const pa = PRIORITY_IDS.has(a.id) ? 0 : 1;
      const pb = PRIORITY_IDS.has(b.id) ? 0 : 1;
      if (pa !== pb) return pa - pb;
      const ca = isConfigured(a.id) ? 0 : 1;
      const cb = isConfigured(b.id) ? 0 : 1;
      if (ca !== cb) return ca - cb;
      return b.stars - a.stars;
    });
    return result;
  }, [inactiveCatalogEntries, selectedCategory, PRIORITY_IDS, isConfigured]);

  const catalogCategoryCounts = useMemo(() => {
    const counts: Record<string, number> = { all: inactiveCatalogEntries.length };
    for (const e of inactiveCatalogEntries) {
      counts[e.category] = (counts[e.category] || 0) + 1;
    }
    return counts;
  }, [inactiveCatalogEntries]);

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
    const hasEmpty = Object.entries(credValues).some(([, v]) => !v.trim());
    if (hasEmpty) { alert(t('alert.fillAllRequired')); return; }
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
      alert(t('alert.configFailed', { error: err.response?.data?.detail || err.message }));
      setInstalling(null);
      return;
    }
    try {
      if (shouldConnect) {
        await mcpAPI.connect(entryId);
      }
      await onRefreshTools();
      fetchStatuses();
      if (!shouldConnect) {
        alert(t('alert.mcpConfiguredDisabled', { name: entryName }));
      }
    } catch (err: any) {
      alert(t('alert.connectFailed', { error: err.response?.data?.detail || err.message }));
    } finally {
      setInstalling(null);
    }
  };

  const handleInstallNoAuth = async (entry: MCPCatalogEntry) => {
    let shouldConnect = false;
    let installRes: Awaited<ReturnType<typeof mcpAPI.catalogInstall>> | null = null;
    try {
      setInstalling(entry.id);
      // Ensure the entry is in flocks.json (idempotent if auto_setup already ran)
      installRes = await mcpAPI.catalogInstall(entry.id);
      shouldConnect = installRes.data?.config?.enabled !== false;
      onConfiguredChange(entry.id);
    } catch (err: any) {
      alert(t('alert.configFailed', { error: err.response?.data?.detail || err.message }));
      setInstalling(null);
      return;
    }
    try {
      if (shouldConnect) {
        await mcpAPI.connect(entry.id);
      }
      await onRefreshTools();
      fetchStatuses();
      if (!shouldConnect) {
        alert(t('alert.mcpConfiguredDisabled', { name: entry.name }));
      }
    } catch (err: any) {
      alert(t('alert.connectFailed', { error: err.response?.data?.detail || err.message }));
    } finally {
      setInstalling(null);
    }
  };

  return (
    <div className="space-y-4">
      {/* Category pills */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <button
          onClick={() => setSelectedCategory('all')}
          className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${selectedCategory === 'all' ? 'bg-purple-50 text-purple-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
        >{t('api.filterAll')}</button>
        {Object.entries(catalogCategories).map(([id, cat]) =>
          catalogCategoryCounts[id] ? (
            <button key={id} onClick={() => setSelectedCategory(id)} className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${selectedCategory === id ? 'bg-purple-50 text-purple-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}>
              {cat.label} ({catalogCategoryCounts[id]})
            </button>
          ) : null
        )}
      </div>

      {modules.length === 0 && filteredCatalog.length === 0 ? (
        <EmptyState icon={<Cloud className="w-16 h-16" />} title={t('api.noTools')} description={t('api.noToolsDesc')} />
      ) : (
        <div className="grid gap-4 grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {modules.map((moduleName) => {
            const moduleTools = toolsByModule[moduleName];
            const isSelected = selectedModule === moduleName;
            const svcStatus = apiStatuses[moduleName]?.status;
            const borderColor = svcStatus === 'healthy' ? 'border-l-green-500' : svcStatus === 'error' ? 'border-l-red-500' : 'border-l-gray-300';
            return (
              <div
                key={moduleName}
                onClick={() => setSelectedModule(isSelected ? null : moduleName)}
                className={`relative bg-white rounded-xl border border-l-4 ${borderColor} overflow-hidden cursor-pointer h-[180px] flex flex-col transition-all duration-150 ${isSelected ? 'border-slate-400 shadow-md ring-2 ring-slate-200' : 'border-gray-200 shadow-sm hover:shadow-md hover:border-gray-300'}`}
              >
                <div className="flex-1 px-4 pt-4 pb-2 min-h-0 flex flex-col gap-1.5">
                  <div className="flex items-start gap-1.5 flex-wrap">
                    <span className="text-sm font-semibold text-gray-900 truncate max-w-[120px] capitalize">{moduleName}</span>
                    <span className="px-1.5 py-0.5 text-xs font-medium rounded-full shrink-0 bg-green-100 text-green-700">
                      {t('statusBadge.active')}
                    </span>
                    <span className="px-1.5 py-0.5 bg-purple-100 text-purple-700 text-xs font-medium rounded-full shrink-0">API</span>
                  </div>
                  <div className="flex items-center gap-1 text-xs text-gray-500 mt-auto">
                    <Wrench className="w-3 h-3 shrink-0" /><span>{moduleTools.length} {t('api.tools')}</span>
                    {apiStatuses[moduleName]?.latency_ms != null && (
                      <span className="ml-auto text-[10px] text-gray-400">{apiStatuses[moduleName].latency_ms}ms</span>
                    )}
                  </div>
                </div>
                <div className="border-t border-gray-100 px-4 py-2 flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                  <button onClick={() => setSelectedModule(isSelected ? null : moduleName)} className={`flex-1 flex items-center justify-center gap-1 py-1 px-2 border rounded-lg text-xs font-medium transition-colors ${isSelected ? 'border-purple-300 text-purple-700 bg-purple-50' : 'border-gray-300 text-gray-700 hover:bg-gray-50'}`}>
                    <Settings className="w-3 h-3" /> {t('api.manage')}
                  </button>
                </div>
              </div>
            );
          })}

          {/* Catalog entries */}
          {filteredCatalog.map((entry) => {
            const isSelected = selectedModule === entry.id;

            return (
              <div
                key={`catalog-${entry.id}`}
                className={`relative bg-white rounded-xl border overflow-hidden cursor-default h-[180px] flex flex-col transition-all duration-150 ${isSelected ? 'border-slate-400 shadow-md ring-2 ring-slate-200' : 'border-gray-200 shadow-sm'}`}
                style={{ borderLeftWidth: 4, borderLeftColor: '#9CA3AF' }}
              >
                <div className="flex-1 px-4 pt-4 pb-2 min-h-0 flex flex-col gap-1.5">
                  <div className="flex items-start gap-1.5 flex-wrap">
                    <span className="text-sm font-semibold text-gray-900 truncate max-w-[120px]">{entry.name}</span>
                    <span className="px-1.5 py-0.5 text-xs font-medium rounded-full shrink-0 bg-gray-100 text-gray-600">
                      {t('statusBadge.inactive')}
                    </span>
                    <span className="px-1.5 py-0.5 bg-purple-100 text-purple-700 text-xs font-medium rounded-full shrink-0">API</span>
                    {PRIORITY_IDS.has(entry.id) && (
                      <span className="px-1.5 py-0.5 bg-amber-100 text-amber-800 text-xs font-medium rounded-full shrink-0">{t('api.recommended')}</span>
                    )}
                  </div>
                  <p className="text-xs text-gray-500 line-clamp-2 leading-relaxed">{getCatalogDescription(entry, i18n.language)}</p>
                  <div className="flex items-center gap-2 text-xs text-gray-500 mt-auto">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${LANG_COLORS[entry.language] || 'bg-gray-100 text-gray-600'}`}>{entry.language}</span>
                    <span className="flex items-center gap-0.5"><Star className="w-3 h-3" />{entry.stars}</span>
                  </div>
                </div>
                <div className="border-t border-gray-100 px-4 py-2 flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => {
                      if (entry.requires_auth && !isConfigured(entry.id)) {
                        openCredModal(entry);
                      } else {
                        handleInstallNoAuth(entry);
                      }
                    }}
                    disabled={installing === entry.id}
                    className="flex-1 flex items-center justify-center gap-1 py-1 px-2 bg-slate-700 text-white rounded-lg text-xs font-medium hover:bg-slate-800 disabled:opacity-50 transition-colors"
                  >
                    <Download className="w-3 h-3" />
                    {installing === entry.id ? t('api.configuring') : t('button.install')}
                  </button>
                  <a
                    href={`https://github.com/${entry.github}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center justify-center w-7 h-7 border border-gray-300 text-gray-500 rounded-lg hover:bg-gray-50 transition-colors"
                    title="GitHub"
                  >
                    <ExternalLink className="w-3.5 h-3.5" />
                  </a>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Credential Input Modal */}
      {credModalEntry && (
        <>
          <div className="fixed inset-0 bg-black/40 z-40" onClick={() => setCredModalEntry(null)} />
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md" onClick={(e) => e.stopPropagation()}>
              <div className="px-6 py-4 border-b border-gray-200">
                <h3 className="text-lg font-semibold text-gray-900">{credModalEntry.name}</h3>
                <p className="text-sm text-gray-500 mt-1">{t('credentials.configNote')}</p>
              </div>
              <div className="px-6 py-4 space-y-4">
                {Object.entries(credModalEntry.env_vars).filter(([, spec]) => spec.secret).map(([key, spec]) => (
                  <div key={key}>
                    <label className="block text-sm font-medium text-gray-700 mb-1">{key}</label>
                    <p className="text-xs text-gray-500 mb-1.5">{spec.description}</p>
                    <input
                      type="password"
                      value={credValues[key] || ''}
                      onChange={(e) => setCredValues(prev => ({ ...prev, [key]: e.target.value }))}
                      placeholder={t('credentials.enterField', { field: key })}
                      className="w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                    />
                  </div>
                ))}
              </div>
              <div className="px-6 py-4 border-t border-gray-200 flex gap-3 justify-end">
                <button onClick={() => setCredModalEntry(null)} className="px-4 py-2 text-sm text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors">{t('button.cancel')}</button>
                <button
                  onClick={handleCredSubmit}
                  disabled={installing === credModalEntry.id}
                  className="px-4 py-2 text-sm text-white bg-purple-600 rounded-lg hover:bg-purple-700 disabled:opacity-50 transition-colors"
                >
                  {installing === credModalEntry.id ? t('api.configuring') : t('button.confirmConfig')}
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {/* API Service Detail Drawer */}
      {selectedModule && (() => {
        const catalogEntry = catalogEntries.find(e => e.id === selectedModule);
        const displayName = catalogEntry?.name || selectedModule;
        const isCatalogOnly = !!catalogEntry && !modules.includes(selectedModule);
        return (
          <>
            <div className="fixed inset-0 bg-black/40 z-40" onClick={() => setSelectedModule(null)} />
            <div className="fixed right-0 top-0 bottom-0 z-50 flex flex-col w-full bg-white shadow-2xl" style={{ maxWidth: DETAIL_DRAWER_WIDTH }} onClick={(e) => e.stopPropagation()}>
              <div className="flex-shrink-0 border-b border-gray-200">
                <div className="flex items-center gap-3 px-6 py-4">
                  <div className="w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0 bg-purple-50">
                    <Cloud className="w-5 h-5 text-purple-600" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h2 className="text-lg font-semibold text-gray-900">{displayName}</h2>
                    <p className="text-sm text-gray-500">{t('api.serviceConfig')}</p>
                  </div>
                  <button onClick={() => setSelectedModule(null)} className="text-gray-400 hover:text-gray-600 p-2 rounded-lg hover:bg-gray-100 flex-shrink-0">
                    <X className="w-5 h-5" />
                  </button>
                </div>
              </div>
              <div className="flex-1 overflow-y-auto">
                {catalogEntry && (
                  <div className="px-6 py-4 bg-gray-50 border-b border-gray-200">
                    <p className="text-sm text-gray-600">{getCatalogDescription(catalogEntry, i18n.language)}</p>
                  </div>
                )}
                {isCatalogOnly ? (
                  <CatalogAPIDetailPanel
                    entry={catalogEntry}
                    catalogCategories={catalogCategories}
                  />
                ) : (
                  <APIServiceDetailPanel
                    serviceName={selectedModule}
                    serviceTools={selectedModuleTools}
                    onSelectTool={onSelectTool}
                    onTestingStart={() => handleTestingStart(selectedModule)}
                    onTestingEnd={() => handleTestingEnd(selectedModule)}
                    initialStatus={apiStatuses[selectedModule]}
                    onTestResult={(name, result) => setApiStatuses(prev => ({ ...prev, [name]: result }))}
                  />
                )}
              </div>
            </div>
          </>
        );
      })()}
    </div>
  );
}

// ============================================================================
// Catalog API Detail Panel (for catalog-only entries with test connectivity)
// ============================================================================

function CatalogAPIDetailPanel({
  entry,
  catalogCategories,
}: {
  entry: MCPCatalogEntry;
  catalogCategories: Record<string, MCPCatalogCategory>;
}) {
  const { t, i18n } = useTranslation('tool');
  const [testing, setTesting] = useState(false);
  const [testElapsed, setTestElapsed] = useState(0);
  const [testResult, setTestResult] = useState<{success: boolean; message: string; latency_ms?: number; tool_tested?: string} | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
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
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
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
          {/* Left: Service Info */}
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
                  <a href={`https://github.com/${entry.github}`} target="_blank" rel="noopener noreferrer" className="text-sm text-sky-700 hover:text-sky-900">{entry.github}</a>
                </div>
                {entry.requires_auth && (
                  <div className="pt-2 border-t border-gray-100">
                    <span className="text-sm text-gray-500 block mb-2">{t('serviceInfo.requiredKeys')}</span>
                    {Object.entries(entry.env_vars).filter(([,s]) => s.secret).map(([key, spec]) => (
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
                      {entry.tags.map((tag: string) => (
                        <span key={tag} className="px-2 py-0.5 bg-gray-100 text-gray-700 text-xs rounded">#{tag}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Right: Quick Actions & Test */}
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
                  className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 border rounded-lg text-sm font-medium transition-colors ${
                    testing ? 'border-red-300 bg-red-50 text-red-600 cursor-not-allowed' : 'bg-white border-gray-300 text-gray-700 hover:bg-gray-50'
                  }`}
                >
                  {testing ? (
                    <><RefreshCw className="w-4 h-4 animate-spin" />{t('button.testing')}</>
                  ) : (
                    <><Activity className="w-4 h-4" />{t('button.testConnectivity')}</>
                  )}
                </button>
                <button
                  onClick={handleConnect}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-slate-700 text-white rounded-lg text-sm font-medium hover:bg-slate-800 transition-colors"
                >
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
                  {testResult.success ? (
                    <CheckCircle className="w-4 h-4 text-green-600 mr-2 mt-0.5 flex-shrink-0" />
                  ) : (
                    <XCircle className="w-4 h-4 text-red-600 mr-2 mt-0.5 flex-shrink-0" />
                  )}
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

// ============================================================================
// KvRow helper
// ============================================================================

function KvRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center py-2.5 px-4 gap-4">
      <span className="text-sm text-gray-500 shrink-0">{label}</span>
      <span className="text-sm text-gray-900 truncate text-right">{value}</span>
    </div>
  );
}

// ============================================================================
// API Service Detail Panel (cjtools-style with KvRow + inline API key)
// ============================================================================

function APIServiceDetailPanel({
  serviceName,
  serviceTools,
  onSelectTool,
  onTestingStart,
  onTestingEnd,
  initialStatus,
  onTestResult,
}: {
  serviceName: string;
  serviceTools: Tool[];
  onSelectTool: (tool: Tool) => void;
  onTestingStart?: () => void;
  onTestingEnd?: () => void;
  initialStatus?: { status: string; latency_ms?: number };
  onTestResult?: (name: string, result: { status: string; latency_ms?: number }) => void;
}) {
  const { t, i18n } = useTranslation('tool');
  const [detailTab, setDetailTab] = useState<'overview' | 'tools'>('overview');
  const [metadata, setMetadata] = useState<any>(null);
  const [metadataLoading, setMetadataLoading] = useState(true);
  const [credentials, setCredentials] = useState<any>(null);

  const [apiKeyInput, setApiKeyInput] = useState('');
  const [showApiKey, setShowApiKey] = useState(false);
  const [apiKeySaving, setApiKeySaving] = useState(false);
  const apiKeyOrigRef = useRef('');

  const [quickTesting, setQuickTesting] = useState(false);
  const [quickTestResult, setQuickTestResult] = useState<{success: boolean; message: string; latency_ms?: number; tool_tested?: string} | null>(null);
  const quickTestTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (quickTestTimerRef.current) clearInterval(quickTestTimerRef.current);
    };
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
      setMetadata(meta);
      const cred = credRes.status === 'fulfilled' ? credRes.value.data : null;
      setCredentials(cred);
      const fullKey = cred?.api_key || '';
      setApiKeyInput(fullKey);
      apiKeyOrigRef.current = fullKey;
    } catch (err) {
      console.error('Failed to load API service data:', err);
      setMetadata(null);
    } finally {
      setMetadataLoading(false);
    }
  }, [serviceName]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleSaveApiKey = async () => {
    if (!apiKeyInput.trim()) return;
    setApiKeySaving(true);
    try {
      await providerAPI.setServiceCredentials(serviceName, { api_key: apiKeyInput.trim() });
      const credRes = await providerAPI.getServiceCredentials(serviceName);
      const cred = credRes.data;
      setCredentials(cred);
      const savedKey = cred?.api_key || '';
      setApiKeyInput(savedKey);
      apiKeyOrigRef.current = savedKey;
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
            className={`flex items-center gap-1.5 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
              detailTab === tab.key
                ? 'border-purple-500 text-purple-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
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
                  healthy:   { label: t('statusBadge.connected'), cls: 'bg-green-100 text-green-800', dot: 'bg-green-500' },
                  testing:   { label: t('button.testing'), cls: 'bg-amber-100 text-amber-900', dot: 'bg-amber-500 animate-pulse' },
                  error:     { label: t('statusBadge.error'), cls: 'bg-red-100 text-red-800', dot: 'bg-red-500' },
                  unknown:   { label: t('statusBadge.unknown'), cls: 'bg-gray-100 text-gray-600', dot: 'bg-gray-400' },
                };
                const c = cfg[status] || cfg.unknown;
                return (
                  <div className="flex justify-between items-center py-2.5 px-4 gap-4">
                    <span className="text-sm text-gray-500 shrink-0">{t('detail.connectionStatus')}</span>
                    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${c.cls}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />
                      {c.label}
                      {quickTestResult?.success && quickTestResult.latency_ms != null && (
                        <span className="opacity-75">{'\u00b7'} {quickTestResult.latency_ms}ms</span>
                      )}
                    </span>
                  </div>
                );
              })()}
              <KvRow label={t('serviceInfo.name')} value={metadata?.name || serviceName} />
              {metadata?.version && <KvRow label={t('serviceInfo.version')} value={metadata.version} />}
              {getMetadataDescription(metadata, i18n.language) && (
                <KvRow label={t('serviceInfo.description')} value={getMetadataDescription(metadata, i18n.language)} />
              )}
              <KvRow label={t('serviceInfo.type')} value={t('serviceInfo.apiType')} />
              {metadata?.category && <KvRow label={t('serviceInfo.category')} value={metadata.category} />}
              {metadata?.docs_url && (
                <div className="flex justify-between items-center py-2.5 px-4 gap-4">
                  <span className="text-sm text-gray-500 shrink-0">{t('serviceInfo.documentation')}</span>
                  <a href={metadata.docs_url} target="_blank" rel="noopener noreferrer" className="text-sm text-sky-700 hover:text-sky-900">
                    {t('serviceInfo.viewDocs')} →
                  </a>
                </div>
              )}
              {credentials?.secret_id && (
                <div className="flex justify-between items-center py-2.5 px-4 gap-4">
                  <span className="text-sm text-gray-500 shrink-0">Secret ID</span>
                  <code className="text-sm font-mono bg-gray-100 px-2 py-1 rounded truncate">{credentials.secret_id}</code>
                </div>
              )}
              {credentials !== null && (
                <div className="flex items-center py-2 px-4 gap-3">
                  <span className="text-sm text-gray-500 shrink-0">API Key</span>
                  <div className="flex-1 flex items-center gap-1.5 min-w-0">
                    <input
                      type={showApiKey ? 'text' : 'password'}
                      value={apiKeyInput}
                      onChange={(e) => setApiKeyInput(e.target.value)}
                      placeholder={t('serviceInfo.enterApiKey')}
                      className="flex-1 min-w-0 px-2 py-1 border border-gray-200 rounded text-sm font-mono bg-gray-50 focus:outline-none focus:ring-1 focus:ring-slate-400 focus:bg-white focus:border-slate-400 transition-colors"
                    />
                    <button
                      type="button"
                      onClick={() => setShowApiKey(v => !v)}
                      className="shrink-0 text-gray-400 hover:text-gray-600 p-1 rounded transition-colors"
                      title={showApiKey ? t('detail.hide') : t('detail.show')}
                    >
                      {showApiKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                    </button>
                    {apiKeyInput !== apiKeyOrigRef.current && (
                      <>
                        <button
                          type="button"
                          onClick={handleSaveApiKey}
                          disabled={apiKeySaving || !apiKeyInput.trim()}
                          className="shrink-0 inline-flex items-center gap-1 px-2 py-1 bg-slate-700 text-white rounded text-xs font-medium hover:bg-slate-800 disabled:opacity-50 transition-colors"
                        >
                          {apiKeySaving ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                          {t('button.save')}
                        </button>
                        <button
                          type="button"
                          onClick={() => setApiKeyInput(apiKeyOrigRef.current)}
                          className="shrink-0 px-2 py-1 border border-gray-300 text-gray-600 rounded text-xs hover:bg-gray-50 transition-colors"
                        >
                          {t('button.cancel')}
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )}
              <KvRow label={t('serviceInfo.toolCount')} value={String(serviceTools.length)} />
            </div>

            <div className="flex flex-col gap-2">
              <button
                onClick={handleQuickTestConnectivity}
                disabled={quickTesting}
                className="inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-white border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 disabled:opacity-50 text-sm font-medium transition-colors"
              >
                {quickTesting ? <><RefreshCw className="w-4 h-4 animate-spin" />{t('button.testing')}</> : <><Activity className="w-4 h-4" />{t('button.testConnectivity')}</>}
              </button>
              {!quickTesting && quickTestResult && (
                <div className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${quickTestResult.success ? 'bg-green-50 border-green-200 text-green-800' : 'bg-red-50 border-red-200 text-red-800'}`}>
                  {quickTestResult.success ? <CheckCircle className="w-4 h-4 mt-0.5 flex-shrink-0" /> : <XCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />}
                  <span>{quickTestResult.message}</span>
                  {quickTestResult.latency_ms != null && <span className="text-xs opacity-90"> {'\u00b7'} {quickTestResult.latency_ms}ms</span>}
                </div>
              )}
            </div>
          </div>
        ) : (
          serviceTools.length === 0 ? (
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
                        <td className="px-5 py-2.5 whitespace-nowrap"><button onClick={() => onSelectTool(tool)} className="text-sm text-sky-700 hover:text-sky-900">{t('detail.testDetail')}</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )
        )}
      </div>
    </div>
  );
}

// ============================================================================
// Provider Credentials Card Component (similar to MCP)
// ============================================================================

function ProviderCredentialsCard({ providerId, onTestingStart, onTestingEnd }: { providerId: string; onTestingStart?: () => void; onTestingEnd?: () => void }) {
  const { t } = useTranslation('tool');
  const [credentials, setCredentials] = useState<any | null>(null);
  const [editing, setEditing] = useState(false);
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testElapsed, setTestElapsed] = useState(0);
  const [testResult, setTestResult] = useState<{success: boolean; message: string; latency_ms?: number; tool_tested?: string} | null>(null);
  const [loading, setLoading] = useState(true);
  const testTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    loadCredentials();
  }, [providerId]);

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (testTimerRef.current) clearInterval(testTimerRef.current);
    };
  }, []);

  const loadCredentials = async () => {
    try {
      setLoading(true);
      const res = await providerAPI.getCredentials(providerId);
      setCredentials(res.data);
    } catch (err) {
      console.error('Failed to load provider credentials:', err);
      setCredentials(null);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      if (!apiKey) {
        alert(t('alert.apiKeyRequired'));
        return;
      }

      await providerAPI.setCredentials(providerId, {
        api_key: apiKey,
      });
      alert(t('alert.credSaved'));
      setEditing(false);
      setApiKey('');
      await loadCredentials();
    } catch (err: any) {
      alert(t('alert.saveFailed', { error: err.message || t('alert.unknownError') }));
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    setTestElapsed(0);
    onTestingStart?.();

    // Start an elapsed-time counter (updates every 100ms)
    const startTime = Date.now();
    testTimerRef.current = setInterval(() => {
      setTestElapsed(((Date.now() - startTime) / 1000));
    }, 100);

    try {
      const res = await providerAPI.testCredentials(providerId);
      setTestResult(res.data);
    } catch (err: any) {
      setTestResult({
        success: false,
        message: t('alert.testFailed', { error: err.message || t('alert.unknownError') })
      });
    } finally {
      // Stop timer
      if (testTimerRef.current) {
        clearInterval(testTimerRef.current);
        testTimerRef.current = null;
      }
      setTesting(false);
      // Notify parent: testing ended -> refresh status
      onTestingEnd?.();
    }
  };

  const handleDelete = async () => {
    if (!confirm(t('alert.confirmDeleteCred'))) return;
    
    try {
      await providerAPI.deleteCredentials(providerId);
      alert(t('alert.credDeleted'));
      await loadCredentials();
    } catch (err: any) {
      alert(t('alert.deleteFailed', { error: err.message || t('alert.unknownError') }));
    }
  };

  if (loading) {
    return (
      <div className="bg-white rounded-lg border border-gray-200 p-4">
        <div className="flex justify-center py-4">
          <LoadingSpinner />
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold text-gray-900 flex items-center">
          <Shield className="w-4 h-4 mr-2 text-gray-500" />
          {t('credentials.title')}
        </h4>
        {credentials?.has_credential && !editing && (
          <button
            onClick={handleDelete}
            className="text-xs text-slate-600 hover:text-slate-800"
          >
            {t('credentials.delete')}
          </button>
        )}
      </div>

      {!editing ? (
        <div className="space-y-3">
          {credentials?.has_credential && credentials.secret_id && (
            <div className="flex items-center justify-between">
              <span className="text-sm text-gray-500">Secret ID</span>
              <code className="text-sm font-mono bg-gray-100 px-2 py-1 rounded">
                {credentials.secret_id}
              </code>
            </div>
          )}
          
          {credentials?.api_key_masked && (
            <div className="flex items-center justify-between">
              <span className="text-sm text-gray-500">API Key</span>
              <code className="text-sm font-mono bg-gray-100 px-2 py-1 rounded">
                {credentials.api_key_masked}
              </code>
            </div>
          )}

          <div className="flex gap-2 pt-2">
            <button
            onClick={() => setEditing(true)}
            className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-700 hover:bg-gray-50"
          >
            {credentials?.has_credential ? t('credentials.modify') : t('credentials.configure')}
          </button>
            {credentials?.has_credential && (
              <button
                onClick={handleTest}
                disabled={testing}
                className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 border rounded-lg text-sm transition-colors ${
                  testing
                    ? 'border-red-300 bg-red-50 text-red-600 cursor-not-allowed'
                    : 'border-gray-300 text-gray-700 hover:bg-gray-50'
                }`}
                title={t('credentials.testConnTitle')}
              >
                {testing ? (
                  <><RefreshCw className="w-3.5 h-3.5 animate-spin" />{t('credentials.testing')}</>
                ) : (
                  <><Activity className="w-3.5 h-3.5" />{t('credentials.testConn')}</>
                )}
              </button>
            )}
          </div>

          {/* Testing in-progress indicator */}
          {testing && (
            <div className="p-3 rounded-lg border border-red-200 bg-red-50 text-red-700">
              <div className="flex items-center">
                <div className="relative mr-3">
                  <div className="w-8 h-8 rounded-full border-2 border-red-200 border-t-red-600 animate-spin" />
                </div>
                <div className="flex-1">
                  <div className="text-sm font-medium">{t('credentials.testingProgress')}</div>
                  <div className="text-xs mt-0.5 text-red-600">
                    {t('credentials.testingWait')}
                    <span className="ml-2 font-mono tabular-nums">{testElapsed.toFixed(1)}s</span>
                  </div>
                </div>
              </div>
              {/* Progress bar animation */}
              <div className="mt-2.5 h-1 bg-red-100 rounded-full overflow-hidden">
                <div className="h-full bg-red-500 rounded-full animate-pulse" style={{ width: `${Math.min(95, testElapsed * 8)}%`, transition: 'width 0.3s ease-out' }} />
              </div>
            </div>
          )}

          {/* Test result */}
          {!testing && testResult && (
            <div className={`p-2.5 rounded text-xs ${
              testResult.success 
                ? 'bg-green-50 border border-green-200 text-green-700' 
                : 'bg-red-50 border border-red-200 text-red-700'
            }`}>
              <div className="flex items-start">
                {testResult.success ? (
                  <CheckCircle className="w-3.5 h-3.5 mr-1.5 mt-0.5 flex-shrink-0" />
                ) : (
                  <XCircle className="w-3.5 h-3.5 mr-1.5 mt-0.5 flex-shrink-0" />
                )}
                <div className="flex-1">
                  <div>{testResult.message}</div>
                  {testResult.latency_ms != null && (
                    <div className="mt-1 opacity-75">{t('credentials.latency')}: {testResult.latency_ms}ms</div>
                  )}
                  {testResult.tool_tested && (
                    <div className="mt-1 opacity-75 text-xs">{t('credentials.toolTested')}: {testResult.tool_tested}</div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1.5">API Key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={t('credentials.apiKeyPlaceholder')}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-purple-500"
            />
          </div>

          <div className="flex gap-2 pt-2">
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex-1 px-3 py-2 bg-purple-600 text-white rounded-lg text-sm disabled:opacity-50 hover:bg-purple-700"
            >
              {saving ? t('credentials.saving') : t('credentials.save')}
            </button>
            <button
              onClick={() => {
                setEditing(false);
                setApiKey('');
              }}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm text-gray-700 hover:bg-gray-50"
            >
              {t('common:button.cancel')}
            </button>
          </div>

          <div className="p-2 bg-amber-50 border border-amber-200 rounded text-xs text-amber-700">
            <div className="flex items-start">
              <Info className="w-3.5 h-3.5 mr-1.5 mt-0.5 flex-shrink-0" />
              <div>{t('credentials.storageNote')}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Tool Table (全量工具 tab)
// ============================================================================

function ToolTable({
  tools,
  sort,
  filters,
  filterOptions,
  currentPage,
  totalPages,
  totalCount,
  pageSize,
  onSort,
  onToggleFilter,
  onClearFilter,
  onPageChange,
  onSelect,
}: {
  tools: Tool[];
  sort: SortState;
  filters: ColumnFilters;
  filterOptions: Record<string, string[]>;
  currentPage: number;
  totalPages: number;
  totalCount: number;
  pageSize: number;
  onSort: (f: SortField) => void;
  onToggleFilter: (f: keyof ColumnFilters, v: string) => void;
  onClearFilter: (f: keyof ColumnFilters) => void;
  onPageChange: (page: number) => void;
  onSelect: (tool: Tool) => void;
}) {
  const { t, i18n } = useTranslation('tool');
  return (
    <div className="bg-white rounded-lg border border-gray-200 overflow-hidden flex flex-col">
      <div className="overflow-x-auto flex-1">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              {/* category - 1st column */}
              <SortFilterHeader
                label={t('table.category')}
                field="category"
                sort={sort}
                filterValues={filterOptions.category}
                activeFilters={filters.category}
                onSort={onSort}
                onToggleFilter={onToggleFilter}
                onClearFilter={onClearFilter}
                renderLabel={(v) => t(`category.${CATEGORY_I18N_KEY[v] || v}`, { defaultValue: v })}
              />
              {/* source */}
              <SortFilterHeader
                label={t('table.source')}
                field="source"
                sort={sort}
                filterValues={filterOptions.source}
                activeFilters={filters.source}
                onSort={onSort}
                onToggleFilter={onToggleFilter}
                onClearFilter={onClearFilter}
                renderLabel={(v) => SOURCE_BADGE[v]?.label || v}
              />
              {/* provider (source_name) */}
              <SortFilterHeader
                label={t('table.provider')}
                field="source_name"
                sort={sort}
                filterValues={filterOptions.source_name}
                activeFilters={filters.source_name}
                onSort={onSort}
                onToggleFilter={onToggleFilter}
                onClearFilter={onClearFilter}
              />
              {/* tool name - no sort/filter */}
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                {t('table.toolName')}
              </th>
              {/* description */}
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                {t('table.description')}
              </th>
              {/* status (enabled) */}
              <SortFilterHeader
                label={t('table.status')}
                field="enabled"
                sort={sort}
                filterValues={filterOptions.enabled}
                activeFilters={filters.enabled}
                onSort={onSort}
                onToggleFilter={onToggleFilter}
                onClearFilter={onClearFilter}
                renderLabel={(v) => (v === 'true' ? t('enabledBadge.enabled') : t('enabledBadge.disabled'))}
              />
              {/* actions */}
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                {t('table.actions')}
              </th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-200">
            {tools.map((tool) => {
              const sb = SOURCE_BADGE[tool.source] || SOURCE_BADGE.custom;
              return (
                <tr key={tool.name} className="hover:bg-gray-50 cursor-pointer" onClick={() => onSelect(tool)}>
                  {/* category */}
                  <td className="px-6 py-4 whitespace-nowrap min-w-[80px]">
                    <span className="text-sm text-gray-700">{t(`category.${CATEGORY_I18N_KEY[tool.category] || tool.category}`, { defaultValue: tool.category })}</span>
                  </td>
                  {/* source */}
                  <td className="px-6 py-4 whitespace-nowrap min-w-[80px]">
                    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${sb.className}`}>
                      {sb.label}
                    </span>
                  </td>
                  {/* provider */}
                  <td className="px-6 py-4 whitespace-nowrap">
                    <span className="text-sm text-gray-700">{tool.source_name || 'Flocks'}</span>
                  </td>
                  {/* tool name */}
                  <td className="px-6 py-4 whitespace-nowrap">
                    <span className="text-sm font-medium text-gray-900 font-mono">{tool.name}</span>
                  </td>
                  {/* description */}
                  <td className="px-6 py-4">
                    <span className="text-sm text-gray-600 line-clamp-1 max-w-sm">{tool.description}</span>
                  </td>
                  {/* status */}
                  <td className="px-6 py-4 whitespace-nowrap">
                    <EnabledBadge enabled={tool.enabled} />
                  </td>
                  {/* actions */}
                  <td className="px-6 py-4 whitespace-nowrap text-sm font-medium">
                    <button onClick={(e) => { e.stopPropagation(); onSelect(tool); }} className="text-sky-700 hover:text-sky-950 mr-3">
                      {t('table.test')}
                    </button>
                    <button onClick={(e) => { e.stopPropagation(); onSelect(tool); }} className="text-gray-600 hover:text-gray-900">
                      {t('table.detail')}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <Pagination
        currentPage={currentPage}
        totalPages={totalPages}
        totalCount={totalCount}
        pageSize={pageSize}
        onPageChange={onPageChange}
      />
    </div>
  );
}

// ============================================================================
// Pagination
// ============================================================================

function Pagination({
  currentPage,
  totalPages,
  totalCount,
  pageSize,
  onPageChange,
}: {
  currentPage: number;
  totalPages: number;
  totalCount: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}) {
  const { t } = useTranslation('tool');
  if (totalPages <= 1) return null;

  const startItem = (currentPage - 1) * pageSize + 1;
  const endItem = Math.min(currentPage * pageSize, totalCount);

  const getPages = () => {
    const pages: (number | string)[] = [];
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) pages.push(i);
    } else {
      pages.push(1);
      if (currentPage > 3) pages.push('...');
      const start = Math.max(2, currentPage - 1);
      const end = Math.min(totalPages - 1, currentPage + 1);
      for (let i = start; i <= end; i++) pages.push(i);
      if (currentPage < totalPages - 2) pages.push('...');
      pages.push(totalPages);
    }
    return pages;
  };

  return (
    <div className="bg-white px-6 py-4 border-t border-gray-200 flex items-center justify-between">
      <div className="text-sm text-gray-700">{t('pagination.showing', { start: startItem, end: endItem, total: totalCount })}</div>
      <div className="flex items-center space-x-1">
        <button
          onClick={() => onPageChange(Math.max(1, currentPage - 1))}
          disabled={currentPage === 1}
          className={`p-2 rounded-md text-sm ${currentPage === 1 ? 'text-gray-300 cursor-not-allowed' : 'text-gray-600 hover:bg-gray-100'}`}
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        {getPages().map((page, i) =>
          typeof page === 'string' ? (
            <span key={`e-${i}`} className="px-2 text-gray-400">...</span>
          ) : (
            <button
              key={page}
              onClick={() => onPageChange(page)}
              className={`px-3 py-1 rounded-md text-sm font-medium ${currentPage === page ? 'bg-slate-700 text-white' : 'text-gray-700 hover:bg-gray-100'}`}
            >
              {page}
            </button>
          )
        )}
        <button
          onClick={() => onPageChange(Math.min(totalPages, currentPage + 1))}
          disabled={currentPage === totalPages}
          className={`p-2 rounded-md text-sm ${currentPage === totalPages ? 'text-gray-300 cursor-not-allowed' : 'text-gray-600 hover:bg-gray-100'}`}
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

// ============================================================================
// Tool Detail Drawer (cjtools-style right-side drawer)
// ============================================================================

function ToolDetailDrawer({
  tool,
  testParams,
  testResult,
  testing,
  onClose,
  onTestParamsChange,
  onTest,
  onDelete,
  onEnabledChange,
}: {
  tool: Tool;
  testParams: string;
  testResult: any;
  testing: boolean;
  onClose: () => void;
  onTestParamsChange: (v: string) => void;
  onTest: () => void;
  onDelete?: (name: string) => Promise<void>;
  onEnabledChange?: (name: string, enabled: boolean) => void;
}) {
  const { t, i18n } = useTranslation('tool');
  const [section, setSection] = useState<'info' | 'test'>('info');
  const [enabled, setEnabled] = useState(tool.enabled);
  const [toggling, setToggling] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const sb = SOURCE_BADGE[tool.source] || SOURCE_BADGE.custom;

  const handleToggleEnabled = async () => {
    if (toggling) return;
    const next = !enabled;
    setToggling(true);
    try {
      await toolAPI.setEnabled(tool.name, next);
      setEnabled(next);
      onEnabledChange?.(tool.name, next);
    } catch {
      // revert on error
    } finally {
      setToggling(false);
    }
  };

  const handleDelete = async () => {
    if (!onDelete || deleting) return;
    if (!window.confirm(t('alert.confirmRemoveLocalTool', { name: tool.name }))) return;
    setDeleting(true);
    try {
      await onDelete(tool.name);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-40" onClick={onClose} />

      <div className="fixed right-0 top-0 bottom-0 z-50 flex flex-col bg-white shadow-2xl w-[520px] max-w-full">
        <div className="flex-shrink-0 border-b border-gray-200">
          <div className="flex items-center gap-3 px-6 py-4">
            <div className="w-8 h-8 bg-gray-100 rounded-lg flex items-center justify-center flex-shrink-0">
              <Wrench className="w-4 h-4 text-gray-600" />
            </div>
            <div className="flex-1 min-w-0">
              <h2 className="text-lg font-semibold text-gray-900 font-mono truncate">{tool.name}</h2>
              <div className="flex items-center gap-2 mt-0.5">
                <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${sb.className}`}>
                  {sb.label}
                </span>
                {tool.source_name && (
                  <span className="text-xs text-gray-500 truncate">{tool.source_name}</span>
                )}
                <span className="text-xs text-gray-400">{t(`category.${CATEGORY_I18N_KEY[tool.category] || tool.category}`, { defaultValue: tool.category })}</span>
              </div>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <button onClick={onClose} className="p-1 rounded hover:bg-gray-100 transition-colors">
                <X className="w-5 h-5 text-gray-400" />
              </button>
            </div>
          </div>

          <div className="flex px-6">
            <button
              onClick={() => setSection('info')}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                section === 'info'
                  ? 'border-slate-700 text-slate-800'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
            >
              <Info className="w-3.5 h-3.5" />{t('toolDetail.tabInfo')}
            </button>
            <button
              onClick={() => setSection('test')}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                section === 'test'
                  ? 'border-slate-700 text-slate-800'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
              }`}
            >
              <TestTube className="w-3.5 h-3.5" />{t('toolDetail.tabTest')}
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          {section === 'info' ? (
            <div className="space-y-5">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('toolDetail.description')}</label>
                <p className="text-sm text-gray-600 leading-relaxed">{getLocalizedToolDescription(tool, i18n.language) || t('detail.noDescription')}</p>
              </div>

              <div className="flex flex-wrap gap-4 items-start">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('toolDetail.status')}</label>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={handleToggleEnabled}
                      disabled={toggling}
                      className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none disabled:opacity-50 ${enabled ? 'bg-slate-700' : 'bg-gray-200'}`}
                    >
                      <span
                        className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${enabled ? 'translate-x-5' : 'translate-x-0'}`}
                      />
                    </button>
                    <span className={`text-sm font-medium ${enabled ? 'text-slate-700' : 'text-gray-400'}`}>
                      {toggling ? t('toolDetail.updating') : enabled ? t('toolDetail.enabled') : t('toolDetail.disabled')}
                    </span>
                  </div>
                </div>
                {tool.requires_confirmation && (
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">{t('toolDetail.security')}</label>
                    <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-amber-100 text-amber-800">
                      <AlertTriangle className="w-3.5 h-3.5 mr-1" />{t('toolDetail.requiresConfirmation')}
                    </span>
                  </div>
                )}
              </div>

              {tool.parameters && tool.parameters.length > 0 && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">{t('toolDetail.params', { count: tool.parameters.length })}</label>
                  <div className="rounded-lg border border-gray-200 overflow-hidden">
                    <table className="min-w-full divide-y divide-gray-200">
                      <thead className="bg-gray-50">
                        <tr>
                          <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">{t('toolDetail.paramName')}</th>
                          <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">{t('toolDetail.paramType')}</th>
                          <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">{t('toolDetail.paramRequired')}</th>
                          <th className="px-4 py-2 text-left text-xs font-medium text-gray-500">{t('toolDetail.paramDesc')}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-100 bg-white">
                        {tool.parameters.map((param: any, idx: number) => (
                          <tr key={idx} className="hover:bg-gray-50">
                            <td className="px-4 py-2.5">
                              <code className="text-xs font-mono text-gray-900 bg-gray-100 px-1.5 py-0.5 rounded">{param.name}</code>
                            </td>
                            <td className="px-4 py-2.5 text-xs text-gray-500">{param.type}</td>
                            <td className="px-4 py-2.5">
                              {param.required
                                ? <span className="text-xs text-slate-700 font-medium">{t('toolDetail.yes')}</span>
                                : <span className="text-xs text-gray-400">{t('toolDetail.no')}</span>}
                            </td>
                            <td className="px-4 py-2.5 text-xs text-gray-600">{param.description}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {tool.source === 'plugin_py' && (
                <div className="flex items-center justify-end gap-2">
                  <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="inline-flex items-center gap-2 px-4 py-2.5 border border-red-300 text-red-700 rounded-lg hover:bg-red-50 text-sm font-medium transition-colors disabled:opacity-50"
                  >
                    <Trash2 className="w-4 h-4" />
                    {deleting ? t('button.deleting') : t('button.delete')}
                  </button>
                </div>
              )}

              {tool.source === 'mcp' && (
                <div className="bg-slate-50 border border-slate-200 rounded-lg p-4">
                  <h4 className="text-sm font-medium text-slate-900 mb-1">{t('toolDetail.mcpToolTitle')}</h4>
                  <p className="text-sm text-slate-700">{t('toolDetail.mcpToolDesc', { server: tool.source_name })}</p>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">{t('toolDetail.testParams')}</label>
                <textarea
                  value={testParams}
                  onChange={(e) => onTestParamsChange(e.target.value)}
                  placeholder='{"param": "value"}'
                  rows={6}
                  className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-slate-400 font-mono text-sm bg-gray-50 resize-none"
                />
              </div>
              <button
                onClick={onTest}
                disabled={testing || !tool.enabled}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-slate-700 text-white rounded-lg hover:bg-slate-800 disabled:opacity-50 disabled:cursor-not-allowed font-medium text-sm transition-colors"
              >
                {testing
                  ? <><RefreshCw className="w-4 h-4 animate-spin" />{t('toolDetail.executing')}</>
                  : <><Play className="w-4 h-4" />{t('toolDetail.runTest')}</>}
              </button>
              {!tool.enabled && (
                <p className="text-xs text-amber-600 text-center">{t('toolDetail.disabledNote')}</p>
              )}
              {testResult && (
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">{t('toolDetail.testResult')}</label>
                  <div className={`rounded-lg border p-4 ${testResult.success ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
                    <div className="flex items-center mb-2">
                      {testResult.success
                        ? <CheckCircle className="w-4 h-4 text-green-600 mr-2" />
                        : <XCircle className="w-4 h-4 text-red-600 mr-2" />}
                      <span className={`text-sm font-medium ${testResult.success ? 'text-green-800' : 'text-red-800'}`}>
                        {testResult.success ? t('toolDetail.execSuccess') : t('toolDetail.execFailed')}
                      </span>
                    </div>
                    {testResult.error && <p className="text-sm text-red-700 mb-2">{testResult.error}</p>}
                    {testResult.output != null && (
                      <pre className="text-xs bg-white/60 p-3 rounded-md overflow-x-auto max-h-60 font-mono">
                        {typeof testResult.output === 'string'
                          ? testResult.output
                          : JSON.stringify(testResult.output, null, 2)}
                      </pre>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

// ============================================================================
// Small Shared Components
// ============================================================================

function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation('tool');
  const config: Record<string, { labelKey: string; className: string; dot: string }> = {
    connected:    { labelKey: 'statusBadge.connected',    className: 'bg-green-100 text-green-800',  dot: 'bg-green-500' },
    connecting:   { labelKey: 'statusBadge.connecting',   className: 'bg-yellow-100 text-yellow-800', dot: 'bg-yellow-500 animate-pulse' },
    disconnected: { labelKey: 'statusBadge.disconnected', className: 'bg-gray-100 text-gray-600',    dot: 'bg-gray-400' },
    error:        { labelKey: 'statusBadge.error',        className: 'bg-red-100 text-red-800',      dot: 'bg-red-500' },
    failed:       { labelKey: 'statusBadge.failed',       className: 'bg-red-100 text-red-800',      dot: 'bg-red-500' },
    needs_auth:   { labelKey: 'statusBadge.needsAuth',    className: 'bg-amber-100 text-amber-800',  dot: 'bg-amber-500' },
    disabled:     { labelKey: 'statusBadge.disabled',     className: 'bg-gray-100 text-gray-500',    dot: 'bg-gray-300' },
  };
  const c = config[status] || config.disconnected;
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${c.className}`}>
      <span className={`w-1.5 h-1.5 rounded-full mr-1.5 ${c.dot}`} />
      {t(c.labelKey)}
    </span>
  );
}

/** Status badge for API services (similar to MCP StatusBadge, with API-specific states) */
function ApiStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation('tool');
  const config: Record<string, { labelKey: string; className: string; dot: string }> = {
    connected:      { labelKey: 'apiStatusBadge.connected',     className: 'bg-green-100 text-green-800', dot: 'bg-green-500' },
    testing:        { labelKey: 'apiStatusBadge.testing',       className: 'bg-red-100 text-red-800',   dot: 'bg-red-500 animate-pulse' },
    error:          { labelKey: 'apiStatusBadge.error',         className: 'bg-red-100 text-red-800',     dot: 'bg-red-500' },
    not_configured: { labelKey: 'apiStatusBadge.notConfigured', className: 'bg-amber-100 text-amber-800', dot: 'bg-amber-500' },
    unknown:        { labelKey: 'apiStatusBadge.unknown',       className: 'bg-gray-100 text-gray-600',   dot: 'bg-gray-400' },
  };
  const c = config[status] || config.unknown;
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${c.className}`}>
      <span className={`w-1.5 h-1.5 rounded-full mr-1.5 ${c.dot}`} />
      {t(c.labelKey)}
    </span>
  );
}

function EnabledBadge({ enabled }: { enabled: boolean }) {
  const { t } = useTranslation('tool');
  return enabled ? (
    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
      <CheckCircle className="w-3 h-3 mr-1" />{t('enabledBadge.enabled')}
    </span>
  ) : (
    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
      <XCircle className="w-3 h-3 mr-1" />{t('enabledBadge.disabled')}
    </span>
  );
}


// ============================================================================
// Catalog Tab Content (MCP 工具目录)
// ============================================================================

const LANG_COLORS: Record<string, string> = {
  python: 'bg-red-100 text-red-700',
  typescript: 'bg-sky-100 text-sky-700',
  go: 'bg-cyan-100 text-cyan-700',
  rust: 'bg-orange-100 text-orange-700',
  java: 'bg-red-100 text-red-700',
  c: 'bg-gray-100 text-gray-700',
};

// CatalogBrowser removed — catalog UI is now inline in MCPTabContent / APITabContent

// (CatalogBrowser component removed — catalog UI is inline in MCPTabContent / APITabContent)

