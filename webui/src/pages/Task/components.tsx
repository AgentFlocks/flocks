import { IconCircleCheck, IconAlertCircle, IconLoader2, IconCalendar, IconRobot, IconRouteSquare2 } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { TaskStatus, ExecutionMode, DashboardCounts } from '@/api/task';
import { STATUS_CONFIG, PRIORITY_CONFIG, SOURCE_CONFIG } from './helpers';

export function StatusBadge({ status }: { status: TaskStatus }) {
  const { t } = useTranslation('task');
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.pending;
  const Icon = cfg.icon;
  return (
    <span className={`inline-flex items-center gap-2 rounded-tag px-2 py-0.5 text-xs font-medium ${cfg.color}`}>
      <Icon aria-hidden="true" className={`h-4 w-4 ${status === 'running' ? 'animate-spin' : ''}`} />
      {t(`status.${status}`, { defaultValue: status })}
    </span>
  );
}

export function PriorityBadge({ priority }: { priority: string }) {
  const { t } = useTranslation('task');
  const c = PRIORITY_CONFIG[priority] ?? { color: 'bg-subtle text-fg-secondary' };
  return (
    <span className={`px-2 py-0.5 rounded-pill text-xs font-medium ${c.color}`}>
      {t(`priority.${priority}`, { defaultValue: priority })}
    </span>
  );
}

export function SourceBadge({ sourceType }: { sourceType: string }) {
  const { t } = useTranslation('task');
  const c = SOURCE_CONFIG[sourceType] ?? { color: 'bg-subtle text-fg-secondary' };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-pill text-xs font-medium ${c.color}`}>
      {t(`source.${sourceType}`, { defaultValue: sourceType })}
    </span>
  );
}

export function ModeBadge({ mode, agent }: { mode: ExecutionMode; agent: string }) {
  if (mode === 'workflow')
    return <span className="inline-flex items-center gap-1 text-xs text-ai"><IconRouteSquare2 aria-hidden="true" className="w-4 h-4" /> Workflow</span>;
  return <span className="inline-flex items-center gap-1 text-xs text-fg-secondary"><IconRobot aria-hidden="true" className="w-4 h-4" /> {agent}</span>;
}

export function ActionButton({ icon, label, onClick, color }: {
  icon: React.ReactNode; label: string; onClick: () => void; color: string;
}) {
  const colors: Record<string, string> = {
    blue:   'bg-info-soft text-info hover:bg-info-soft',
    green:  'bg-success-soft text-success hover:bg-success-soft',
    yellow: 'bg-warning-soft text-warning hover:bg-warning-soft',
    red:    'bg-danger-soft text-danger hover:bg-danger-soft',
    gray:   'bg-subtle text-fg-secondary hover:bg-hover',
  };
  return (
    <button onClick={onClick} className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-control transition-colors ${colors[color] ?? colors.gray}`}>
      {icon} {label}
    </button>
  );
}

export function DashboardCards({ counts }: { counts: DashboardCounts | null }) {
  const { t } = useTranslation('task');
  const cards = [
    { label: t('dashboard.completedWeek'),   value: counts?.completed_week ?? 0,  icon: <IconCircleCheck aria-hidden="true" className="w-5 h-5 text-success" />, bg: 'bg-success-soft' },
    { label: t('dashboard.scheduledActive'), value: counts?.scheduled_active ?? 0, icon: <IconCalendar aria-hidden="true" className="w-5 h-5 text-ai" />,     bg: 'bg-ai-soft' },
    { label: t('dashboard.running'),         value: counts?.running ?? 0,          icon: <IconLoader2 aria-hidden="true" className="w-5 h-5 text-info" />,         bg: 'bg-info-soft' },
    { label: t('dashboard.failedWeek'),      value: counts?.failed_week ?? 0,      icon: <IconAlertCircle aria-hidden="true" className="w-5 h-5 text-danger" />,      bg: 'bg-danger-soft' },
  ];
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      {cards.map(card => (
        <div key={card.label} className={`${card.bg} rounded-card p-4 flex items-center gap-3`}>
          <div className="p-2 bg-surface rounded-control shadow-sm">{card.icon}</div>
          <div>
            <p className="text-3xl font-bold tabular-nums text-fg">{card.value}</p>
            <p className="text-xs text-fg-muted">{card.label}</p>
          </div>
        </div>
      ))}
    </div>
  );
}
