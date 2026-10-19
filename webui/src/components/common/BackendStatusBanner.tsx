import { useEffect, useSyncExternalStore } from 'react';
import { AlertCircle, RefreshCw, CheckCircle, WifiOff } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useBackendStatus } from '@/hooks/useBackendStatus';

// The layout mounts an inline banner under its fixed header; while it is mounted the
// window-level banner (App) stays out of the way instead of fighting the header for the top.
let mountedInlineBanners = 0;
const inlineBannerListeners = new Set<() => void>();

function emitInlineBannerChange() {
  inlineBannerListeners.forEach((listener) => listener());
}

function subscribeInlineBanner(listener: () => void) {
  inlineBannerListeners.add(listener);
  return () => {
    inlineBannerListeners.delete(listener);
  };
}

interface BackendStatusBannerProps {
  /** Render in the page flow (under the layout's header) instead of fixed to the window top. */
  inline?: boolean;
}

export function BackendStatusBanner({ inline = false }: BackendStatusBannerProps) {
  const { t } = useTranslation('common');
  const { status, message, checked, checkHealth } = useBackendStatus();
  const inlineBannerMounted = useSyncExternalStore(
    subscribeInlineBanner,
    () => mountedInlineBanners > 0,
    () => false,
  );

  useEffect(() => {
    if (!inline) return;
    mountedInlineBanners += 1;
    emitInlineBannerChange();
    return () => {
      mountedInlineBanners = Math.max(0, mountedInlineBanners - 1);
      emitInlineBannerChange();
    };
  }, [inline]);

  // Before the first check answers, status is only the initial 'connecting'.
  if (!checked || status === 'connected' || (!inline && inlineBannerMounted)) {
    return null;
  }

  // Opaque in dark mode too: the window-level banner overlays the page, which must not show through.
  // The dark colours are amber-500/15 and red-500/15 laid over zinc-900.
  const getBannerStyle = () => {
    switch (status) {
      case 'connecting':
        return 'bg-yellow-50 border-yellow-200 text-yellow-800 dark:border-amber-500/35 dark:bg-[#392c19] dark:text-amber-100';
      case 'disconnected':
        return 'bg-red-50 border-red-200 text-red-800 dark:border-red-400/30 dark:bg-[#381f21] dark:text-red-100';
      case 'error':
        return 'bg-red-50 border-red-200 text-red-800 dark:border-red-400/30 dark:bg-[#381f21] dark:text-red-100';
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

  // Inline it pushes the page down rather than covering it, so the header, its menus,
  // drawers and previews keep their own stacking.
  const placement = inline ? 'relative flex-shrink-0' : 'fixed top-0 left-0 right-0 z-50';

  return (
    <div data-backend-status-banner={inline ? 'inline' : 'window'} className={`${placement} border-b ${getBannerStyle()} transition-all duration-300`}>
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
