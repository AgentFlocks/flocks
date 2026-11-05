import { useState } from 'react';
import { Check, Copy, XCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { EnterpriseUpgradeState } from '@/api/enterpriseUpgrade';
import type { ReleaseUpdateOutcome } from '@/utils/releaseUpdate';

/** Where an upgrade stopped and why: what somebody needs to act on it, or to pass it on. */
export interface UpgradeFailure {
  /** The step, a key of update:failure.steps (download, install, bundle, preflight, start...). */
  step?: string | null;
  /** Why, in the words of whatever failed. */
  reason?: string | null;
  exitCode?: number | null;
  /** How the machine was left, a key of update:failure.recovery. */
  recovery?: string | null;
  /** Where the whole log is, on the machine. */
  logPath?: string | null;
  logTail?: string[] | null;
}

/** A failed upgrade of an open install to the enterprise edition, as the server last recorded it. */
export function failureOfEnterpriseUpgrade(state: EnterpriseUpgradeState | null | undefined, fallback?: string | null): UpgradeFailure {
  if (!state) return { reason: fallback ?? null };
  return {
    step: state.failed_step ?? (state.log ? 'install' : 'download'),
    reason: state.reason ?? state.message ?? fallback ?? null,
    exitCode: state.exit_code ?? null,
    // The open edition answering is how this came to be read: the record of an install that got as
    // far as switching the machine says more, and without one it was never touched.
    recovery: state.recovery ?? 'unchanged',
    logPath: state.log ?? null,
    logTail: state.log_tail ?? null,
  };
}

/** A failed update of a release install, as its upgrade unit answered it. */
export function failureOfReleaseUpdate(outcome: ReleaseUpdateOutcome, fallback?: string | null): UpgradeFailure {
  return {
    step: outcome.failed_step ?? null,
    reason: outcome.detail || fallback || null,
    recovery: outcome.recovery ?? null,
    logPath: outcome.log ?? null,
    logTail: outcome.log_tail ?? null,
  };
}

export default function UpgradeFailureDetails({ failure }: { failure: UpgradeFailure }) {
  const { t } = useTranslation('update');
  const [copied, setCopied] = useState(false);
  const { step, reason, exitCode, recovery, logPath, logTail } = failure;
  const stepLabel = step ? t(`failure.steps.${step}`, { defaultValue: step }) : null;
  const heading = stepLabel ? t('failure.stoppedAt', { step: stepLabel }) : t('upgradeFailed');
  const recoveryText = recovery ? t(`failure.recovery.${recovery}`, { defaultValue: '' }) : '';
  const exitText = typeof exitCode === 'number' ? t('failure.exitCode', { code: exitCode }) : '';
  const hasLog = Boolean(logTail && logTail.length > 0);

  const copy = async () => {
    const text = [
      heading,
      reason ? `${t('failure.reason')}${reason}` : '',
      exitText,
      recoveryText,
      logPath ? `${t('failure.logPath')}${logPath}` : '',
      hasLog ? logTail!.join('\n') : '',
    ].filter(Boolean).join('\n');
    await navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    }).catch(() => {});
  };

  return (
    <div role="alert" data-testid="upgrade-failure" className="space-y-1.5 rounded-lg bg-red-50 p-2.5 text-xs text-red-700">
      <div className="flex items-start gap-2 font-medium text-red-600">
        <XCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />
        <span className="min-w-0 [overflow-wrap:anywhere]">{heading}</span>
      </div>
      <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">
        <span>{t('failure.reason')}</span>
        <span data-testid="upgrade-failure-reason">{reason || t('failure.noReason')}</span>
      </p>
      {exitText && <p>{exitText}</p>}
      {recoveryText && <p className="[overflow-wrap:anywhere]">{recoveryText}</p>}
      {logPath && (
        <p className="[overflow-wrap:anywhere]">
          {t('failure.logPath')}<code className="font-mono">{logPath}</code>
        </p>
      )}
      {hasLog && (
        <details open>
          <summary className="cursor-pointer text-red-600">{t('failure.logTail', { count: logTail!.length })}</summary>
          <pre
            tabIndex={0}
            data-testid="upgrade-failure-log"
            className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap rounded bg-white p-2 font-mono text-[11px] leading-4 text-gray-700 [overflow-wrap:anywhere]"
          >
            {logTail!.join('\n')}
          </pre>
        </details>
      )}
      <button
        type="button"
        onClick={() => void copy()}
        className="inline-flex items-center gap-1 rounded border border-red-200 bg-white px-2 py-1 text-xs text-red-600 transition-colors hover:bg-red-100"
      >
        {copied ? <Check className="h-3 w-3" aria-hidden="true" /> : <Copy className="h-3 w-3" aria-hidden="true" />}
        {copied ? t('failure.copied') : t('failure.copy')}
      </button>
    </div>
  );
}
