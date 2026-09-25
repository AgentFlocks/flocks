import { IconAlertCircle, IconRefresh, IconCircleCheck, IconWifiOff } from '@tabler/icons-react';
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
        return 'bg-warning-soft border-warning text-warning dark:border-warning dark:bg-warning-soft dark:text-warning';
      case 'disconnected':
        return 'bg-danger-soft border-danger text-danger dark:border-danger dark:bg-danger-soft dark:text-danger';
      case 'error':
        return 'bg-danger-soft border-danger text-danger dark:border-danger dark:bg-danger-soft dark:text-danger';
      default:
        return 'bg-canvas border-line text-fg dark:border-line dark:bg-surface dark:text-fg';
    }
  };

  const getIcon = () => {
    switch (status) {
      case 'connecting':
        return <IconRefresh aria-hidden="true" className="w-5 h-5 animate-spin" />;
      case 'disconnected':
        return <IconWifiOff aria-hidden="true" className="w-5 h-5" />;
      case 'error':
        return <IconAlertCircle aria-hidden="true" className="w-5 h-5" />;
      default:
        return <IconCircleCheck aria-hidden="true" className="w-5 h-5" />;
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
            className="px-4 py-2 bg-surface hover:bg-hover rounded-lg text-sm font-medium transition-colors duration-200 flex items-center space-x-2 dark:bg-subtle dark:hover:bg-hover"
          >
            <IconRefresh aria-hidden="true" className="w-4 h-4" />
            <span>{t('backend.retry')}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
