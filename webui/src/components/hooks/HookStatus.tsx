/**
 * Hook Status Component
 * 
 * Displays hook system status and statistics
 */

import { useTranslation } from 'react-i18next';
import { useHooks } from '@/hooks/useHooks';
import { Rocket, CheckCircle, XCircle, Info, Activity } from 'lucide-react';
import LoadingSpinner from '@/components/common/LoadingSpinner';

export default function HookStatus() {
  const { t } = useTranslation('common');
  const { status, loading, error } = useHooks();

  if (loading) {
    return (
      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex items-center justify-center py-8">
          <LoadingSpinner />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex items-center gap-2 mb-4">
          <Rocket className="w-5 h-5" />
          <h2 className="text-lg font-semibold">{t('hookStatus.title')}</h2>
        </div>
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-red-700">{error}</p>
        </div>
      </div>
    );
  }

  if (!status) {
    return null;
  }

  return (
    <div className="bg-white rounded-lg shadow">
      {/* Header */}
      <div className="px-6 py-4 border-b border-gray-200">
        <div className="flex items-center gap-2">
          <Rocket className="w-5 h-5 text-red-600" />
          <h2 className="text-lg font-semibold">{t('hookStatus.title')}</h2>
        </div>
      </div>

      <div className="p-6 space-y-6">
        {/* Overall Status */}
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-3">{t('hookStatus.systemStatus')}</h3>
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-gray-50 rounded-lg p-4">
              <div className="flex items-center gap-2 mb-1">
                {status.enabled ? (
                  <CheckCircle className="w-4 h-4 text-green-600" />
                ) : (
                  <XCircle className="w-4 h-4 text-gray-400" />
                )}
                <span className="text-sm text-gray-600">{t('hookStatus.statusLabel')}</span>
              </div>
              <p className="text-lg font-semibold">
                {status.enabled ? t('hookStatus.enabled') : t('hookStatus.disabled')}
              </p>
            </div>
            
            <div className="bg-gray-50 rounded-lg p-4">
              <div className="flex items-center gap-2 mb-1">
                <Activity className="w-4 h-4 text-red-600" />
                <span className="text-sm text-gray-600">{t('hookStatus.totalHandlers')}</span>
              </div>
              <p className="text-lg font-semibold">{status.stats.total_handlers}</p>
            </div>
            
            <div className="bg-gray-50 rounded-lg p-4">
              <div className="flex items-center gap-2 mb-1">
                <Activity className="w-4 h-4 text-purple-600" />
                <span className="text-sm text-gray-600">{t('hookStatus.eventKeys')}</span>
              </div>
              <p className="text-lg font-semibold">{status.stats.total_event_keys}</p>
            </div>
          </div>
        </div>

        {/* Session Memory Hook */}
        <div className="border border-gray-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold text-gray-800">Session Memory Hook</h3>
            <span className={`px-2 py-1 text-xs rounded-full ${
              status.session_memory.enabled 
                ? 'bg-green-100 text-green-700' 
                : 'bg-gray-100 text-gray-600'
            }`}>
              {status.session_memory.enabled ? t('hookStatus.enabled') : t('hookStatus.disabled')}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-4 mb-4">
            <div>
              <label className="text-xs text-gray-500">{t('hookStatus.autoSaveLabel')}</label>
              <div className="flex items-center gap-2 mt-1">
                {status.session_memory.enabled ? (
                  <CheckCircle className="w-4 h-4 text-green-600" />
                ) : (
                  <XCircle className="w-4 h-4 text-gray-400" />
                )}
                <span className="text-sm">{status.session_memory.enabled ? t('hookStatus.yes') : t('hookStatus.no')}</span>
              </div>
            </div>
            
            <div>
              <label className="text-xs text-gray-500">{t('hookStatus.messageCount')}</label>
              <p className="text-sm mt-1">{status.session_memory.message_count} {t('hookStatus.messageCountLabel')}</p>
            </div>
            
            <div>
              <label className="text-xs text-gray-500">{t('hookStatus.useLlmSlugLabel')}</label>
              <p className="text-sm mt-1">{status.session_memory.use_llm_slug ? t('hookStatus.yes') : t('hookStatus.no')}</p>
            </div>
            
            <div>
              <label className="text-xs text-gray-500">{t('hookStatus.slugTimeout')}</label>
              <p className="text-sm mt-1">{status.session_memory.slug_timeout} {t('hookStatus.slugTimeoutLabel')}</p>
            </div>
          </div>

          {status.session_memory.enabled && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 flex gap-2">
              <Info className="w-4 h-4 text-red-600 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-red-800">
                <p className="font-medium">{t('hookStatus.autoSaveEnabled')}</p>
                <p className="text-xs text-red-700 mt-1">
                  {t('hookStatus.autoSaveDesc')}
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Registered Hooks */}
        {status.stats.total_handlers > 0 && (
          <div>
            <h3 className="text-sm font-medium text-gray-700 mb-3">{t('hookStatus.registeredHooks')}</h3>
            <div className="border border-gray-200 rounded-lg divide-y divide-gray-200">
              {Object.entries(status.stats.event_keys).map(([key, info]) => (
                <div key={key} className="p-3">
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <div className="w-2 h-2 bg-red-500 rounded-full animate-pulse" />
                      <span className="font-medium text-sm">{key}</span>
                    </div>
                    <span className="text-xs text-gray-500">
                      {info.handler_count} {t('hookStatus.handlers')}
                    </span>
                  </div>
                  <div className="ml-4 text-xs text-gray-600">
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
