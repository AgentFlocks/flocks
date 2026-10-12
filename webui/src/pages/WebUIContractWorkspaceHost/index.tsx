import { useContext, useEffect, useMemo, useState } from 'react';
import { Link, Navigate, useLocation, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { AlertCircle, Loader2 } from 'lucide-react';
import { useWebUIContractPages } from '@/hooks/useWebUIContractPages';
import { hubAPI, type HubSceneSuite } from '@/api/hub';
import { useDelayedVisible } from '@/hooks/useDelayedVisible';
import { ThemeContext } from '@/contexts/ThemeContext';
import { PaneActiveContext } from '@/components/layout/PaneActiveContext';
import PageRuntimeHost from '@/pages/WebUIContractPageHost/PageRuntimeHost';
import { buildWebUIContractWorkspacePageList, buildWebUIContractWorkspaceSections, findWorkspaceSection, workspaceSectionHref } from '@/utils/webuiContractWorkspaceSections';

export default function WebUIContractWorkspaceHost() {
  const location = useLocation();
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
  // Hidden tabs stay mounted; the override must follow the tab on screen.
  const paneActive = useContext(PaneActiveContext);
  // A workspace that is missing may simply belong to a suite this edition
  // cannot install; say so instead of "not found".
  const [missingSuite, setMissingSuite] = useState<HubSceneSuite | null>(null);
  const workspace = useMemo(
    () => workspaces.find((item) => item.id === workspaceId),
    [workspaceId, workspaces],
  );
  const sections = useMemo(
    () => (workspace ? buildWebUIContractWorkspaceSections(workspace, i18n.language) : []),
    [i18n.language, workspace],
  );
  // Declared order (section by section); only used to find the current page
  // and a fallback default, so the sidebar's per-section drag order is not needed here.
  const pages = useMemo(
    () => (workspace ? buildWebUIContractWorkspacePageList(workspace, i18n.language) : []),
    [i18n.language, workspace],
  );
  useEffect(() => {
    if (loading || !workspaceId || workspace) {
      setMissingSuite(null);
      return;
    }
    let cancelled = false;
    void hubAPI.sceneSuites()
      .then((response) => {
        if (cancelled) return;
        const suites = Array.isArray(response.data) ? response.data : [];
        setMissingSuite(suites.find((suite) => suite.workspaceId === workspaceId) ?? null);
      })
      .catch(() => {
        if (!cancelled) setMissingSuite(null);
      });
    return () => {
      cancelled = true;
    };
  }, [loading, workspace, workspaceId]);

  const currentPage = pages.find((page) => page.id === pageId);
  const currentSection = currentPage
    ? findWorkspaceSection(sections, currentPage.id, location.search)
    : undefined;
  const temporaryThemeOverride = currentSection?.themeOverride && theme !== currentSection.themeOverride
    ? currentSection.themeOverride
    : null;

  useEffect(() => {
    // Only the visible pane owns the override, and only its cleanup releases
    // it: an inactive pane setting null here could clobber the active one's.
    if (!paneActive || !temporaryThemeOverride) return undefined;

    setTemporaryThemeOverride(temporaryThemeOverride);
    return () => setTemporaryThemeOverride(null);
  }, [paneActive, setTemporaryThemeOverride, temporaryThemeOverride]);

  const proOnlyNotice = (
    <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
      <AlertCircle aria-hidden="true" className="mt-0.5 h-5 w-5 flex-shrink-0" />
      <div>
        <div className="font-medium">{t('workspace.proOnlyTitle')}</div>
        <div className="mt-1 text-sm">{t('workspace.proOnlyBody')}</div>
        <Link
          to="/scenes/suites"
          className="mt-3 inline-flex rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-sm text-amber-900 hover:bg-amber-100 dark:border-amber-800 dark:bg-transparent dark:text-amber-200"
        >
          {t('workspace.proOnlyAction')}
        </Link>
      </div>
    </div>
  );

  // Pro is active, but this suite is licensed on its own and that licence is not in force.
  const unlicensedNotice = (
    <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
      <AlertCircle aria-hidden="true" className="mt-0.5 h-5 w-5 flex-shrink-0" />
      <div>
        <div className="font-medium">{t('workspace.unlicensedTitle')}</div>
        <div className="mt-1 text-sm">{t('workspace.unlicensedBody')}</div>
        <Link
          to="/settings/suite-licenses"
          className="mt-3 inline-flex rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-sm text-amber-900 hover:bg-amber-100 dark:border-amber-800 dark:bg-transparent dark:text-amber-200"
        >
          {t('workspace.unlicensedAction')}
        </Link>
      </div>
    </div>
  );

  if (!workspaceId) {
    return <div className="text-sm text-zinc-500">{t('workspace.missingWorkspaceId')}</div>;
  }

  if (loading) {
    if (!showLoading) return null;
    return (
      <div className="flex items-center gap-2 text-sm text-zinc-500">
        <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
        {t('workspace.loading')}
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-amber-900">
        <AlertCircle aria-hidden="true" className="mt-0.5 h-5 w-5 flex-shrink-0" />
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
    if (missingSuite && missingSuite.workspaceEnabled === false) {
      return (
        <div className="flex items-start gap-3 rounded-lg border border-zinc-200 bg-zinc-50 p-4 text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-200">
          <AlertCircle aria-hidden="true" className="mt-0.5 h-5 w-5 flex-shrink-0" />
          <div>
            <div className="font-medium">{t('workspace.disabledTitle')}</div>
            <div className="mt-1 text-sm">{t('workspace.disabledBody')}</div>
            <Link
              to="/scenes/suites"
              className="mt-3 inline-flex rounded-lg border border-zinc-300 bg-white px-3 py-1.5 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:bg-transparent dark:text-zinc-200"
            >
              {t('workspace.proOnlyAction')}
            </Link>
          </div>
        </div>
      );
    }
    if (missingSuite?.edition === 'pro' && missingSuite.licensed === false) return unlicensedNotice;
    if (missingSuite?.edition === 'pro') return proOnlyNotice;
    return <div className="text-sm text-zinc-500">{t('workspace.notFound')}</div>;
  }

  // Installed, but locked: its suite needs a Pro license that is not active,
  // or its own suite licence, which is not in force.
  if (workspace.locked) return workspace.licensed === false ? unlicensedNotice : proOnlyNotice;

  if (pages.length === 0) {
    return <div className="text-sm text-zinc-500">{t('workspace.empty')}</div>;
  }

  if (!pageId) {
    // Opening the workspace itself lands on its default page instead of an empty frame.
    const defaultPage = pages.find((page) => page.id === workspace.defaultPageId)
      ?? pages.find((page) => page.buildStatus === 'ready')
      ?? pages[0];
    const defaultSection = sections.find(section => section.pages.some(page => page.id === defaultPage.id));
    return <Navigate to={defaultSection ? workspaceSectionHref(workspace.route, defaultSection, defaultPage.id) : `${workspace.route}/${defaultPage.id}`} replace />;
  }

  if (!currentPage) {
    return <div className="text-sm text-zinc-500">{t('workspace.pageNotFound')}</div>;
  }

  const pageContentClassName = currentSection?.contentPadding === 'none'
    ? 'h-full min-w-0 overflow-x-auto'
    : 'h-full min-w-0 overflow-x-auto p-6';

  return (
    <div className="bg-zinc-50 dark:bg-zinc-950 h-full min-h-0 overflow-hidden">
      <div className={pageContentClassName}>
        <PageRuntimeHost
          key={`${currentPage.id}:${location.key}`}
          pageId={currentPage.id}
          initialBuildHash={currentPage.buildStatus === 'ready' ? currentPage.buildHash : undefined}
        />
      </div>
    </div>
  );
}
