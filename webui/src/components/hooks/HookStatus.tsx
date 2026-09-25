/**
 * Hook Status Component
 * 
 * Displays hook system status and statistics
 */

import { useTranslation } from 'react-i18next';
import { useHooks } from '@/hooks/useHooks';
import { IconRocket, IconCircleCheck, IconCircleX, IconActivity } from '@tabler/icons-react';
import LoadingSpinner from '@/components/common/LoadingSpinner';

export default function HookStatus() {
  const { t } = useTranslation('common');
  const { status, loading, error } = useHooks();

  if (loading) {
    return (
      <div className="bg-surface rounded-lg shadow p-6">
        <div className="flex items-center justify-center py-8">
          <LoadingSpinner />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-surface rounded-lg shadow p-6">
        <div className="flex items-center gap-2 mb-4">
          <IconRocket aria-hidden="true" className="w-5 h-5" />
          <h2 className="text-title-card font-semibold">{t('hookStatus.title')}</h2>
        </div>
        <div className="bg-danger-soft border border-danger rounded-lg p-4">
          <p className="text-danger">{error}</p>
        </div>
      </div>
    );
  }

  if (!status) {
    return null;
  }

  return (
    <div className="bg-surface rounded-lg shadow">
      {/* Header */}
      <div className="px-6 py-4 border-b border-line">
        <div className="flex items-center gap-2">
          <IconRocket aria-hidden="true" className="w-5 h-5 text-danger" />
          <h2 className="text-title-card font-semibold">{t('hookStatus.title')}</h2>
        </div>
      </div>

      <div className="p-6 space-y-6">
        {/* Overall Status */}
        <div>
          <h3 className="text-sm font-medium text-fg-secondary mb-3">{t('hookStatus.systemStatus')}</h3>
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-canvas rounded-lg p-4">
              <div className="flex items-center gap-2 mb-1">
                {status.enabled ? (
                  <IconCircleCheck aria-hidden="true" className="w-4 h-4 text-success" />
                ) : (
                  <IconCircleX aria-hidden="true" className="w-4 h-4 text-fg-muted" />
                )}
                <span className="text-sm text-fg-secondary">{t('hookStatus.statusLabel')}</span>
              </div>
              <p className="text-title-card font-semibold">
                {status.enabled ? t('hookStatus.enabled') : t('hookStatus.disabled')}
              </p>
            </div>
            
            <div className="bg-canvas rounded-lg p-4">
              <div className="flex items-center gap-2 mb-1">
                <IconActivity aria-hidden="true" className="w-4 h-4 text-danger" />
                <span className="text-sm text-fg-secondary">{t('hookStatus.totalHandlers')}</span>
              </div>
              <p className="text-title-card font-semibold">{status.stats.total_handlers}</p>
            </div>
            
            <div className="bg-canvas rounded-lg p-4">
              <div className="flex items-center gap-2 mb-1">
                <IconActivity aria-hidden="true" className="w-4 h-4 text-ai" />
                <span className="text-sm text-fg-secondary">{t('hookStatus.eventKeys')}</span>
              </div>
              <p className="text-title-card font-semibold">{status.stats.total_event_keys}</p>
            </div>
          </div>
        </div>

        {/* Registered Hooks */}
        {status.stats.total_handlers > 0 && (
          <div>
            <h3 className="text-sm font-medium text-fg-secondary mb-3">{t('hookStatus.registeredHooks')}</h3>
            <div className="border border-line rounded-lg divide-y divide-line">
              {Object.entries(status.stats.event_keys).map(([key, info]) => (
                <div key={key} className="p-3">
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-2 bg-action rounded-full animate-pulse" />
                      <span className="font-medium text-sm">{key}</span>
                    </div>
                    <span className="text-xs text-fg-muted">
                      {info.handler_count} {t('hookStatus.handlers')}
                    </span>
                  </div>
                  <div className="ml-4 text-xs text-fg-secondary">
                    {info.handlers.join(', ')}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
