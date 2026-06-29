import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Activity, AlertCircle, ChevronLeft, ChevronRight, Loader2, ShieldCheck } from 'lucide-react';
import {
  webuiContractPagesAPI,
  type WebUIContractPageListItem,
  type WebUIContractWorkspaceListItem,
} from '@/api/webuiContractPages';
import { useSSE } from '@/hooks/useSSE';
import { ThemeContext } from '@/contexts/ThemeContext';
import { resolveWebUIContractPageIcon } from '@/utils/webuiContractPageIcons';
import PageRuntimeHost from '@/pages/WebUIContractPageHost/PageRuntimeHost';

interface WorkspaceSection {
  id: string;
  label: string;
  pages: WebUIContractPageListItem[];
  defaultPageId: string;
  showSidebar: boolean;
}

function buildWorkspaceSections(
  workspaceId: string | undefined,
  pages: WebUIContractPageListItem[],
  workspaceDefaultPageId?: string | null,
): WorkspaceSection[] {
  if (workspaceId === 'soc_ui') {
    const posturePages = pages.filter((page) => page.id === 'alert-denoise-triage-dashboard');
    const operationPages = pages.filter((page) => page.id === 'soc-overview' || page.id === 'soc-alerts');
    return [
      posturePages.length > 0
        ? {
            id: 'posture',
            label: '态势',
            pages: posturePages,
            defaultPageId: posturePages[0].id,
            showSidebar: false,
          }
        : null,
      operationPages.length > 0
        ? {
            id: 'operations',
            label: '告警运营',
            pages: operationPages,
            defaultPageId: operationPages.find((page) => page.id === 'soc-overview')?.id ?? operationPages[0].id,
            showSidebar: true,
          }
        : null,
    ].filter((section): section is WorkspaceSection => section !== null);
  }

  if (pages.length === 0) return [];
  const defaultPageId = workspaceDefaultPageId && pages.some((page) => page.id === workspaceDefaultPageId)
    ? workspaceDefaultPageId
    : pages.find((page) => page.buildStatus === 'ready')?.id ?? pages[0].id;
  return [
    {
      id: 'pages',
      label: '页面',
      pages,
      defaultPageId,
      showSidebar: true,
    },
  ];
}

export default function WebUIContractWorkspaceHost() {
  const { workspaceId, pageId } = useParams<{ workspaceId: string; pageId?: string }>();
  const { t } = useTranslation('webuiContractPage');
  const [workspaces, setWorkspaces] = useState<WebUIContractWorkspaceListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sectionSidebarCollapsed, setSectionSidebarCollapsed] = useState(false);
  const { theme, setTemporaryThemeOverride } = useContext(ThemeContext);

  const fetchWorkspaces = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const response = await webuiContractPagesAPI.listWorkspaces(true);
      setWorkspaces(Array.isArray(response.data) ? response.data : []);
    } catch (err: unknown) {
      setWorkspaces([]);
      setError(err instanceof Error ? err.message : t('workspace.loadFailed'));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void fetchWorkspaces();
  }, [fetchWorkspaces]);

  useSSE({
    url: '/api/event',
    onEvent: useCallback((evt) => {
      if (evt.type === 'contracts.webui.pages.nav_changed') {
        void fetchWorkspaces(true);
      }
    }, [fetchWorkspaces]),
    reconnect: { maxRetries: 5, initialDelay: 2000 },
  });

  const workspace = useMemo(
    () => workspaces.find((item) => item.id === workspaceId),
    [workspaceId, workspaces],
  );
  const pages = useMemo(
    () => [...(workspace?.pages ?? [])].sort((a, b) => a.order - b.order || a.title.localeCompare(b.title)),
    [workspace?.pages],
  );
  const sections = useMemo(
    () => buildWorkspaceSections(workspaceId, pages, workspace?.defaultPageId),
    [pages, workspace?.defaultPageId, workspaceId],
  );
  const defaultPageId = sections[0]?.defaultPageId ?? null;
  const currentPage = pages.find((page) => page.id === pageId);
  const currentSection = sections.find((section) => section.pages.some((page) => page.id === currentPage?.id)) ?? sections[0];
  const shouldUsePostureDarkTheme = workspaceId === 'soc_ui' && currentSection?.id === 'posture' && theme === 'light';

  useEffect(() => {
    if (!shouldUsePostureDarkTheme) {
      setTemporaryThemeOverride(null);
      return undefined;
    }

    setTemporaryThemeOverride('dark');
    return () => setTemporaryThemeOverride(null);
  }, [setTemporaryThemeOverride, shouldUsePostureDarkTheme]);

  if (!workspaceId) {
    return <div className="text-sm text-zinc-500">{t('workspace.missingWorkspaceId')}</div>;
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-zinc-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        {t('workspace.loading')}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-amber-900">
        <AlertCircle className="mt-0.5 h-5 w-5 flex-shrink-0" />
        <div>
          <div className="font-medium">{t('workspace.unavailableTitle')}</div>
          <div className="mt-1 text-sm">{error}</div>
          <button
            type="button"
            onClick={() => void fetchWorkspaces()}
            className="mt-3 rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-sm hover:bg-amber-100"
          >
            {t('host.retry')}
          </button>
        </div>
      </div>
    );
  }

  if (!workspace) {
    return <div className="text-sm text-zinc-500">{t('workspace.notFound')}</div>;
  }

  if (!pageId && defaultPageId) {
    return <Navigate to={`${workspace.route}/${defaultPageId}`} replace />;
  }

  if (pages.length === 0) {
    return <div className="text-sm text-zinc-500">{t('workspace.empty')}</div>;
  }

  if (!currentPage) {
    return <div className="text-sm text-zinc-500">{t('workspace.pageNotFound')}</div>;
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-zinc-50 dark:bg-zinc-950">
      <div className="border-b border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950">
        <div className="flex justify-center px-4">
          <nav className="flex min-h-14 items-stretch" aria-label={t('workspace.sectionNavigation')}>
            {sections.map((section) => {
              const isActive = section.id === currentSection?.id;
              const SectionIcon = section.id === 'posture' ? Activity : ShieldCheck;
              return (
                <Link
                  key={section.id}
                  to={`${workspace.route}/${section.defaultPageId}`}
                  className={`relative inline-flex min-w-32 items-center justify-center gap-2 border-x border-transparent px-7 text-sm font-semibold transition-colors ${
                    isActive
                      ? 'border-zinc-200 bg-zinc-50 text-zinc-950 before:absolute before:inset-x-0 before:top-0 before:h-0.5 before:bg-red-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-50'
                      : 'text-zinc-500 hover:bg-zinc-50 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-50'
                  }`}
                >
                  <SectionIcon className={`h-4 w-4 ${isActive ? 'text-red-500' : 'text-zinc-400'}`} />
                  <span>{section.label}</span>
                </Link>
              );
            })}
          </nav>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {currentSection?.showSidebar && (
          <aside
            className={`relative z-10 shrink-0 border-b border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-950 lg:border-b-0 lg:border-r ${
              sectionSidebarCollapsed ? 'lg:w-16' : 'lg:w-56'
            }`}
          >
            <button
              type="button"
              onClick={() => setSectionSidebarCollapsed((value) => !value)}
              className="absolute -right-3 top-5 z-10 hidden h-11 w-6 items-center justify-center rounded-full border border-zinc-200 bg-white text-zinc-500 shadow-sm transition-colors hover:border-zinc-300 hover:bg-zinc-50 hover:text-zinc-900 focus:outline-none focus:ring-2 focus:ring-red-100 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-50 lg:flex"
              title={sectionSidebarCollapsed ? t('workspace.expandSidebar') : t('workspace.collapseSidebar')}
              aria-label={sectionSidebarCollapsed ? t('workspace.expandSidebar') : t('workspace.collapseSidebar')}
            >
              {sectionSidebarCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
            </button>
            <nav className={`flex gap-1 overflow-x-auto px-4 py-4 lg:flex-col lg:overflow-visible ${sectionSidebarCollapsed ? 'lg:px-2' : ''}`}>
              {currentSection.pages.map((page) => {
                const PageIcon = resolveWebUIContractPageIcon(page.icon);
                const isActive = page.id === currentPage.id;
                return (
                  <Link
                    key={page.id}
                    to={`${workspace.route}/${page.id}`}
                    aria-label={page.title}
                    className={`flex min-w-36 items-center gap-2 rounded-md px-3 py-2 text-sm font-semibold transition-colors lg:min-w-0 ${
                      sectionSidebarCollapsed ? 'lg:justify-center lg:px-2' : ''
                    } ${
                      isActive
                        ? 'bg-zinc-100 text-zinc-950 dark:bg-zinc-900 dark:text-zinc-50'
                        : 'text-zinc-600 hover:bg-zinc-50 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-zinc-50'
                    }`}
                  >
                    <PageIcon className={`h-4 w-4 flex-shrink-0 ${isActive ? 'text-zinc-600 dark:text-zinc-300' : 'text-zinc-400 dark:text-zinc-500'}`} />
                    <span className={`truncate ${sectionSidebarCollapsed ? 'lg:hidden' : ''}`}>{page.title}</span>
                  </Link>
                );
              })}
            </nav>
          </aside>
        )}

        <section className="min-w-0 flex-1 overflow-hidden">
          <div className="h-full min-w-0 overflow-x-auto">
            <PageRuntimeHost key={currentPage.id} pageId={currentPage.id} />
          </div>
        </section>
      </div>
    </div>
  );
}
