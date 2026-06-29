import { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { AlertCircle, Loader2 } from 'lucide-react';
import {
  webuiContractPagesAPI,
  type WebUIContractPageListItem,
  type WebUIContractWorkspaceListItem,
} from '@/api/webuiContractPages';
import { useSSE } from '@/hooks/useSSE';
import { ThemeContext } from '@/contexts/ThemeContext';
import PageRuntimeHost from '@/pages/WebUIContractPageHost/PageRuntimeHost';

interface WorkspaceSection {
  id: string;
  label: string;
  pages: WebUIContractPageListItem[];
  defaultPageId: string;
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
          }
        : null,
      operationPages.length > 0
        ? {
            id: 'operations',
            label: '告警运营',
            pages: operationPages,
            defaultPageId: operationPages.find((page) => page.id === 'soc-overview')?.id ?? operationPages[0].id,
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
    },
  ];
}

export default function WebUIContractWorkspaceHost() {
  const { workspaceId, pageId } = useParams<{ workspaceId: string; pageId?: string }>();
  const { t } = useTranslation('webuiContractPage');
  const [workspaces, setWorkspaces] = useState<WebUIContractWorkspaceListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
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
  const currentPage = pages.find((page) => page.id === pageId);
  const currentSection = currentPage
    ? sections.find((section) => section.pages.some((page) => page.id === currentPage.id))
    : undefined;
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

  if (pages.length === 0) {
    return <div className="text-sm text-zinc-500">{t('workspace.empty')}</div>;
  }

  if (!pageId) {
    return (
      <div className="flex h-full items-center justify-center bg-zinc-50 text-sm text-zinc-500 dark:bg-zinc-950 dark:text-zinc-400">
        {t('workspace.selectPage')}
      </div>
    );
  }

  if (!currentPage) {
    return <div className="text-sm text-zinc-500">{t('workspace.pageNotFound')}</div>;
  }

  const pageContentClassName = currentSection?.id === 'posture'
    ? 'h-full min-w-0 overflow-x-auto'
    : 'h-full min-w-0 overflow-x-auto p-6';

  return (
    <div className="h-full min-h-0 overflow-hidden bg-zinc-50 dark:bg-zinc-950">
      <div className={pageContentClassName}>
        <PageRuntimeHost key={currentPage.id} pageId={currentPage.id} />
      </div>
    </div>
  );
}
