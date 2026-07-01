import { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  Bot,
  Boxes,
  CheckCircle,
  ChevronRight,
  Download,
  FileText,
  GitBranch,
  Loader2,
  PackageCheck,
  Power,
  PowerOff,
  RefreshCw,
  Search,
  ServerCog,
  Shield,
  Sparkles,
  Trash2,
  Wrench,
  Workflow,
  X,
} from 'lucide-react';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useToast } from '@/components/common/Toast';
import { hubAPI, type HubCatalogEntry, type HubPluginType } from '@/api/hub';

type PluginView = 'installed' | 'marketplace';
type ActionKind = 'install' | 'update' | 'uninstall';
type PluginFamily = 'all' | 'agent' | 'skill' | 'mcp' | 'apiTool' | 'pythonTool' | 'generatedTool' | 'tool' | 'device' | 'workflow';
type PluginSection = 'overview' | 'tools' | 'skills' | 'agents' | 'marketplace';

const ToolPage = lazy(() => import('@/pages/Tool'));
const SkillPage = lazy(() => import('@/pages/Skill'));
const AgentPage = lazy(() => import('@/pages/Agent'));
const HubPage = lazy(() => import('@/pages/Hub'));

interface PluginText {
  title: string;
  description: string;
  installed: string;
  marketplace: string;
  installedHint: string;
  marketplaceHint: string;
  searchPlaceholder: string;
  all: string;
  refresh: string;
  family: string;
  sections: Record<PluginSection, string>;
  sectionDescriptions: Record<PluginSection, string>;
  emptyTitle: string;
  emptyHint: string;
  openWorkspace: string;
  pluginActions: string;
  enabled: string;
  disabled: string;
  nextStep: string;
  permissions: string;
  risk: string;
  version: string;
  source: string;
  installPath: string;
  noInstallPath: string;
  actions: Record<ActionKind, string>;
  states: Record<string, string>;
  types: Record<HubPluginType, string>;
  families: Record<PluginFamily, string>;
  next: Record<HubPluginType, string>;
  toast: {
    refreshed: string;
    actionDone: string;
    actionFailed: string;
  };
}

const TEXT: Record<'zh' | 'en', PluginText> = {
  zh: {
    title: '插件管理',
    description: '统一管理智能体、技能、工具、设备和工作流插件，安装后直接进入对应配置。',
    installed: '已安装',
    marketplace: '插件广场',
    installedHint: '当前可用、可配置、可更新的插件资产。',
    marketplaceHint: '浏览可安装插件，并在安装前查看权限和风险。',
    searchPlaceholder: '搜索名称、描述、标签或使用场景',
    all: '全部',
    refresh: '刷新',
    family: 'Flocks 分类',
    sections: {
      overview: '总览',
      tools: '工具',
      skills: '技能',
      agents: '智能体',
      marketplace: '插件广场',
    },
    sectionDescriptions: {
      overview: '统一查看插件安装状态，并执行开关、安装、卸载等插件级操作。',
      tools: '管理 MCP、API Tool、本地 Python Tool、设备工具等 Flocks 工具能力。',
      skills: '管理 Rex 和子 Agent 可加载的技能，包含启用、禁用、依赖安装和编辑。',
      agents: '管理子 Agent 配置、能力边界、工具白名单和创建入口。',
      marketplace: '浏览可安装插件，安装前查看 manifest、依赖、权限和文件内容。',
    },
    emptyTitle: '没有匹配的插件',
    emptyHint: '换一个类型或清空搜索条件。',
    openWorkspace: '完整页面',
    pluginActions: '插件操作',
    enabled: '已启用',
    disabled: '已停用',
    nextStep: '下一步',
    permissions: '权限',
    risk: '风险',
    version: '版本',
    source: '来源',
    installPath: '安装位置',
    noInstallPath: '未安装',
    actions: {
      install: '安装',
      update: '更新',
      uninstall: '卸载',
    },
    states: {
      available: '可安装',
      installed: '已安装',
      updateAvailable: '可更新',
      localOnly: '仅本地',
      broken: '异常',
      incompatible: '不兼容',
    },
    types: {
      agent: '智能体',
      skill: '技能',
      tool: '工具',
      device: '设备',
      workflow: '工作流',
    },
    families: {
      all: '全部分类',
      agent: '智能体',
      skill: '技能',
      mcp: 'MCP',
      apiTool: 'API Tool',
      pythonTool: 'Python Tool',
      generatedTool: 'Generated',
      tool: '其他工具',
      device: '设备',
      workflow: '工作流',
    },
    next: {
      agent: '创建会话或调整智能体配置',
      skill: '查看触发说明和依赖状态',
      tool: '测试工具或配置 API/MCP 服务',
      device: '设备接入保留为独立主入口，可在插件安装后添加设备实例并测试凭据',
      workflow: '打开工作流并运行验证',
    },
    toast: {
      refreshed: '插件列表已刷新',
      actionDone: '操作完成',
      actionFailed: '操作失败',
    },
  },
  en: {
    title: 'Plugin Management',
    description: 'Manage agents, skills, tools, devices, and workflow plugins from one workspace.',
    installed: 'Installed',
    marketplace: 'Marketplace',
    installedHint: 'Assets that are ready to use, configure, or update.',
    marketplaceHint: 'Browse installable plugins and inspect permissions before installing.',
    searchPlaceholder: 'Search names, descriptions, tags, or use cases',
    all: 'All',
    refresh: 'Refresh',
    family: 'Flocks family',
    sections: {
      overview: 'Overview',
      tools: 'Tools',
      skills: 'Skills',
      agents: 'Agents',
      marketplace: 'Marketplace',
    },
    sectionDescriptions: {
      overview: 'Review plugin install state and run plugin-level enable, install, and uninstall actions.',
      tools: 'Manage MCP, API Tool, local Python Tool, device tools, and other Flocks tool capabilities.',
      skills: 'Manage skills that Rex and sub-agents can load, including enablement, dependencies, and editing.',
      agents: 'Manage sub-agent configuration, boundaries, tool allowlists, and creation flows.',
      marketplace: 'Browse installable plugins and inspect manifests, dependencies, permissions, and files.',
    },
    emptyTitle: 'No matching plugins',
    emptyHint: 'Try another type or clear the search.',
    openWorkspace: 'Full page',
    pluginActions: 'Plugin actions',
    enabled: 'Enabled',
    disabled: 'Disabled',
    nextStep: 'Next step',
    permissions: 'Permissions',
    risk: 'Risk',
    version: 'Version',
    source: 'Source',
    installPath: 'Install path',
    noInstallPath: 'Not installed',
    actions: {
      install: 'Install',
      update: 'Update',
      uninstall: 'Uninstall',
    },
    states: {
      available: 'Available',
      installed: 'Installed',
      updateAvailable: 'Update available',
      localOnly: 'Local only',
      broken: 'Broken',
      incompatible: 'Incompatible',
    },
    types: {
      agent: 'Agent',
      skill: 'Skill',
      tool: 'Tool',
      device: 'Device',
      workflow: 'Workflow',
    },
    families: {
      all: 'All families',
      agent: 'Agent',
      skill: 'Skill',
      mcp: 'MCP',
      apiTool: 'API Tool',
      pythonTool: 'Python Tool',
      generatedTool: 'Generated',
      tool: 'Other tools',
      device: 'Device',
      workflow: 'Workflow',
    },
    next: {
      agent: 'Create a session or adjust agent settings',
      skill: 'Review trigger instructions and dependency status',
      tool: 'Test the tool or configure API/MCP services',
      device: 'Device Integration remains a primary entry. Add instances and test credentials there after installing plugins',
      workflow: 'Open the workflow and validate a run',
    },
    toast: {
      refreshed: 'Plugin list refreshed',
      actionDone: 'Action complete',
      actionFailed: 'Action failed',
    },
  },
};

const TYPE_ORDER: HubPluginType[] = ['agent', 'skill', 'tool', 'device', 'workflow'];
const FAMILY_ORDER: PluginFamily[] = ['agent', 'skill', 'mcp', 'apiTool', 'pythonTool', 'generatedTool', 'tool', 'device', 'workflow'];
const SECTION_ORDER: PluginSection[] = ['agents', 'skills', 'tools', 'marketplace'];

const TYPE_META: Record<HubPluginType, {
  icon: typeof Bot;
  tone: string;
  href: string;
}> = {
  agent: {
    icon: Bot,
    tone: 'bg-cyan-50 text-cyan-700 border-cyan-200 dark:bg-cyan-950/40 dark:text-cyan-200 dark:border-cyan-800',
    href: '/agents',
  },
  skill: {
    icon: FileText,
    tone: 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-200 dark:border-emerald-800',
    href: '/skills',
  },
  tool: {
    icon: Wrench,
    tone: 'bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-950/40 dark:text-indigo-200 dark:border-indigo-800',
    href: '/tools',
  },
  device: {
    icon: ServerCog,
    tone: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-200 dark:border-amber-800',
    href: '/devices',
  },
  workflow: {
    icon: Workflow,
    tone: 'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-950/40 dark:text-rose-200 dark:border-rose-800',
    href: '/workflows',
  },
};

function isInstalledState(state: HubCatalogEntry['state']) {
  return state === 'installed' || state === 'updateAvailable' || state === 'localOnly' || state === 'broken';
}

function actionFor(entry: HubCatalogEntry): ActionKind | null {
  if (entry.state === 'available') return 'install';
  if (entry.state === 'updateAvailable') return 'update';
  if (entry.state === 'installed' || entry.state === 'localOnly' || entry.state === 'broken') return 'uninstall';
  return null;
}

function isZh(language: string) {
  return language.toLowerCase().startsWith('zh');
}

function resolvePluginSection(section?: string): PluginSection {
  if (section === 'tools' || section === 'skills' || section === 'agents' || section === 'marketplace') {
    return section;
  }
  return 'agents';
}

function descriptionFor(entry: HubCatalogEntry, language: string) {
  return isZh(language) ? (entry.descriptionCn || entry.description) : (entry.description || entry.descriptionCn || '');
}

function riskClass(level: string) {
  if (level === 'high') return 'bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-200 dark:border-red-800';
  if (level === 'medium') return 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-200 dark:border-amber-800';
  return 'bg-zinc-50 text-zinc-600 border-zinc-200 dark:bg-zinc-900 dark:text-zinc-300 dark:border-zinc-700';
}

function stateClass(state: string) {
  if (state === 'installed') return 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-200 dark:border-emerald-800';
  if (state === 'updateAvailable') return 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-200 dark:border-blue-800';
  if (state === 'broken') return 'bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-200 dark:border-red-800';
  if (state === 'incompatible') return 'bg-zinc-100 text-zinc-400 border-zinc-200 dark:bg-zinc-900 dark:text-zinc-500 dark:border-zinc-800';
  return 'bg-white text-zinc-600 border-zinc-200 dark:bg-zinc-900 dark:text-zinc-300 dark:border-zinc-700';
}

function pluginKey(entry: HubCatalogEntry) {
  return `${entry.type}:${entry.id}`;
}

function pluginFamily(entry: HubCatalogEntry): PluginFamily {
  if (entry.type !== 'tool') return entry.type;
  const path = `${entry.manifestPath || ''} ${entry.installPath || ''}`.toLowerCase();
  const tags = entry.tags.map(tag => tag.toLowerCase());
  if (path.includes('/mcp/') || tags.includes('mcp')) return 'mcp';
  if (path.includes('/api/') || tags.includes('api')) return 'apiTool';
  if (path.includes('/python/') || tags.includes('python')) return 'pythonTool';
  if (path.includes('/generated/') || tags.includes('generated')) return 'generatedTool';
  return 'tool';
}

function buildCounts(items: HubCatalogEntry[]) {
  const counts: Record<HubPluginType | 'all', number> = {
    all: items.length,
    agent: 0,
    skill: 0,
    tool: 0,
    device: 0,
    workflow: 0,
  };
  items.forEach(item => {
    counts[item.type] += 1;
  });
  return counts;
}

function buildFamilyCounts(items: HubCatalogEntry[]) {
  const counts: Record<PluginFamily, number> = {
    all: items.length,
    agent: 0,
    skill: 0,
    mcp: 0,
    apiTool: 0,
    pythonTool: 0,
    generatedTool: 0,
    tool: 0,
    device: 0,
    workflow: 0,
  };
  items.forEach(item => {
    counts[pluginFamily(item)] += 1;
  });
  return counts;
}

export default function PluginManagerPage() {
  const params = useParams();
  const { i18n } = useTranslation();
  const text = isZh(i18n.language) ? TEXT.zh : TEXT.en;
  const sectionParam = params.section;
  const activeSection = resolvePluginSection(sectionParam);
  const { success: showSuccess, error: showError } = useToast();
  const [items, setItems] = useState<HubCatalogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState<PluginView>('installed');
  const [typeFilter, setTypeFilter] = useState<HubPluginType | 'all'>('all');
  const [familyFilter, setFamilyFilter] = useState<PluginFamily>('all');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<HubCatalogEntry | null>(null);
  const [actionKey, setActionKey] = useState<string | null>(null);

  const loadCatalog = async ({ silent = false }: { silent?: boolean } = {}) => {
    try {
      if (!silent) setLoading(true);
      const res = await hubAPI.catalog();
      const nextItems = Array.isArray(res.data) ? res.data : [];
      setItems(nextItems);
      setSelected(current => {
        if (!current) return current;
        return nextItems.find(item => pluginKey(item) === pluginKey(current)) ?? current;
      });
      return nextItems;
    } finally {
      if (!silent) setLoading(false);
    }
  };

  useEffect(() => {
    if (activeSection === 'overview' && items.length === 0) {
      void loadCatalog();
    }
  }, [activeSection, items.length]);

  const baseItems = useMemo(
    () => items.filter(item => (view === 'installed' ? isInstalledState(item.state) : item.state !== 'installed')),
    [items, view],
  );

  const counts = useMemo(() => buildCounts(baseItems), [baseItems]);
  const familyCounts = useMemo(() => buildFamilyCounts(baseItems), [baseItems]);
  const installedCount = useMemo(() => items.filter(item => isInstalledState(item.state)).length, [items]);
  const updateCount = useMemo(() => items.filter(item => item.state === 'updateAvailable').length, [items]);
  const availableCount = useMemo(() => items.filter(item => item.state === 'available').length, [items]);

  const filteredItems = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return baseItems.filter(item => {
      if (typeFilter !== 'all' && item.type !== typeFilter) return false;
      if (familyFilter !== 'all' && pluginFamily(item) !== familyFilter) return false;
      if (!normalizedQuery) return true;
      const haystack = [
        item.id,
        item.name,
        item.description,
        item.descriptionCn ?? '',
        item.category,
        item.source,
        ...item.tags,
        ...item.useCases,
        ...item.capabilities,
      ].join(' ').toLowerCase();
      return haystack.includes(normalizedQuery);
    });
  }, [baseItems, familyFilter, query, typeFilter]);

  const selectedAction = selected ? actionFor(selected) : null;

  useEffect(() => {
    if (!selected) return;
    if (!filteredItems.some(item => pluginKey(item) === pluginKey(selected))) {
      setSelected(null);
    }
  }, [filteredItems, selected]);

  const runAction = async (entry: HubCatalogEntry, action: ActionKind) => {
    const key = `${pluginKey(entry)}:${action}`;
    setActionKey(key);
    try {
      if (action === 'install') await hubAPI.install(entry.type, entry.id);
      if (action === 'update') await hubAPI.update(entry.type, entry.id);
      if (action === 'uninstall') await hubAPI.uninstall(entry.type, entry.id);
      const nextItems = await loadCatalog({ silent: true });
      const updated = nextItems?.find(item => pluginKey(item) === pluginKey(entry));
      if (updated) setSelected(updated);
      showSuccess(text.toast.actionDone);
    } catch (err) {
      showError(text.toast.actionFailed, err instanceof Error ? err.message : undefined);
    } finally {
      setActionKey(null);
    }
  };

  const runEnabledAction = async (entry: HubCatalogEntry, enabled: boolean) => {
    const key = `${pluginKey(entry)}:enabled`;
    setActionKey(key);
    try {
      await hubAPI.setEnabled(entry.type, entry.id, enabled);
      const nextItems = await loadCatalog({ silent: true });
      const updated = nextItems?.find(item => pluginKey(item) === pluginKey(entry));
      if (updated) setSelected(updated);
      showSuccess(text.toast.actionDone);
    } catch (err) {
      showError(text.toast.actionFailed, err instanceof Error ? err.message : undefined);
    } finally {
      setActionKey(null);
    }
  };

  if (activeSection === 'overview' && loading) {
    return <div className="h-full flex items-center justify-center"><LoadingSpinner /></div>;
  }

  return (
    <div className="h-full min-h-[calc(100vh-3rem)]">
      <header className="mb-3 rounded-lg border border-zinc-200 bg-white px-3 py-2 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <div className="grid gap-2 lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] lg:items-center">
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md bg-red-50 text-red-600 dark:bg-red-950/40 dark:text-red-300">
              <PackageCheck className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <h1 className="text-base font-semibold leading-5 text-zinc-950 dark:text-zinc-50">{text.title}</h1>
                <span className="text-xs font-medium text-zinc-400 dark:text-zinc-500">{text.sections[activeSection]}</span>
              </div>
              <p className="mt-0.5 truncate text-xs leading-4 text-zinc-500 dark:text-zinc-400">
                {text.sectionDescriptions[activeSection]}
              </p>
            </div>
          </div>
          <div className="justify-self-center">
            <PluginSectionNav activeSection={activeSection} text={text} />
          </div>
          <div aria-hidden="true" className="hidden lg:block" />
        </div>
      </header>

      <div className="space-y-3">
        {activeSection !== 'overview' ? (
          <section className="rounded-lg border border-zinc-200 bg-white p-2 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
            <Suspense fallback={<div className="flex min-h-72 items-center justify-center"><LoadingSpinner /></div>}>
              <PluginSectionContent section={activeSection} />
            </Suspense>
          </section>
        ) : (
        <>
        <section className="grid gap-2.5">
          <div className="rounded-lg border border-zinc-200 bg-white p-3 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
            <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
              <div>
                <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
                  {view === 'installed' ? text.installed : text.marketplace}
                </div>
                <div className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                  {view === 'installed' ? text.installedHint : text.marketplaceHint}
                </div>
              </div>
              <div className="inline-flex rounded-lg border border-zinc-200 bg-zinc-50 p-0.5 dark:border-zinc-800 dark:bg-zinc-950">
                {(['installed', 'marketplace'] as PluginView[]).map(tab => (
                  <button
                    key={tab}
                    type="button"
                    onClick={() => setView(tab)}
                    className={`rounded-md px-2.5 py-1 text-sm font-medium transition-colors ${
                      view === tab
                        ? 'bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-50'
                        : 'text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100'
                    }`}
                  >
                    {tab === 'installed' ? text.installed : text.marketplace}
                  </button>
                ))}
              </div>
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-3">
              <Metric label={text.installed} value={installedCount} icon={<CheckCircle className="h-3.5 w-3.5" />} />
              <Metric label={text.states.updateAvailable} value={updateCount} icon={<GitBranch className="h-3.5 w-3.5" />} />
              <Metric label={text.states.available} value={availableCount} icon={<Download className="h-3.5 w-3.5" />} />
            </div>
          </div>

          <div className="rounded-lg border border-zinc-200 bg-white p-3 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
              <input
                value={query}
                onChange={event => setQuery(event.target.value)}
                placeholder={text.searchPlaceholder}
                className="w-full rounded-lg border border-zinc-300 bg-white py-1.5 pl-9 pr-3 text-sm text-zinc-900 outline-none transition focus:border-zinc-500 focus:ring-2 focus:ring-zinc-200 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-50 dark:focus:border-zinc-500 dark:focus:ring-zinc-800"
              />
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <TypeFilterButton
                active={typeFilter === 'all'}
                label={text.all}
                count={counts.all}
                onClick={() => setTypeFilter('all')}
              />
              {TYPE_ORDER.map(type => (
                <TypeFilterButton
                  key={type}
                  active={typeFilter === type}
                  label={text.types[type]}
                  count={counts[type]}
                  type={type}
                  onClick={() => setTypeFilter(type)}
                />
              ))}
            </div>
            <div className="mt-2 border-t border-zinc-100 pt-2 dark:border-zinc-800">
              <div className="mb-1.5 text-xs font-medium text-zinc-400">{text.family}</div>
              <div className="flex flex-wrap gap-1.5">
                <FamilyFilterButton
                  active={familyFilter === 'all'}
                  label={text.families.all}
                  count={familyCounts.all}
                  onClick={() => setFamilyFilter('all')}
                />
                {FAMILY_ORDER.map(family => (
                  <FamilyFilterButton
                    key={family}
                    active={familyFilter === family}
                    label={text.families[family]}
                    count={familyCounts[family]}
                    onClick={() => setFamilyFilter(family)}
                  />
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className={selected ? 'grid gap-4 xl:grid-cols-[minmax(0,1fr)_390px]' : 'grid gap-4'}>
          <div className="min-h-[520px] rounded-lg border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
            {filteredItems.length === 0 ? (
              <div className="flex h-[520px] flex-col items-center justify-center px-6 text-center">
                <Boxes className="h-9 w-9 text-zinc-300 dark:text-zinc-700" />
                <div className="mt-3 text-sm font-semibold text-zinc-900 dark:text-zinc-50">{text.emptyTitle}</div>
                <div className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{text.emptyHint}</div>
              </div>
            ) : (
              <div className="divide-y divide-zinc-100 dark:divide-zinc-800">
                {filteredItems.map(entry => (
                  <PluginRow
                    key={pluginKey(entry)}
                    entry={entry}
                    text={text}
                    language={i18n.language}
                    selected={selected ? pluginKey(selected) === pluginKey(entry) : false}
                    actionKey={actionKey}
                    onSelect={() => setSelected(entry)}
                    onAction={(action) => void runAction(entry, action)}
                    onToggleEnabled={(enabled) => void runEnabledAction(entry, enabled)}
                  />
                ))}
              </div>
            )}
          </div>

          {selected && (
            <aside className="rounded-lg border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
              <div className="flex h-full min-h-[520px] flex-col">
                <div className="border-b border-zinc-100 p-4 dark:border-zinc-800">
                  <div className="flex items-start justify-between gap-3">
                    <PluginIdentity entry={selected} text={text} language={i18n.language} />
                    <button
                      type="button"
                      onClick={() => setSelected(null)}
                      className="rounded-md p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                      aria-label="Close"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                  <p className="mt-3 text-sm leading-6 text-zinc-600 dark:text-zinc-300">
                    {descriptionFor(selected, i18n.language) || selected.id}
                  </p>
                </div>

                <div className="flex-1 space-y-4 overflow-y-auto p-4">
                  <div className="grid grid-cols-2 gap-2">
                    <InfoTile label={text.version} value={selected.version} />
                    <InfoTile label={text.source} value={selected.source} />
                    <InfoTile label={text.risk} value={selected.riskLevel} />
                    <InfoTile label="Trust" value={selected.trust} />
                  </div>

                  <div>
                    <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-400">{text.permissions}</div>
                    <div className="flex flex-wrap gap-1.5">
                      {[...selected.capabilities, ...selected.domains].slice(0, 8).map(item => (
                        <span key={item} className="rounded-md border border-zinc-200 px-2 py-1 text-xs text-zinc-600 dark:border-zinc-700 dark:text-zinc-300">
                          {item}
                        </span>
                      ))}
                      {selected.capabilities.length === 0 && selected.domains.length === 0 && (
                        <span className="rounded-md border border-zinc-200 px-2 py-1 text-xs text-zinc-400 dark:border-zinc-700">
                          -
                        </span>
                      )}
                    </div>
                  </div>

                  <InfoBlock
                    label={text.installPath}
                    value={selected.installPath || text.noInstallPath}
                  />

                  {selected.brokenReason && (
                    <InfoBlock label={text.states.broken} value={selected.brokenReason} danger />
                  )}
                </div>

                <div className="space-y-2 border-t border-zinc-100 p-4 dark:border-zinc-800">
                  <div className="rounded-lg bg-zinc-50 p-3 dark:bg-zinc-950">
                    <div className="flex items-center gap-2 text-sm font-semibold text-zinc-900 dark:text-zinc-50">
                      <Sparkles className="h-4 w-4 text-zinc-500" />
                      {text.nextStep}
                    </div>
                    <div className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{text.next[selected.type]}</div>
                  </div>
                  <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-700">
                    <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-400">{text.pluginActions}</div>
                    <div className="grid grid-cols-2 gap-2">
                      {isInstalledState(selected.state) && !selected.native && (
                        <ToggleEnabledButton
                          enabled={selected.enabled}
                          loading={actionKey === `${pluginKey(selected)}:enabled`}
                          text={text}
                          onClick={() => void runEnabledAction(selected, !selected.enabled)}
                        />
                      )}
                      {selectedAction && (
                        <button
                          type="button"
                          onClick={() => void runAction(selected, selectedAction)}
                          disabled={actionKey === `${pluginKey(selected)}:${selectedAction}`}
                          className={`inline-flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-white disabled:opacity-60 ${
                            selectedAction === 'uninstall'
                              ? 'bg-red-600 hover:bg-red-700'
                              : 'bg-zinc-900 hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white'
                          }`}
                        >
                          {actionKey === `${pluginKey(selected)}:${selectedAction}` ? <Loader2 className="h-4 w-4 animate-spin" /> : actionIcon(selectedAction)}
                          {text.actions[selectedAction]}
                        </button>
                      )}
                      <Link
                        to={TYPE_META[selected.type].href}
                        className="inline-flex items-center justify-center gap-2 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
                      >
                        {text.openWorkspace}
                        <ChevronRight className="h-4 w-4" />
                      </Link>
                    </div>
                  </div>
                </div>
              </div>
            </aside>
          )}
        </section>
        </>
        )}
      </div>
    </div>
  );
}

function PluginSectionNav({ activeSection, text }: { activeSection: PluginSection; text: PluginText }) {
  return (
    <nav className="flex flex-wrap gap-1 rounded-md border border-zinc-200 bg-zinc-50 p-0.5 dark:border-zinc-800 dark:bg-zinc-950">
      {SECTION_ORDER.map(section => (
        <Link
          key={section}
          to={`/plugins/${section}`}
          className={`rounded-[5px] px-2.5 py-1 text-sm font-medium leading-5 transition-colors ${
            activeSection === section
              ? 'bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900'
              : 'text-zinc-500 hover:bg-zinc-50 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-50'
          }`}
        >
          {text.sections[section]}
        </Link>
      ))}
    </nav>
  );
}

function PluginSectionContent({ section }: { section: PluginSection }) {
  if (section === 'tools') return <ToolPage embedded />;
  if (section === 'skills') return <SkillPage embedded />;
  if (section === 'agents') return <AgentPage embedded />;
  if (section === 'marketplace') return <HubPage embedded />;
  return null;
}

function Metric({ label, value, icon }: { label: string; value: number; icon: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2 dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex min-w-0 items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
        <span className="flex-shrink-0">{icon}</span>
        <span className="truncate">{label}</span>
      </div>
      <div className="text-lg font-semibold leading-none text-zinc-900 dark:text-zinc-50">{value}</div>
    </div>
  );
}

function TypeFilterButton({
  active,
  label,
  count,
  type,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number;
  type?: HubPluginType;
  onClick: () => void;
}) {
  const Icon = type ? TYPE_META[type].icon : Boxes;
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors ${
        active
          ? 'border-zinc-900 bg-zinc-900 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900'
          : 'border-zinc-200 bg-white text-zinc-600 hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:text-zinc-50'
      }`}
    >
      <Icon className="h-3.5 w-3.5" />
      <span>{label}</span>
      <span className={active ? 'text-white/70 dark:text-zinc-600' : 'text-zinc-400'}>{count}</span>
    </button>
  );
}

function FamilyFilterButton({
  active,
  label,
  count,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium transition-colors ${
        active
          ? 'border-zinc-800 bg-zinc-800 text-white dark:border-zinc-100 dark:bg-zinc-100 dark:text-zinc-900'
          : 'border-zinc-200 bg-zinc-50 text-zinc-500 hover:border-zinc-300 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-400 dark:hover:text-zinc-50'
      }`}
    >
      <span>{label}</span>
      <span className={active ? 'text-white/70 dark:text-zinc-600' : 'text-zinc-400'}>{count}</span>
    </button>
  );
}

function ToggleEnabledButton({
  enabled,
  loading,
  text,
  onClick,
}: {
  enabled: boolean;
  loading: boolean;
  text: PluginText;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={loading}
      className={`inline-flex min-w-20 items-center justify-center gap-1.5 rounded-lg border px-2.5 py-2 text-sm font-medium transition-colors disabled:opacity-60 ${
        enabled
          ? 'border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200 dark:hover:bg-emerald-900/50'
          : 'border-zinc-200 bg-zinc-50 text-zinc-500 hover:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800'
      }`}
    >
      {loading ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : enabled ? (
        <Power className="h-4 w-4" />
      ) : (
        <PowerOff className="h-4 w-4" />
      )}
      {enabled ? text.enabled : text.disabled}
    </button>
  );
}

function PluginRow({
  entry,
  text,
  language,
  selected,
  actionKey,
  onSelect,
  onAction,
  onToggleEnabled,
}: {
  entry: HubCatalogEntry;
  text: PluginText;
  language: string;
  selected: boolean;
  actionKey: string | null;
  onSelect: () => void;
  onAction: (action: ActionKind) => void;
  onToggleEnabled: (enabled: boolean) => void;
}) {
  const action = actionFor(entry);
  const running = action ? actionKey === `${pluginKey(entry)}:${action}` : false;
  const enableRunning = actionKey === `${pluginKey(entry)}:enabled`;
  return (
    <div
      className={`grid gap-3 p-4 transition-colors md:grid-cols-[minmax(0,1fr)_auto] ${
        selected ? 'bg-zinc-50 dark:bg-zinc-950' : 'hover:bg-zinc-50/70 dark:hover:bg-zinc-950/70'
      }`}
    >
      <button type="button" onClick={onSelect} className="min-w-0 text-left">
        <PluginIdentity entry={entry} text={text} language={language} />
        <p className="mt-2 line-clamp-2 text-sm leading-6 text-zinc-500 dark:text-zinc-400">
          {descriptionFor(entry, language) || entry.id}
        </p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          <span className={`rounded-full border px-2 py-0.5 text-xs ${stateClass(entry.state)}`}>
            {text.states[entry.state] ?? entry.state}
          </span>
          <span className={`rounded-full border px-2 py-0.5 text-xs ${riskClass(entry.riskLevel)}`}>
            <Shield className="mr-1 inline h-3 w-3" />
            {entry.riskLevel}
          </span>
          <span className="rounded-full border border-zinc-200 bg-zinc-50 px-2 py-0.5 text-xs text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400">
            {text.families[pluginFamily(entry)]}
          </span>
          {entry.tags.slice(0, 3).map(tag => (
            <span key={tag} className="rounded-full border border-zinc-200 px-2 py-0.5 text-xs text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
              {tag}
            </span>
          ))}
        </div>
      </button>
      <div className="flex items-center gap-2 md:justify-end">
        {isInstalledState(entry.state) && !entry.native && (
          <ToggleEnabledButton
            enabled={entry.enabled}
            loading={enableRunning}
            text={text}
            onClick={() => onToggleEnabled(!entry.enabled)}
          />
        )}
        {action && (
          <button
            type="button"
            onClick={() => onAction(action)}
            disabled={running}
            className={`inline-flex min-w-20 items-center justify-center gap-1.5 rounded-lg px-2.5 py-2 text-sm font-medium text-white disabled:opacity-60 ${
              action === 'uninstall'
                ? 'bg-red-600 hover:bg-red-700'
                : 'bg-zinc-900 hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white'
            }`}
          >
            {running ? <Loader2 className="h-4 w-4 animate-spin" /> : actionIcon(action)}
            {text.actions[action]}
          </button>
        )}
      </div>
    </div>
  );
}

function PluginIdentity({ entry, text, language }: { entry: HubCatalogEntry; text: PluginText; language: string }) {
  const meta = TYPE_META[entry.type];
  const Icon = meta.icon;
  return (
    <div className="flex min-w-0 items-start gap-3">
      <div className={`flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg border ${meta.tone}`}>
        <Icon className="h-5 w-5" />
      </div>
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h3 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-50">{entry.name}</h3>
          <span className={`rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${meta.tone}`}>
            {text.types[entry.type]}
          </span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-400">
          <span>{entry.id}</span>
          <span>{entry.version}</span>
          {entry.descriptionCn && !isZh(language) ? <span>CN</span> : null}
        </div>
      </div>
    </div>
  );
}

function InfoTile({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-700">
      <div className="text-xs text-zinc-400">{label}</div>
      <div className="mt-1 truncate text-sm font-medium text-zinc-800 dark:text-zinc-100">{value || '-'}</div>
    </div>
  );
}

function InfoBlock({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return (
    <div>
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-400">{label}</div>
      <div className={`break-all rounded-lg border px-3 py-2 text-sm ${
        danger
          ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200'
          : 'border-zinc-200 bg-zinc-50 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300'
      }`}
      >
        {value}
      </div>
    </div>
  );
}

function actionIcon(action: ActionKind) {
  if (action === 'uninstall') return <Trash2 className="h-4 w-4" />;
  if (action === 'update') return <RefreshCw className="h-4 w-4" />;
  return <Download className="h-4 w-4" />;
}
