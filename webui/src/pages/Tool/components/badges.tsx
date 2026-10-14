import { useTranslation } from 'react-i18next';
import { CheckCircle, XCircle } from 'lucide-react';

interface BadgeConfig {
  labelKey: string;
  className: string;
  dot: string;
}

const MCP_STATUS_KEYS: Record<string, Omit<BadgeConfig, 'labelKey'> & { labelKey: string }> = {
  connected: { labelKey: 'statusBadge.connected', className: 'bg-green-100 text-green-800', dot: 'bg-green-500' },
  connecting: { labelKey: 'statusBadge.connecting', className: 'bg-yellow-100 text-yellow-800', dot: 'bg-yellow-500 animate-pulse' },
  disconnected: { labelKey: 'statusBadge.disconnected', className: 'bg-gray-100 text-gray-600', dot: 'bg-gray-400' },
  error: { labelKey: 'statusBadge.error', className: 'bg-red-100 text-red-800', dot: 'bg-red-500' },
  failed: { labelKey: 'statusBadge.failed', className: 'bg-red-100 text-red-800', dot: 'bg-red-500' },
  needs_auth: { labelKey: 'statusBadge.needsAuth', className: 'bg-amber-100 text-amber-800', dot: 'bg-amber-500' },
  disabled: { labelKey: 'statusBadge.disabled', className: 'bg-gray-100 text-gray-500', dot: 'bg-gray-300' },
};

const API_STATUS_KEYS: Record<string, Omit<BadgeConfig, 'labelKey'> & { labelKey: string }> = {
  connected: { labelKey: 'apiStatusBadge.connected', className: 'bg-green-100 text-green-800', dot: 'bg-green-500' },
  testing: { labelKey: 'apiStatusBadge.testing', className: 'bg-red-100 text-red-800', dot: 'bg-red-500 animate-pulse' },
  error: { labelKey: 'apiStatusBadge.error', className: 'bg-red-100 text-red-800', dot: 'bg-red-500' },
  disabled: { labelKey: 'apiStatusBadge.disabled', className: 'bg-gray-100 text-gray-500', dot: 'bg-gray-300' },
  not_configured: { labelKey: 'apiStatusBadge.notConfigured', className: 'bg-amber-100 text-amber-800', dot: 'bg-amber-500' },
  unknown: { labelKey: 'apiStatusBadge.unknown', className: 'bg-gray-100 text-gray-600', dot: 'bg-gray-400' },
};

export function StatusBadge({ status, variant = 'mcp' }: { status: string; variant?: 'mcp' | 'api' }) {
  const { t } = useTranslation('tool');
  const configs = variant === 'api' ? API_STATUS_KEYS : MCP_STATUS_KEYS;
  const fallback = variant === 'api' ? API_STATUS_KEYS.unknown : MCP_STATUS_KEYS.disconnected;
  const c = configs[status] || fallback;

  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${c.className}`}>
      <span className={`w-1.5 h-1.5 rounded-full mr-1.5 ${c.dot}`} />
      {t(c.labelKey)}
    </span>
  );
}

export function EnabledBadge({ enabled }: { enabled: boolean }) {
  const { t } = useTranslation('tool');
  return enabled ? (
    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
      <CheckCircle className="w-3 h-3 mr-1" />{t('enabledBadge.enabled')}
    </span>
  ) : (
    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600">
      <XCircle className="w-3 h-3 mr-1" />{t('enabledBadge.disabled')}
    </span>
  );
}
