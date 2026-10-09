import { pageActionLabel } from '../pageActionLabel';
import PageDialog from '../PageDialog';
import { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  IconTool,
  IconSearch,
  IconRefresh,
  IconTestPipe,
  IconLayoutGrid,
  IconDatabase,
  IconCloud,
  IconX,
  IconCircleCheck,
  IconCircleX,
  IconChevronLeft,
  IconChevronRight,
  IconAlertTriangle,
  IconPlayerPlay,
  IconInfoCircle,
  IconPower,
  IconPlugOff,
  IconFileText,
  IconChevronDown,
  IconChevronUp,
  IconServer,
  IconArrowUp,
  IconArrowDown,
  IconFilter,
  IconSettings,
  IconActivity,
  IconClock,
  IconShield,
  IconBolt,
  IconPlus,
  IconCode,
  IconDownload,
  IconStar,
  IconExternalLink,
  IconTag,
  IconTrash,
  IconEye,
  IconEyeOff,
  IconDeviceFloppy,
} from '@tabler/icons-react';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useToast } from '@/components/common/Toast';
import { useToolPage } from '@/hooks/useTools';
import GroupNav, { useGroupDrag, type GroupDrag } from '@/components/plugin-groups/GroupNav';
import PluginGroupButton from '@/components/plugin-groups/PluginGroupButton';
import { assertGroupItemsEditable, saveGroupItems, type GroupSelection } from '@/components/plugin-groups/groupView';
import { useAuth } from '@/contexts/AuthContext';
import { usePluginViewMode } from '@/hooks/usePluginViewMode';
import PluginViewToggle from '@/components/plugin-groups/PluginViewToggle';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { canDirectlyTestTool, listAllToolPages, toolAPI, Tool, ToolFixture } from '@/api/tool';
import { mcpAPI, MCPServer } from '@/api/mcp';
import { providerAPI } from '@/api/provider';
import client from '@/api/client';
import type { MCPServerDetail, MCPCredentials, MCPCredentialInput, MCPCatalogEntry, MCPCatalogCategory } from '@/types';
import { MCPSheet, APISheet, GenerateToolSheet, MCPFormFields, buildMCPFormDataFromConfig } from './ToolSheets';
import type { MCPFormData, ConnStatus as MCPConnStatus } from './ToolSheets';
import MCPTabContent from './components/MCPTabContent';
import APITabContent from './components/APITabContent';
import LocalTabContent from './components/LocalTabContent';
import { getSourceLabel } from './constants';
import { getCatalogDescription, getMetadataDescription } from '@/utils/mcpCatalog';
import { extractErrorMessage } from '@/utils/error';
import { getLocalizedToolDescription, getLocalizedFixtureLabel } from './toolDisplay';
import {
  DEFAULT_TOOL_TAB,
  TOOL_PAGE_SIZE,
  getTabSourceFilter,
  shouldLoadMcpCatalog,
  type TabKey,
} from './tabLoading';
import { getApiServiceCount, getMcpServiceCount, getToolTabCounts } from './tabCounts';

// ============================================================================
// Constants & Config
// ============================================================================

interface TabConfig {
  key: TabKey;
  label: string;
  icon: React.ReactNode;
}

/** Tool source categories use data or neutral accents; connection status is separate. */
const SOURCE_BADGE: Record<string, { className: string }> = {
  mcp: { className: 'bg-data-soft text-data' },
  api: { className: 'bg-data-soft text-data' },
  device: { className: 'bg-data-soft text-data' },
  plugin_py: { className: 'bg-subtle text-fg-secondary' },
  plugin_yaml: { className: 'bg-data-soft text-data' },
  builtin: { className: 'bg-subtle text-fg-secondary' },
  custom: { className: 'bg-subtle text-fg-secondary' },
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
  device: 6,
};

const PAGE_SIZE = TOOL_PAGE_SIZE;

/** MCP/API 服务器详情抽屉宽度（px） */
const DETAIL_DRAWER_WIDTH = 480;
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

function joinFilterValues(values: Set<string>): string | undefined {
  if (values.size === 0) return undefined;
  return Array.from(values).sort().join(',');
}

function mergeFacetKeys(facets: Record<string, number>, activeValues: Set<string> = new Set()): string[] {
  return Array.from(new Set([...Object.keys(facets), ...Array.from(activeValues)])).sort();
}

// ============================================================================
// Main Page
// ============================================================================

export default function ToolPage() {
  const { t, i18n } = useTranslation('tool');
  const toast = useToast();

  const TABS: TabConfig[] = [
    { key: 'all', label: t('tabs.all'), icon: <IconLayoutGrid aria-hidden="true" className="w-5 h-5" /> },
    { key: 'mcp', label: t('tabs.mcp'), icon: <IconDatabase aria-hidden="true" className="w-5 h-5" /> },
    { key: 'api', label: t('tabs.api'), icon: <IconCloud aria-hidden="true" className="w-5 h-5" /> },
    { key: 'local', label: t('tabs.local'), icon: <IconCode aria-hidden="true" className="w-5 h-5" /> },
  ];

  const [activeTab, setActiveTab] = useState<TabKey>(DEFAULT_TOOL_TAB);
  const [viewMode, setViewMode] = usePluginViewMode(`tools.${activeTab}`, 'list');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedTool, setSelectedTool] = useState<Tool | null>(null);
  const [testParams, setTestParams] = useState('{}');
  const [testResult, setTestResult] = useState<any>(null);
  const [testing, setTesting] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [toolGroup, setToolGroup] = useState<GroupSelection>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshDone, setRefreshDone] = useState(false);

  // Sheet state
  const [showMCPSheet, setShowMCPSheet] = useState(false);
  const [serviceRefreshKey, setServiceRefreshKey] = useState(0);
  const [showAPISheet, setShowAPISheet] = useState(false);
  const [showGenerateSheet, setShowGenerateSheet] = useState(false);
  // Sort: default by 类别 (source) MCP -> API -> 内置
  const [sort, setSort] = useState<SortState>({ field: 'source', dir: 'asc' });
  const [filters, setFilters] = useState<ColumnFilters>(EMPTY_FILTERS);

  const [apiServicesCount, setApiServicesCount] = useState(0);
  const [mcpInventory, setMcpInventory] = useState<Record<string, unknown> | MCPServer[]>({});
  const debouncedSearchQuery = useDebouncedValue(searchQuery, 250);
  const tabSourceFilter = getTabSourceFilter(activeTab);
  const sourceFilterParam = useMemo(() => {
    const values = new Set(filters.source);
    if (tabSourceFilter) {
      const tabSources = Array.isArray(tabSourceFilter) ? tabSourceFilter : [tabSourceFilter];
      tabSources.forEach((source) => values.add(source));
    }
    return joinFilterValues(values);
  }, [filters.source, tabSourceFilter]);
  // Service tabs group their native inventories, never their child tools.
  const toolGroupFilter = activeTab === 'all' || activeTab === 'local' ? toolGroup : null;
  const toolPageParams = useMemo(() => ({
    group: toolGroupFilter ?? undefined,
    source: sourceFilterParam,
    category: joinFilterValues(filters.category),
    sourceName: joinFilterValues(filters.source_name),
    enabled: joinFilterValues(filters.enabled),
    q: debouncedSearchQuery.trim() || undefined,
    sortBy: sort.field,
    sortDir: sort.dir,
    offset: (currentPage - 1) * PAGE_SIZE,
    limit: PAGE_SIZE,
  }), [
    sourceFilterParam,
    toolGroupFilter,
    filters.category,
    filters.source_name,
    filters.enabled,
    debouncedSearchQuery,
    sort.field,
    sort.dir,
    currentPage,
  ]);
  const {
    tools,
    total: totalTools,
    facets: toolFacets,
    loading,
    error,
    initialized: toolPageInitialized,
    refetch,
    reload: reloadToolPage,
  } = useToolPage(toolPageParams);
  const [hasLoadedToolPage, setHasLoadedToolPage] = useState(false);
  // Do not apply a previous query's service facets during the input debounce or load.
  const apiToolSearchPending = searchQuery.trim().toLowerCase() !== debouncedSearchQuery.trim().toLowerCase()
    || loading || !toolPageInitialized;

  // Catalog data (fetched once at top level, shared with MCP & API tabs)
  const [catalogEntries, setCatalogEntries] = useState<MCPCatalogEntry[]>([]);
  const [catalogCategories, setCatalogCategories] = useState<Record<string, MCPCatalogCategory>>({});
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogRetryKey, setCatalogRetryKey] = useState(0);
  const [configuredIds, setConfiguredIds] = useState<Set<string>>(new Set());
  const catalogLoadedRef = useRef(false);

  useEffect(() => {
    if (!shouldLoadMcpCatalog(activeTab) || catalogLoadedRef.current) return;
    let cancelled = false;

    const loadCatalog = async () => {
      let loaded = false;
      try {
        setCatalogLoading(true);
        setCatalogError(null);
        const [entriesRes, catsRes] = await Promise.all([
          mcpAPI.catalogList(),
          mcpAPI.catalogCategories(),
        ]);
        if (cancelled) return;
        setCatalogEntries(entriesRes.data);
        setCatalogCategories(catsRes.data);
        loaded = true;
        // Get currently configured IDs (no auto-setup to avoid re-adding removed entries)
        try {
          const confRes = await mcpAPI.catalogConfigured();
          if (!cancelled) setConfiguredIds(new Set(confRes.data));
        } catch { /* ignore */ }
      } catch (err) {
        console.error('Failed to load catalog:', err);
        if (!cancelled) {
          setCatalogError(err instanceof Error ? err.message : 'Failed to load MCP catalog');
        }
      } finally {
        if (!cancelled) {
          catalogLoadedRef.current = loaded;
          setCatalogLoading(false);
        }
      }
    };
    void loadCatalog();

    return () => {
      cancelled = true;
    };
  }, [activeTab, catalogRetryKey]);

  useEffect(() => {
    if (!shouldLoadMcpCatalog(activeTab)) {
      setCatalogLoading(false);
    }
  }, [activeTab]);

  const onConfiguredChange = useCallback((id: string) => {
    setConfiguredIds(prev => new Set(prev).add(id));
  }, []);

  const onConfiguredRemove = useCallback((id: string) => {
    setConfiguredIds(prev => { const next = new Set(prev); next.delete(id); return next; });
  }, []);

  const fetchApiServicesCount = useCallback(async (force = false) => {
    try {
      const res = await providerAPI.listApiServices(force ? { force: true } : undefined);
      setApiServicesCount(getApiServiceCount(Array.isArray(res.data) ? res.data : []));
    } catch {
      // Keep the previous native inventory count on a failed reload.
    }
  }, []);

  const fetchMcpInventory = useCallback(async () => {
    try {
      const res = await mcpAPI.list();
      setMcpInventory(res.data);
    } catch {
      // A tool facet cannot stand in for missing/zero-tool native services.
    }
  }, []);

  useEffect(() => {
    void fetchApiServicesCount();
    void fetchMcpInventory();
  }, [fetchApiServicesCount, fetchMcpInventory]);

  const refreshToolData = useCallback(async () => {
    const [refreshResult] = await Promise.all([
      refetch(),
      fetchApiServicesCount(true),
      fetchMcpInventory(),
    ]);
    return refreshResult;
  }, [refetch, fetchApiServicesCount, fetchMcpInventory]);

  const { user } = useAuth();
  const readOnlyReason = user?.role === 'admin' ? undefined : t('pluginGroups:readOnly.admin');
  const asGroupItem = (tool: Tool) => ({
    key: tool.name, name: tool.name, group: tool.group,
    readOnlyReason: tool.group_readonly ? t('pluginGroups:readOnly.system') : readOnlyReason,
  });
  const toolGroupItems = tools.map(asGroupItem);
  const toolGroupDrag = useGroupDrag(toolGroupItems, toast.warning);
  const groupFacets = toolFacets.group ?? {};
  const hasToolInventoryFilters = !!(toolPageParams.q || toolPageParams.source || toolPageParams.category || toolPageParams.sourceName || toolPageParams.enabled);
  const nativeGroupNav = {
    groups: Object.entries(groupFacets).filter(([name, count]) => name && count > 0).map(([name, count]) => ({
      name, count, readOnlyReason: readOnlyReason || toolGroupItems.find((item) => item.group === name && item.readOnlyReason)?.readOnlyReason,
    })),
    total: toolFacets.group ? Object.values(groupFacets).reduce((sum, count) => sum + count, 0) : totalTools,
    ungroupedCount: groupFacets[''] ?? 0,
    inventoryComplete: !hasToolInventoryFilters && toolPageInitialized && !loading && !error,
  };
  const saveToolGroup = (name: string, group: string | null) => toolAPI.updateGroup(name, group);
  const reloadToolGroups = async () => {
    await reloadToolPage();
    if (!toolGroup) return;
    // Filtered facets cannot establish that the final native member left.
    const { data } = await toolAPI.listPage({ offset: 0, limit: 1 });
    if (!data.facets.group) throw new Error(t('pluginGroups:errors.refresh'));
    if (!(data.facets.group[toolGroup] > 0)) {
      setToolGroup((current) => current === toolGroup ? null : current);
      setCurrentPage(1);
    }
  };
  const moveToolGroup = async (key: string, group: string | null) => {
    const { data } = await toolAPI.get(key);
    await saveGroupItems([asGroupItem(data)], group, saveToolGroup, reloadToolGroups, t);
  };
  const ensureNewToolGroup = async (name: string) => {
    // Query-independent native facets include names hidden by search, columns or tabs.
    // Only one summary row is needed; do not fetch every tool just to validate a name.
    const { data } = await toolAPI.listPage({ offset: 0, limit: 1 });
    if (!data.facets.group) throw new Error(t('pluginGroups:errors.refresh'));
    if ((data.facets.group[name] ?? 0) > 0) throw new Error(t('pluginGroups:validation.duplicate'));
  };
  const createToolGroup = async (key: string, group: string) => {
    await ensureNewToolGroup(group);
    await moveToolGroup(key, group);
  };
  const changeToolGroup = async (from: string, to: string | null) => {
    if (to !== null) await ensureNewToolGroup(to);
    // Audit the entire native group, including members hidden by query, columns,
    // tabs or pagination. Counts and the loaded 25 rows cannot prove writability.
    const wholeGroup = await listAllToolPages({ group: from, sortBy: toolPageParams.sortBy, sortDir: toolPageParams.sortDir }, { requireComplete: true });
    assertGroupItemsEditable(wholeGroup.map(asGroupItem), t);
    // Keep the existing write scope explicit in the single native confirmation.
    const members = hasToolInventoryFilters
      ? await listAllToolPages({ ...toolPageParams, group: from }, { requireComplete: true })
      : wholeGroup;
    await saveGroupItems(members.map(asGroupItem), to, saveToolGroup, reloadToolGroups, t, true);
  };
  const selectToolGroup = useCallback((name: GroupSelection) => {
    setToolGroup(name);
    setCurrentPage(1);
  }, []);
  // A failed first request has no facets, not an authoritative empty inventory.
  // Do not let it clear the selected group and silently fall back to All.
  const toolGroupNav = toolFacets.group ? <GroupNav preferenceKey={`tools.${activeTab}`} {...nativeGroupNav} items={toolGroupItems}
    selection={toolGroup} onSelect={selectToolGroup} onMove={moveToolGroup} onCreate={createToolGroup} onRename={changeToolGroup}
    onDelete={(name) => changeToolGroup(name, null)} {...toolGroupDrag} /> : null;

  const showRefreshOutcome = useCallback((status: 'success' | 'partial' | 'error', message: string) => {
    if (status === 'partial') {
      toast.warning(t('alert.refreshPartialTitle'), message || t('alert.refreshPartialDefault'));
    } else if (status === 'error') {
      toast.error(t('alert.refreshFailedTitle'), message || t('alert.unknownError'));
    }
  }, [t, toast]);

  const refreshToolDataAfterMutation = useCallback(async () => {
    try {
      const result = await refreshToolData();
      showRefreshOutcome(result.status, result.message);
    } catch (err: unknown) {
      const message = extractErrorMessage(err, t('alert.unknownError'));
      toast.error(t('alert.refreshFailedTitle'), message);
    }
  }, [refreshToolData, showRefreshOutcome, t, toast]);

  // The backend still marks some valid MCP catalog entries as "api" based on category.
  // Until API catalog has its own rendering path, show all catalog entries in the MCP tab.
  const mcpCatalogEntries = useMemo(() => catalogEntries, [catalogEntries]);
  // API catalog not yet implemented — keep this empty so entries are not duplicated across tabs.
  const apiCatalogEntries = useMemo(() => [] as MCPCatalogEntry[], []);

  const tabCounts = useMemo(
    () => getToolTabCounts(totalTools, toolFacets, {
      api: apiServicesCount,
      mcp: getMcpServiceCount(mcpInventory, mcpCatalogEntries),
    }),
    [totalTools, toolFacets, apiServicesCount, mcpInventory, mcpCatalogEntries],
  );
  const enabledSummary = useMemo(() => ({
    active: toolFacets.enabled['true'] ?? tools.filter((tool) => tool.enabled).length,
    inactive: toolFacets.enabled['false'] ?? 0,
  }), [toolFacets.enabled, tools]);

  // Filter dropdown options come from server-side facets, plus active values
  // so a selected filter remains visible even when it narrows the result set.
  const filterOptions = useMemo(() => {
    return {
      category: mergeFacetKeys(toolFacets.category, filters.category),
      source: mergeFacetKeys(toolFacets.source, filters.source)
        .sort((a, b) => (SOURCE_SORT_ORDER[a] ?? 99) - (SOURCE_SORT_ORDER[b] ?? 99)),
      source_name: mergeFacetKeys(toolFacets.source_name, filters.source_name),
      enabled: ['true', 'false'],
    };
  }, [toolFacets, filters.category, filters.source, filters.source_name]);

  // Pagination
  const processedTools = tools;
  const totalPages = Math.max(1, Math.ceil(totalTools / PAGE_SIZE));
  const paginatedTools = tools;

  useEffect(() => {
    if (toolPageInitialized && currentPage > totalPages) setCurrentPage(totalPages);
  }, [currentPage, totalPages, toolPageInitialized]);

  useEffect(() => {
    if (toolPageInitialized && !error) {
      setHasLoadedToolPage(true);
    }
  }, [toolPageInitialized, error]);

  const handleTabChange = (tab: TabKey) => {
    setActiveTab(tab);
    selectToolGroup(null);
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
      setRefreshDone(false);
      const [result] = await Promise.all([
        refreshToolData(),
        new Promise((r) => setTimeout(r, 600)),
      ]);
      if (result.status === 'success') {
        setRefreshDone(true);
        setTimeout(() => setRefreshDone(false), 2000);
      } else {
        showRefreshOutcome(result.status, result.message);
      }
    } catch (err: unknown) {
      const message = extractErrorMessage(err, t('alert.unknownError'));
      toast.error(t('alert.refreshFailedTitle'), message);
    } finally {
      setServiceRefreshKey((key) => key + 1);
      setRefreshing(false);
    }
  };

  const handleTest = async () => {
    if (!selectedTool) return;
    if (!canDirectlyTestTool(selectedTool)) return;
    try {
      setTesting(true);
      setTestResult(null);
      const params = JSON.parse(testParams);
      const response = await toolAPI.test(selectedTool.name, params);
      const result = response.data;
      setTestResult(result);
      if (result.metadata?.disabled === true) {
        setSelectedTool((current) => (
          current?.name === selectedTool.name
            ? { ...current, enabled: false }
            : current
        ));
        void reloadToolPage().catch((err: unknown) => {
          toast.error(
            t('alert.refreshFailedTitle'),
            extractErrorMessage(err, t('alert.unknownError')),
          );
        });
      }
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
    toolAPI.get(tool.name)
      .then((response) => {
        setSelectedTool((current) => (
          current?.name === tool.name ? response.data : current
        ));
      })
      .catch(() => {
        // Keep the lightweight row data visible; retry happens when reopened.
      });
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

  const isInitialLoading = loading && !hasLoadedToolPage;
  const isTabPageLoading = loading && hasLoadedToolPage && !toolPageInitialized;

  if (isInitialLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner delayMs={180} />
      </div>
    );
  }

  if (error && !hasLoadedToolPage) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-danger mb-4">{error}</p>
          <button onClick={() => void handleRefresh()} className="px-4 py-2 bg-subtle text-fg rounded-control hover:bg-hover">
            {t('button.retry')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-w-0 space-y-6" aria-busy={loading}>
      {/* Page Header — wraps on narrow viewports instead of squeezing the title */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div className="shrink-0 text-danger">
            <IconTool aria-hidden="true" className="w-8 h-8" />
          </div>
          <div className="min-w-0">
            <h1 className="text-title-page font-bold text-fg whitespace-nowrap">{t('pageTitle')}</h1>
          <p className="mt-1 text-sm text-fg-muted whitespace-nowrap">
            <span className="font-semibold text-fg-secondary">{enabledSummary.active}</span> {t('statusBadge.active')}
            <span className="mx-1.5 text-fg-placeholder">·</span>
            <span className="font-semibold text-fg-secondary">{enabledSummary.inactive}</span> {t('statusBadge.inactive')}
          </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <PluginViewToggle value={viewMode} onChange={setViewMode} />
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            title={refreshDone ? t('button.refreshDone') : t('button.refreshList')}
            className={`ui-btn-icon inline-flex items-center justify-center inline-flex items-center px-3 py-2 border rounded-control text-sm font-medium transition-all ${
              refreshDone
                ? 'border-success text-success bg-success-soft'
                : 'border-line-control text-fg-secondary bg-surface hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled'
            }`}
          >
            <IconRefresh aria-hidden="true" className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={() => setShowMCPSheet(true)}
            className="inline-flex items-center px-4 py-2 border border-line-control rounded-control text-sm font-medium text-fg-secondary bg-surface hover:bg-hover transition-colors"
          >
            <IconDatabase aria-hidden="true" className="w-4 h-4 mr-1.5" />
            {t('button.addMCP')}
          </button>
          <button
            onClick={() => setShowAPISheet(true)}
            className="inline-flex items-center px-4 py-2 border border-line-control rounded-control text-sm font-medium text-fg-secondary bg-surface hover:bg-hover transition-colors"
          >
            <IconCloud aria-hidden="true" className="w-4 h-4 mr-1.5" />
            {t('button.addAPI')}
          </button>
          <button
            onClick={() => setShowGenerateSheet(true)}
            className="inline-flex items-center px-4 py-2 border border-transparent rounded-control text-sm font-medium text-action-fg bg-action hover:bg-action-hover transition-colors shadow-sm"
          >
            <IconPlus aria-hidden="true" className="w-4 h-4 mr-1.5" />
            {t('button.createTool')}
          </button>
        </div>
      </div>

      {/* Tabs row with search */}
      <div className="border-b border-line">
        <div className="flex min-h-14 flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <nav className="-mb-px flex max-w-full space-x-8 overflow-x-auto">
            {TABS.map((tab) => (
              <button
                key={tab.key}
                onClick={() => handleTabChange(tab.key)}
                className={`h-14 px-1 border-b-2 font-medium text-sm whitespace-nowrap transition-colors ${
                  activeTab === tab.key
                    ? 'border-line-control text-fg'
                    : 'border-transparent text-fg-muted hover:text-fg-secondary hover:border-line-control'
                }`}
              >
                <div className="flex h-full items-center">
                  <span className={activeTab === tab.key ? 'text-fg-secondary' : 'text-fg-muted'}>
                    {tab.icon}
                  </span>
                  <span className="ml-2">{tab.label}</span>
                  <span className="ml-2 inline-flex min-w-10 justify-center rounded-pill bg-subtle px-2.5 py-0.5 text-xs font-medium tabular-nums text-fg">
                    {tabCounts[tab.key] || 0}
                  </span>
                </div>
              </button>
            ))}
          </nav>

          {/* Query controls stay on the left; native actions remain above on the right. */}
          <div className="relative mb-px order-first w-full sm:w-auto">
            <IconSearch aria-hidden="true" className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-fg-muted" />
            <input
              type="text"
              placeholder={t('search.placeholder')}
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
              className="ui-input w-full sm:w-64 pl-9 pr-4  text-sm border border-line-control rounded-control focus:border-transparent bg-surface"
            />
          </div>
        </div>
      </div>

      {error && (
        <div className="rounded-control border border-danger bg-danger-soft px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}

      {activeTab === 'mcp' && catalogError && (
        <div className="flex items-center justify-between rounded-control border border-warning bg-warning-soft px-4 py-3 text-sm text-warning">
          <span>{catalogError}</span>
          <button
            type="button"
            onClick={() => setCatalogRetryKey((value) => value + 1)}
            className="rounded-control border border-warning bg-surface px-3 py-1.5 font-medium hover:bg-warning-soft"
          >
            {t('button.retry')}
          </button>
        </div>
      )}

      {/* Tab Content */}
      <div className="min-h-[420px] [scrollbar-gutter:stable]">
        {/* Native service inventories own their filters/detail state. Loading a
            child-tool query must not unmount them and reset the business group. */}
        {activeTab === 'mcp' ? (
          <MCPTabContent
            tools={processedTools}
            searchQuery={searchQuery}
            onServersChange={setMcpInventory}
            onSelectTool={openDetail}
            onRefreshTools={refreshToolDataAfterMutation}
            catalogEntries={mcpCatalogEntries}
            catalogCategories={catalogCategories}
            catalogLoading={catalogLoading}
            configuredIds={configuredIds}
            onConfiguredChange={onConfiguredChange}
            onConfiguredRemove={onConfiguredRemove}
            refreshKey={serviceRefreshKey}
            viewMode={viewMode}
          />
        ) : activeTab === 'api' ? (
          <APITabContent
            tools={processedTools}
            searchQuery={searchQuery}
            matchingToolServices={apiToolSearchPending || error ? undefined : toolFacets.source_name}
            toolSearchPending={apiToolSearchPending}
            onServiceCountChange={setApiServicesCount}
            refreshKey={serviceRefreshKey}
            onSelectTool={openDetail}
            onRefreshTools={refreshToolDataAfterMutation}
            catalogEntries={apiCatalogEntries}
            catalogCategories={catalogCategories}
            catalogLoading={catalogLoading}
            configuredIds={configuredIds}
            onConfiguredChange={onConfiguredChange}
            viewMode={viewMode}
          />
        ) : isTabPageLoading ? (
          <div className="flex min-h-[420px] items-center justify-center rounded-lg border border-line bg-surface">
            <LoadingSpinner delayMs={180} />
          </div>
        ) : activeTab === 'local' ? (
          <div className="flex flex-col gap-4 md:flex-row">
            {toolGroupNav}
          <div className="min-w-0 flex-1 overflow-x-auto rounded-control border border-line bg-surface">
            <LocalTabContent
              tools={processedTools}
              searchQuery={searchQuery}
              selectedToolName={selectedTool?.name}
              groupDrag={toolGroupDrag}
              viewMode={viewMode}
              onSelectTool={openDetail}
              onRefreshTools={refreshToolDataAfterMutation}
            />
            <Pagination
              currentPage={currentPage}
              totalPages={totalPages}
              totalCount={totalTools}
              pageSize={PAGE_SIZE}
              onPageChange={setCurrentPage}
            />
          </div>
          </div>
        ) : (
          /* All tab: active tools only */
          <div className="flex flex-col gap-4 md:flex-row">
          {toolGroupNav}
          <div className="min-w-0 flex-1 space-y-4">
            {/* Inactive services callout */}
            {(mcpCatalogEntries.length > 0 || apiCatalogEntries.length > 0) && !searchQuery && (
              <div className="flex items-center justify-between px-4 py-3 bg-subtle border border-line rounded-control text-sm">
                <span className="text-fg-secondary">
                  <span className="font-medium text-fg">{catalogEntries.length}</span> {t('summary.inactiveServicesAvailable')}
                </span>
                <div className="flex items-center gap-3">
                  {mcpCatalogEntries.length > 0 && (
                    <button
                      onClick={() => handleTabChange('mcp')}
                      className="flex items-center gap-1 text-fg-secondary hover:text-fg font-medium"
                    >
                      MCP <IconChevronRight aria-hidden="true" className="w-4 h-4" />
                    </button>
                  )}
                  {apiCatalogEntries.length > 0 && (
                    <button
                      onClick={() => handleTabChange('api')}
                      className="flex items-center gap-1 text-ai hover:text-ai font-medium"
                    >
                      API <IconChevronRight aria-hidden="true" className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </div>
            )}
            {totalTools === 0 ? (
              <EmptyState
                icon={<IconTool aria-hidden="true" className="w-8 h-8" />}
                title={searchQuery ? t('empty.noMatchingTools') : t('empty.noTools')}
                description={searchQuery ? t('empty.adjustFilter') : t('empty.noToolsInCategory')}
              />
            ) : (
              <ToolTable
                tools={paginatedTools}
                groupDrag={toolGroupDrag}
                viewMode={viewMode}
                sort={sort}
                filters={filters}
                filterOptions={filterOptions}
                currentPage={currentPage}
                totalPages={totalPages}
                totalCount={totalTools}
                pageSize={PAGE_SIZE}
                onSort={toggleSort}
                onToggleFilter={toggleFilter}
                onClearFilter={clearFilter}
                onPageChange={setCurrentPage}
                onSelect={openDetail}
              />
            )}
          </div>
          </div>
        )}
      </div>

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
              const { data: deleteResult } = await toolAPI.delete(name);
              setSelectedTool(null);
              setTestResult(null);
              if (deleteResult.status === 'partial') {
                toast.warning(
                  t('alert.deletePartialTitle'),
                  deleteResult.errors?.join('; ') || deleteResult.message,
                );
              }
              await handleRefresh();
            } catch (err: unknown) {
              alert(t('alert.deleteFailed', {
                error: extractErrorMessage(err, t('alert.unknownError')),
              }));
            }
          }}
          onEnabledChange={(name, newEnabled, updated) => {
            // carry the overlay flags too: a partial update would let the
            // drawer's prop-sync effect restore a stale "已自定义" badge
            setSelectedTool((prev) => prev ? {
              ...prev,
              enabled: newEnabled,
              ...(updated ? {
                enabled_customized: updated.enabled_customized,
                enabled_default: updated.enabled_default,
              } : {}),
            } : prev);
            void refreshToolDataAfterMutation();
          }}
        />
      )}

      {/* MCP Sheet */}
      {showMCPSheet && (
        <MCPSheet
          onClose={() => {
            setShowMCPSheet(false);
            void handleRefresh();
          }}
          onSaved={() => { setShowMCPSheet(false); void handleRefresh(); }}
          onRefresh={handleRefresh}
        />
      )}

      {/* API Sheet */}
      {showAPISheet && (
        <APISheet
          onClose={() => {
            setShowAPISheet(false);
            void handleRefresh();
          }}
        />
      )}

      {/* Generate Tool Sheet */}
      {showGenerateSheet && (
        <GenerateToolSheet
          onClose={() => {
            setShowGenerateSheet(false);
            void handleRefresh();
          }}
        />
      )}


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
  asDiv = false,
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
  asDiv?: boolean;
}) {
  const { t } = useTranslation('tool');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLElement | null>(null);

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

  const inner = (
    <>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onSort(field)}
          className={`flex items-center gap-0.5 hover:text-fg transition-colors ${isActive ? 'text-fg-secondary' : ''}`}
        >
          {label}
          {isActive && (sort.dir === 'asc' ? <IconArrowUp aria-hidden="true" className="w-4 h-4" /> : <IconArrowDown aria-hidden="true" className="w-4 h-4" />)}
        </button>
        <button aria-label={pageActionLabel('filter')}
          onClick={() => setOpen((v) => !v)}
          className={`ui-btn-icon inline-flex items-center justify-center p-0.5 rounded-control hover:bg-hover transition-colors ${hasFilter ? 'text-fg-secondary' : 'text-fg-muted'}`}
        >
          <IconFilter aria-hidden="true" className="w-4 h-4" />
        </button>
      </div>

      {open && (
        <div className="absolute left-0 top-full mt-1 z-20 bg-surface rounded-control shadow-lg border border-line py-2 min-w-[160px] max-h-60 overflow-y-auto">
          {hasFilter && (
            <button
              onClick={() => { onClearFilter(field); setOpen(false); }}
              className="w-full text-left px-3 py-1.5 text-xs text-fg-secondary hover:bg-hover"
            >
              {t('button.clearFilter')}
            </button>
          )}
          {filterValues.map((v) => {
            const checked = activeFilters.has(v);
            const displayLabel = renderLabel ? renderLabel(v) : v;
            return (
              <label key={v} className="flex items-center px-3 py-1.5 hover:bg-hover cursor-pointer">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => onToggleFilter(field, v)}
                  className="w-3.5 h-3.5 rounded-tag border-line-control text-fg-secondary mr-2"
                />
                <span className="text-xs text-fg-secondary">{displayLabel}</span>
              </label>
            );
          })}
        </div>
      )}
    </>
  );

  if (asDiv) {
    return (
      <div
        className="relative whitespace-nowrap inline-flex"
        ref={ref as React.RefObject<HTMLDivElement>}
      >
        {inner}
      </div>
    );
  }

  return (
    <th
      className="px-6 py-3 text-left text-xs font-medium text-fg-muted uppercase tracking-wider relative whitespace-nowrap"
      ref={ref as React.RefObject<HTMLTableCellElement>}
    >
      {inner}
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
            className={`px-2.5 py-1 rounded-pill text-xs font-medium transition-colors ${selectedCategory === 'all' ? 'bg-subtle text-fg-secondary' : 'bg-subtle text-fg-secondary hover:bg-hover'}`}
          >{t('catalog.all')}</button>
          {Object.entries(catalogCategories).map(([id, cat]) =>
            catalogCategoryCounts[id] ? (
              <button key={id} onClick={() => setSelectedCategory(id)} className={`px-2.5 py-1 rounded-pill text-xs font-medium transition-colors ${selectedCategory === id ? 'bg-subtle text-fg-secondary' : 'bg-subtle text-fg-secondary hover:bg-hover'}`}>
                {cat.label} ({catalogCategoryCounts[id]})
              </button>
            ) : null
          )}
        </div>
        <button
          onClick={fetchServers}
          className="inline-flex items-center px-3 py-2 text-sm text-fg-secondary bg-surface border border-line-control rounded-control hover:bg-hover flex-shrink-0"
        >
          <IconRefresh aria-hidden="true" className="w-4 h-4 mr-1.5" />
          {t('mcp.refreshStatus')}
        </button>
      </div>

      {serversLoading && catalogLoading ? (
        <div className="flex justify-center py-12"><LoadingSpinner /></div>
      ) : userServers.length === 0 && filteredCatalog.length === 0 ? (
        <EmptyState icon={<IconServer aria-hidden="true" className="w-8 h-8" />} title={t('mcp.noServers')} description={t('mcp.noServersDesc')} />
      ) : (
        <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {/* User-added servers (not from catalog) */}
          {userServers.map((server) => {
            const serverTools = toolsByServer[server.name] || [];
            const isSelected = selectedServer === server.name;
            const isActive = server.status === 'connected';
            const isError = server.status === 'error';
            const borderColor = isActive ? 'var(--status-success)' : isError ? 'var(--status-danger)' : 'var(--border-subtle)';
            return (
              <div
                key={server.name}
                onClick={() => {
                  if (isSelected) { setSelectedServer(null); setSelectedServerData(null); }
                  else { setSelectedServer(server.name); setSelectedServerData(server); }
                }}
                className={`relative bg-surface rounded-card border overflow-hidden cursor-pointer h-[180px] flex flex-col transition-all duration-150 ${isSelected ? 'border-line-control ring-2 ring-focus' : 'border-line shadow-sm hover:border-line-control'}`}
                style={{ borderLeftWidth: 4, borderLeftColor: borderColor }}
              >
                <div className="flex-1 px-4 pt-4 pb-2 min-h-0 flex flex-col gap-1.5">
                  <div className="flex items-start gap-1.5 flex-wrap">
                    <span className="text-sm font-semibold text-fg truncate max-w-[120px]">{server.name}</span>
                    <span className={`px-1.5 py-0.5 text-xs font-medium rounded-pill shrink-0 ${isActive ? 'bg-success-soft text-success' : isError ? 'bg-danger-soft text-danger' : 'bg-subtle text-fg-secondary'}`}>
                      {isActive ? t('statusBadge.active') : isError ? t('statusBadge.error') : t('statusBadge.inactive')}
                    </span>
                    <span className="px-1.5 py-0.5 bg-subtle text-fg-secondary text-xs font-medium rounded-pill shrink-0">MCP</span>
                  </div>
                  {server.url && <p className="text-xs text-fg-muted font-mono truncate">{server.url}</p>}
                  <div className="flex items-center gap-1 text-xs text-fg-muted mt-auto">
                    <IconTool aria-hidden="true" className="w-4 h-4 shrink-0" /><span>{serverTools.length} {t('mcp.tools')}</span>
                    <span className="mx-1">·</span>
                    <IconFileText aria-hidden="true" className="w-4 h-4 shrink-0" /><span>{server.resources?.length || 0} {t('mcp.resources')}</span>
                  </div>
                  {server.connected_at && (
                    <div className="flex items-center gap-1">
                      <IconClock aria-hidden="true" className="w-4 h-4 shrink-0" /><span>{t('mcp.connectedFor', { duration: formatDuration(server.connected_at, t) })}</span>
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
            const borderColor = isActive ? 'var(--status-success)' : isError ? 'var(--status-danger)' : 'var(--border-subtle)';
            const isSelected = selectedServer === entry.id;

            return (
              <div
                key={`catalog-${entry.id}`}
                onClick={() => isActive ? setSelectedServer(isSelected ? null : entry.id) : undefined}
                className={`relative bg-surface rounded-card border overflow-hidden h-[180px] flex flex-col transition-all duration-150 ${isActive ? 'cursor-pointer' : 'cursor-default'} ${isSelected ? 'border-line-control ring-2 ring-focus' : 'border-line shadow-sm hover:border-line-control'}`}
                style={{ borderLeftWidth: 4, borderLeftColor: borderColor }}
              >
                <div className="flex-1 px-4 pt-4 pb-2 min-h-0 flex flex-col gap-1.5">
                  <div className="flex items-start gap-1.5 flex-wrap">
                    <span className="text-sm font-semibold text-fg truncate max-w-[120px]">{entry.name}</span>
                    <span className={`px-1.5 py-0.5 text-xs font-medium rounded-pill shrink-0 ${isActive ? 'bg-success-soft text-success' : isError ? 'bg-danger-soft text-danger' : 'bg-subtle text-fg-secondary'}`}>
                      {isActive ? t('statusBadge.active') : isError ? t('statusBadge.error') : t('statusBadge.inactive')}
                    </span>
                    <span className="px-1.5 py-0.5 bg-subtle text-fg-secondary text-xs font-medium rounded-pill shrink-0">MCP</span>
                    {PRIORITY_IDS.has(entry.id) && (
                      <span className="px-1.5 py-0.5 bg-warning-soft text-warning text-xs font-medium rounded-pill shrink-0">{t('mcp.recommended')}</span>
                    )}
                  </div>
                  <p className="text-xs text-fg-muted line-clamp-2 ">{getCatalogDescription(entry, i18n.language)}</p>
                  <div className="flex items-center gap-2 text-xs text-fg-muted mt-auto">
                    <span className={`px-1.5 py-0.5 rounded-tag text-xs font-medium ${LANG_COLORS[entry.language] || 'bg-subtle text-fg-secondary'}`}>{entry.language}</span>
                    <span className="flex items-center gap-0.5"><IconStar aria-hidden="true" className="w-4 h-4" />{entry.stars}</span>
                  </div>
                </div>
                <div className="border-t border-line px-4 py-2 flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                  {isActive ? (
                    <>
                      <button
                        onClick={() => setSelectedServer(isSelected ? null : entry.id)}
                        className={`flex-1 flex items-center justify-center gap-1 py-1 px-2 border rounded-control text-xs font-medium transition-colors ${isSelected ? 'border-line-control text-fg bg-subtle' : 'border-line-control text-fg-secondary hover:bg-hover'}`}
                      >
                        <IconSettings aria-hidden="true" className="w-4 h-4" /> {t('mcp.manage')}
                      </button>
                      <button onClick={(e) => handleDisconnect(entry.id, e)} className="ui-btn-icon inline-flex items-center justify-center flex items-center justify-center   border border-line-control text-fg-secondary rounded-control hover:bg-hover transition-colors" title={t('mcp.disconnectTitle')}>
                        <IconPlugOff aria-hidden="true" className="w-4 h-4" />
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
                        className="flex-1 flex items-center justify-center gap-1 py-1 px-2 bg-subtle text-fg rounded-control text-xs font-medium hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled transition-colors"
                      >
                        <IconDownload aria-hidden="true" className="w-4 h-4" />
                        {installing === entry.id ? t('mcp.configuring') : t('button.install')}
                      </button>
                      <a
                        href={`https://github.com/${entry.github}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center justify-center w-7 h-7 border border-line-control text-fg-muted rounded-control hover:bg-hover transition-colors"
                        title="GitHub"
                      >
                        <IconExternalLink aria-hidden="true" className="w-4 h-4" />
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
          <div className="fixed inset-0 bg-overlay-backdrop z-40" onClick={() => setCredModalEntry(null)} />
          <PageDialog onClose={() => setCredModalEntry(null)} className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="bg-surface rounded-card shadow-overlay w-full max-w-md" onClick={(e) => e.stopPropagation()}>
              <div className="px-6 py-4 border-b border-line">
                <h3 className="text-title-card font-semibold text-fg">{credModalEntry.name}</h3>
                <p className="text-sm text-fg-muted mt-1">{t('credentials.configNote')}</p>
              </div>
              <div className="px-6 py-4 space-y-4">
                {Object.entries(credModalEntry.env_vars).filter(([, spec]) => spec.secret).map(([key, spec]) => (
                  <div key={key}>
                    <label className="block text-sm font-medium text-fg-secondary mb-1">{key}</label>
                    <p className="text-xs text-fg-muted mb-1.5">{spec.description}</p>
                    <input
                      type="password"
                      value={credValues[key] || ''}
                      onChange={(e) => setCredValues(prev => ({ ...prev, [key]: e.target.value }))}
                      placeholder={t('credentials.enterField', { field: key })}
                      className="ui-input w-full px-3  text-sm border border-line-control rounded-control focus:border-transparent"
                    />
                  </div>
                ))}
              </div>
              <div className="px-6 py-4 border-t border-line flex gap-3 justify-end">
                <button
                  onClick={() => setCredModalEntry(null)}
                  className="px-4 py-2 text-sm text-fg-secondary border border-line-control rounded-control hover:bg-hover transition-colors"
                >{t('button.cancel')}</button>
                <button
                  onClick={handleCredSubmit}
                  disabled={installing === credModalEntry.id}
                  className="ui-btn ui-btn-primary"
                >
                  {installing === credModalEntry.id ? t('mcp.configuring') : t('button.confirmConfig')}
                </button>
              </div>
            </div>
          </PageDialog>
        </>
      )}

      {/* Server Detail Drawer */}
      {selectedServer && (selectedServerData || selectedServerObj) && (() => {
        const drawerServer = selectedServerData || selectedServerObj!;
        const catEntry = catalogEntries.find(e => e.id === selectedServer);
        const displayName = catEntry?.name || drawerServer.name;
        return (
          <PageDialog variant="panels" className="contents" onClose={() => { setSelectedServer(null); setSelectedServerData(null); setSelectedToolFromMCP(null); }}>
            <div className="fixed inset-0 bg-overlay-backdrop z-40" onClick={() => { setSelectedServer(null); setSelectedServerData(null); setSelectedToolFromMCP(null); }} />

            {selectedToolFromMCP && (
              <MCPToolDetailPanel
                tool={selectedToolFromMCP}
                onClose={() => setSelectedToolFromMCP(null)}
              />
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
                      <a href={`https://github.com/${catEntry.github}`} target="_blank" rel="noopener noreferrer" className="text-info hover:text-info flex items-center gap-0.5">
                        <IconExternalLink aria-hidden="true" className="w-4 h-4" /> GitHub
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
          </PageDialog>
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
            className={`flex items-center gap-1.5 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
              detailTab === tab.key
                ? 'border-line-control text-fg'
                : 'border-transparent text-fg-muted hover:text-fg-secondary'
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
              const detailFormData: MCPFormData = buildMCPFormDataFromConfig(
                server.name,
                serverDetail?.config,
                server.url,
              );
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
              {server.status === 'connected' ? (
                <button onClick={onDisconnect} className="inline-flex items-center gap-2 px-4 py-2.5 border border-line-control text-fg-secondary rounded-control hover:bg-hover text-sm font-medium transition-colors">
                  <IconPlugOff aria-hidden="true" className="w-4 h-4" />{t('detail.disconnectConn')}
                </button>
              ) : (
                <button onClick={onConnect} className="ui-btn ui-btn-primary">
                  <IconPower aria-hidden="true" className="w-4 h-4" />{t('detail.connectConn')}
                </button>
              )}
              {onRemove && (
                <button
                  onClick={onRemove}
                  className="inline-flex items-center gap-2 px-4 py-2.5 border border-line-control text-fg-secondary rounded-control hover:bg-hover text-sm font-medium transition-colors"
                >
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
                    <tr
                      key={tool.name}
                      onClick={() => onSelectTool(tool)}
                      className="hover:bg-hover cursor-pointer transition-colors"
                    >
                      <td className="px-4 py-3">
                        <span className="text-sm font-medium text-fg font-mono break-all">{tool.name}</span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-sm text-fg-secondary line-clamp-2 ">{tool.description}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : (
          (!serverDetail?.resources || serverDetail.resources.length === 0) ? (
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
  const [fixtures, setFixtures] = useState<ToolFixture[]>([]);
  const [fixturesLoading, setFixturesLoading] = useState(true);

  useEffect(() => {
    setFixturesLoading(true);
    toolAPI.listFixtures(tool.name)
      .then((res) => setFixtures(res.data ?? []))
      .catch(() => setFixtures([]))
      .finally(() => setFixturesLoading(false));
  }, [tool.name]);

  const handleTest = async () => {
    try {
      setTesting(true);
      setTestResult(null);
      const params = JSON.parse(testParams);
      const res = await toolAPI.test(tool.name, params);
      setTestResult({ success: res.data.success, data: res.data.output, error: res.data.error });
    } catch (err: any) {
      setTestResult({ success: false, error: err.response?.data?.detail ?? err.message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div
      className="fixed top-0 bottom-0 z-50 flex flex-col bg-surface shadow-overlay border-r border-line"
      style={{ right: DETAIL_DRAWER_WIDTH, width: TOOL_PANEL_WIDTH }}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex-shrink-0 border-b border-line">
        <div className="flex items-center gap-3 px-6 py-4">
          <div className="w-8 h-8 bg-subtle rounded-control flex items-center justify-center flex-shrink-0">
            <IconTool aria-hidden="true" className="w-4 h-4 text-fg-secondary" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-title-section font-semibold text-fg font-mono truncate">{tool.name}</h2>
            {tool.source_name && (
              <p className="text-xs text-fg-muted mt-0.5">{tool.source_name}</p>
            )}
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
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                section === tab
                  ? 'border-line-control text-fg'
                  : 'border-transparent text-fg-muted hover:text-fg-secondary hover:border-line-control'
              }`}
            >
              {tab === 'info' ? <><IconInfoCircle aria-hidden="true" className="w-4 h-4" />{t('toolDetail.tabInfo')}</> : <><IconTestPipe aria-hidden="true" className="w-4 h-4" />{t('toolDetail.tabTest')}</>}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {section === 'info' ? (
          <div className="space-y-5">
            <div>
              <label className="block text-sm font-medium text-fg-secondary mb-1.5">{t('toolDetail.description')}</label>
              <p className="text-sm text-fg-secondary ">{tool.description || t('toolDetail.noDescription')}</p>
            </div>
            {tool.parameters && tool.parameters.length > 0 && (
              <div>
                <label className="block text-sm font-medium text-fg-secondary mb-2">
                  {t('toolDetail.params', { count: tool.parameters.length })}
                </label>
                <div className="rounded-control border border-line overflow-hidden">
                  <table className="min-w-full divide-y divide-line">
                    <thead className="bg-subtle">
                      <tr>
                        <th className="px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('toolDetail.paramName')}</th>
                        <th className="px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('toolDetail.paramType')}</th>
                        <th className="px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('toolDetail.paramRequired')}</th>
                        <th className="px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('toolDetail.paramDesc')}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line bg-surface">
                      {tool.parameters.map((param: any, idx: number) => (
                        <tr key={idx}>
                          <td className="px-4 py-2.5">
                            <code className="text-xs font-mono text-fg bg-subtle px-1.5 py-0.5 rounded-tag">{param.name}</code>
                          </td>
                          <td className="px-4 py-2.5 text-xs text-fg-muted">{param.type}</td>
                          <td className="px-4 py-2.5">
                            {param.required
                              ? <span className="text-xs text-fg-secondary font-medium">{t('toolDetail.yes')}</span>
                              : <span className="text-xs text-fg-muted">{t('toolDetail.no')}</span>}
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
              <label className="block text-sm font-medium text-fg-secondary mb-1.5">{t('toolDetail.selectFixture')}</label>
              <select
                value=""
                disabled={fixturesLoading || fixtures.length === 0}
                onChange={(e) => {
                  const idx = parseInt(e.target.value, 10);
                  if (!isNaN(idx) && fixtures[idx]) {
                    setTestParams(JSON.stringify(fixtures[idx].params, null, 2));
                  }
                }}
                className="ui-select w-full px-3  border border-line-control rounded-control text-sm bg-surface text-fg-muted disabled:bg-subtle disabled:cursor-not-allowed"
              >
                <option value="">
                  {fixturesLoading ? t('toolDetail.updating') : t('toolDetail.selectFixturePlaceholder')}
                </option>
                {fixtures.map((f, idx) => (
                  <option key={idx} value={idx}>
                    {f.tags.includes('smoke') ? '✦ ' : ''}{getLocalizedFixtureLabel(f, i18n.language)}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-fg-secondary mb-2">{t('toolDetail.testParams')}</label>
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
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-subtle text-fg rounded-control hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed font-medium text-sm transition-colors"
            >
              {testing
                ? <><IconRefresh aria-hidden="true" className="w-4 h-4 animate-spin" />{t('toolDetail.executing')}</>
                : <><IconPlayerPlay aria-hidden="true" className="w-4 h-4" />{t('toolDetail.runTest')}</>}
            </button>
            {!tool.enabled && (
              <p className="text-xs text-warning text-center">{t('toolDetail.disabledNote')}</p>
            )}
            {testResult && (
              <div>
                <label className="block text-sm font-medium text-fg-secondary mb-2">{t('toolDetail.testResult')}</label>
                <div className={`rounded-control border p-4 ${testResult.success ? 'bg-success-soft border-success' : 'bg-danger-soft border-danger'}`}>
                  <div className="flex items-center mb-2">
                    {testResult.success
                      ? <IconCircleCheck aria-hidden="true" className="w-4 h-4 text-success mr-2" />
                      : <IconCircleX aria-hidden="true" className="w-4 h-4 text-danger mr-2" />}
                    <span className={`text-sm font-medium ${testResult.success ? 'text-success' : 'text-danger'}`}>
                      {testResult.success ? t('toolDetail.execSuccess') : t('toolDetail.execFailed')}
                    </span>
                  </div>
                  <pre className="text-xs overflow-auto max-h-48 whitespace-pre-wrap break-all text-fg-secondary bg-surface rounded-tag p-2">
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
      <div className="bg-surface rounded-control border border-line p-4">
        <div className="flex justify-center py-4">
          <LoadingSpinner />
        </div>
      </div>
    );
  }

  return (
    <div className="bg-surface rounded-control border border-line p-4">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold text-fg flex items-center">
          <IconShield aria-hidden="true" className="w-4 h-4 mr-2 text-fg-muted" />
          {t('credentials.title')}
        </h4>
        {credentials?.has_credential && !editing && (
          <button
            onClick={handleDelete}
            className="text-xs text-fg-secondary hover:text-fg"
          >
            {t('credentials.delete')}
          </button>
        )}
      </div>

      {!editing ? (
        <div className="space-y-3">
          {credentials?.has_credential && credentials.secret_id && (
            <div className="flex items-center justify-between">
              <span className="text-sm text-fg-muted">Secret ID</span>
              <code className="text-sm font-mono bg-subtle px-2 py-1 rounded-tag">
                {credentials.secret_id}
              </code>
            </div>
          )}
          
          {credentials?.api_key_masked && (
            <div className="flex items-center justify-between">
              <span className="text-sm text-fg-muted">API Key</span>
              <code className="text-sm font-mono bg-subtle px-2 py-1 rounded-tag">
                {credentials.api_key_masked}
              </code>
            </div>
          )}

          <div className="flex gap-2 pt-2">
            <button
              onClick={() => setEditing(true)}
              className="flex-1 px-3 py-2 border border-line-control rounded-control text-sm text-fg-secondary hover:bg-hover"
            >
              {credentials?.has_credential ? t('credentials.modify') : t('credentials.configure')}
            </button>
            {credentials?.has_credential && (
              <button
                onClick={handleTest}
                className="flex-1 px-3 py-2 border border-line-control rounded-control text-sm text-fg-secondary hover:bg-hover"
              >
                {t('button.test')}
              </button>
            )}
          </div>

          {testResult && (
            <div className={`p-2.5 rounded-tag text-xs ${
              testResult.success 
                ? 'bg-success-soft border border-success text-success'
                : 'bg-danger-soft border border-danger text-danger'
            }`}>
              <div className="flex items-start">
                {testResult.success ? (
                  <IconCircleCheck aria-hidden="true" className="w-4 h-4 mr-1.5 mt-0.5 flex-shrink-0" />
                ) : (
                  <IconCircleX aria-hidden="true" className="w-4 h-4 mr-1.5 mt-0.5 flex-shrink-0" />
                )}
                <div className="flex-1">
                  <div>{testResult.message}</div>
                  {testResult.latency_ms != null && (
                    <div className="mt-1 text-fg-muted">{t('credentials.latency', { latency: testResult.latency_ms })}</div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-fg-muted mb-1.5">API Key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={t('credentials.enterKey')}
              className="ui-input w-full px-3  border border-line-control rounded-control text-sm font-mono"
            />
          </div>

          <div className="flex gap-2 pt-2">
            <button
              onClick={handleSave}
              disabled={saving}
              className="ui-btn ui-btn-primary flex-1"
            >
              {saving ? t('credentials.saving') : t('credentials.save')}
            </button>
            <button
              onClick={() => {
                setEditing(false);
                setApiKey('');
              }}
              className="px-3 py-2 border border-line-control rounded-control text-sm text-fg-secondary hover:bg-hover"
            >
              {t('credentials.cancel')}
            </button>
          </div>

          <div className="p-2 bg-warning-soft border border-warning rounded-tag text-xs text-warning">
            <div className="flex items-start">
              <IconInfoCircle aria-hidden="true" className="w-4 h-4 mr-1.5 mt-0.5 flex-shrink-0" />
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
          className={`px-2.5 py-1 rounded-pill text-xs font-medium transition-colors ${selectedCategory === 'all' ? 'bg-ai-soft text-ai' : 'bg-subtle text-fg-secondary hover:bg-hover'}`}
        >{t('api.filterAll')}</button>
        {Object.entries(catalogCategories).map(([id, cat]) =>
          catalogCategoryCounts[id] ? (
            <button key={id} onClick={() => setSelectedCategory(id)} className={`px-2.5 py-1 rounded-pill text-xs font-medium transition-colors ${selectedCategory === id ? 'bg-ai-soft text-ai' : 'bg-subtle text-fg-secondary hover:bg-hover'}`}>
              {cat.label} ({catalogCategoryCounts[id]})
            </button>
          ) : null
        )}
      </div>

      {modules.length === 0 && filteredCatalog.length === 0 ? (
        <EmptyState icon={<IconCloud aria-hidden="true" className="w-8 h-8" />} title={t('api.noTools')} description={t('api.noToolsDesc')} />
      ) : (
        <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {modules.map((moduleName) => {
            const moduleTools = toolsByModule[moduleName];
            const isSelected = selectedModule === moduleName;
            const svcStatus = apiStatuses[moduleName]?.status;
            const borderColor = svcStatus === 'healthy' ? 'border-l-success' : svcStatus === 'error' ? 'border-l-danger' : 'border-l-line-control';
            return (
              <div
                key={moduleName}
                onClick={() => setSelectedModule(isSelected ? null : moduleName)}
                className={`relative bg-surface rounded-card border border-l-4 ${borderColor} overflow-hidden cursor-pointer h-[180px] flex flex-col transition-all duration-150 ${isSelected ? 'border-line-control ring-2 ring-focus' : 'border-line shadow-sm hover:border-line-control'}`}
              >
                <div className="flex-1 px-4 pt-4 pb-2 min-h-0 flex flex-col gap-1.5">
                  <div className="flex items-start gap-1.5 flex-wrap">
                    <span className="text-sm font-semibold text-fg truncate max-w-[120px] capitalize">{moduleName}</span>
                    <span className="px-1.5 py-0.5 text-xs font-medium rounded-pill shrink-0 bg-success-soft text-success">
                      {t('statusBadge.active')}
                    </span>
                    <span className="px-1.5 py-0.5 bg-ai-soft text-ai text-xs font-medium rounded-pill shrink-0">API</span>
                  </div>
                  <div className="flex items-center gap-1 text-xs text-fg-muted mt-auto">
                    <IconTool aria-hidden="true" className="w-4 h-4 shrink-0" /><span>{moduleTools.length} {t('api.tools')}</span>
                    {apiStatuses[moduleName]?.latency_ms != null && (
                      <span className="ml-auto text-xs text-fg-muted">{apiStatuses[moduleName].latency_ms}ms</span>
                    )}
                  </div>
                </div>
                <div className="border-t border-line px-4 py-2 flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                  <button onClick={() => setSelectedModule(isSelected ? null : moduleName)} className={`flex-1 flex items-center justify-center gap-1 py-1 px-2 border rounded-control text-xs font-medium transition-colors ${isSelected ? 'border-ai text-ai bg-ai-soft' : 'border-line-control text-fg-secondary hover:bg-hover'}`}>
                    <IconSettings aria-hidden="true" className="w-4 h-4" /> {t('api.manage')}
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
                className={`relative bg-surface rounded-card border overflow-hidden cursor-default h-[180px] flex flex-col transition-all duration-150 ${isSelected ? 'border-line-control ring-2 ring-focus' : 'border-line shadow-sm'}`}
                style={{ borderLeftWidth: 4, borderLeftColor: 'var(--border-subtle)' }}
              >
                <div className="flex-1 px-4 pt-4 pb-2 min-h-0 flex flex-col gap-1.5">
                  <div className="flex items-start gap-1.5 flex-wrap">
                    <span className="text-sm font-semibold text-fg truncate max-w-[120px]">{entry.name}</span>
                    <span className="px-1.5 py-0.5 text-xs font-medium rounded-pill shrink-0 bg-subtle text-fg-secondary">
                      {t('statusBadge.inactive')}
                    </span>
                    <span className="px-1.5 py-0.5 bg-ai-soft text-ai text-xs font-medium rounded-pill shrink-0">API</span>
                    {PRIORITY_IDS.has(entry.id) && (
                      <span className="px-1.5 py-0.5 bg-warning-soft text-warning text-xs font-medium rounded-pill shrink-0">{t('api.recommended')}</span>
                    )}
                  </div>
                  <p className="text-xs text-fg-muted line-clamp-2 ">{getCatalogDescription(entry, i18n.language)}</p>
                  <div className="flex items-center gap-2 text-xs text-fg-muted mt-auto">
                    <span className={`px-1.5 py-0.5 rounded-tag text-xs font-medium ${LANG_COLORS[entry.language] || 'bg-subtle text-fg-secondary'}`}>{entry.language}</span>
                    <span className="flex items-center gap-0.5"><IconStar aria-hidden="true" className="w-4 h-4" />{entry.stars}</span>
                  </div>
                </div>
                <div className="border-t border-line px-4 py-2 flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => {
                      if (entry.requires_auth && !isConfigured(entry.id)) {
                        openCredModal(entry);
                      } else {
                        handleInstallNoAuth(entry);
                      }
                    }}
                    disabled={installing === entry.id}
                    className="flex-1 flex items-center justify-center gap-1 py-1 px-2 bg-subtle text-fg rounded-control text-xs font-medium hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled transition-colors"
                  >
                    <IconDownload aria-hidden="true" className="w-4 h-4" />
                    {installing === entry.id ? t('api.configuring') : t('button.install')}
                  </button>
                  <a
                    href={`https://github.com/${entry.github}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center justify-center w-7 h-7 border border-line-control text-fg-muted rounded-control hover:bg-hover transition-colors"
                    title="GitHub"
                  >
                    <IconExternalLink aria-hidden="true" className="w-4 h-4" />
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
          <div className="fixed inset-0 bg-overlay-backdrop z-40" onClick={() => setCredModalEntry(null)} />
          <PageDialog onClose={() => setCredModalEntry(null)} className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="bg-surface rounded-card shadow-overlay w-full max-w-md" onClick={(e) => e.stopPropagation()}>
              <div className="px-6 py-4 border-b border-line">
                <h3 className="text-title-card font-semibold text-fg">{credModalEntry.name}</h3>
                <p className="text-sm text-fg-muted mt-1">{t('credentials.configNote')}</p>
              </div>
              <div className="px-6 py-4 space-y-4">
                {Object.entries(credModalEntry.env_vars).filter(([, spec]) => spec.secret).map(([key, spec]) => (
                  <div key={key}>
                    <label className="block text-sm font-medium text-fg-secondary mb-1">{key}</label>
                    <p className="text-xs text-fg-muted mb-1.5">{spec.description}</p>
                    <input
                      type="password"
                      value={credValues[key] || ''}
                      onChange={(e) => setCredValues(prev => ({ ...prev, [key]: e.target.value }))}
                      placeholder={t('credentials.enterField', { field: key })}
                      className="ui-input w-full px-3  text-sm border border-line-control rounded-control focus:border-transparent"
                    />
                  </div>
                ))}
              </div>
              <div className="px-6 py-4 border-t border-line flex gap-3 justify-end">
                <button onClick={() => setCredModalEntry(null)} className="px-4 py-2 text-sm text-fg-secondary border border-line-control rounded-control hover:bg-hover transition-colors">{t('button.cancel')}</button>
                <button
                  onClick={handleCredSubmit}
                  disabled={installing === credModalEntry.id}
                  className="px-4 py-2 text-sm text-action-fg bg-action rounded-control hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled transition-colors"
                >
                  {installing === credModalEntry.id ? t('api.configuring') : t('button.confirmConfig')}
                </button>
              </div>
            </div>
          </PageDialog>
        </>
      )}

      {/* API Service Detail Drawer */}
      {selectedModule && (() => {
        const catalogEntry = catalogEntries.find(e => e.id === selectedModule);
        const displayName = catalogEntry?.name || selectedModule;
        const isCatalogOnly = !!catalogEntry && !modules.includes(selectedModule);
        return (
          <PageDialog variant="panels" className="contents" onClose={() => setSelectedModule(null)}>
            <div className="fixed inset-0 bg-overlay-backdrop z-40" onClick={() => setSelectedModule(null)} />
            <div data-ui-overlay="drawer" className="fixed right-0 top-0 bottom-0 z-50 flex flex-col w-full bg-surface shadow-overlay" style={{ maxWidth: DETAIL_DRAWER_WIDTH }} onClick={(e) => e.stopPropagation()}>
              <div className="flex-shrink-0 border-b border-line">
                <div className="flex items-center gap-3 px-6 py-4">
                  <div className="w-10 h-10 rounded-control flex items-center justify-center flex-shrink-0 bg-ai-soft">
                    <IconCloud aria-hidden="true" className="w-5 h-5 text-ai" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <h2 className="text-title-section font-semibold text-fg">{displayName}</h2>
                    <p className="text-sm text-fg-muted">{t('api.serviceConfig')}</p>
                  </div>
                  <button aria-label={pageActionLabel('close')} onClick={() => setSelectedModule(null)} className="ui-btn-icon inline-flex items-center justify-center text-fg-muted hover:text-fg-secondary p-2 rounded-control hover:bg-hover flex-shrink-0">
                    <IconX aria-hidden="true" className="w-5 h-5" />
                  </button>
                </div>
              </div>
              <div className="flex-1 overflow-y-auto">
                {catalogEntry && (
                  <div className="px-6 py-4 bg-subtle border-b border-line">
                    <p className="text-sm text-fg-secondary">{getCatalogDescription(catalogEntry, i18n.language)}</p>
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
          </PageDialog>
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
    <div className="bg-surface" onClick={(e) => e.stopPropagation()}>
      <div className="p-5">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Left: Service Info */}
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
                  <a href={`https://github.com/${entry.github}`} target="_blank" rel="noopener noreferrer" className="text-sm text-info hover:text-info">{entry.github}</a>
                </div>
                {entry.requires_auth && (
                  <div className="pt-2 border-t border-line">
                    <span className="text-sm text-fg-muted block mb-2">{t('serviceInfo.requiredKeys')}</span>
                    {Object.entries(entry.env_vars).filter(([,s]) => s.secret).map(([key, spec]) => (
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
                      {entry.tags.map((tag: string) => (
                        <span key={tag} className="px-2 py-0.5 bg-subtle text-fg-secondary text-xs rounded-tag">#{tag}</span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Right: Quick Actions & Test */}
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
                  className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 border rounded-control text-sm font-medium transition-colors ${
                    testing ? 'border-danger bg-danger-soft text-danger cursor-not-allowed' : 'bg-surface border-line-control text-fg-secondary hover:bg-hover'
                  }`}
                >
                  {testing ? (
                    <><IconRefresh aria-hidden="true" className="w-4 h-4 animate-spin" />{t('button.testing')}</>
                  ) : (
                    <><IconActivity aria-hidden="true" className="w-4 h-4" />{t('button.testConnectivity')}</>
                  )}
                </button>
                <button
                  onClick={handleConnect}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-subtle text-fg rounded-control text-sm font-medium hover:bg-hover transition-colors"
                >
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
                  {testResult.success ? (
                    <IconCircleCheck aria-hidden="true" className="w-4 h-4 text-success mr-2 mt-0.5 flex-shrink-0" />
                  ) : (
                    <IconCircleX aria-hidden="true" className="w-4 h-4 text-danger mr-2 mt-0.5 flex-shrink-0" />
                  )}
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

// ============================================================================
// KvRow helper
// ============================================================================

function KvRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center py-2.5 px-4 gap-4">
      <span className="text-sm text-fg-muted shrink-0">{label}</span>
      <span className="text-sm text-fg truncate text-right">{value}</span>
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
    <div className="bg-surface" onClick={(e) => e.stopPropagation()}>
      <div className="flex items-center border-b border-line px-5">
        {([
          { key: 'overview' as const, label: t('detail.tabs.overview'), icon: <IconInfoCircle aria-hidden="true" className="w-4 h-4" /> },
          { key: 'tools' as const, label: t('detail.tabs.tools', { count: serviceTools.length }), icon: <IconTool aria-hidden="true" className="w-4 h-4" /> },
        ]).map((tab) => (
          <button
            key={tab.key}
            onClick={() => setDetailTab(tab.key)}
            className={`flex items-center gap-1.5 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
              detailTab === tab.key
                ? 'border-ai text-ai'
                : 'border-transparent text-fg-muted hover:text-fg-secondary'
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
            <div className="rounded-control border border-line divide-y divide-line">
              {(() => {
                const status = quickTesting
                  ? 'testing'
                  : quickTestResult != null
                    ? (quickTestResult.success ? 'connected' : 'error')
                    : (initialStatus?.status || 'unknown');
                const cfg: Record<string, { label: string; cls: string; dot: string }> = {
                  connected: { label: t('statusBadge.connected'), cls: 'bg-success-soft text-success', dot: 'bg-success' },
                  healthy:   { label: t('statusBadge.connected'), cls: 'bg-success-soft text-success', dot: 'bg-success' },
                  testing:   { label: t('button.testing'), cls: 'bg-warning-soft text-warning', dot: 'bg-warning animate-pulse' },
                  error:     { label: t('statusBadge.error'), cls: 'bg-danger-soft text-danger', dot: 'bg-danger' },
                  unknown:   { label: t('statusBadge.unknown'), cls: 'bg-subtle text-fg-secondary', dot: 'bg-subtle' },
                };
                const c = cfg[status] || cfg.unknown;
                return (
                  <div className="flex justify-between items-center py-2.5 px-4 gap-4">
                    <span className="text-sm text-fg-muted shrink-0">{t('detail.connectionStatus')}</span>
                    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-pill text-xs font-medium ${c.cls}`}>
                      <span className={`w-1.5 h-1.5 rounded-pill ${c.dot}`} />
                      {c.label}
                      {quickTestResult?.success && quickTestResult.latency_ms != null && (
                        <span className="text-fg-muted">{'\u00b7'} {quickTestResult.latency_ms}ms</span>
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
                  <span className="text-sm text-fg-muted shrink-0">{t('serviceInfo.documentation')}</span>
                  <a href={metadata.docs_url} target="_blank" rel="noopener noreferrer" className="text-sm text-info hover:text-info">
                    {t('serviceInfo.viewDocs')} →
                  </a>
                </div>
              )}
              {credentials?.secret_id && (
                <div className="flex justify-between items-center py-2.5 px-4 gap-4">
                  <span className="text-sm text-fg-muted shrink-0">Secret ID</span>
                  <code className="text-sm font-mono bg-subtle px-2 py-1 rounded-tag truncate">{credentials.secret_id}</code>
                </div>
              )}
              {credentials !== null && (
                <div className="flex items-center py-2 px-4 gap-3">
                  <span className="text-sm text-fg-muted shrink-0">API Key</span>
                  <div className="flex-1 flex items-center gap-1.5 min-w-0">
                    <input
                      type={showApiKey ? 'text' : 'password'}
                      value={apiKeyInput}
                      onChange={(e) => setApiKeyInput(e.target.value)}
                      placeholder={t('serviceInfo.enterApiKey')}
                      className="ui-input flex-1 min-w-0 px-2  border border-line rounded-tag text-sm font-mono bg-subtle focus:bg-surface focus:border-line-control transition-colors"
                    />
                    <button
                      type="button"
                      onClick={() => setShowApiKey(v => !v)}
                      className="ui-btn-icon inline-flex items-center justify-center shrink-0 text-fg-muted hover:text-fg-secondary p-1 rounded-control transition-colors"
                      title={showApiKey ? t('detail.hide') : t('detail.show')}
                    >
                      {showApiKey ? <IconEyeOff aria-hidden="true" className="w-4 h-4" /> : <IconEye aria-hidden="true" className="w-4 h-4" />}
                    </button>
                    {apiKeyInput !== apiKeyOrigRef.current && (
                      <>
                        <button
                          type="button"
                          onClick={handleSaveApiKey}
                          disabled={apiKeySaving || !apiKeyInput.trim()}
                          className="ui-btn ui-btn-primary ui-btn-sm shrink-0"
                        >
                          {apiKeySaving ? <IconRefresh aria-hidden="true" className="w-4 h-4 animate-spin" /> : <IconDeviceFloppy aria-hidden="true" className="w-4 h-4" />}
                          {t('button.save')}
                        </button>
                        <button
                          type="button"
                          onClick={() => setApiKeyInput(apiKeyOrigRef.current)}
                          className="shrink-0 px-2 py-1 border border-line-control text-fg-secondary rounded-tag text-xs hover:bg-hover transition-colors"
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
                className="inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-surface border border-line-control text-fg-secondary rounded-control hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled text-sm font-medium transition-colors"
              >
                {quickTesting ? <><IconRefresh aria-hidden="true" className="w-4 h-4 animate-spin" />{t('button.testing')}</> : <><IconActivity aria-hidden="true" className="w-4 h-4" />{t('button.testConnectivity')}</>}
              </button>
              {!quickTesting && quickTestResult && (
                <div className={`flex items-start gap-2 rounded-control border p-3 text-sm ${quickTestResult.success ? 'bg-success-soft border-success text-success' : 'bg-danger-soft border-danger text-danger'}`}>
                  {quickTestResult.success ? <IconCircleCheck aria-hidden="true" className="w-4 h-4 mt-0.5 flex-shrink-0" /> : <IconCircleX aria-hidden="true" className="w-4 h-4 mt-0.5 flex-shrink-0" />}
                  <span>{quickTestResult.message}</span>
                  {quickTestResult.latency_ms != null && <span className="text-xs text-fg-muted"> {'\u00b7'} {quickTestResult.latency_ms}ms</span>}
                </div>
              )}
            </div>
          </div>
        ) : (
          serviceTools.length === 0 ? (
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
                        <td className="px-5 py-2.5 whitespace-nowrap"><button onClick={() => onSelectTool(tool)} className="text-sm text-info hover:text-info">{t('detail.testDetail')}</button></td>
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
      <div className="bg-surface rounded-control border border-line p-4">
        <div className="flex justify-center py-4">
          <LoadingSpinner />
        </div>
      </div>
    );
  }

  return (
    <div className="bg-surface rounded-control border border-line p-4">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-semibold text-fg flex items-center">
          <IconShield aria-hidden="true" className="w-4 h-4 mr-2 text-fg-muted" />
          {t('credentials.title')}
        </h4>
        {credentials?.has_credential && !editing && (
          <button
            onClick={handleDelete}
            className="text-xs text-fg-secondary hover:text-fg"
          >
            {t('credentials.delete')}
          </button>
        )}
      </div>

      {!editing ? (
        <div className="space-y-3">
          {credentials?.has_credential && credentials.secret_id && (
            <div className="flex items-center justify-between">
              <span className="text-sm text-fg-muted">Secret ID</span>
              <code className="text-sm font-mono bg-subtle px-2 py-1 rounded-tag">
                {credentials.secret_id}
              </code>
            </div>
          )}
          
          {credentials?.api_key_masked && (
            <div className="flex items-center justify-between">
              <span className="text-sm text-fg-muted">API Key</span>
              <code className="text-sm font-mono bg-subtle px-2 py-1 rounded-tag">
                {credentials.api_key_masked}
              </code>
            </div>
          )}

          <div className="flex gap-2 pt-2">
            <button
            onClick={() => setEditing(true)}
            className="flex-1 px-3 py-2 border border-line-control rounded-control text-sm text-fg-secondary hover:bg-hover"
          >
            {credentials?.has_credential ? t('credentials.modify') : t('credentials.configure')}
          </button>
            {credentials?.has_credential && (
              <button
                onClick={handleTest}
                disabled={testing}
                className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 border rounded-control text-sm transition-colors ${
                  testing
                    ? 'border-danger bg-danger-soft text-danger cursor-not-allowed'
                    : 'border-line-control text-fg-secondary hover:bg-hover'
                }`}
                title={t('credentials.testConnTitle')}
              >
                {testing ? (
                  <><IconRefresh aria-hidden="true" className="w-4 h-4 animate-spin" />{t('credentials.testing')}</>
                ) : (
                  <><IconActivity aria-hidden="true" className="w-4 h-4" />{t('credentials.testConn')}</>
                )}
              </button>
            )}
          </div>

          {/* Testing in-progress indicator */}
          {testing && (
            <div className="p-3 rounded-control border border-danger bg-danger-soft text-danger">
              <div className="flex items-center">
                <div className="relative mr-3">
                  <div className="w-8 h-8 rounded-pill border-2 border-danger border-t-danger animate-spin" />
                </div>
                <div className="flex-1">
                  <div className="text-sm font-medium">{t('credentials.testingProgress')}</div>
                  <div className="text-xs mt-0.5 text-danger">
                    {t('credentials.testingWait')}
                    <span className="ml-2 font-mono tabular-nums">{testElapsed.toFixed(1)}s</span>
                  </div>
                </div>
              </div>
              {/* Progress bar animation */}
              <div className="mt-2.5 h-1 bg-danger-soft rounded-pill overflow-hidden">
                <div className="h-full bg-danger rounded-pill animate-pulse" style={{ width: `${Math.min(95, testElapsed * 8)}%`, transition: 'width 0.3s ease-out' }} />
              </div>
            </div>
          )}

          {/* Test result */}
          {!testing && testResult && (
            <div className={`p-2.5 rounded-tag text-xs ${
              testResult.success 
                ? 'bg-success-soft border border-success text-success'
                : 'bg-danger-soft border border-danger text-danger'
            }`}>
              <div className="flex items-start">
                {testResult.success ? (
                  <IconCircleCheck aria-hidden="true" className="w-4 h-4 mr-1.5 mt-0.5 flex-shrink-0" />
                ) : (
                  <IconCircleX aria-hidden="true" className="w-4 h-4 mr-1.5 mt-0.5 flex-shrink-0" />
                )}
                <div className="flex-1">
                  <div>{testResult.message}</div>
                  {testResult.latency_ms != null && (
                    <div className="mt-1 text-fg-muted">{t('credentials.latency')}: {testResult.latency_ms}ms</div>
                  )}
                  {testResult.tool_tested && (
                    <div className="mt-1 text-fg-muted text-xs">{t('credentials.toolTested')}: {testResult.tool_tested}</div>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div>
            <label className="block text-xs text-fg-muted mb-1.5">API Key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={t('credentials.apiKeyPlaceholder')}
              className="ui-input w-full px-3  border border-line-control rounded-control text-sm font-mono"
            />
          </div>

          <div className="flex gap-2 pt-2">
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex-1 px-3 py-2 bg-action text-action-fg rounded-control text-sm disabled:bg-disabled disabled:text-fg-disabled hover:bg-action-hover"
            >
              {saving ? t('credentials.saving') : t('credentials.save')}
            </button>
            <button
              onClick={() => {
                setEditing(false);
                setApiKey('');
              }}
              className="px-3 py-2 border border-line-control rounded-control text-sm text-fg-secondary hover:bg-hover"
            >
              {t('common:button.cancel')}
            </button>
          </div>

          <div className="p-2 bg-warning-soft border border-warning rounded-tag text-xs text-warning">
            <div className="flex items-start">
              <IconInfoCircle aria-hidden="true" className="w-4 h-4 mr-1.5 mt-0.5 flex-shrink-0" />
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
  groupDrag,
  viewMode,
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
  groupDrag: GroupDrag;
  viewMode: 'list' | 'cards';
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
  const { t } = useTranslation('tool');
  // Column layout, left-to-right.  Every data column uses ``minmax(min, fr)``
  // so any leftover width on wide screens is shared *proportionally* across
  // the table rather than dumped into a single gap between "name" and
  // "source".  Long names truncate via ``truncate`` on the inner button.
  //
  //   1) 32px                       icon
  //   2) minmax(220px, 3fr)         tool name (dominant column, truncates)
  //   3) minmax(80px,  1fr)         source badge
  //   4) minmax(140px, 1.6fr)       provider / source name
  //   5) minmax(80px,  1fr)         enabled badge
  //   6) minmax(90px,  1fr)         actions button
  //
  // Ratio 3 : 1 : 1.6 : 1 : 1  ≈ keeps the name comfortably wide while
  // distributing the rest so the small badge columns don't feel orphaned.
  // Actions hold the group button and 管理 side by side (44px + the text button).
  const GRID_COLS =
    '32px minmax(220px, 3fr) minmax(80px, 1fr) minmax(140px, 1.6fr) minmax(80px, 1fr) minmax(124px, 1.2fr)';

  return (
    <div className="bg-surface rounded-control border border-line overflow-x-auto flex flex-col">
      {/* Header (min-w keeps the grid columns intact; the wrapper scrolls on phones) */}
      <div
        className={viewMode === 'list'
          ? 'grid min-w-[720px] items-center gap-3 px-4 py-2.5 bg-subtle border-b border-line text-xs font-medium text-fg-muted uppercase tracking-wide'
          : 'flex flex-wrap items-center gap-4 px-4 py-2.5 bg-subtle border-b border-line text-xs font-medium text-fg-muted uppercase tracking-wide [&>:first-child]:hidden [&>:nth-child(2)]:hidden [&>:last-child]:hidden'}
        style={viewMode === 'list' ? { gridTemplateColumns: GRID_COLS } : undefined}
      >
        <div />
        <div>{t('table.toolName')}</div>
        <div className="text-center">
          <SortFilterHeader
            label={t('table.source')}
            field="source"
            sort={sort}
            filterValues={filterOptions.source}
            activeFilters={filters.source}
            onSort={onSort}
            onToggleFilter={onToggleFilter}
            onClearFilter={onClearFilter}
            renderLabel={(v) => getSourceLabel(v, t)}
            asDiv
          />
        </div>
        <div>
          <SortFilterHeader
            label={t('table.provider')}
            field="source_name"
            sort={sort}
            filterValues={filterOptions.source_name}
            activeFilters={filters.source_name}
            onSort={onSort}
            onToggleFilter={onToggleFilter}
            onClearFilter={onClearFilter}
            asDiv
          />
        </div>
        <div className="text-center">
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
            asDiv
          />
        </div>
        <div className="text-center">{t('table.actions')}</div>
      </div>

      {/* Both layouts retain the same native fields and action callbacks. */}
      <div className={viewMode === 'list' ? 'flex-1 divide-y divide-line' : 'grid gap-3 p-3 sm:grid-cols-2 xl:grid-cols-3'}>
        {tools.map((tool) => {
          const sb = SOURCE_BADGE[tool.source] || SOURCE_BADGE.custom;
          const sourceLabel = getSourceLabel(tool.source, t);
          const categoryLabel = t(`category.${CATEGORY_I18N_KEY[tool.category] || tool.category}`, { defaultValue: tool.category });

          return (
            <div
              key={tool.name}
              {...groupDrag.dragProps(tool.name)}
              className={`grid items-center gap-3 px-4 py-3 hover:bg-hover transition-colors${viewMode === 'cards' ? ' rounded-control border border-line bg-surface [&>:nth-child(n+3)]:col-span-2' : ' min-w-[720px]'}`}
              style={{ gridTemplateColumns: viewMode === 'list' ? GRID_COLS : '32px minmax(0, 1fr)' }}
            >
              {/* Icon */}
              <div className={`w-8 h-8 flex items-center justify-center rounded-control ${tool.enabled ? 'bg-subtle' : 'bg-subtle'}`}>
                <IconTool aria-hidden="true" className={`w-4 h-4 ${tool.enabled ? 'text-fg-secondary' : 'text-fg-muted'}`} />
              </div>

              {/* Name (no description, kept compact).
                  Name is a `<button>` rather than a `<span>` so the click
                  affordance matches the Hub catalog (see ``Hub/index.tsx``
                  HubTable row), letting users open the tool detail drawer
                  without hunting for the "manage" action on the right.
                  Font is `text-xs` (one step smaller than the previous
                  `text-sm`) because the Tool list is denser than other tabs
                  and the mono name otherwise visually overpowers the row. */}
              <div className="min-w-0 flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => onSelect(tool)}
                  title={tool.name}
                  className="text-xs font-medium text-fg font-mono truncate
 hover:text-fg-secondary transition-colors
 focus-visible:underline text-left min-w-0"
                >
                  {tool.name}
                </button>
                <span className="text-xs text-fg-muted shrink-0">{categoryLabel}</span>
              </div>

              {/* Source column */}
              <div className="text-center">
                <span className={`inline-flex items-center px-1.5 py-0.5 rounded-tag text-xs font-medium ${sb.className}`}>
                  {sourceLabel}
                </span>
              </div>

              {/* Provider column */}
              <div className="text-xs text-fg-secondary truncate">
                {tool.source_name || 'Flocks'}
              </div>

              {/* Status column */}
              <div className="text-center">
                <EnabledBadge enabled={tool.enabled} />
              </div>

              {/* Actions column */}
              <div className="w-full flex flex-wrap items-center justify-center gap-1">
                <PluginGroupButton grouping={groupDrag} itemKey={tool.name} iconOnly className="border border-line hover:border-line-control" />
                <button
                  onClick={() => onSelect(tool)}
                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-control border border-line text-fg-secondary text-xs font-medium hover:bg-hover hover:text-fg transition-colors"
                >
                  <IconSettings aria-hidden="true" className="w-4 h-4" />
                  {t('mcp.manage')}
                </button>
              </div>
            </div>
          );
        })}
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
    <div className="bg-surface px-6 py-4 border-t border-line flex items-center justify-between">
      <div className="text-sm text-fg-secondary">{t('pagination.showing', { start: startItem, end: endItem, total: totalCount })}</div>
      <div className="flex items-center space-x-1">
        <button aria-label={pageActionLabel('previous')}
          onClick={() => onPageChange(Math.max(1, currentPage - 1))}
          disabled={currentPage === 1}
          className={`ui-btn-icon inline-flex items-center justify-center p-2 rounded-control text-sm ${currentPage === 1 ? 'text-fg-placeholder cursor-not-allowed' : 'text-fg-secondary hover:bg-hover'}`}
        >
          <IconChevronLeft aria-hidden="true" className="w-4 h-4" />
        </button>
        {getPages().map((page, i) =>
          typeof page === 'string' ? (
            <span key={`e-${i}`} className="px-2 text-fg-muted">...</span>
          ) : (
            <button
              key={page}
              onClick={() => onPageChange(page)}
              aria-current={currentPage === page ? 'page' : undefined}
              className={`px-3 py-1 rounded-control text-sm font-medium ${currentPage === page ? 'bg-selected text-fg' : 'text-fg-secondary hover:bg-selected'}`}
            >
              {page}
            </button>
          )
        )}
        <button aria-label={pageActionLabel('next')}
          onClick={() => onPageChange(Math.min(totalPages, currentPage + 1))}
          disabled={currentPage === totalPages}
          className={`ui-btn-icon inline-flex items-center justify-center p-2 rounded-control text-sm ${currentPage === totalPages ? 'text-fg-placeholder cursor-not-allowed' : 'text-fg-secondary hover:bg-hover'}`}
        >
          <IconChevronRight aria-hidden="true" className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

// ============================================================================
// Tool Detail Drawer (cjtools-style right-side drawer)
// ============================================================================

export function ToolDetailDrawer({
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
  onEnabledChange?: (name: string, enabled: boolean, updated?: Tool) => void;
}) {
  const { t, i18n } = useTranslation('tool');
  const [section, setSection] = useState<'info' | 'test'>('info');
  const [enabled, setEnabled] = useState(tool.enabled);
  const [customized, setCustomized] = useState<boolean>(!!tool.enabled_customized);
  const [enabledDefault, setEnabledDefault] = useState<boolean>(
    tool.enabled_default ?? tool.enabled
  );
  const [toggling, setToggling] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [fixtures, setFixtures] = useState<ToolFixture[]>([]);
  const [fixturesLoading, setFixturesLoading] = useState(true);
  const sb = SOURCE_BADGE[tool.source] || SOURCE_BADGE.custom;
  const sourceLabel = getSourceLabel(tool.source, t);
  const canDirectTest = canDirectlyTestTool(tool);

  useEffect(() => {
    setEnabled(tool.enabled);
    setCustomized(!!tool.enabled_customized);
    setEnabledDefault(tool.enabled_default ?? tool.enabled);
  }, [tool.enabled, tool.enabled_customized, tool.enabled_default]);

  useEffect(() => {
    if (testResult?.metadata?.disabled === true) {
      setEnabled(false);
    }
  }, [testResult]);

  useEffect(() => {
    setFixturesLoading(true);
    toolAPI.listFixtures(tool.name)
      .then((res) => setFixtures(res.data ?? []))
      .catch(() => setFixtures([]))
      .finally(() => setFixturesLoading(false));
  }, [tool.name]);


  const handleToggleEnabled = async () => {
    if (toggling) return;
    const next = !enabled;
    setToggling(true);
    try {
      const { data: updated } = await toolAPI.setEnabled(tool.name, next);
      setEnabled(updated.enabled);
      setCustomized(!!updated.enabled_customized);
      setEnabledDefault(updated.enabled_default ?? updated.enabled);
      onEnabledChange?.(tool.name, updated.enabled, updated);
    } catch (err: unknown) {
      // state was never flipped, so nothing to revert; surface the reason (e.g. 403 仅管理员可执行该操作)
      alert(extractErrorMessage(err, t('alert.unknownError')));
    } finally {
      setToggling(false);
    }
  };

  const handleResetSetting = async () => {
    if (resetting) return;
    setResetting(true);
    try {
      const { data: updated } = await toolAPI.resetSetting(tool.name);
      setEnabled(updated.enabled);
      setCustomized(!!updated.enabled_customized);
      setEnabledDefault(updated.enabled_default ?? updated.enabled);
      onEnabledChange?.(tool.name, updated.enabled, updated);
    } catch (err: unknown) {
      alert(extractErrorMessage(err, t('alert.unknownError')));
    } finally {
      setResetting(false);
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
    <PageDialog variant="panels" className="contents" onClose={onClose}>
      <div className="fixed inset-0 bg-overlay-backdrop z-40" onClick={onClose} />

      <div data-ui-overlay="drawer" className="fixed right-0 top-0 bottom-0 z-50 flex min-h-0 flex-col bg-surface shadow-overlay w-full max-w-drawer">
        <div className="flex-shrink-0 border-b border-line">
          <div className="flex items-center gap-3 px-6 py-4">
            <div className="w-8 h-8 bg-subtle rounded-control flex items-center justify-center flex-shrink-0">
              <IconTool aria-hidden="true" className="w-4 h-4 text-fg-secondary" />
            </div>
            <div className="flex-1 min-w-0">
              <h2 className="text-title-section font-semibold text-fg font-mono truncate">{tool.name}</h2>
              <div className="flex items-center gap-2 mt-0.5">
                <span className={`inline-flex items-center px-1.5 py-0.5 rounded-tag text-xs font-medium ${sb.className}`}>
                  {sourceLabel}
                </span>
                {tool.source_name && (
                  <span className="text-xs text-fg-muted truncate">{tool.source_name}</span>
                )}
                <span className="text-xs text-fg-muted">{t(`category.${CATEGORY_I18N_KEY[tool.category] || tool.category}`, { defaultValue: tool.category })}</span>
              </div>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <button aria-label={pageActionLabel('close')} onClick={onClose} className="ui-btn-icon inline-flex items-center justify-center p-1 rounded-control hover:bg-hover transition-colors">
                <IconX aria-hidden="true" className="w-5 h-5 text-fg-muted" />
              </button>
            </div>
          </div>

          <div className="flex px-6">
            <button
              onClick={() => setSection('info')}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                section === 'info'
                  ? 'border-line-control text-fg'
                  : 'border-transparent text-fg-muted hover:text-fg-secondary hover:border-line-control'
              }`}
            >
              <IconInfoCircle aria-hidden="true" className="w-4 h-4" />{t('toolDetail.tabInfo')}
            </button>
            <button
              onClick={() => setSection('test')}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                section === 'test'
                  ? 'border-line-control text-fg'
                  : 'border-transparent text-fg-muted hover:text-fg-secondary hover:border-line-control'
              }`}
            >
              <IconTestPipe aria-hidden="true" className="w-4 h-4" />{t('toolDetail.tabTest')}
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          {section === 'info' ? (
            <div className="space-y-5">
              <div>
                <label className="block text-sm font-medium text-fg-secondary mb-1.5">{t('toolDetail.description')}</label>
                <p className="text-sm text-fg-secondary whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                  {getLocalizedToolDescription(tool, i18n.language) || t('toolDetail.noDescription')}
                </p>
              </div>

              <div className="flex flex-wrap gap-4 items-start">
                <div>
                  <label className="block text-sm font-medium text-fg-secondary mb-1.5">{t('toolDetail.status')}</label>
                  <div className="flex items-center gap-2 flex-wrap">
                    <button
                      type="button"
                      role="switch"
                      aria-checked={enabled}
                      aria-label={t('toolDetail.status')}
                      onClick={handleToggleEnabled}
                      disabled={toggling}
                      className="ui-btn-icon inline-flex shrink-0 items-center justify-center rounded-control disabled:cursor-not-allowed">
<span className="ui-switch" aria-hidden="true" aria-checked={enabled} aria-disabled={toggling} />
</button>
                    <span className={`text-sm font-medium ${enabled ? 'text-fg-secondary' : 'text-fg-muted'}`}>
                      {toggling ? t('toolDetail.updating') : enabled ? t('toolDetail.enabled') : t('toolDetail.disabled')}
                    </span>
                    {customized && (
                      <>
                        <span
                          title={t('toolDetail.customizedTooltip', {
                            defaultValue: '当前状态来自用户自定义，YAML 默认值为 {{def}}',
                            def: enabledDefault ? t('toolDetail.enabled') : t('toolDetail.disabled'),
                          })}
                          className="inline-flex items-center px-2 py-0.5 rounded-pill text-xs font-medium bg-warning-soft text-warning"
                        >
                          {t('toolDetail.customized', { defaultValue: '已自定义' })}
                        </span>
                        <button
                          onClick={handleResetSetting}
                          disabled={resetting || toggling}
                          className="inline-flex items-center px-2 py-0.5 rounded-tag text-xs font-medium text-fg-secondary hover:text-fg hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled"
                        >
                          {resetting
                            ? t('toolDetail.resetting', { defaultValue: '重置中…' })
                            : t('toolDetail.resetSetting', { defaultValue: '恢复默认' })}
                        </button>
                      </>
                    )}
                  </div>
                </div>
                {tool.requires_confirmation && (
                  <div>
                    <label className="block text-sm font-medium text-fg-secondary mb-1.5">{t('toolDetail.security')}</label>
                    <span className="inline-flex items-center px-2.5 py-1 rounded-pill text-xs font-medium bg-warning-soft text-warning">
                      <IconAlertTriangle aria-hidden="true" className="w-4 h-4 mr-1" />{t('toolDetail.requiresConfirmation')}
                    </span>
                  </div>
                )}
              </div>

              {tool.parameters && tool.parameters.length > 0 && (
                <div>
                  <label className="block text-sm font-medium text-fg-secondary mb-2">{t('toolDetail.params', { count: tool.parameters.length })}</label>
                  <div className="rounded-control border border-line overflow-hidden">
                    <div className="overflow-x-auto">
                      <table className="min-w-full table-fixed divide-y divide-line">
                      <thead className="bg-subtle">
                        <tr>
                          <th className="w-[28%] px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('toolDetail.paramName')}</th>
                          <th className="w-[16%] px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('toolDetail.paramType')}</th>
                          <th className="w-[14%] px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('toolDetail.paramRequired')}</th>
                          <th className="w-[42%] px-4 py-2 text-left text-xs font-medium text-fg-muted">{t('toolDetail.paramDesc')}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-line bg-surface">
                        {tool.parameters.map((param: any, idx: number) => (
                          <tr key={idx} className="hover:bg-hover">
                            <td className="px-4 py-2.5 align-top">
                              <code className="text-xs font-mono text-fg bg-subtle px-1.5 py-0.5 rounded-tag break-all">{param.name}</code>
                            </td>
                            <td className="px-4 py-2.5 align-top text-xs text-fg-muted whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{param.type}</td>
                            <td className="px-4 py-2.5 align-top">
                              {param.required
                                ? <span className="text-xs text-fg-secondary font-medium">{t('toolDetail.yes')}</span>
                                : <span className="text-xs text-fg-muted">{t('toolDetail.no')}</span>}
                            </td>
                            <td className="px-4 py-2.5 align-top text-xs text-fg-secondary whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                              {param.description}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              )}

              {tool.source === 'plugin_py' && (
                <div className="flex items-center justify-end gap-2">
                  <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="inline-flex items-center gap-2 px-4 py-2.5 border border-danger text-danger rounded-control hover:bg-danger-soft text-sm font-medium transition-colors disabled:bg-disabled disabled:text-fg-disabled"
                  >
                    <IconTrash aria-hidden="true" className="w-4 h-4" />
                    {deleting ? t('button.deleting') : t('button.delete')}
                  </button>
                </div>
              )}

              {tool.source === 'mcp' && (
                <div className="bg-subtle border border-line rounded-control p-4">
                  <h4 className="text-sm font-medium text-fg mb-1">{t('toolDetail.mcpToolTitle')}</h4>
                  <p className="text-sm text-fg-secondary">{t('toolDetail.mcpToolDesc', { server: tool.source_name })}</p>
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-fg-secondary mb-1.5">{t('toolDetail.selectFixture')}</label>
                <select
                  value=""
                  disabled={fixturesLoading || fixtures.length === 0}
                  onChange={(e) => {
                    const idx = parseInt(e.target.value, 10);
                    if (!isNaN(idx) && fixtures[idx]) {
                      onTestParamsChange(JSON.stringify(fixtures[idx].params, null, 2));
                    }
                  }}
                  className="ui-select w-full px-3  border border-line-control rounded-control text-sm bg-surface text-fg-muted disabled:bg-subtle disabled:cursor-not-allowed"
                >
                  <option value="">
                    {fixturesLoading ? t('toolDetail.updating') : t('toolDetail.selectFixturePlaceholder')}
                  </option>
                  {fixtures.map((f, idx) => (
                    <option key={idx} value={idx}>
                      {f.tags.includes('smoke') ? '✦ ' : ''}{getLocalizedFixtureLabel(f, i18n.language)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium text-fg-secondary mb-2">{t('toolDetail.testParams')}</label>
                <textarea
                  value={testParams}
                  onChange={(e) => onTestParamsChange(e.target.value)}
                  placeholder='{"param": "value"}'
                  rows={6}
                  className="ui-textarea w-full px-4  border border-line-control rounded-control font-mono text-sm bg-subtle resize-none"
                />
              </div>
              <button
                onClick={onTest}
                disabled={testing || !enabled || !canDirectTest}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-subtle text-fg rounded-control hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed font-medium text-sm transition-colors"
              >
                {testing
                  ? <><IconRefresh aria-hidden="true" className="w-4 h-4 animate-spin" />{t('toolDetail.executing')}</>
                  : <><IconPlayerPlay aria-hidden="true" className="w-4 h-4" />{t('toolDetail.runTest')}</>}
              </button>
              {!enabled && (
                <p className="text-xs text-warning text-center">{t('toolDetail.disabledNote')}</p>
              )}
              {canDirectTest ? null : (
                <p className="text-xs text-warning text-center">{t('toolDetail.sessionTestOnly')}</p>
              )}
              {testResult && (
                <div>
                  <label className="block text-sm font-medium text-fg-secondary mb-2">{t('toolDetail.testResult')}</label>
                  <div className={`rounded-control border p-4 ${testResult.success ? 'bg-success-soft border-success' : 'bg-danger-soft border-danger'}`}>
                    <div className="flex items-center mb-2">
                      {testResult.success
                        ? <IconCircleCheck aria-hidden="true" className="w-4 h-4 text-success mr-2" />
                        : <IconCircleX aria-hidden="true" className="w-4 h-4 text-danger mr-2" />}
                      <span className={`text-sm font-medium ${testResult.success ? 'text-success' : 'text-danger'}`}>
                        {testResult.success ? t('toolDetail.execSuccess') : t('toolDetail.execFailed')}
                      </span>
                    </div>
                    {testResult.error && <p className="text-sm text-danger mb-2">{testResult.error}</p>}
                    {testResult.output != null && (
                      <pre className="text-xs bg-surface p-3 rounded-control overflow-x-auto max-h-60 font-mono">
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
    </PageDialog>
  );
}

// ============================================================================
// Small Shared Components
// ============================================================================

function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation('tool');
  const config: Record<string, { labelKey: string; className: string; dot: string }> = {
    connected:    { labelKey: 'statusBadge.connected',    className: 'bg-success-soft text-success',  dot: 'bg-success' },
    connecting:   { labelKey: 'statusBadge.connecting',   className: 'bg-warning-soft text-warning', dot: 'bg-warning animate-pulse' },
    disconnected: { labelKey: 'statusBadge.disconnected', className: 'bg-subtle text-fg-secondary',    dot: 'bg-subtle' },
    error:        { labelKey: 'statusBadge.error',        className: 'bg-danger-soft text-danger',      dot: 'bg-danger' },
    failed:       { labelKey: 'statusBadge.failed',       className: 'bg-danger-soft text-danger',      dot: 'bg-danger' },
    needs_auth:   { labelKey: 'statusBadge.needsAuth',    className: 'bg-warning-soft text-warning',  dot: 'bg-warning' },
    disabled:     { labelKey: 'statusBadge.disabled',     className: 'bg-subtle text-fg-muted',    dot: 'bg-selected' },
  };
  const c = config[status] || config.disconnected;
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-pill text-xs font-medium ${c.className}`}>
      <span className={`w-1.5 h-1.5 rounded-pill mr-1.5 ${c.dot}`} />
      {t(c.labelKey)}
    </span>
  );
}

/** Status badge for API services (similar to MCP StatusBadge, with API-specific states) */
function ApiStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation('tool');
  const config: Record<string, { labelKey: string; className: string; dot: string }> = {
    connected:      { labelKey: 'apiStatusBadge.connected',     className: 'bg-success-soft text-success', dot: 'bg-success' },
    testing:        { labelKey: 'apiStatusBadge.testing',       className: 'bg-danger-soft text-danger',   dot: 'bg-danger animate-pulse' },
    error:          { labelKey: 'apiStatusBadge.error',         className: 'bg-danger-soft text-danger',     dot: 'bg-danger' },
    not_configured: { labelKey: 'apiStatusBadge.notConfigured', className: 'bg-warning-soft text-warning', dot: 'bg-warning' },
    unknown:        { labelKey: 'apiStatusBadge.unknown',       className: 'bg-subtle text-fg-secondary',   dot: 'bg-subtle' },
  };
  const c = config[status] || config.unknown;
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-pill text-xs font-medium ${c.className}`}>
      <span className={`w-1.5 h-1.5 rounded-pill mr-1.5 ${c.dot}`} />
      {t(c.labelKey)}
    </span>
  );
}

function EnabledBadge({ enabled }: { enabled: boolean }) {
  const { t } = useTranslation('tool');
  return enabled ? (
    <span className="inline-flex items-center px-2.5 py-0.5 rounded-pill text-xs font-medium bg-success-soft text-success">
      <IconCircleCheck aria-hidden="true" className="w-4 h-4 mr-1" />{t('enabledBadge.enabled')}
    </span>
  ) : (
    <span className="inline-flex items-center px-2.5 py-0.5 rounded-pill text-xs font-medium bg-subtle text-fg-secondary">
      <IconCircleX aria-hidden="true" className="w-4 h-4 mr-1" />{t('enabledBadge.disabled')}
    </span>
  );
}


// ============================================================================
// Catalog Tab Content (MCP 工具目录)
// ============================================================================

const LANG_COLORS: Record<string, string> = {
  python: 'bg-subtle text-fg-secondary',
  typescript: 'bg-subtle text-fg-secondary',
  go: 'bg-subtle text-fg-secondary',
  rust: 'bg-subtle text-fg-secondary',
  java: 'bg-subtle text-fg-secondary',
  c: 'bg-subtle text-fg-secondary',
};

// CatalogBrowser removed — catalog UI is now inline in MCPTabContent / APITabContent

// (CatalogBrowser component removed — catalog UI is inline in MCPTabContent / APITabContent)
