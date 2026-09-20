import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { AlertCircle, ArrowUpCircle, Download, Loader2, Power, RefreshCw, Trash2 } from 'lucide-react';
import { hubAPI, type HubInstallProgressEvent, type HubSceneSuite } from '@/api/hub';
import SuiteInstallProgressPanel, {
  applySuiteInstallProgressEvent,
  createSuiteInstallProgressState,
  failSuiteInstallProgress,
  type SuiteInstallProgressState,
} from '@/components/hub/SuiteInstallProgressPanel';
import { webuiContractPagesAPI } from '@/api/webuiContractPages';
import { flocksproUsersApi } from '@/api/flocksproUsers';
import { useToast } from '@/components/common/Toast';
import { notifySceneSuitesChanged, useSceneSuiteUpdates } from '@/hooks/useSceneSuiteUpdates';
import { sceneSuiteErrorMessage } from '@/utils/sceneSuites';

const TEXT = {
  zh: {
    title: '添加场景',
    description: '安装后会自动启用场景，也可以在这里启用、停用和卸载已安装的场景。',
    refresh: '刷新',
    install: '安装',
    update: '更新',
    uninstall: '卸载',
    enable: '启用',
    disable: '停用',
    installed: '已安装',
    partial: '待补全',
    completeInstall: '补全安装',
    partialWorkspace: '此场景已存在，部分套件组件缺失。点击“补全安装”恢复完整套件。',
    partialSuite: '套件安装不完整，点击“补全安装”恢复缺失的场景组件。',
    available: '未安装',
    updateAvailable: '有更新',
    broken: '安装异常',
    incompatible: '不兼容',
    localOnly: '仅本地',
    disabled: '已停用',
    proBadge: '需要 Flocks Pro',
    proHint: '当前为开源版，升级后可安装该场景。',
    upgrade: '去升级',
    currentVersion: '已装 v{v}',
    latestVersion: '最新 v{v}',
    pagesVersion: '场景页面 v{installed} → v{latest}',
    empty: '还没有可用的场景套件',
    loadFailed: '加载场景套件失败',
    actionFailed: '操作失败',
    installed_ok: '安装完成，场景已启用',
    installedEnableFailed: '安装已完成，但启用失败。请点击“启用”重试。',
    updatedEnableFailed: '更新已完成，但启用失败。请点击“启用”重试。',
    workspaceMissing: '未找到该套件的场景工作区，请刷新后重试。',
    workspaceDisabled: '此场景尚未启用，点击“启用”即可打开场景。',
    workspacePending: '此场景已启用，页面尚未就绪。请刷新状态；如安装异常，请更新或重新安装场景。',
    uninstalled_ok: '已卸载',
    updated_ok: '已更新，场景已启用',
    enabled_ok: '已启用',
    disabled_ok: '已停用',
  },
  en: {
    title: 'Add scene',
    description: 'Scenes are enabled after installation. You can also enable, disable or uninstall installed scenes here.',
    refresh: 'Refresh',
    install: 'Install',
    update: 'Update',
    uninstall: 'Uninstall',
    enable: 'Enable',
    disable: 'Disable',
    installed: 'Installed',
    partial: 'Incomplete',
    completeInstall: 'Complete installation',
    partialWorkspace: 'This scene exists, but some suite components are missing. Complete the installation to restore the full suite.',
    partialSuite: 'This suite is incomplete. Complete the installation to restore the missing scene components.',
    available: 'Not installed',
    updateAvailable: 'Update available',
    broken: 'Broken',
    incompatible: 'Incompatible',
    localOnly: 'Local only',
    disabled: 'Disabled',
    proBadge: 'Flocks Pro required',
    proHint: 'This is the open-source edition; upgrade to install this scene.',
    upgrade: 'Upgrade',
    currentVersion: 'installed v{v}',
    latestVersion: 'latest v{v}',
    pagesVersion: 'scene pages v{installed} → v{latest}',
    empty: 'No scene suite is available yet',
    loadFailed: 'Failed to load scene suites',
    actionFailed: 'Action failed',
    installed_ok: 'Installed and enabled',
    installedEnableFailed: 'Installed, but enabling failed. Click Enable to retry.',
    updatedEnableFailed: 'Updated, but enabling failed. Click Enable to retry.',
    workspaceMissing: 'The scene workspace was not found. Refresh and try again.',
    workspaceDisabled: 'This scene is disabled. Click Enable to open it.',
    workspacePending: 'This scene is enabled, but its pages are not ready. Refresh its status, or update or reinstall the scene if installation is incomplete.',
    uninstalled_ok: 'Uninstalled',
    updated_ok: 'Updated and enabled',
    enabled_ok: 'Enabled',
    disabled_ok: 'Disabled',
  },
};

type SuiteAction = 'install' | 'update' | 'uninstall' | 'enable' | 'disable';

/** The progress panel takes a catalog-shaped entry, whose nameCn is not nullable. */
function progressEntry(suite: HubSceneSuite) {
  return { id: suite.id, name: suite.name, nameCn: suite.nameCn ?? undefined };
}

function suiteName(suite: HubSceneSuite, zh: boolean): string {
  return (zh && suite.nameCn) ? suite.nameCn : suite.name;
}

function suiteDescription(suite: HubSceneSuite, zh: boolean): string {
  return (zh && suite.descriptionCn) ? suite.descriptionCn : suite.description;
}

export default function SceneSuitesPage() {
  const { i18n } = useTranslation('nav');
  const zh = i18n.language.toLowerCase().startsWith('zh');
  const text = zh ? TEXT.zh : TEXT.en;
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const mountedRef = useRef(false);
  const latestSearchParamsRef = useRef(searchParams);
  const targetWorkspace = searchParams.get('workspace');
  const pagesUnavailable = searchParams.get('reason') === 'pages-unavailable';
  const targetCardRef = useRef<HTMLElement | null>(null);
  const { suites, loading, error, refetch } = useSceneSuiteUpdates();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [hasPro, setHasPro] = useState(false);
  // Installing a suite pulls in its pages, tool and workflows one by one; show
  // the same per-component progress the hub used to show.
  const [installProgress, setInstallProgress] = useState<SuiteInstallProgressState | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    void flocksproUsersApi.hasCapability().then(setHasPro).catch(() => setHasPro(false));
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    latestSearchParamsRef.current = searchParams;
  }, [searchParams]);

  useEffect(() => {
    if (loading || !targetWorkspace) return;
    targetCardRef.current?.scrollIntoView?.({ block: 'nearest' });
    targetCardRef.current?.focus({ preventScroll: true });
  }, [loading, targetWorkspace]);

  const runAction = useCallback(async (suite: HubSceneSuite, action: SuiteAction) => {
    setBusyId(suite.id);
    if (action !== 'install') setInstallProgress(null);
    let installed = false;
    let updated = false;
    let changed = false;
    let enabledWorkspaceId: string | null = null;
    // Installing or updating a scene is done to use it: both leave it enabled,
    // whatever an earlier 停用 left in the workspace state.
    const enableSuiteWorkspace = async () => {
      // A component ID and its WebUI workspace ID are different identifiers.
      // Prefer the catalog's explicit mapping, and refresh when it was absent.
      const workspaceId = suite.workspaceId || (await refetch({ silent: true, rejectOnError: true }))
        .find((entry) => entry.id === suite.id)?.workspaceId;
      if (!workspaceId) throw new Error(text.workspaceMissing);
      await webuiContractPagesAPI.setWorkspaceEnabled(workspaceId, true);
      enabledWorkspaceId = workspaceId;
    };
    try {
      if (action === 'install') {
        setInstallProgress(createSuiteInstallProgressState(progressEntry(suite)));
        await hubAPI.installStream('component', suite.id, (event: HubInstallProgressEvent) => {
          setInstallProgress((current) => applySuiteInstallProgressEvent(current, event));
        });
        installed = true;
        changed = true;
        await enableSuiteWorkspace();
      }
      // Updating the suite carries its pages along (the installer updates an
      // outdated child), so one call is enough.
      if (action === 'update') {
        await hubAPI.update('component', suite.id);
        updated = true;
        changed = true;
        await enableSuiteWorkspace();
      }
      if (action === 'uninstall') await hubAPI.uninstall('component', suite.id);
      if (action === 'enable' || action === 'disable') {
        if (!suite.workspaceId) throw new Error(text.workspaceMissing);
        await webuiContractPagesAPI.setWorkspaceEnabled(suite.workspaceId, action === 'enable');
        if (action === 'enable') enabledWorkspaceId = suite.workspaceId;
      }
      changed = true;
      const currentParams = latestSearchParamsRef.current;
      if (
        mountedRef.current
        && enabledWorkspaceId
        && currentParams.get('workspace') === enabledWorkspaceId
        && currentParams.get('reason') === 'pages-unavailable'
      ) {
        // Installation may finish after the user leaves or selects another
        // scene. Only clear the reason belonging to the still-visible target.
        const next = new URLSearchParams(currentParams);
        next.delete('reason');
        setSearchParams(next, { replace: true });
      }
      toast.success(text[`${action === 'install' ? 'installed' : action === 'uninstall' ? 'uninstalled' : action === 'update' ? 'updated' : action}_ok` as keyof typeof text]);
    } catch (err: unknown) {
      const detail = sceneSuiteErrorMessage(err, text.actionFailed);
      if (action === 'install' && !installed) {
        setInstallProgress((current) => failSuiteInstallProgress(current, progressEntry(suite), detail || text.actionFailed));
      }
      toast.error(
        installed ? text.installedEnableFailed : updated ? text.updatedEnableFailed : `${text.actionFailed}: ${suiteName(suite, zh)}`,
        detail,
      );
    } finally {
      if (changed) await notifySceneSuitesChanged();
      setBusyId(null);
    }
  }, [refetch, setSearchParams, text, toast, zh]);

  const stateLabel = useMemo(() => ({
    installed: text.installed,
    partial: text.partial,
    available: text.available,
    updateAvailable: text.updateAvailable,
    broken: text.broken,
    incompatible: text.incompatible,
    localOnly: text.localOnly,
  }), [text]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-zinc-500 dark:text-zinc-400">
        <Loader2 className="h-4 w-4 animate-spin" />
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-4xl">
      {installProgress && (
        <div className="mb-4">
          <SuiteInstallProgressPanel
            progress={installProgress}
            language={i18n.language}
            onClose={() => setInstallProgress(null)}
          />
        </div>
      )}
      <div className="mb-5 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-bold text-zinc-900 dark:text-zinc-50">{text.title}</h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{text.description}</p>
        </div>
        <button
          type="button"
          onClick={() => void refetch()}
          disabled={busyId !== null}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-1.5 text-sm font-medium text-zinc-600 transition-colors hover:bg-zinc-50 dark:border-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-900"
        >
          <RefreshCw className="h-4 w-4" />
          {text.refresh}
        </button>
      </div>

      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {suites.length === 0 && !error && (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">{text.empty}</p>
      )}

      <div className="space-y-3">
        {suites.map((suite) => {
          const proLocked = suite.edition === 'pro' && !hasPro;
          const isPartial = suite.state === 'partial';
          const isInstalled = isPartial || suite.state === 'installed' || suite.state === 'updateAvailable' || suite.state === 'localOnly';
          const hasWorkspace = suite.workspaceEnabled != null;
          // "有更新" can come from the page package alone while the suite
          // version is unchanged; say so next to the suite versions.
          const pagesBehind = isInstalled
            && !!suite.workspaceVersion
            && !!suite.workspaceLatestVersion
            && suite.workspaceVersion !== suite.workspaceLatestVersion;
          const busy = busyId === suite.id;
          const targeted = !!targetWorkspace && targetWorkspace === suite.workspaceId;
          return (
            <section
              key={suite.id}
              data-suite-id={suite.id}
              ref={targeted ? targetCardRef : undefined}
              tabIndex={targeted ? -1 : undefined}
              className={`rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 ${targeted ? 'ring-2 ring-zinc-500 ring-offset-2 dark:ring-offset-zinc-950' : ''}`}
            >
              <div className="flex flex-col items-start justify-between gap-4 sm:flex-row">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-50">{suiteName(suite, zh)}</h2>
                    <span className="rounded border border-zinc-200 px-1.5 py-0.5 text-[11px] font-medium text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
                      {stateLabel[suite.state] ?? suite.state}
                    </span>
                    {suite.edition === 'pro' && (
                      <span className="rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[11px] font-semibold text-amber-700 dark:border-amber-800 dark:bg-amber-950/50 dark:text-amber-300">
                        {text.proBadge}
                      </span>
                    )}
                    {isInstalled && suite.workspaceEnabled === false && (
                      <span className="rounded border border-zinc-300 px-1.5 py-0.5 text-[11px] font-medium text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
                        {text.disabled}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{suiteDescription(suite, zh)}</p>
                  <p className="mt-1 text-xs text-zinc-400 dark:text-zinc-500">
                    {suite.installedVersion ? text.currentVersion.replace('{v}', suite.installedVersion) + ' · ' : ''}
                    {text.latestVersion.replace('{v}', suite.version)}
                    {pagesBehind && (
                      <>
                        {' · '}
                        <span className="text-amber-600 dark:text-amber-400">
                          {text.pagesVersion.replace('{installed}', suite.workspaceVersion ?? '').replace('{latest}', suite.workspaceLatestVersion ?? '')}
                        </span>
                      </>
                    )}
                  </p>
                  {proLocked && (
                    <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">{text.proHint}</p>
                  )}
                  {isPartial && (
                    <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">
                      {hasWorkspace ? text.partialWorkspace : text.partialSuite}
                    </p>
                  )}
                  {targeted && isInstalled && hasWorkspace && (suite.workspaceEnabled === false || (!isPartial && pagesUnavailable)) && (
                    <p className="mt-2 text-sm text-amber-700 dark:text-amber-300">
                      {suite.workspaceEnabled === false ? text.workspaceDisabled : text.workspacePending}
                    </p>
                  )}
                </div>

                <div className="flex shrink-0 flex-col items-end gap-2">
                  {proLocked ? (
                    <Link
                      to="/settings/flockspro"
                      className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500 px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-amber-600"
                    >
                      <ArrowUpCircle className="h-4 w-4" />
                      {text.upgrade}
                    </Link>
                  ) : (
                    <>
                      {(!isInstalled || isPartial) && (
                        <button
                          type="button"
                          disabled={busyId !== null}
                          onClick={() => void runAction(suite, 'install')}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-zinc-900 px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
                        >
                          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                          {isPartial ? text.completeInstall : text.install}
                        </button>
                      )}
                      {suite.state === 'updateAvailable' && (
                        <button
                          type="button"
                          disabled={busyId !== null}
                          onClick={() => void runAction(suite, 'update')}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
                        >
                          <ArrowUpCircle className="h-4 w-4" />
                          {text.update}
                        </button>
                      )}
                      {isInstalled && suite.workspaceId && hasWorkspace && (
                        <button
                          type="button"
                          disabled={busyId !== null}
                          onClick={() => void runAction(suite, suite.workspaceEnabled === false ? 'enable' : 'disable')}
                          className={suite.workspaceEnabled === false
                            ? 'inline-flex items-center gap-1.5 rounded-lg bg-zinc-900 px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white'
                            : 'inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800'}
                        >
                          <Power className="h-4 w-4" />
                          {suite.workspaceEnabled === false ? text.enable : text.disable}
                        </button>
                      )}
                      {isInstalled && (
                        <button
                          type="button"
                          disabled={busyId !== null}
                          onClick={() => void runAction(suite, 'uninstall')}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 transition-colors hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950/40"
                        >
                          <Trash2 className="h-4 w-4" />
                          {text.uninstall}
                        </button>
                      )}
                    </>
                  )}
                </div>
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
