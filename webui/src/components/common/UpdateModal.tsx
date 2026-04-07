import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import {
  X,
  RefreshCw,
  ArrowUpCircle,
  CheckCircle,
  XCircle,
  ExternalLink,
  Loader2,
  Sparkles,
  ChevronDown,
  ChevronUp,
  Container,
  BellOff,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { checkUpdate, applyUpdate, VersionInfo, UpdateProgress } from '@/api/update';

// ------------------------------------------------------------------ //

const UPGRADE_PAGE_MARKER = 'flocks-upgrade-in-progress';
const HEALTH_POLL_INTERVAL = 2000;
const HEALTH_POLL_TIMEOUT = 5 * 60 * 1000;

export const UPDATE_DISMISSED_KEY = 'flocks-update-dismissed';

interface UpdateModalProps {
  onClose: () => void;
  onDismiss?: () => void;
}

export default function UpdateModal({ onClose, onDismiss }: UpdateModalProps) {
  const { t } = useTranslation('update');
  const [info, setInfo] = useState<VersionInfo | null>(null);
  const [checking, setChecking] = useState(false);
  const [upgrading, setUpgrading] = useState(false);
  const [steps, setSteps] = useState<UpdateProgress[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [showReleaseNotes, setShowReleaseNotes] = useState(false);
  // useRef avoids stale closure: the `restarting` value inside async callbacks
  // always reflects the latest state even after re-renders.
  const restartingRef = useRef(false);
  const setRestartingSync = (val: boolean) => {
    restartingRef.current = val;
    setRestarting(val);
  };

  useEffect(() => {
    fetchVersion();
  }, []);

  const fetchVersion = useCallback(async () => {
    setChecking(true);
    setError(null);
    try {
      const data = await checkUpdate();
      setInfo(data);
      if (data.error) setError(data.error);
    } catch (e: any) {
      setError(e.message ?? t('checkFailed'));
    } finally {
      setChecking(false);
    }
  }, [t]);

  const handleUpgrade = useCallback(async () => {
    if (!info?.has_update) return;
    setUpgrading(true);
    setSteps([]);
    setError(null);

    try {
      await applyUpdate(info.latest_version!, (progress) => {
        setSteps((prev) => {
          const existingIndex = prev.findIndex((item) => item.stage === progress.stage);
          if (existingIndex === -1) {
            return [...prev, progress];
          }

          const next = [...prev];
          next[existingIndex] = progress;
          return next;
        });
        if (progress.stage === 'restarting') {
          setRestartingSync(true);
          pollUntilReady();
        }
      });
    } catch (e: any) {
      // Use the ref to avoid stale closure — restarting may have been set
      // to true by the progress callback before this catch fires.
      if (!restartingRef.current) {
        setError(e.message ?? t('upgradeFailed'));
        setUpgrading(false);
      }
    }
  }, [info, t]);

  const isBusy = upgrading || restarting;
  const showProgressDialog = upgrading || restarting || steps.length > 0;
  const safeClose = () => {
    if (!isBusy) onClose();
  };

  const pollUntilReady = () => {
    const start = Date.now();
    const poll = async () => {
      if (Date.now() - start > HEALTH_POLL_TIMEOUT) {
        setError(t('restartTimeout'));
        setRestartingSync(false);
        setUpgrading(false);
        return;
      }

      try {
        const rootResponse = await fetch('/', { cache: 'no-store' });
        const rootHtml = await rootResponse.text();
        const stillShowingUpgradePage = rootHtml.includes(UPGRADE_PAGE_MARKER);

        if (rootResponse.ok && !stillShowingUpgradePage) {
          const healthResponse = await fetch('/api/health', { cache: 'no-store' });
          if (healthResponse.ok) {
            window.location.reload();
            return;
          }
        }
      } catch {
      }

      setTimeout(() => {
        void poll();
      }, HEALTH_POLL_INTERVAL);
    };
    setTimeout(() => {
      void poll();
    }, 1500);
  };

  const renderStep = (step: UpdateProgress, index: number) => {
    const label = t(`stageLabels.${step.stage}`, { defaultValue: step.stage });
    const isError = step.stage === 'error';
    const isSpinning = step.stage === 'restarting';
    return (
      <div key={index} className="flex items-center gap-2.5 py-1 text-sm">
        {isError
          ? <XCircle className="w-4 h-4 text-red-500 flex-shrink-0" />
          : isSpinning
          ? <Loader2 className="w-4 h-4 text-blue-500 animate-spin flex-shrink-0" />
          : <CheckCircle className="w-4 h-4 text-green-500 flex-shrink-0" />
        }
        <span className={isError ? 'text-red-600' : 'text-gray-700'}>{label}</span>
        {!isError && !isSpinning && (
          <span className="text-gray-400 text-xs truncate">{step.message}</span>
        )}
      </div>
    );
  };

  return createPortal(
    showProgressDialog ? (
      <>
        <div
          className="fixed inset-0 z-[90] bg-black/30"
          onClick={safeClose}
        />

        <div className="fixed inset-0 z-[100] flex items-center justify-center pointer-events-none">
          <div
            className="pointer-events-auto w-full max-w-md mx-4 bg-white rounded-2xl shadow-2xl border border-gray-200 overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-gray-500" />
                <span className="text-sm font-semibold text-gray-800">{t('title')}</span>
              </div>
              <button
                onClick={safeClose}
                disabled={isBusy}
                className="p-1 text-gray-400 hover:text-gray-600 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="px-4 pt-4 pb-2">
              <div className="rounded-2xl border border-amber-200 bg-gradient-to-br from-amber-50 via-orange-50 to-rose-50 p-4 shadow-sm">
                <div className="flex items-start gap-3">
                  <span className="flex h-10 w-10 items-center justify-center rounded-full bg-amber-500 text-white shadow-sm">
                    <ArrowUpCircle className="h-5 w-5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-semibold text-amber-950">
                      {upgrading || restarting ? t('upgrading') : t('newVersionTitle')}
                    </div>
                    {info?.latest_version && (
                      <div className="mt-1 text-2xl font-bold text-amber-900">
                        v{info.latest_version}
                      </div>
                    )}
                    <p className="mt-2 text-sm leading-6 text-amber-800">
                      {t('confirmUpgradeDesc')}
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {steps.length > 0 && (
              <div className="px-4 pb-3 pt-2">
                <div className="space-y-0">
                  {steps.map((s, i) => renderStep(s, i))}
                </div>
                {restarting && (
                  <p className="mt-2 text-xs text-gray-400">{t('restarting')}</p>
                )}
              </div>
            )}

            {error && (
              <div className="px-4 pb-3">
                <div className="flex items-start gap-2 text-xs text-red-600 bg-red-50 rounded-lg p-2.5">
                  <XCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                  <span>{error}</span>
                </div>
              </div>
            )}

            <div className="px-4 pb-4 flex items-center gap-2 flex-wrap">
              {!restarting && (
                <span className="flex items-center gap-1.5 text-xs text-gray-500">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  {t('upgrading')}
                </span>
              )}

              {restarting && (
                <span className="flex items-center gap-1.5 text-xs text-blue-600">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  {t('waitingRestart')}
                </span>
              )}

              {!isBusy && error && (
                <button
                  onClick={safeClose}
                  className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
                >
                  {t('laterAction')}
                </button>
              )}
            </div>
          </div>
        </div>
      </>
    ) : (
      <div className="fixed inset-x-4 bottom-4 z-[100] pointer-events-none sm:inset-x-auto sm:left-4 sm:w-full sm:max-w-sm lg:left-6 lg:bottom-6">
        <div
          className="pointer-events-auto rounded-2xl border border-amber-200 bg-white shadow-2xl overflow-hidden"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between border-b border-amber-100 bg-gradient-to-r from-amber-50 via-orange-50 to-rose-50 px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-amber-500 text-white shadow-sm">
                <ArrowUpCircle className="h-4 w-4" />
              </span>
              <div>
                <div className="text-sm font-semibold text-amber-950">
                  {info?.has_update ? t('newVersionTitle') : t('title')}
                </div>
                {info?.latest_version && (
                  <div className="text-xs text-amber-700">v{info.latest_version}</div>
                )}
              </div>
            </div>
            <button
              onClick={safeClose}
              className="p-1 text-gray-400 hover:text-gray-600 rounded transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="px-4 py-4">
            {info?.has_update ? (
              <>
                <div className="rounded-xl border border-amber-100 bg-amber-50/70 px-3 py-2 text-xs text-amber-800">
                  <div className="font-medium">{t('confirmUpgrade', { version: info.latest_version })}</div>
                  <div className="mt-1 leading-5">{t('newVersionDesc')}</div>
                </div>
                {info.update_allowed === false && (
                  <div className="mt-2 rounded-xl border border-blue-200 bg-blue-50 px-3 py-2.5 text-xs text-blue-800">
                    <div className="flex items-center gap-1.5 font-medium">
                      <Container className="w-3.5 h-3.5 flex-shrink-0" />
                      {t('dockerModeTitle')}
                    </div>
                    <p className="mt-1 leading-5">{t('dockerModeDesc')}</p>
                    <code className="mt-1.5 block rounded bg-blue-100 px-2 py-1 text-xs text-blue-900 select-all">
                      {t('dockerUpgradeHint')}
                    </code>
                  </div>
                )}
              </>
            ) : (
              <div className="rounded-xl border border-gray-100 bg-gray-50 px-3 py-2 text-xs text-gray-600">
                {checking ? t('checkUpdate') : error ?? t('upToDate')}
              </div>
            )}
          </div>

          <div className="px-4 pb-3 space-y-3">
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-400">{t('currentVersion')}</span>
              <span className="font-medium text-gray-700">{info ? `v${info.current_version}` : '—'}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-400">{t('latestVersion')}</span>
              <div className="flex items-center gap-1.5">
                {checking ? (
                  <Loader2 className="w-3 h-3 text-gray-400 animate-spin" />
                ) : info?.latest_version ? (
                  <>
                    <span className="font-medium text-gray-700">v{info.latest_version}</span>
                    {info.has_update ? (
                      <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-amber-100 text-amber-700">{t('hasUpdate')}</span>
                    ) : (
                      <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-green-100 text-green-700">{t('upToDate')}</span>
                    )}
                  </>
                ) : (
                  <span className="text-gray-400">—</span>
                )}
              </div>
            </div>
          </div>

          {info?.has_update && info.release_notes && (
            <div className="px-4 pb-3">
              <button
                onClick={() => setShowReleaseNotes((prev) => !prev)}
                className="flex w-full items-center justify-between rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 text-left transition-colors hover:bg-gray-100"
              >
                <span className="text-xs font-medium text-gray-600">{t('viewReleaseNotes')}</span>
                {showReleaseNotes ? (
                  <ChevronUp className="w-4 h-4 text-gray-400" />
                ) : (
                  <ChevronDown className="w-4 h-4 text-gray-400" />
                )}
              </button>

              {showReleaseNotes && (
                <div className="mt-2">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-xs font-medium text-gray-500">{t('releaseNotes')}</span>
                    {info.release_url && (
                      <a
                        href={info.release_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-0.5 text-xs text-blue-500 hover:text-blue-700"
                      >
                        {t('details')} <ExternalLink className="w-3 h-3" />
                      </a>
                    )}
                  </div>
                  <pre className="text-xs text-gray-500 bg-gray-50 rounded-lg p-2.5 whitespace-pre-wrap max-h-32 overflow-y-auto leading-relaxed">
                    {info.release_notes.trim()}
                  </pre>
                </div>
              )}
            </div>
          )}

          <div className="flex items-center gap-2 px-4 pb-4">
            <button
              onClick={fetchVersion}
              disabled={checking}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${checking ? 'animate-spin' : ''}`} />
              {t('checkUpdate')}
            </button>

            <button
              onClick={safeClose}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-600 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors"
            >
              {t('laterAction')}
            </button>

            {info?.has_update && onDismiss && (
              <button
                onClick={() => {
                  localStorage.setItem(UPDATE_DISMISSED_KEY, info.current_version);
                  onDismiss();
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-400 hover:text-gray-600 transition-colors"
                title={t('dismissAction')}
              >
                <BellOff className="w-3.5 h-3.5" />
                {t('dismissAction')}
              </button>
            )}

            {info?.has_update && info.update_allowed !== false && (
              <button
                onClick={handleUpgrade}
                className="ml-auto flex items-center gap-1.5 px-4 py-2 text-xs font-semibold text-white bg-amber-500 hover:bg-amber-600 rounded-lg shadow-sm transition-colors"
              >
                <ArrowUpCircle className="w-3.5 h-3.5" />
                {t('confirmAction')}
              </button>
            )}
          </div>
        </div>
      </div>
    ),
    document.body,
  );
}
