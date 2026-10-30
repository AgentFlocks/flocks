import { useCallback } from 'react';
import { CheckCircle, Circle, Loader2, MinusCircle, XCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { UpgradeHandoffProgress, UpgradeHandoffStepStatus } from '@/api/update';

function StepIcon({ status }: { status: UpgradeHandoffStepStatus }) {
  if (status === 'running') return <Loader2 className="h-3.5 w-3.5 flex-shrink-0 animate-spin text-blue-500" />;
  if (status === 'done') return <CheckCircle className="h-3.5 w-3.5 flex-shrink-0 text-green-500" />;
  if (status === 'failed') return <XCircle className="h-3.5 w-3.5 flex-shrink-0 text-red-500" />;
  if (status === 'skipped') return <MinusCircle className="h-3.5 w-3.5 flex-shrink-0 text-gray-300" />;
  return <Circle className="h-3.5 w-3.5 flex-shrink-0 text-gray-300" />;
}

/** Message for a failed restart step: which step, why, and how to bring the service back. */
export function useHandoffFailureMessage() {
  const { t } = useTranslation('update');
  return useCallback((progress: UpgradeHandoffProgress) => {
    const stepId = progress.failed_step ?? progress.step ?? '';
    const step = t(`handoff.steps.${stepId}`, { defaultValue: stepId });
    const error = progress.error || t('handoff.noDetail');
    return `${t('handoff.failed', { step, error })} ${t('handoff.recoveryHint')}`;
  }, [t]);
}

/** Steps of the restart stage, reported by the upgrade handoff while the service is offline. */
export default function UpgradeHandoffSteps({ progress }: { progress: UpgradeHandoffProgress }) {
  const { t } = useTranslation('update');
  return (
    <ol aria-label={t('handoff.title')} className="mt-1.5 space-y-1">
      {progress.steps.map((step) => (
        <li key={step.id} data-status={step.status} className="flex items-center gap-2 text-xs">
          <StepIcon status={step.status} />
          <span
            className={
              step.status === 'failed'
                ? 'text-red-600'
                : step.status === 'running'
                ? 'font-medium text-gray-800'
                : step.status === 'done'
                ? 'text-gray-600'
                : 'text-gray-400'
            }
          >
            {t(`handoff.steps.${step.id}`, { defaultValue: step.id })}
          </span>
          <span className="sr-only">{t(`handoff.statuses.${step.status}`)}</span>
        </li>
      ))}
    </ol>
  );
}
