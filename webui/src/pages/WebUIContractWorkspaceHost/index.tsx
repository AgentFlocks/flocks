import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { IconAlertCircle, IconLoader2 } from '@tabler/icons-react';
import {
  webuiContractPagesAPI,
  type WebUIContractWorkspaceListItem,
} from '@/api/webuiContractPages';
import { hubAPI, type HubSceneSuite } from '@/api/hub';
import { useSSE } from '@/hooks/useSSE';
import { useDelayedVisible } from '@/hooks/useDelayedVisible';
import { ThemeContext } from '@/contexts/ThemeContext';
import { PaneActiveContext } from '@/components/layout/PaneActiveContext';
import PageRuntimeHost from '@/pages/WebUIContractPageHost/PageRuntimeHost';
import {
  buildWebUIContractWorkspacePageList,
  buildWebUIContractWorkspaceSections,
} from '@/utils/webuiContractWorkspaceSections';

export default function WebUIContractWorkspaceHost() {
  const { workspaceId, pageId } = useParams<{ workspaceId: string; pageId?: string }>();
  const { t, i18n } = useTranslation('webuiContractPage');
  const [workspaces, setWorkspaces] = useState<WebUIContractWorkspaceListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const showLoading = useDelayedVisible(loading ? 180 : 0);
  const { theme, setTemporaryThemeOverride } = useContext(ThemeContext);
  // Hidden tabs stay mounted; the override must follow the tab on screen.
  const paneActive = useContext(PaneActiveContext);
  // A workspace that is missing may simply belong to a suite this edition
  // cannot install; say so instead of "not found".
  const [missingSuite, setMissingSuite] = useState<HubSceneSuite | null>(null);
  // Keep the fetcher stable: a new `t` identity must not trigger a refetch loop.
  const tRef = useRef(t);
  useEffect(() => {
    tRef.current = t;
  }, [t]);

  const fetchWorkspaces = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const response = await webuiContractPagesAPI.listWorkspaces(true);
      setWorkspaces(Array.isArray(response.data) ? response.data : []);
    } catch (err: unknown) {
      setWorkspaces([]);
      setError(err instanceof Error ? err.message : tRef.current('workspace.loadFailed'));
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

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
    ? sections.find((section) => section.pages.some((page) => page.id === currentPage.id))
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

  // Full-bleed route: keep the host's own status views clear of the mobile
  // layout's floating menu button (see PageRuntimeHost).
  const statusClassName = 'p-4 pt-20 lg:p-6';

  if (!workspaceId) {
    return <div className={`${statusClassName} text-sm text-fg-muted`}>{t('workspace.missingWorkspaceId')}</div>;
  }

  if (loading) {
    if (!showLoading) return null;
    return (
      <div className={`${statusClassName} flex items-center gap-2 text-sm text-fg-muted`}>
        <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
        {t('workspace.loading')}
      </div>
    );
  }

  if (error) {
    return (
      <div className={statusClassName}>
        <div className="flex items-start gap-3 rounded-control border border-warning bg-warning-soft p-4 text-warning">
          <IconAlertCircle aria-hidden="true" className="mt-0.5 h-5 w-5 flex-shrink-0" />
          <div>
            <div className="font-medium">{t('workspace.unavailableTitle')}</div>
            <div className="mt-1 text-sm">{error}</div>
            <button
              type="button"
              onClick={() => void fetchWorkspaces()}
              className="mt-3 rounded-control border border-warning bg-surface px-3 py-1.5 text-sm hover:bg-warning-soft"
            >
              {t('host.retry')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (!workspace) {
    if (missingSuite && missingSuite.workspaceEnabled === false) {
      return (
        <div className="flex items-start gap-3 rounded-control border border-line bg-subtle p-4 text-fg-secondary ">
          <IconAlertCircle aria-hidden="true" className="mt-0.5 h-5 w-5 flex-shrink-0" />
          <div>
            <div className="font-medium">{t('workspace.disabledTitle')}</div>
            <div className="mt-1 text-sm">{t('workspace.disabledBody')}</div>
            <Link
              to="/scenes/suites"
              className="mt-3 inline-flex rounded-control border border-line-control bg-surface px-3 py-1.5 text-sm hover:bg-hover "
            >
              {t('workspace.proOnlyAction')}
            </Link>
          </div>
        </div>
      );
    }
    if (missingSuite?.edition === 'pro') {
      return (
        <div className="flex items-start gap-3 rounded-control border border-warning bg-warning-soft p-4 text-warning ">
          <IconAlertCircle aria-hidden="true" className="mt-0.5 h-5 w-5 flex-shrink-0" />
          <div>
            <div className="font-medium">{t('workspace.proOnlyTitle')}</div>
            <div className="mt-1 text-sm">{t('workspace.proOnlyBody')}</div>
            <Link
              to="/scenes/suites"
              className="mt-3 inline-flex rounded-control border border-warning bg-surface px-3 py-1.5 text-sm text-warning hover:bg-warning-soft "
            >
              {t('workspace.proOnlyAction')}
            </Link>
          </div>
        </div>
      );
    }
    return <div className={`${statusClassName} text-sm text-fg-muted`}>{t('workspace.notFound')}</div>;
  }

  if (pages.length === 0) {
    return <div className={`${statusClassName} text-sm text-fg-muted`}>{t('workspace.empty')}</div>;
  }

  if (!pageId) {
    // Opening the workspace itself lands on its default page instead of an empty frame.
    const defaultPage = pages.find((page) => page.id === workspace.defaultPageId)
      ?? pages.find((page) => page.buildStatus === 'ready')
      ?? pages[0];
    return <Navigate to={`${workspace.route}/${defaultPage.id}`} replace />;
  }

  if (!currentPage) {
    return <div className={`${statusClassName} text-sm text-fg-muted`}>{t('workspace.pageNotFound')}</div>;
  }

  const pageContentClassName = currentSection?.contentPadding === 'none'
    ? 'h-full min-w-0 overflow-x-auto'
    : 'h-full min-w-0 overflow-x-auto p-6';

  return (
    <div className="h-full min-h-0 overflow-hidden bg-subtle ">
      <div className={pageContentClassName}>
        <PageRuntimeHost key={currentPage.id} pageId={currentPage.id} />
      </div>
    </div>
  );
}
