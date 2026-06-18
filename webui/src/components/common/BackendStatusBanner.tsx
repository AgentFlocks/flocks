import { AlertCircle, RefreshCw, CheckCircle, WifiOff } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useBackendStatus } from '@/hooks/useBackendStatus';

export function BackendStatusBanner() {
  const { t } = useTranslation('common');
  const { status, message, checkHealth } = useBackendStatus();

  if (status === 'connected') {
    return null;
  }

  const getBannerStyle = () => {
    switch (status) {
      case 'connecting':
        return 'bg-yellow-50 border-yellow-200 text-yellow-800 dark:border-amber-500/35 dark:bg-amber-500/15 dark:text-amber-100';
      case 'disconnected':
        return 'bg-red-50 border-red-200 text-red-800 dark:border-red-400/30 dark:bg-red-500/15 dark:text-red-100';
      case 'error':
        return 'bg-red-50 border-red-200 text-red-800 dark:border-red-400/30 dark:bg-red-500/15 dark:text-red-100';
      default:
        return 'bg-gray-50 border-gray-200 text-gray-800 dark:border-[#4a5563] dark:bg-[#303842] dark:text-[#d7dee8]';
    }
  };

  const getIcon = () => {
    switch (status) {
      case 'connecting':
        return <RefreshCw className="w-5 h-5 animate-spin" />;
      case 'disconnected':
        return <WifiOff className="w-5 h-5" />;
      case 'error':
        return <AlertCircle className="w-5 h-5" />;
      default:
        return <CheckCircle className="w-5 h-5" />;
    }
  };

  const getMessage = () => {
    switch (status) {
      case 'connecting':
        return t('backend.connecting');
      case 'disconnected':
        return t('backend.disconnected');
      case 'error':
        return message || t('backend.error');
      default:
        return message || t('backend.checking');
    }
  };

  return (
    <div className={`fixed top-0 left-0 right-0 lg:left-64 z-50 border-b ${getBannerStyle()} transition-all duration-300`}>
      <div className="px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-3">
            {getIcon()}
            <div>
              <div className="font-medium">{getMessage()}</div>
              {status === 'connecting' && (
                <div className="text-sm opacity-75 mt-1">
                  {t('backend.restarting')}
                </div>
              )}
              {status === 'disconnected' && (
                <div className="text-sm opacity-75 mt-1">
                  {t('backend.runningHint')}
                </div>
              )}
            </div>
          </div>
          
          <button
            onClick={checkHealth}
            className="px-4 py-2 bg-white/50 hover:bg-white/80 rounded-lg text-sm font-medium transition-colors duration-200 flex items-center space-x-2 dark:bg-[#46515e]/70 dark:hover:bg-[#5a6573]"
          >
            <RefreshCw className="w-4 h-4" />
            <span>{t('backend.retry')}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
