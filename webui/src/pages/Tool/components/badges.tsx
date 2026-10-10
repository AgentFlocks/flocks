import { useTranslation } from 'react-i18next';
import {
  AlertTriangle,
  CheckCircle,
  Loader2,
  PowerOff,
  XCircle,
  type LucideIcon,
} from 'lucide-react';

interface BadgeConfig {
  labelKey: string;
  className: string;
  icon: LucideIcon;
  pending?: boolean;
}

const MCP_STATUS_KEYS: Record<string, Omit<BadgeConfig, 'labelKey'> & { labelKey: string }> = {
  connected: { labelKey: 'statusBadge.connected', className: 'bg-green-100 text-green-800', icon: CheckCircle },
  connecting: { labelKey: 'statusBadge.connecting', className: 'bg-yellow-100 text-yellow-800', icon: Loader2, pending: true },
  disconnected: { labelKey: 'statusBadge.disconnected', className: 'bg-gray-100 text-gray-600', icon: PowerOff },
  error: { labelKey: 'statusBadge.error', className: 'bg-red-100 text-red-800', icon: XCircle },
  failed: { labelKey: 'statusBadge.failed', className: 'bg-red-100 text-red-800', icon: XCircle },
  needs_auth: { labelKey: 'statusBadge.needsAuth', className: 'bg-amber-100 text-amber-800', icon: AlertTriangle },
  disabled: { labelKey: 'statusBadge.disabled', className: 'bg-gray-100 text-gray-500', icon: PowerOff },
};

const API_STATUS_KEYS: Record<string, Omit<BadgeConfig, 'labelKey'> & { labelKey: string }> = {
  connected: { labelKey: 'apiStatusBadge.connected', className: 'bg-green-100 text-green-800', icon: CheckCircle },
  testing: { labelKey: 'apiStatusBadge.testing', className: 'bg-red-100 text-red-800', icon: Loader2, pending: true },
  error: { labelKey: 'apiStatusBadge.error', className: 'bg-red-100 text-red-800', icon: XCircle },
  disabled: { labelKey: 'apiStatusBadge.disabled', className: 'bg-gray-100 text-gray-500', icon: PowerOff },
  not_configured: { labelKey: 'apiStatusBadge.notConfigured', className: 'bg-amber-100 text-amber-800', icon: AlertTriangle },
  unknown: { labelKey: 'apiStatusBadge.unknown', className: 'bg-gray-100 text-gray-600', icon: PowerOff },
};

export function StatusBadge({ status, variant = 'mcp' }: { status: string; variant?: 'mcp' | 'api' }) {
  const { t } = useTranslation('tool');
  const configs = variant === 'api' ? API_STATUS_KEYS : MCP_STATUS_KEYS;
  const fallback = variant === 'api' ? API_STATUS_KEYS.unknown : MCP_STATUS_KEYS.disconnected;
  const c = configs[status] || fallback;
  const Icon = c.icon;

  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${c.className}`}>
      <Icon aria-hidden="true" className={`mr-1.5 h-4 w-4 ${c.pending ? 'animate-spin' : ''}`} />
      {t(c.labelKey)}
    </span>
  );
}

export function EnabledBadge({ enabled }: { enabled: boolean }) {
  const { t } = useTranslation('tool');
  return enabled ? (
    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
      <CheckCircle aria-hidden="true" className="w-3 h-3 mr-1" />{t('enabledBadge.enabled')}
    </span>
  ) : (
    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
      <XCircle aria-hidden="true" className="w-3 h-3 mr-1" />{t('enabledBadge.disabled')}
    </span>
  );
}
