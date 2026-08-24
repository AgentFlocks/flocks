import { useContext, useEffect, useMemo } from 'react';
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { AlertCircle, Loader2 } from 'lucide-react';
import { useWebUIContractPages } from '@/hooks/useWebUIContractPages';
import { useDelayedVisible } from '@/hooks/useDelayedVisible';
import { ThemeContext } from '@/contexts/ThemeContext';
import PageRuntimeHost from '@/pages/WebUIContractPageHost/PageRuntimeHost';
import { buildWebUIContractWorkspaceSections } from '@/utils/webuiContractWorkspaceSections';

export default function WebUIContractWorkspaceHost() {
  const { workspaceId, pageId } = useParams<{ workspaceId: string; pageId?: string }>();
  const { t, i18n } = useTranslation('webuiContractPage');
  const {
    workspaces,
    loading: resourceLoading,
    error,
    refetch,
  } = useWebUIContractPages();
  const loading = resourceLoading && workspaces.length === 0;
  const showLoading = useDelayedVisible(loading ? 180 : 0);
  const { theme, setTemporaryThemeOverride } = useContext(ThemeContext);

  const workspace = useMemo(
    () => workspaces.find((item) => item.id === workspaceId),
    [workspaceId, workspaces],
  );
  const pages = useMemo(
    () => [...(workspace?.pages ?? [])].sort((a, b) => a.order - b.order || a.title.localeCompare(b.title)),
    [workspace?.pages],
  );
  const sections = useMemo(
    () => (workspace ? buildWebUIContractWorkspaceSections(workspace, i18n.language) : []),
    [i18n.language, workspace],
  );
  const currentPage = pages.find((page) => page.id === pageId);
  const currentSection = currentPage
    ? sections.find((section) => section.pages.some((page) => page.id === currentPage.id))
    : undefined;
  const temporaryThemeOverride = currentSection?.themeOverride && theme !== currentSection.themeOverride
    ? currentSection.themeOverride
    : null;

  useEffect(() => {
    if (!temporaryThemeOverride) {
      setTemporaryThemeOverride(null);
      return undefined;
    }

    setTemporaryThemeOverride(temporaryThemeOverride);
    return () => setTemporaryThemeOverride(null);
  }, [setTemporaryThemeOverride, temporaryThemeOverride]);

  if (!workspaceId) {
    return <div className="text-sm text-zinc-500">{t('workspace.missingWorkspaceId')}</div>;
  }

  if (loading) {
    if (!showLoading) return null;
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
            onClick={() => void refetch()}
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

  const pageContentClassName = currentSection?.contentPadding === 'none'
    ? 'h-full min-w-0 overflow-x-auto'
    : 'h-full min-w-0 overflow-x-auto p-6';

  return (
    <div className="h-full min-h-0 overflow-hidden bg-zinc-50 dark:bg-zinc-950">
      <div className={pageContentClassName}>
        <PageRuntimeHost
          key={currentPage.id}
          pageId={currentPage.id}
          initialBuildHash={currentPage.buildStatus === 'ready' ? currentPage.buildHash : undefined}
        />
      </div>
    </div>
  );
}
