import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { AlertCircle, ArrowUpCircle, Download, Loader2, Power, RefreshCw, Trash2 } from 'lucide-react';
import { hubAPI, type HubSceneSuite } from '@/api/hub';
import { webuiContractPagesAPI } from '@/api/webuiContractPages';
import { flocksproUsersApi } from '@/api/flocksproUsers';
import { useToast } from '@/components/common/Toast';

const TEXT = {
  zh: {
    title: '场景套件',
    description: '场景工作区的套件都在这里装、停用和卸载；插件广场不再单独提供入口。',
    refresh: '刷新',
    install: '安装',
    update: '更新',
    uninstall: '卸载',
    enable: '启用',
    disable: '停用',
    installed: '已安装',
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
    empty: '还没有可用的场景套件',
    loadFailed: '加载场景套件失败',
    actionFailed: '操作失败',
    installed_ok: '安装完成',
    uninstalled_ok: '已卸载',
    updated_ok: '已更新',
    enabled_ok: '已启用',
    disabled_ok: '已停用',
  },
  en: {
    title: 'Scene Suites',
    description: 'Install, disable and remove scene workspace suites here; the plugin hub no longer lists them.',
    refresh: 'Refresh',
    install: 'Install',
    update: 'Update',
    uninstall: 'Uninstall',
    enable: 'Enable',
    disable: 'Disable',
    installed: 'Installed',
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
    empty: 'No scene suite is available yet',
    loadFailed: 'Failed to load scene suites',
    actionFailed: 'Action failed',
    installed_ok: 'Installed',
    uninstalled_ok: 'Uninstalled',
    updated_ok: 'Updated',
    enabled_ok: 'Enabled',
    disabled_ok: 'Disabled',
  },
};

type SuiteAction = 'install' | 'update' | 'uninstall' | 'enable' | 'disable';

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
  const [suites, setSuites] = useState<HubSceneSuite[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [hasPro, setHasPro] = useState(false);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const response = await hubAPI.sceneSuites();
      setSuites(Array.isArray(response.data) ? response.data : []);
      setError(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : text.loadFailed);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [text.loadFailed]);

  useEffect(() => {
    void load();
    void flocksproUsersApi.hasCapability().then(setHasPro).catch(() => setHasPro(false));
  }, [load]);

  const runAction = useCallback(async (suite: HubSceneSuite, action: SuiteAction) => {
    setBusyId(suite.id);
    try {
      if (action === 'install') await hubAPI.install('component', suite.id);
      // Updating the suite carries its pages along (the installer updates an
      // outdated child), so one call is enough.
      if (action === 'update') await hubAPI.update('component', suite.id);
      if (action === 'uninstall') await hubAPI.uninstall('component', suite.id);
      if ((action === 'enable' || action === 'disable') && suite.workspaceId) {
        await webuiContractPagesAPI.setWorkspaceEnabled(suite.workspaceId, action === 'enable');
      }
      toast.success(text[`${action === 'install' ? 'installed' : action === 'uninstall' ? 'uninstalled' : action === 'update' ? 'updated' : action}_ok` as keyof typeof text]);
      // The sidebar picks the change up from the contracts.webui SSE event.
      await load(true);
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : '';
      toast.error(`${text.actionFailed}: ${suiteName(suite, zh)}`, detail);
    } finally {
      setBusyId(null);
    }
  }, [load, text, toast, zh]);

  const stateLabel = useMemo(() => ({
    installed: text.installed,
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
      <div className="mb-5 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-bold text-zinc-900 dark:text-zinc-50">{text.title}</h1>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{text.description}</p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
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
          const isInstalled = suite.state === 'installed' || suite.state === 'updateAvailable' || suite.state === 'localOnly';
          const busy = busyId === suite.id;
          return (
            <section
              key={suite.id}
              data-suite-id={suite.id}
              className="rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
            >
              <div className="flex items-start justify-between gap-4">
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
                  </p>
                  {proLocked && (
                    <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">{text.proHint}</p>
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
                      {!isInstalled && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void runAction(suite, 'install')}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-zinc-900 px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-zinc-800 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
                        >
                          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                          {text.install}
                        </button>
                      )}
                      {suite.state === 'updateAvailable' && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void runAction(suite, 'update')}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
                        >
                          <ArrowUpCircle className="h-4 w-4" />
                          {text.update}
                        </button>
                      )}
                      {isInstalled && suite.workspaceId && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void runAction(suite, suite.workspaceEnabled === false ? 'enable' : 'disable')}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-1.5 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
                        >
                          <Power className="h-4 w-4" />
                          {suite.workspaceEnabled === false ? text.enable : text.disable}
                        </button>
                      )}
                      {isInstalled && (
                        <button
                          type="button"
                          disabled={busy}
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
