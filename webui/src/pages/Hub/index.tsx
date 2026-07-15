import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Archive,
  CheckCircle,
  ChevronRight,
  Download,
  FileText,
  Folder,
  FolderOpen,
  GitBranch,
  Info,
  Loader2,
  RefreshCw,
  Search,
  Table2,
  Trash2,
  X,
} from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useToast } from '@/components/common/Toast';
import { extractErrorMessage } from '@/utils/error';
import SuiteInstallProgressPanel, {
  applySuiteInstallProgressEvent,
  createSuiteInstallProgressState,
  failSuiteInstallProgress,
  type SuiteInstallProgressState,
} from '@/components/hub/SuiteInstallProgressPanel';
import {
  hubAPI,
  HubCatalogEntry,
  type HubCatalogFacets,
  HubFileContent,
  HubFileNode,
  HubInstallProgressEvent,
  HubManifest,
  HubPluginType,
} from '@/api/hub';
import type { WorkflowJSON } from '@/api/workflow';
import { useTranslation } from 'react-i18next';
import { getCatalogDescription } from '@/utils/mcpCatalog';
import { useDebouncedValue } from '@/hooks/useDebouncedValue';
import { useProductName } from '@/contexts/ProductNameContext';
import { useAuth } from '@/contexts/AuthContext';

type ViewMode = 'table' | 'tree';

const FlowCanvas = lazy(() => import('@/pages/WorkflowDetail/FlowCanvas'));
const HUB_LOADING_DELAY_MS = 180;

interface HubTaxonomyCategory {
  id: string;
  name: string;
  nameCn?: string;
  description?: string;
}

interface HubTaxonomyLabel {
  name?: string;
  nameCn?: string;
}

interface HubTaxonomyResponse {
  categories?: HubTaxonomyCategory[];
  tags?: string[];
  tagLabels?: Record<string, HubTaxonomyLabel>;
  useCases?: string[];
  useCaseLabels?: Record<string, HubTaxonomyLabel>;
  counts?: {
    type?: Record<string, number>;
    category?: Record<string, number>;
    tags?: Record<string, number>;
    useCases?: Record<string, number>;
    state?: Record<string, number>;
  };
}

const TYPE_LABEL: Record<HubPluginType, string> = {
  skill: 'Skill',
  agent: 'Agent',
  tool: 'Tool',
  device: 'Device',
  workflow: 'Workflow',
  webui: 'WebUI',
  component: 'Scenario Suite',
};

const TYPE_LABEL_CN: Record<HubPluginType, string> = {
  skill: 'Skill',
  agent: 'Agent',
  tool: 'Tool',
  device: '设备',
  workflow: 'Workflow',
  webui: 'WebUI',
  component: '场景套件',
};

const HUB_PLUGIN_TYPES: HubPluginType[] = ['skill', 'agent', 'tool', 'device', 'workflow', 'webui', 'component'];

function normalizePluginType(value: string | null): HubPluginType | '' {
  if (HUB_PLUGIN_TYPES.includes(value as HubPluginType)) {
    return value as HubPluginType;
  }
  return '';
}

function formatPluginTypeLabel(type: HubPluginType, language: string): string {
  if (language.toLowerCase().startsWith('zh')) {
    return TYPE_LABEL_CN[type] ?? TYPE_LABEL[type];
  }
  return TYPE_LABEL[type];
}

const HUB_TEXT = {
  zh: {
    description: '浏览打包的本地插件广场，并安装到本机插件目录。',
    treeView: '目录视图',
    tableView: '表格视图',
    refresh: '刷新',
    type: '类型',
    category: '分类',
    useCase: '使用场景',
    status: '状态',
    action: '操作',
    all: '全部',
    searchPlaceholder: '搜索插件名称、描述、Tag、使用场景',
    collapseFilters: '收起筛选',
    expandFilters: '展开筛选',
    moreFilters: '更多筛选',
    clear: '清空',
    showing: '显示',
    of: '共',
    plugins: '个插件',
    perPage: '每页',
    previous: '上一页',
    next: '下一页',
    name: '名称',
    noMatches: '没有匹配的插件',
    parseWorkflowFailed: 'workflow.json 解析失败',
    readWorkflowFailed: '无法读取 workflow.json',
    suiteInstallTitle: '场景套件安装进度',
    suiteInstallRunning: '正在安装',
    suiteInstallCompleted: '安装完成',
    suiteInstallFailed: '安装失败',
    suiteInstallProgress: '安装进度',
    suiteInstallDismiss: '关闭',
    refreshFailed: '刷新 Hub 失败',
    refreshReloadFailed: 'Hub 刷新已完成，但列表重载失败',
    actionRefreshFailed: '操作已完成，但刷新 Hub 列表失败',
    actionFailed: { install: '安装失败', update: '更新失败', uninstall: '卸载失败' },
    unknownError: '未知错误',
    suiteItemStatuses: {
      pending: '等待中',
      installing: '安装中',
      installed: '已安装',
      skipped: '已跳过',
      failed: '失败',
      completed: '已完成',
    },
    tabs: { overview: '概览', flow: '流程图', files: '文件', deps: '依赖', permissions: '权限', versions: '版本' },
    id: 'ID',
    manifest: 'Manifest',
    trust: '信任等级',
    workflowDiagram: 'Workflow 可视化流程图',
    selectFile: '点击左侧文件查看详情',
    actions: { install: '安装', update: '更新', uninstall: '卸载' },
    states: {
      available: '可安装',
      installed: '已安装',
      updateAvailable: '可更新',
      incompatible: '不兼容',
      broken: '异常',
      localOnly: '仅本地',
    },
  },
  en: {
    description: 'Browse bundled Hub plugins and install them into the local plugin directory.',
    treeView: 'Directory View',
    tableView: 'Table View',
    refresh: 'Refresh',
    type: 'Type',
    category: 'Category',
    useCase: 'Use Case',
    status: 'Status',
    action: 'Action',
    all: 'All',
    searchPlaceholder: 'Search plugin name, description, tag, use case',
    collapseFilters: 'Collapse filters',
    expandFilters: 'Expand filters',
    moreFilters: 'More filters',
    clear: 'Clear',
    showing: 'Showing',
    of: 'of',
    plugins: 'plugins',
    perPage: 'Per page',
    previous: 'Previous',
    next: 'Next',
    name: 'Name',
    noMatches: 'No matching plugins',
    parseWorkflowFailed: 'Failed to parse workflow.json',
    readWorkflowFailed: 'Failed to read workflow.json',
    suiteInstallTitle: 'Scenario suite install progress',
    suiteInstallRunning: 'Installing',
    suiteInstallCompleted: 'Installed',
    suiteInstallFailed: 'Install failed',
    suiteInstallProgress: 'Progress',
    suiteInstallDismiss: 'Close',
    refreshFailed: 'Hub refresh failed',
    refreshReloadFailed: 'Hub refresh completed, but catalog reload failed',
    actionRefreshFailed: 'Action completed, but Hub reload failed',
    actionFailed: { install: 'Install failed', update: 'Update failed', uninstall: 'Uninstall failed' },
    unknownError: 'Unknown error',
    suiteItemStatuses: {
      pending: 'Pending',
      installing: 'Installing',
      installed: 'Installed',
      skipped: 'Skipped',
      failed: 'Failed',
      completed: 'Completed',
    },
    tabs: { overview: 'Overview', flow: 'Flow', files: 'Files', deps: 'Dependencies', permissions: 'Permissions', versions: 'Versions' },
    id: 'ID',
    manifest: 'Manifest',
    trust: 'Trust',
    workflowDiagram: 'Workflow diagram',
    selectFile: 'Select a file on the left to preview it',
    actions: { install: 'Install', update: 'Update', uninstall: 'Uninstall' },
    states: {
      available: 'Available',
      installed: 'Installed',
      updateAvailable: 'Update available',
      incompatible: 'Incompatible',
      broken: 'Broken',
      localOnly: 'Local only',
    },
  },
};

type HubText = typeof HUB_TEXT.zh;

function formatTaxonomyLabel(id: string, labels?: Record<string, HubTaxonomyLabel>, language = 'zh-CN') {
  const label = labels?.[id];
  if (!label) return id;
  const nameCn = label.nameCn?.trim();
  const name = label.name?.trim();
  return language.toLowerCase().startsWith('zh') ? (nameCn || name || id) : (name || nameCn || id);
}

function getHubDescription(entry: Pick<HubCatalogEntry, 'description' | 'descriptionCn'>, language: string) {
  return getCatalogDescription(
    { description: entry.description, descriptionCn: entry.descriptionCn },
    language,
  );
}

function getHubName(entry: Pick<HubCatalogEntry, 'id' | 'name' | 'nameCn'>, language: string) {
  const name = entry.name?.trim();
  const nameCn = entry.nameCn?.trim();
  return language.toLowerCase().startsWith('zh') ? (nameCn || name || entry.id) : (name || nameCn || entry.id);
}

const EMPTY_HUB_FACETS: HubCatalogFacets = {
  type: {},
  category: {},
  tags: {},
  useCases: {},
  state: {},
  trust: {},
  riskLevel: {},
};

export default function HubPage() {
  const { i18n } = useTranslation();
  const { user } = useAuth();
  const { productName } = useProductName();
  const toast = useToast();
  const [searchParams] = useSearchParams();
  const text = i18n.language.toLowerCase().startsWith('zh') ? HUB_TEXT.zh : HUB_TEXT.en;
  const hubTitle = `${productName} Hub`;
  const hubDescription = i18n.language.toLowerCase().startsWith('zh')
    ? `浏览随 ${productName} 打包的本地插件广场，并安装到本机插件目录。`
    : `Browse bundled ${hubTitle} plugins and install them into the local plugin directory.`;
  const urlPluginId = searchParams.get('plugin') || searchParams.get('id') || '';
  const urlType = normalizePluginType(searchParams.get('type'));
  const [catalogItems, setCatalogItems] = useState<HubCatalogEntry[]>([]);
  const [treeItems, setTreeItems] = useState<HubCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [treeLoading, setTreeLoading] = useState(false);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [treeError, setTreeError] = useState<string | null>(null);
  const [hasLoadedCatalog, setHasLoadedCatalog] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [query, setQuery] = useState(searchParams.get('q') || urlPluginId);
  const [typeFilter, setTypeFilter] = useState<HubPluginType | ''>(urlType);
  const [stateFilter, setStateFilter] = useState(searchParams.get('state') || '');
  const [tagFilter, setTagFilter] = useState('');
  const [useCaseFilter, setUseCaseFilter] = useState('');
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>('table');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [selected, setSelected] = useState<HubCatalogEntry | null>(null);
  const [actionId, setActionId] = useState<string | null>(null);
  const [suiteInstallProgress, setSuiteInstallProgress] = useState<SuiteInstallProgressState | null>(null);
  const [taxonomy, setTaxonomy] = useState<HubTaxonomyResponse | null>(null);
  const [totalItems, setTotalItems] = useState(0);
  const [facetCounts, setFacetCounts] = useState<HubCatalogFacets>(EMPTY_HUB_FACETS);
  const catalogRequestIdRef = useRef(0);
  const treeRequestIdRef = useRef(0);
  const debouncedQuery = useDebouncedValue(query, 250);
  const catalogRequestKey = JSON.stringify([
    query,
    debouncedQuery,
    typeFilter,
    useCaseFilter,
    tagFilter,
    stateFilter,
    page,
    pageSize,
  ]);
  const treeRequestKey = JSON.stringify([
    query,
    debouncedQuery,
    typeFilter,
    useCaseFilter,
    tagFilter,
    stateFilter,
  ]);
  const currentCatalogRequestKeyRef = useRef(catalogRequestKey);
  const currentTreeRequestKeyRef = useRef(treeRequestKey);
  currentCatalogRequestKeyRef.current = catalogRequestKey;
  currentTreeRequestKeyRef.current = treeRequestKey;
  const canManageHub = user?.role === 'admin';

  const fetchCatalog = useCallback(async (silent = false, propagateError = false) => {
    const requestKey = catalogRequestKey;
    if (requestKey !== currentCatalogRequestKeyRef.current) return null;
    const requestId = ++catalogRequestIdRef.current;
    const isCurrentRequest = () => (
      requestId === catalogRequestIdRef.current
      && requestKey === currentCatalogRequestKeyRef.current
    );
    try {
      if (!silent) setLoading(true);
      setCatalogError(null);
      const res = await hubAPI.catalogPage({
        q: debouncedQuery.trim() || undefined,
        type: typeFilter || undefined,
        useCases: useCaseFilter || undefined,
        tags: tagFilter || undefined,
        state: stateFilter || undefined,
        offset: (page - 1) * pageSize,
        limit: pageSize,
      });
      if (!isCurrentRequest()) return null;
      const nextItems = Array.isArray(res.data.items) ? res.data.items : [];
      setCatalogItems(nextItems);
      setTotalItems(res.data.total ?? nextItems.length);
      setFacetCounts(res.data.facets ?? EMPTY_HUB_FACETS);
      return nextItems;
    } catch (error) {
      const currentRequest = isCurrentRequest();
      if (currentRequest) {
        setCatalogError(error instanceof Error ? error.message : 'Failed to load Hub catalog');
      }
      if (propagateError && currentRequest) throw error;
      return null;
    } finally {
      if (isCurrentRequest()) {
        setHasLoadedCatalog(true);
        if (!silent) setLoading(false);
      }
    }
  }, [catalogRequestKey, debouncedQuery, page, pageSize, stateFilter, tagFilter, typeFilter, useCaseFilter]);

  const fetchTreeCatalog = useCallback(async (silent = false, propagateError = false) => {
    const requestKey = treeRequestKey;
    if (requestKey !== currentTreeRequestKeyRef.current) return;
    const requestId = ++treeRequestIdRef.current;
    const isCurrentRequest = () => (
      requestId === treeRequestIdRef.current
      && requestKey === currentTreeRequestKeyRef.current
    );
    try {
      if (!silent) setTreeLoading(true);
      setTreeError(null);
      const res = await hubAPI.catalog({
        q: debouncedQuery.trim() || undefined,
        type: typeFilter || undefined,
        useCases: useCaseFilter || undefined,
        tags: tagFilter || undefined,
        state: stateFilter || undefined,
      });
      if (!isCurrentRequest()) return;
      setTreeItems(Array.isArray(res.data) ? res.data : []);
    } catch (error) {
      const currentRequest = isCurrentRequest();
      if (currentRequest) {
        setTreeError(error instanceof Error ? error.message : 'Failed to load Hub tree');
      }
      if (propagateError && currentRequest) throw error;
    } finally {
      if (isCurrentRequest() && !silent) {
        setTreeLoading(false);
      }
    }
  }, [debouncedQuery, stateFilter, tagFilter, treeRequestKey, typeFilter, useCaseFilter]);

  useEffect(() => {
    if (query !== debouncedQuery) return;
    void fetchCatalog();
  }, [debouncedQuery, fetchCatalog, query]);

  useEffect(() => {
    if (viewMode === 'tree' && query === debouncedQuery) {
      void fetchTreeCatalog();
    }
  }, [debouncedQuery, fetchTreeCatalog, query, viewMode]);

  useEffect(() => {
    hubAPI.categories({ includeCounts: false }).then(res => setTaxonomy(res.data as HubTaxonomyResponse)).catch(() => setTaxonomy(null));
  }, []);

  useEffect(() => {
    if (!urlPluginId || catalogItems.length === 0) return;
    const target = catalogItems.find(item => item.id === urlPluginId && (!urlType || item.type === urlType));
    if (target) {
      setSelected(target);
      setTypeFilter(target.type);
      setQuery(urlPluginId);
    }
  }, [catalogItems, urlPluginId, urlType]);

  const items = catalogItems;

  const useCases = useMemo(
    () => taxonomy?.useCases ?? Array.from(new Set(catalogItems.flatMap(item => item.useCases))).sort(),
    [catalogItems, taxonomy],
  );
  const tags = useMemo(
    () => taxonomy?.tags ?? Array.from(new Set(catalogItems.flatMap(item => item.tags))).sort(),
    [catalogItems, taxonomy],
  );
  const activeFilterCount = [typeFilter, useCaseFilter, tagFilter, stateFilter].filter(Boolean).length;
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const currentPage = Math.min(page, totalPages);
  const pagedItems = items;
  const isInitialLoading = loading && !hasLoadedCatalog;
  const visibleCatalogError = viewMode === 'tree' ? (treeError || catalogError) : catalogError;

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);
  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await hubAPI.refresh();
    } catch (error) {
      toast.error(
        text.refreshFailed,
        extractErrorMessage(error, text.unknownError),
      );
      setRefreshing(false);
      return;
    }

    try {
      await Promise.all([
        fetchCatalog(true, true),
        viewMode === 'tree' ? fetchTreeCatalog(true, true) : Promise.resolve(),
      ]);
    } catch (error) {
      toast.error(
        text.refreshReloadFailed,
        extractErrorMessage(error, text.unknownError),
      );
    } finally {
      setRefreshing(false);
    }
  };

  const handleSuiteInstallProgress = (progress: HubInstallProgressEvent) => {
    setSuiteInstallProgress(current => applySuiteInstallProgressEvent(current, progress));
  };

  const runAction = async (entry: HubCatalogEntry, action: 'install' | 'update' | 'uninstall') => {
    if (!canManageHub) return;
    const key = `${entry.type}:${entry.id}:${action}`;
    setActionId(key);
    try {
      if (action === 'install' && entry.type === 'component') {
        setSuiteInstallProgress(createSuiteInstallProgressState(entry));
        await hubAPI.installStream(entry.type, entry.id, handleSuiteInstallProgress);
      } else if (action === 'install') {
        await hubAPI.install(entry.type, entry.id);
      }
      if (action === 'update') await hubAPI.update(entry.type, entry.id);
      if (action === 'uninstall') await hubAPI.uninstall(entry.type, entry.id);
    } catch (error) {
      if (action === 'install' && entry.type === 'component') {
        const message = extractErrorMessage(error, text.suiteInstallFailed);
        setSuiteInstallProgress(current => failSuiteInstallProgress(current, entry, message));
      } else {
        toast.error(
          `${text.actionFailed[action]}: ${getHubName(entry, i18n.language)}`,
          extractErrorMessage(error, text.unknownError),
        );
      }
      setActionId(null);
      return;
    }

    try {
      const [, , refreshedEntry] = await Promise.all([
        fetchCatalog(true, true),
        viewMode === 'tree' ? fetchTreeCatalog(true, true) : Promise.resolve(),
        hubAPI.catalog({ q: entry.id, type: entry.type }).then(res => (
          (Array.isArray(res.data) ? res.data : []).find(item => (
            item.type === entry.type && item.id === entry.id
          )) ?? null
        )),
      ]);
      setSelected(current => (
        current?.type === entry.type && current?.id === entry.id ? refreshedEntry : current
      ));
    } catch (error) {
      toast.error(
        `${text.actionRefreshFailed}: ${getHubName(entry, i18n.language)}`,
        extractErrorMessage(error, text.unknownError),
      );
    } finally {
      setActionId(null);
    }
  };

  const resetFacetFilters = () => {
    setTypeFilter('');
    setUseCaseFilter('');
    setTagFilter('');
    setStateFilter('');
    setPage(1);
  };

  if (isInitialLoading) {
    return <div className="h-full flex items-center justify-center"><LoadingSpinner delayMs={HUB_LOADING_DELAY_MS} /></div>;
  }

  if (catalogError && catalogItems.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-center">
          <p className="mb-4 text-red-600">{catalogError}</p>
          <button
            type="button"
            onClick={() => void fetchCatalog()}
            className="rounded-lg bg-slate-700 px-4 py-2 text-white hover:bg-slate-800"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full min-h-0 flex flex-col" aria-busy={loading}>
      <PageHeader
        title={hubTitle}
        description={hubDescription}
        icon={<Archive className="w-8 h-8" />}
        action={
          <div className="flex items-center gap-2">
            <div className="relative w-80 2xl:w-96">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input
                value={query}
                onChange={e => {
                  setQuery(e.target.value);
                  setPage(1);
                }}
                placeholder={text.searchPlaceholder}
                className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm outline-none bg-white/90 focus:ring-2 focus:ring-slate-200 focus:border-slate-400"
              />
            </div>
            <button
              onClick={() => setViewMode(viewMode === 'table' ? 'tree' : 'table')}
              className="inline-flex items-center gap-2 px-3 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50"
            >
              {viewMode === 'table' ? <Folder className="w-4 h-4" /> : <Table2 className="w-4 h-4" />}
              {viewMode === 'table' ? text.treeView : text.tableView}
            </button>
            {canManageHub && (
              <button
                onClick={handleRefresh}
                disabled={refreshing}
                className="inline-flex items-center gap-2 px-3 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50"
              >
                <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
                {text.refresh}
              </button>
            )}
          </div>
        }
      />

      <div className="shrink-0 px-4 pb-3">
        <div className="rounded-xl border border-gray-200 bg-gradient-to-r from-slate-50 via-white to-white shadow-sm overflow-hidden">
          <div className="p-3">
            <FilterRow
              label={text.type}
              value={typeFilter}
              onChange={value => {
                setTypeFilter(value as HubPluginType | '');
                setPage(1);
              }}
              options={[
                { value: '', label: text.all },
                ...HUB_PLUGIN_TYPES.map(type => ({
                  value: type,
                  label: formatPluginTypeLabel(type, i18n.language),
                  count: facetCounts.type[type] ?? 0,
                })),
              ]}
            />
          </div>

          {filtersOpen && (
            <div className="px-3 pb-2 space-y-2">
              <FilterRow
                label={text.useCase}
                value={useCaseFilter}
                onChange={value => {
                  setUseCaseFilter(value);
                  setPage(1);
                }}
                options={[
                  { value: '', label: text.all },
                  ...useCases.map(useCase => ({
                    value: useCase,
                    label: formatTaxonomyLabel(useCase, taxonomy?.useCaseLabels, i18n.language),
                    title: useCase,
                    count: facetCounts.useCases[useCase] ?? 0,
                  })),
                ]}
              />
              <FilterRow
                label="Tag"
                value={tagFilter}
                onChange={value => {
                  setTagFilter(value);
                  setPage(1);
                }}
                options={[
                  { value: '', label: text.all },
                  ...tags.map(tag => ({
                    value: tag,
                    label: formatTaxonomyLabel(tag, taxonomy?.tagLabels, i18n.language),
                    title: tag,
                    count: facetCounts.tags[tag] ?? 0,
                  })),
                ]}
              />
              <FilterRow
                label={text.status}
                value={stateFilter}
                onChange={value => {
                  setStateFilter(value);
                  setPage(1);
                }}
                options={[
                  { value: '', label: text.all },
                  ...(['available', 'installed', 'updateAvailable', 'incompatible'] as const).map(state => ({
                    value: state,
                    label: text.states[state],
                    count: facetCounts.state[state] ?? 0,
                  })),
                ]}
              />
            </div>
          )}

          <div className="relative h-4 bg-gradient-to-r from-slate-50 via-white to-white">
            <button
              onClick={() => setFiltersOpen(!filtersOpen)}
              aria-label={filtersOpen ? text.collapseFilters : text.expandFilters}
              title={filtersOpen ? text.collapseFilters : text.expandFilters}
              className="absolute left-1/2 -top-px -translate-x-1/2 h-5 w-28 rounded-b-xl border border-t-0 border-slate-300 bg-slate-100 shadow-sm flex items-center justify-center gap-1 text-slate-500 hover:bg-white hover:text-slate-700 hover:border-slate-400"
            >
              <span className="text-[10px] leading-none">{text.moreFilters}</span>
              <ChevronRight className={`w-3 h-3 transition-transform ${filtersOpen ? '-rotate-90' : 'rotate-90'}`} />
            </button>
            {activeFilterCount > 0 && (
              <button
                onClick={resetFacetFilters}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400 hover:text-gray-700"
              >
                {text.clear}
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden p-4 [scrollbar-gutter:stable]">
        {visibleCatalogError && (
          <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            {visibleCatalogError}
          </div>
        )}
        {viewMode === 'table' ? (
          <HubTable items={pagedItems} actionId={actionId} tagLabels={taxonomy?.tagLabels} language={i18n.language} text={text} onSelect={setSelected} onAction={runAction} />
        ) : treeLoading && treeItems.length === 0 ? (
          <div className="flex min-h-[360px] items-center justify-center rounded-lg border border-gray-200 bg-white">
            <LoadingSpinner delayMs={HUB_LOADING_DELAY_MS} />
          </div>
        ) : (
          <HubTree items={treeItems} actionId={actionId} text={text} onSelect={setSelected} onAction={runAction} />
        )}
      </div>

      {viewMode === 'table' && (
        <PaginationBar
          total={totalItems}
          page={currentPage}
          pageSize={pageSize}
          totalPages={totalPages}
          text={text}
          onPageChange={setPage}
          onPageSizeChange={nextPageSize => {
            setPageSize(nextPageSize);
            setPage(1);
          }}
        />
      )}

      {selected && (
        <PluginDetail
          entry={selected}
          language={i18n.language}
          onClose={() => setSelected(null)}
          onAction={runAction}
          actionId={actionId}
          text={text}
        />
      )}

      {suiteInstallProgress && (
        <SuiteInstallProgressPanel
          progress={suiteInstallProgress}
          language={i18n.language}
          onClose={() => setSuiteInstallProgress(null)}
        />
      )}
    </div>
  );
}

interface FilterOptionItem {
  value: string;
  label: string;
  count?: number;
  title?: string;
}

function FilterRow({ label, value, options, onChange }: {
  label: string;
  value: string;
  options: FilterOptionItem[];
  onChange: (value: string) => void;
}) {
  const renderOption = (option: FilterOptionItem) => {
    const active = value === option.value;
    return (
      <button
        key={option.value || 'all'}
        title={option.title}
        onClick={() => onChange(option.value)}
        className={`inline-flex h-8 items-center whitespace-nowrap rounded-md px-2 py-1 font-medium tabular-nums transition-colors ${
          active
            ? 'bg-slate-800 text-white'
            : 'text-gray-600 hover:bg-white hover:text-gray-900'
        }`}
      >
        {option.label}
        {option.count !== undefined && option.value && (
          <span className={active ? 'ml-1 inline-block min-w-3 text-right text-slate-200' : 'ml-1 inline-block min-w-3 text-right text-gray-400'}>{option.count}</span>
        )}
      </button>
    );
  };
  const [allOption, ...facetOptions] = options;

  return (
    <div className="flex min-h-8 items-start gap-3 text-sm">
      <div className="w-20 shrink-0 pt-1.5">
        <span className="inline-flex px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 border border-slate-200 text-xs font-medium">
          {label}
        </span>
      </div>
      <div className="flex-1 min-w-0 flex items-start gap-x-1">
        {allOption && <div className="shrink-0">{renderOption(allOption)}</div>}
        <div className="min-w-0 flex-1 flex flex-wrap gap-x-1 gap-y-1">
          {facetOptions.map(renderOption)}
        </div>
      </div>
    </div>
  );
}

function PaginationBar({ total, page, pageSize, totalPages, text, onPageChange, onPageSizeChange }: {
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
  text: HubText;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}) {
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(total, page * pageSize);
  return (
    <div className="h-12 shrink-0 px-4 py-2 border-t border-gray-200 bg-white flex items-center justify-between text-xs text-gray-500">
      <div>
        {text.showing} {start}-{end} / {text.of} {total} {text.plugins}
      </div>
      <div className="flex items-center gap-2">
        <span>{text.perPage}</span>
        <select
          value={pageSize}
          onChange={e => onPageSizeChange(Number(e.target.value))}
          className="px-2 py-1 border border-gray-200 rounded-md bg-white outline-none"
        >
          {[25, 50, 100].map(size => <option key={size} value={size}>{size}</option>)}
        </select>
        <button
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page <= 1}
          className="px-2 py-1 border border-gray-200 rounded-md hover:bg-gray-50 disabled:opacity-40"
        >
          {text.previous}
        </button>
        <span className="min-w-16 text-center">{page} / {totalPages}</span>
        <button
          onClick={() => onPageChange(Math.min(totalPages, page + 1))}
          disabled={page >= totalPages}
          className="px-2 py-1 border border-gray-200 rounded-md hover:bg-gray-50 disabled:opacity-40"
        >
          {text.next}
        </button>
      </div>
    </div>
  );
}

function HubTable({ items, actionId, tagLabels, language, text, onSelect, onAction }: {
  items: HubCatalogEntry[];
  actionId: string | null;
  tagLabels?: Record<string, HubTaxonomyLabel>;
  language: string;
  text: HubText;
  onSelect: (entry: HubCatalogEntry) => void;
  onAction: (entry: HubCatalogEntry, action: 'install' | 'update' | 'uninstall') => void;
}) {
  return (
    <div className="min-h-full bg-white border border-gray-200 rounded-xl overflow-hidden">
      <table className="min-w-full table-fixed text-xs">
        <colgroup>
          <col style={{ width: '7%' }} />
          <col style={{ width: '38%' }} />
          <col style={{ width: '31%' }} />
          <col style={{ width: '10%' }} />
          <col style={{ width: '14%' }} />
        </colgroup>
        <thead className="bg-gray-50 text-gray-500">
          <tr>
            <th className="text-left px-3 py-2 font-medium">{text.type}</th>
            <th className="text-left px-3 py-2 font-medium">{text.name}</th>
            <th className="text-left px-3 py-2 font-medium">Tag</th>
            <th className="text-left px-3 py-2 font-medium">{text.status}</th>
            <th className="text-right px-3 py-2 font-medium">{text.action}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {items.map(item => (
            <tr key={`${item.type}:${item.id}`} className="hover:bg-gray-50">
              <td className="px-3 py-2 text-gray-500">{formatPluginTypeLabel(item.type, language)}</td>
              <td className="max-w-0 px-3 py-2">
                <button onClick={() => onSelect(item)} className="w-full text-left">
                  <div className="truncate font-medium text-gray-900 hover:text-slate-700">{getHubName(item, language)}</div>
                  <div className="truncate text-[11px] text-gray-500">{getHubDescription(item, language)}</div>
                </button>
              </td>
              <td className="max-w-0 px-3 py-2">
                <div className="flex gap-1 overflow-hidden">
                  {item.tags.slice(0, 3).map(tag => (
                    <Badge key={tag} title={tag}>{formatTaxonomyLabel(tag, tagLabels, language)}</Badge>
                  ))}
                </div>
              </td>
              <td className="px-3 py-2"><StateBadge state={item.state} text={text} /></td>
              <td className="px-3 py-2 text-right"><ActionButtons item={item} actionId={actionId} text={text} onAction={onAction} /></td>
            </tr>
          ))}
          {items.length === 0 && (
            <tr><td colSpan={5} className="px-4 py-12 text-center text-gray-400">{text.noMatches}</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function HubTree({ items, actionId, text, onSelect, onAction }: {
  items: HubCatalogEntry[];
  actionId: string | null;
  text: HubText;
  onSelect: (entry: HubCatalogEntry) => void;
  onAction: (entry: HubCatalogEntry, action: 'install' | 'update' | 'uninstall') => void;
}) {
  const root = useMemo(() => {
    const rootNode: HubTreeNode = { name: 'flockshub', path: 'flockshub', children: [] };
    const nodeMap = new Map<string, HubTreeNode>([[rootNode.path, rootNode]]);

    const ensureNode = (parent: HubTreeNode, name: string) => {
      const path = `${parent.path}/${name}`;
      let node = nodeMap.get(path);
      if (!node) {
        node = { name, path, children: [] };
        nodeMap.set(path, node);
        parent.children.push(node);
      }
      return node;
    };

    items.forEach(item => {
      const parts = item.manifestPath.split('/').filter(Boolean);
      const packageParts = parts[parts.length - 1] === 'manifest.json' ? parts.slice(0, -1) : parts;
      let current = rootNode;
      packageParts.forEach(part => {
        current = ensureNode(current, part);
      });
      current.entry = item;
    });

    const sortNode = (node: HubTreeNode) => {
      node.children.sort((a, b) => {
        if (a.entry && !b.entry) return 1;
        if (!a.entry && b.entry) return -1;
        return a.name.localeCompare(b.name);
      });
      node.children.forEach(sortNode);
    };
    sortNode(rootNode);
    return rootNode;
  }, [items]);

  return (
    <div className="min-h-full bg-white border border-gray-200 rounded-xl p-3">
      <HubTreeNodeView node={root} depth={0} actionId={actionId} text={text} onSelect={onSelect} onAction={onAction} root />
    </div>
  );
}

interface HubTreeNode {
  name: string;
  path: string;
  children: HubTreeNode[];
  entry?: HubCatalogEntry;
}

function HubTreeNodeView({ node, depth, actionId, text, onSelect, onAction, root = false }: {
  node: HubTreeNode;
  depth: number;
  actionId: string | null;
  text: HubText;
  onSelect: (entry: HubCatalogEntry) => void;
  onAction: (entry: HubCatalogEntry, action: 'install' | 'update' | 'uninstall') => void;
  root?: boolean;
}) {
  const [open, setOpen] = useState(depth < 2);
  const hasChildren = node.children.length > 0;
  const paddingLeft = `${depth * 18 + 8}px`;

  return (
    <div>
      <div
        className="flex items-center gap-2 py-1.5 pr-2 rounded-lg hover:bg-gray-50"
        style={{ paddingLeft }}
      >
        {hasChildren ? (
          <button onClick={() => setOpen(!open)} className="shrink-0">
            <ChevronRight className={`w-4 h-4 text-gray-400 transition-transform ${open ? 'rotate-90' : ''}`} />
          </button>
        ) : (
          <span className="w-4 shrink-0" />
        )}
        {hasChildren ? (
          open ? <FolderOpen className="w-4 h-4 shrink-0 text-amber-500" /> : <Folder className="w-4 h-4 shrink-0 text-amber-500" />
        ) : (
          <FileText className="w-4 h-4 shrink-0 text-gray-400" />
        )}
        {node.entry ? (
          <button onClick={() => onSelect(node.entry!)} className="flex-1 min-w-0 text-left">
            <span className="truncate font-mono text-sm text-gray-800">{node.name}</span>
          </button>
        ) : (
          <button onClick={() => hasChildren && setOpen(!open)} className="flex-1 min-w-0 text-left">
            <span className={root ? 'font-medium text-gray-800' : 'font-mono text-sm text-gray-800'}>{node.name}</span>
            <span className="ml-2 text-xs text-gray-400">({countPluginNodes(node)})</span>
          </button>
        )}
        {node.entry && <StateBadge state={node.entry.state} text={text} />}
        {node.entry && <ActionButtons item={node.entry} actionId={actionId} text={text} onAction={onAction} compact />}
      </div>
      {open && (
        <div>
          {node.children.map(child => (
            <HubTreeNodeView
              key={child.path}
              node={child}
              depth={depth + 1}
              actionId={actionId}
              text={text}
              onSelect={onSelect}
              onAction={onAction}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function countPluginNodes(node: HubTreeNode): number {
  return (node.entry ? 1 : 0) + node.children.reduce((sum, child) => sum + countPluginNodes(child), 0);
}

function PluginDetail({ entry, language, onClose, onAction, actionId, text }: {
  entry: HubCatalogEntry;
  language: string;
  onClose: () => void;
  actionId: string | null;
  text: HubText;
  onAction: (entry: HubCatalogEntry, action: 'install' | 'update' | 'uninstall') => void;
}) {
  const [manifest, setManifest] = useState<HubManifest | null>(null);
  const [tree, setTree] = useState<HubFileNode | null>(null);
  const [content, setContent] = useState<HubFileContent | null>(null);
  const [workflowJson, setWorkflowJson] = useState<WorkflowJSON | null>(null);
  const [workflowError, setWorkflowError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'overview' | 'flow' | 'files' | 'deps' | 'permissions' | 'versions'>(
    entry.type === 'workflow' ? 'flow' : entry.type === 'skill' ? 'files' : 'overview'
  );

  useEffect(() => {
    setManifest(null);
    setTree(null);
    setContent(null);
    setWorkflowJson(null);
    setWorkflowError(null);
    setActiveTab(entry.type === 'workflow' ? 'flow' : entry.type === 'skill' ? 'files' : 'overview');
    hubAPI.get(entry.type, entry.id).then(res => setManifest(res.data));
    hubAPI.files(entry.type, entry.id).then(res => setTree(res.data));
    if (entry.type === 'skill') {
      hubAPI.fileContent(entry.type, entry.id, 'SKILL.md')
        .then(res => setContent(res.data))
        .catch(() => undefined);
    }
    if (entry.type === 'workflow') {
      hubAPI.fileContent(entry.type, entry.id, 'workflow.json')
        .then(res => {
          try {
            setWorkflowJson(JSON.parse(res.data.content) as WorkflowJSON);
          } catch (err) {
            setWorkflowError(err instanceof Error ? err.message : text.parseWorkflowFailed);
          }
        })
        .catch(err => {
          setWorkflowError(err instanceof Error ? err.message : text.readWorkflowFailed);
        });
    }
  }, [entry.type, entry.id, text.parseWorkflowFailed, text.readWorkflowFailed]);

  const openFile = async (path: string) => {
    const res = await hubAPI.fileContent(entry.type, entry.id, path);
    setContent(res.data);
  };

  return (
    <div className="fixed inset-y-0 right-0 z-40 w-[860px] max-w-[95vw] bg-white border-l border-gray-200 shadow-xl flex flex-col">
      <div className="px-5 py-4 border-b border-gray-200 flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-gray-900">{getHubName(entry, language)}</h2>
            <StateBadge state={entry.state} text={text} />
          </div>
          <p className="text-sm text-gray-500 mt-1">{getHubDescription(entry, language)}</p>
        </div>
        <button onClick={onClose} className="p-1 rounded-lg hover:bg-gray-100"><X className="w-5 h-5" /></button>
      </div>
      <div className="px-5 py-3 border-b border-gray-100 flex items-center justify-between">
        <div className="flex gap-2">
          {((entry.type === 'workflow'
            ? ['overview', 'flow', 'files', 'deps', 'permissions', 'versions']
            : ['overview', 'files', 'deps', 'permissions', 'versions']) as Array<'overview' | 'flow' | 'files' | 'deps' | 'permissions' | 'versions'>).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-3 py-1.5 text-sm rounded-lg ${activeTab === tab ? 'bg-slate-800 text-white' : 'text-gray-600 hover:bg-gray-100'}`}
            >
              {text.tabs[tab]}
            </button>
          ))}
        </div>
        <ActionButtons item={entry} actionId={actionId} text={text} onAction={onAction} />
      </div>
      <div className="flex-1 overflow-hidden">
        {activeTab === 'overview' && (
          <div className="p-5 space-y-4 overflow-auto h-full text-sm">
            <InfoBlock label="ID" value={entry.id} />
            <InfoBlock label={text.type} value={formatPluginTypeLabel(entry.type, language)} />
            <InfoBlock label="Tag" value={entry.tags.join(', ') || '-'} />
            <InfoBlock label={text.useCase} value={entry.useCases.join(', ') || '-'} />
            <InfoBlock label={text.trust} value={entry.trust} />
            <InfoBlock label={text.manifest} value={entry.manifestPath} />
            {entry.type === 'component' && manifest?.components?.length ? (
              <div>
                <div className="text-xs text-gray-400 mb-2">
                  {language.toLowerCase().startsWith('zh') ? '套件内容' : 'Suite items'}
                </div>
                <div className="space-y-2">
                  {manifest.components.map((item) => (
                    <div
                      key={`${item.type}:${item.id}`}
                      className="flex items-center justify-between rounded-lg border border-gray-200 bg-gray-50 px-3 py-2"
                    >
                      <span className="font-mono text-gray-800">{item.id}</span>
                      <span className="text-xs text-gray-500">{formatPluginTypeLabel(item.type, language)}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        )}
        {activeTab === 'flow' && (
          <div className="h-full bg-slate-50">
            {workflowJson ? (
              <div className="h-full">
                <div className="h-10 px-4 border-b border-gray-200 bg-white flex items-center gap-2 text-sm text-gray-600">
                  <GitBranch className="w-4 h-4" />
                  <span>{text.workflowDiagram}</span>
                </div>
                <div className="h-[calc(100%-2.5rem)]">
                  <Suspense fallback={<div className="h-full flex items-center justify-center"><LoadingSpinner /></div>}>
                    <FlowCanvas workflowJson={workflowJson} editable={false} />
                  </Suspense>
                </div>
              </div>
            ) : workflowError ? (
              <div className="h-full flex items-center justify-center text-sm text-red-500">{workflowError}</div>
            ) : (
              <div className="h-full flex items-center justify-center"><LoadingSpinner /></div>
            )}
          </div>
        )}
        {activeTab === 'files' && (
          <div className="h-full grid grid-cols-[280px_1fr]">
            <div className="border-r border-gray-200 overflow-auto p-3">
              {tree ? <FileTree node={tree} onOpen={openFile} root /> : <LoadingSpinner />}
            </div>
            <div className="overflow-auto">
              {content ? (
                <div className="h-full flex flex-col">
                  <div className="px-4 py-2 border-b border-gray-100 text-xs text-gray-500 font-mono">{content.path} · {content.size} bytes</div>
                  <pre className="p-4 text-xs leading-relaxed whitespace-pre-wrap font-mono text-gray-800">{content.content}</pre>
                </div>
              ) : (
                <div className="h-full flex items-center justify-center text-gray-400 text-sm">{text.selectFile}</div>
              )}
            </div>
          </div>
        )}
        {activeTab === 'deps' && <JsonPanel data={manifest?.dependencies ?? {}} />}
        {activeTab === 'permissions' && <JsonPanel data={{ permissions: manifest?.permissions, risk: manifest?.risk }} />}
        {activeTab === 'versions' && <JsonPanel data={{ bundledVersion: entry.version, installedVersion: entry.installedVersion, source: entry.source, installPath: entry.installPath }} />}
      </div>
    </div>
  );
}

function FileTree({ node, onOpen, root = false }: { node: HubFileNode; onOpen: (path: string) => void; root?: boolean }) {
  const [open, setOpen] = useState(root);
  if (node.type === 'file') {
    return (
      <button
        onClick={() => node.previewable && onOpen(node.path)}
        disabled={!node.previewable}
        className="w-full flex items-center gap-2 px-2 py-1.5 rounded text-left text-sm hover:bg-gray-50 disabled:opacity-40"
      >
        <FileText className="w-4 h-4 text-gray-400" />
        <span className="truncate font-mono">{node.name}</span>
      </button>
    );
  }
  return (
    <div>
      {!root && (
        <button onClick={() => setOpen(!open)} className="w-full flex items-center gap-2 px-2 py-1.5 rounded text-left text-sm hover:bg-gray-50">
          <ChevronRight className={`w-4 h-4 text-gray-400 ${open ? 'rotate-90' : ''}`} />
          <Folder className="w-4 h-4 text-amber-500" />
          <span className="truncate font-mono">{node.name}</span>
        </button>
      )}
      {(open || root) && (
        <div className={root ? '' : 'ml-4'}>
          {node.children.map(child => <FileTree key={child.path || child.name} node={child} onOpen={onOpen} />)}
        </div>
      )}
    </div>
  );
}

function ActionButtons({ item, actionId, text, onAction, compact = false }: {
  item: HubCatalogEntry;
  actionId: string | null;
  text: HubText;
  compact?: boolean;
  onAction: (entry: HubCatalogEntry, action: 'install' | 'update' | 'uninstall') => void;
}) {
  const { user } = useAuth();
  const busy = actionId?.startsWith(`${item.type}:${item.id}:`);
  const buttonClass = compact
    ? 'p-1.5 rounded-md border border-gray-200 hover:bg-gray-50 disabled:opacity-50'
    : 'inline-flex items-center gap-1 whitespace-nowrap px-2 py-1 rounded-md border border-gray-200 text-xs hover:bg-gray-50 disabled:opacity-50';
  if (busy) return <Loader2 className="w-4 h-4 animate-spin text-gray-400" />;
  if (user?.role !== 'admin') return null;
  if (item.native && item.state === 'installed') return null;
  if (item.state === 'available') return <button className={buttonClass} onClick={() => onAction(item, 'install')}><Download className="w-3.5 h-3.5" />{!compact && text.actions.install}</button>;
  if (item.state === 'updateAvailable') return <button className={buttonClass} onClick={() => onAction(item, 'update')}><RefreshCw className="w-3.5 h-3.5" />{!compact && text.actions.update}</button>;
  if (item.state === 'installed') {
    return (
      <button className={buttonClass} onClick={() => onAction(item, 'uninstall')}><Trash2 className="w-3.5 h-3.5" />{!compact && text.actions.uninstall}</button>
    );
  }
  return null;
}

function StateBadge({ state, text }: { state: string; text: HubText }) {
  const cls = state === 'installed'
    ? 'bg-green-50 text-green-700 border-green-200'
    : state === 'available'
      ? 'bg-blue-50 text-blue-700 border-blue-200'
      : 'bg-amber-50 text-amber-700 border-amber-200';
  return <span className={`inline-flex items-center gap-1 whitespace-nowrap px-2 py-0.5 rounded-full border text-xs ${cls}`}>{state === 'installed' && <CheckCircle className="w-3 h-3" />}{text.states[state as keyof HubText['states']] || state}</span>;
}

function Badge({ children, title }: { children: React.ReactNode; title?: string }) {
  return <span title={title} className="shrink-0 whitespace-nowrap px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded text-[10px]">{children}</span>;
}

function InfoBlock({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-gray-400 mb-1">{label}</div>
      <div className="text-gray-800">{value}</div>
    </div>
  );
}

function JsonPanel({ data }: { data: any }) {
  return <pre className="h-full overflow-auto p-5 text-xs font-mono whitespace-pre-wrap">{JSON.stringify(data, null, 2)}</pre>;
}
