/**
 * Shared status badge for workflow service and execution statuses.
 *
 * variant="pill"  — rounded-full with a color dot (used in service lists)
 * variant="badge" — compact rounded without dot (used in execution results)
 */

import { useTranslation } from 'react-i18next';
import {
  CheckCircle,
  CircleMinus,
  Clock,
  Loader2,
  XCircle,
} from 'lucide-react';

interface StatusConfig {
  className: string;
  dot: string;
  icon?: typeof CheckCircle;
}

const STATUS_STYLE_MAP: Record<string, StatusConfig> = {
  running:    { className: 'bg-green-100 text-green-700', dot: 'bg-green-500', icon: Loader2 },
  cancelling: { className: 'bg-amber-100 text-amber-700', dot: 'bg-amber-500' },
  publishing: { className: 'bg-yellow-100 text-yellow-700', dot: 'bg-yellow-500' },
  success:    { className: 'bg-green-100 text-green-700',  dot: 'bg-green-500' },
  SUCCEEDED:  { className: 'bg-green-100 text-green-700',  dot: 'bg-green-500' },
  error:      { className: 'bg-red-100 text-red-700',      dot: 'bg-red-500' },
  FAILED:     { className: 'bg-red-100 text-red-700',      dot: 'bg-red-500' },
  timeout:    { className: 'bg-orange-100 text-orange-700', dot: 'bg-orange-500' },
  cancelled:  { className: 'bg-gray-100 text-gray-600',    dot: 'bg-gray-500' },
  stopped:    { className: 'bg-gray-100 text-gray-500',    dot: 'bg-gray-400' },
};

const FALLBACK: StatusConfig = { className: 'bg-gray-100 text-gray-500', dot: 'bg-gray-400' };

interface WorkflowStatusBadgeProps {
  status: string;
  variant?: 'pill' | 'badge';
}

export default function WorkflowStatusBadge({ status, variant = 'badge' }: WorkflowStatusBadgeProps) {
  const { t } = useTranslation('common');
  const cfg = STATUS_STYLE_MAP[status] ?? FALLBACK;
  const label = t(`workflowStatus.${status}`, { defaultValue: status });
  const Icon = cfg.icon ?? (['success', 'SUCCEEDED'].includes(status) ? CheckCircle : ['error', 'FAILED'].includes(status) ? XCircle : ['cancelling', 'publishing', 'timeout'].includes(status) ? Clock : CircleMinus);

  if (variant === 'pill') {
    return (
      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.className}`}>
        <Icon aria-hidden="true" className={`h-4 w-4 shrink-0 ${status === 'running' ? 'animate-spin motion-reduce:animate-none' : ''}`} />
        {label}
      </span>
    );
  }

  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium ${cfg.className}`}>
      <Icon aria-hidden="true" className={`h-4 w-4 shrink-0 ${status === 'running' ? 'animate-spin motion-reduce:animate-none' : ''}`} />
      {label}
    </span>
  );
}
