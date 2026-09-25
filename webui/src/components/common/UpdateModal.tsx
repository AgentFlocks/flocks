import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import {
  IconX,
  IconRefresh,
  IconCircleArrowUp,
  IconCircleCheck,
  IconCircleX,
  IconLoader2,
  IconSparkles,
  IconContainer,
  IconShieldCheck,
  IconServer,
  IconBellOff,
} from '@tabler/icons-react';
import NoticeCard, { NoticePrimaryAction } from './NoticeCard';
import { useTranslation } from 'react-i18next';
import { checkUpdate, applyUpdate, IncompleteUpdateError, UpdateStreamTimeoutError, VersionInfo, UpdateProgress, DEFAULT_UPDATE_CHANNEL } from '@/api/update';
import { checkRestartReadiness } from '@/utils/restartPolling';
import { UPDATE_DISMISSED_KEY, buildUpdateDismissalKey } from '@/utils/updateDismissal';
import { useDialogFocus } from '@/hooks/useDialogFocus';

// ------------------------------------------------------------------ //

const HEALTH_POLL_INTERVAL = 2000;
const HEALTH_POLL_TIMEOUT = 5 * 60 * 1000;

export { UPDATE_DISMISSED_KEY };

function formatUpdateVersion(version?: string | null): string {
  const raw = (version || '').trim();
  if (!raw) return '—';
  return /^\d/.test(raw) ? `v${raw}` : raw;
}

function clampPercent(value?: number | null): number | null {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return null;
  }
  return Math.max(0, Math.min(100, Math.round(value)));
}

function formatBytes(value?: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    return '—';
  }
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const precision = unitIndex === 0 || size >= 10 ? 0 : 1;
  return `${size.toFixed(precision)} ${units[unitIndex]}`;
}

interface UpdateModalProps {
  initialInfo?: VersionInfo | null;
  forceInitialCheck?: boolean;
  /** Update channel to check and apply; unset means the default channel. */
  channel?: string;
  canUpgrade?: boolean;
  onClose: () => void;
  onDismiss?: () => void;
  onPresented?: () => void;
}

export default function UpdateModal({
  initialInfo,
  forceInitialCheck = false,
  channel = DEFAULT_UPDATE_CHANNEL,
  canUpgrade = true,
  onClose,
  onDismiss,
  onPresented,
}: UpdateModalProps) {
  useEffect(() => { onPresented?.(); }, [onPresented]);
  const { t, i18n } = useTranslation('update');
  const [info, setInfo] = useState<VersionInfo | null>(initialInfo ?? null);
  const [checking, setChecking] = useState(false);
  const [upgrading, setUpgrading] = useState(false);
  const [upgradeAttempted, setUpgradeAttempted] = useState(false);
  const [steps, setSteps] = useState<UpdateProgress[]>([]);
  const [error, setError] = useState<string | null>(initialInfo?.error ?? null);
  const [restarting, setRestarting] = useState(false);
  const modalTitle = t('title');
  const currentDisplayVersion = info?.current_version || null;
  const latestDisplayVersion = info?.latest_version || null;
  // useRef avoids stale closure: the `restarting` value inside async callbacks
  // always reflects the latest state even after re-renders.
  const restartingRef = useRef(false);
  const restartFailureReasonRef = useRef('');
  const setRestartingSync = (val: boolean) => {
    restartingRef.current = val;
    setRestarting(val);
  };

  const fetchVersion = useCallback(async (force = false) => {
    setChecking(true);
    setError(null);
    try {
      const data = await checkUpdate(i18n.language, channel, force);
      setInfo(data);
      if (data.error) setError(data.error);
    } catch (e: any) {
      setError(e.message ?? t('checkFailed'));
    } finally {
      setChecking(false);
    }
  }, [channel, i18n.language, t]);

  useEffect(() => {
    if (!initialInfo) {
      void fetchVersion(forceInitialCheck);
    }
  }, [fetchVersion, forceInitialCheck, initialInfo]);

  const handleUpgrade = useCallback(async () => {
    if (!info?.has_update || !canUpgrade || info.update_allowed === false) return;
    setUpgrading(true);
    setUpgradeAttempted(true);
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
          restartFailureReasonRef.current = '';
          setRestartingSync(true);
          pollUntilReady();
        }
      }, i18n.language, channel);
      if (!restartingRef.current) setUpgrading(false);
    } catch (e: any) {
      // Use the ref to avoid stale closure — restarting may have been set
      // to true by the progress callback before this catch fires.
      if (!restartingRef.current) {
        setError(e instanceof IncompleteUpdateError ? t('streamInterrupted')
          : e instanceof UpdateStreamTimeoutError ? t('streamTimeout') : e.message ?? t('upgradeFailed'));
        setUpgrading(false);
      } else if (e?.message) {
        restartFailureReasonRef.current = e.message;
      }
    }
  }, [canUpgrade, channel, i18n.language, info, t]);

  const isBusy = upgrading || restarting;
  const completed = !isBusy && !error && steps.some((step) => step.stage === 'done');
  const showProgressDialog = upgradeAttempted || upgrading || restarting || steps.length > 0;
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialogFocus(dialogRef, showProgressDialog, isBusy ? undefined : onClose);
  const progressTone = isBusy ? 'bg-warning-soft text-warning' : completed ? 'bg-success-soft text-success' : 'bg-danger-soft text-danger';
  const ProgressIcon = isBusy ? IconCircleArrowUp : completed ? IconCircleCheck : IconCircleX;
  const safeClose = () => {
    if (!isBusy) onClose();
  };

  const pollUntilReady = () => {
    const start = Date.now();
    let lastPollFailure = '';
    const poll = async () => {
      if (Date.now() - start > HEALTH_POLL_TIMEOUT) {
        const reason = restartFailureReasonRef.current || lastPollFailure || t('restartUnknown');
        setError(t('restartTimeout', { reason }));
        setRestartingSync(false);
        setUpgrading(false);
        return;
      }

      const readiness = await checkRestartReadiness();
      if (readiness.ready) {
        window.location.reload();
        return;
      }
      lastPollFailure = readiness.reason || lastPollFailure;

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
    const isSpinning = step.stage === 'restarting' && restarting;
    const downloadPercent = clampPercent(step.percent);
    const hasDownloadProgress = step.stage === 'fetching' && typeof step.downloaded_bytes === 'number';
    const detail = step.bundle_filename || step.message;
    return (
      <div key={index} className="flex items-start gap-2.5 py-1 text-sm">
        {isError
          ? <IconCircleX aria-hidden="true" className="mt-0.5 w-4 h-4 text-danger flex-shrink-0" />
          : isSpinning
          ? <IconLoader2 aria-hidden="true" className="mt-0.5 w-4 h-4 text-info animate-spin flex-shrink-0" />
          : <IconCircleCheck aria-hidden="true" className="mt-0.5 w-4 h-4 text-success flex-shrink-0" />
        }
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className={isError ? 'text-danger' : 'text-fg-secondary'}>{label}</span>
            {!isError && !isSpinning && (
              <span className="min-w-0 truncate text-xs text-fg-muted">{detail}</span>
            )}
          </div>
          {hasDownloadProgress && (
            <div className="mt-1.5 max-w-xs space-y-1">
              <div className="text-xs text-fg-muted">
                {downloadPercent === null
                  ? t('downloadProgressUnknown', { downloaded: formatBytes(step.downloaded_bytes) })
                  : t('downloadProgressLabel', {
                      percent: downloadPercent,
                      downloaded: formatBytes(step.downloaded_bytes),
                      total: formatBytes(step.total_bytes),
                    })}
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-subtle">
                <div
                  className={`h-full rounded-full bg-warning transition-all ${downloadPercent === null ? 'w-1/2 animate-pulse' : ''}`}
                  style={downloadPercent === null ? undefined : { width: `${downloadPercent}%` }}
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={downloadPercent ?? undefined}
                />
              </div>
            </div>
          )}
        </div>
      </div>
    );
  };

  return createPortal(
    showProgressDialog ? (
      <>
        <div
          className="fixed inset-0 z-[90] bg-overlay-backdrop"
          onClick={safeClose}
        />

        <div className="fixed inset-0 z-[100] flex items-center justify-center pointer-events-none">
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-label={modalTitle}
            tabIndex={-1}
            data-ui-overlay="dialog"
            className="pointer-events-auto flex w-full max-w-modal max-h-[calc(100dvh-2rem)] mx-4 flex-col bg-surface rounded-overlay shadow-overlay border border-line overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex shrink-0 items-center justify-between px-4 sm:px-6 py-4 border-b border-line">
              <div className="flex items-center gap-2">
                <IconSparkles aria-hidden="true" className="w-4 h-4 text-fg-muted" />
                <h2 className="text-title-section text-fg">{modalTitle}</h2>
              </div>
              <button
                aria-label={t('closeAction')}
                onClick={safeClose}
                disabled={isBusy}
                className="ui-btn ui-btn-ghost ui-btn-icon text-fg-muted"
              >
                <IconX aria-hidden="true" className="w-4 h-4" />
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="px-4 sm:px-6 pt-4 pb-2">
              <div className={`rounded-card border border-line p-4 ${progressTone}`}>
                <div className="flex items-start gap-3">
                  <span className="flex h-8 w-8 items-center justify-center rounded-control">
                    <ProgressIcon aria-hidden="true" className="h-5 w-5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-semibold">
                      {isBusy ? t('upgrading') : completed ? t('upgradeComplete') : t('upgradeFailed')}
                    </div>
                    {latestDisplayVersion && (
                      <div className="mt-1 text-2xl font-bold">
                        {formatUpdateVersion(latestDisplayVersion)}
                      </div>
                    )}
                    <p className="mt-2 text-sm">
                      {t(isBusy ? 'confirmUpgradeDesc' : completed ? 'upgradeCompleteDesc' : 'upgradeFailedDesc')}
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
                  <p className="mt-2 text-xs text-fg-muted">{t('restarting')}</p>
                )}
              </div>
            )}

            {error && (
              <div className="px-4 pb-3">
                <div className="flex items-start gap-2 text-xs text-danger bg-danger-soft rounded-lg p-2.5">
                  <IconCircleX aria-hidden="true" className="w-4 h-4 flex-shrink-0 mt-0.5" />
                  <span className="min-w-0 break-words">{error}</span>
                </div>
              </div>
            )}

            </div>
            <div className="px-4 sm:px-6 py-4 shrink-0 flex items-center gap-2 flex-wrap">
              {upgrading && !restarting && (
                <span className="flex items-center gap-1.5 text-xs text-fg-muted">
                  <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" />
                  {t('upgrading')}
                </span>
              )}

              {restarting && (
                <span className="flex items-center gap-1.5 text-xs text-info">
                  <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" />
                  {t('waitingRestart')}
                </span>
              )}

              {!isBusy && (
                <button
                  onClick={safeClose}
                  className="ui-btn ui-btn-primary ml-auto"
                >
                  {t('closeAction')}
                </button>
              )}
            </div>
          </div>
        </div>
      </>
    ) : (
      <NoticeCard
        title={info?.has_update ? t('newVersionTitle') : modalTitle}
        subtitle={latestDisplayVersion ? formatUpdateVersion(latestDisplayVersion) : undefined}
        icon={IconCircleArrowUp}
        closeLabel={t('laterAction')}
        onClose={safeClose}
      >
          <div className="px-4 py-4">
            {info?.has_update ? (
              <div className="rounded-xl border border-warning bg-warning-soft px-3 py-2 text-xs text-warning">
                <div className="font-medium">
                  {t('confirmUpgrade', { version: formatUpdateVersion(latestDisplayVersion) })}
                </div>
                <div className="mt-1">{t('newVersionDesc')}</div>
              </div>
            ) : (
              <div className="rounded-xl border border-line bg-canvas px-3 py-2 text-xs text-fg-secondary">
                {checking
                  ? t('checkUpdate')
                  : error ?? (info?.deploy_mode === 'offline' && !info.latest_version ? t('offlineNotChecked') : t('upToDate'))}
              </div>
            )}
            {/* The deploy-mode notice does not depend on has_update: an offline package
                deployment usually cannot reach the update server at all, and the answer to
                "how do I upgrade" is the .run either way. */}
            {info?.update_allowed === false && info.deploy_mode === 'managed' && (
              // Managed (commercial-core supervised) install: the core owns
              // package upgrades, so the Docker guidance would be misleading.
              <div className="mt-2 rounded-xl border border-info bg-info-soft px-3 py-2.5 text-xs text-info">
                <div className="flex items-center gap-1.5 font-medium">
                  <IconShieldCheck aria-hidden="true" className="w-4 h-4 flex-shrink-0" />
                  {t('managedModeTitle')}
                </div>
                <p className="mt-1">{t('managedModeDesc')}</p>
              </div>
            )}
            {info?.update_allowed === false && info.deploy_mode !== 'managed' && (
              <div className="mt-2 rounded-xl border border-info bg-info-soft px-3 py-2.5 text-xs text-info">
                <div className="flex items-center gap-1.5 font-medium">
                  {info.deploy_mode === 'offline' ? (
                    <IconServer aria-hidden="true" className="w-4 h-4 flex-shrink-0" />
                  ) : (
                    <IconContainer aria-hidden="true" className="w-4 h-4 flex-shrink-0" />
                  )}
                  {t(info.deploy_mode === 'offline' ? 'offlineModeTitle' : 'dockerModeTitle')}
                </div>
                <p className="mt-1">
                  {t(info.deploy_mode === 'offline' ? 'offlineModeDesc' : 'dockerModeDesc')}
                </p>
                <code className="mt-1.5 block rounded bg-info-soft px-2 py-1 text-xs text-info select-all">
                  {t(info.deploy_mode === 'offline' ? 'offlineUpgradeHint' : 'dockerUpgradeHint')}
                </code>
              </div>
            )}
          </div>

          <div className="px-4 pb-3 space-y-3">
            <div className="flex items-center justify-between text-xs">
              <span className="text-fg-muted">{t('currentVersion')}</span>
              <span className="font-medium text-fg-secondary">{formatUpdateVersion(currentDisplayVersion)}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-fg-muted">{t('latestVersion')}</span>
              <div className="flex items-center gap-1.5">
                {checking ? (
                  <IconLoader2 aria-hidden="true" className="w-4 h-4 text-fg-muted animate-spin" />
                ) : latestDisplayVersion ? (
                  <>
                    <span className="font-medium text-fg-secondary">{formatUpdateVersion(latestDisplayVersion)}</span>
                    {info?.has_update ? (
                      <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-warning-soft text-warning">{t('hasUpdate')}</span>
                    ) : (
                      <span className="px-1.5 py-0.5 rounded text-xs font-medium bg-success-soft text-success">{t('upToDate')}</span>
                    )}
                  </>
                ) : (
                  <span className="text-fg-muted">—</span>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2 px-4 pb-4">
            <button
              onClick={() => void fetchVersion(true)}
              disabled={checking}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-fg-secondary bg-subtle hover:bg-hover rounded-lg disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed transition-colors"
            >
              <IconRefresh aria-hidden="true" className={`w-4 h-4 ${checking ? 'animate-spin' : ''}`} />
              {t('checkUpdate')}
            </button>

            <button
              onClick={safeClose}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-fg-secondary bg-subtle hover:bg-hover rounded-lg transition-colors"
            >
              {t('laterAction')}
            </button>

            {info?.has_update && onDismiss && (
              <button
                onClick={() => {
                  const dismissalKey = buildUpdateDismissalKey(info);
                  if (dismissalKey) {
                    localStorage.setItem(UPDATE_DISMISSED_KEY, dismissalKey);
                  }
                  onDismiss();
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-fg-muted hover:text-fg-secondary transition-colors"
                title={t('dismissAction')}
              >
                <IconBellOff aria-hidden="true" className="w-4 h-4" />
                {t('dismissAction')}
              </button>
            )}

            {canUpgrade && info?.has_update && info.update_allowed !== false && (
              <NoticePrimaryAction onClick={handleUpgrade} className="ml-auto">
                <IconCircleArrowUp aria-hidden="true" className="w-4 h-4" />
                {t('confirmAction')}
              </NoticePrimaryAction>
            )}
          </div>
      </NoticeCard>
    ),
    document.body,
  );
}
