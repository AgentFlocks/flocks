/**
 * Shared status badge for workflow service and execution statuses.
 *
 * variant="pill"  — rounded-full with a color dot (used in service lists)
 * variant="badge" — compact rounded without dot (used in execution results)
 */

import { useTranslation } from 'react-i18next';
import { IconCircleCheck, IconCircleX, IconClock, IconCircleMinus, IconLoader2 } from '@tabler/icons-react';

interface StatusConfig {
  className: string;
  dot: string;
  icon?: typeof IconCircleCheck;
}

const STATUS_STYLE_MAP: Record<string, StatusConfig> = {
  running:    { className: 'bg-info-soft text-info', dot: 'bg-info', icon: IconLoader2 },
  cancelling: { className: 'bg-warning-soft text-warning', dot: 'bg-warning' },
  publishing: { className: 'bg-warning-soft text-warning', dot: 'bg-warning' },
  success:    { className: 'bg-success-soft text-success',  dot: 'bg-success' },
  SUCCEEDED:  { className: 'bg-success-soft text-success',  dot: 'bg-success' },
  error:      { className: 'bg-danger-soft text-danger',      dot: 'bg-action' },
  FAILED:     { className: 'bg-danger-soft text-danger',      dot: 'bg-action' },
  timeout:    { className: 'bg-warning-soft text-warning', dot: 'bg-warning' },
  cancelled:  { className: 'bg-subtle text-fg-secondary',    dot: 'bg-selected' },
  stopped:    { className: 'bg-subtle text-fg-muted',    dot: 'bg-selected' },
};

const FALLBACK: StatusConfig = { className: 'bg-subtle text-fg-muted', dot: 'bg-selected' };

interface WorkflowStatusBadgeProps {
  status: string;
  variant?: 'pill' | 'badge';
}

export default function WorkflowStatusBadge({ status, variant = 'badge' }: WorkflowStatusBadgeProps) {
  const { t } = useTranslation('common');
  const cfg = STATUS_STYLE_MAP[status] ?? FALLBACK;
  const label = t(`workflowStatus.${status}`, { defaultValue: status });
  const Icon = cfg.icon ?? (['success', 'SUCCEEDED'].includes(status) ? IconCircleCheck : ['error', 'FAILED'].includes(status) ? IconCircleX : ['cancelling', 'publishing', 'timeout'].includes(status) ? IconClock : IconCircleMinus);

  if (variant === 'pill') {
    return (
      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.className}`}>
        <Icon aria-hidden="true" className={`h-4 w-4 shrink-0 ${status === 'running' ? 'animate-spin motion-reduce:animate-none' : ''}`} />
        {label}
      </span>
    );
  }

  return (
    <span className={`ui-badge font-medium ${cfg.className}`}>
      <Icon aria-hidden="true" className={`h-4 w-4 shrink-0 ${status === 'running' ? 'animate-spin motion-reduce:animate-none' : ''}`} />
      {label}
    </span>
  );
}
