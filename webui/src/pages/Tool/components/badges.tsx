import { useTranslation } from 'react-i18next';
import { IconCircleCheck, IconCircleX, IconAlertTriangle, IconPlugOff, IconLoader2, type TablerIcon } from '@tabler/icons-react';

interface BadgeConfig {
  labelKey: string;
  className: string;
  icon: TablerIcon;
  pending?: boolean;
}

const MCP_STATUS_KEYS: Record<string, Omit<BadgeConfig, 'labelKey'> & { labelKey: string }> = {
  connected: { labelKey: 'statusBadge.connected', className: 'bg-success-soft text-success', icon: IconCircleCheck },
  connecting: { labelKey: 'statusBadge.connecting', className: 'bg-info-soft text-info', icon: IconLoader2, pending: true },
  disconnected: { labelKey: 'statusBadge.disconnected', className: 'bg-subtle text-fg-secondary', icon: IconPlugOff },
  error: { labelKey: 'statusBadge.error', className: 'bg-danger-soft text-danger', icon: IconCircleX },
  failed: { labelKey: 'statusBadge.failed', className: 'bg-danger-soft text-danger', icon: IconCircleX },
  needs_auth: { labelKey: 'statusBadge.needsAuth', className: 'bg-warning-soft text-warning', icon: IconAlertTriangle },
  disabled: { labelKey: 'statusBadge.disabled', className: 'bg-disabled text-fg-disabled', icon: IconPlugOff },
};

const API_STATUS_KEYS: Record<string, Omit<BadgeConfig, 'labelKey'> & { labelKey: string }> = {
  connected: { labelKey: 'apiStatusBadge.connected', className: 'bg-success-soft text-success', icon: IconCircleCheck },
  testing: { labelKey: 'apiStatusBadge.testing', className: 'bg-info-soft text-info', icon: IconLoader2, pending: true },
  error: { labelKey: 'apiStatusBadge.error', className: 'bg-danger-soft text-danger', icon: IconCircleX },
  disabled: { labelKey: 'apiStatusBadge.disabled', className: 'bg-disabled text-fg-disabled', icon: IconPlugOff },
  not_configured: { labelKey: 'apiStatusBadge.notConfigured', className: 'bg-warning-soft text-warning', icon: IconAlertTriangle },
  unknown: { labelKey: 'apiStatusBadge.unknown', className: 'bg-subtle text-fg-secondary', icon: IconPlugOff },
};

export function StatusBadge({ status, variant = 'mcp' }: { status: string; variant?: 'mcp' | 'api' }) {
  const { t } = useTranslation('tool');
  const configs = variant === 'api' ? API_STATUS_KEYS : MCP_STATUS_KEYS;
  const fallback = variant === 'api' ? API_STATUS_KEYS.unknown : MCP_STATUS_KEYS.disconnected;
  const c = configs[status] || fallback;
  const Icon = c.icon;

  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-pill text-xs font-medium ${c.className}`}>
      <Icon aria-hidden="true" className={`mr-1.5 h-4 w-4 ${c.pending ? 'animate-spin' : ''}`} />
      {t(c.labelKey)}
    </span>
  );
}

export function EnabledBadge({ enabled }: { enabled: boolean }) {
  const { t } = useTranslation('tool');
  return enabled ? (
    <span className="inline-flex items-center px-2.5 py-0.5 rounded-pill text-xs font-medium bg-success-soft text-success">
      <IconCircleCheck aria-hidden="true" className="w-4 h-4 mr-1" />{t('enabledBadge.enabled')}
    </span>
  ) : (
    <span className="inline-flex items-center px-2.5 py-0.5 rounded-pill text-xs font-medium bg-subtle text-fg-secondary">
      <IconCircleX aria-hidden="true" className="w-4 h-4 mr-1" />{t('enabledBadge.disabled')}
    </span>
  );
}
