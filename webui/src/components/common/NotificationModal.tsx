import { createPortal } from 'react-dom';
import { useEffect } from 'react';
import {
  Bell,
  CheckCircle,
  ExternalLink,
  Gift,
  Loader2,
  Sparkles,
  X,
  BellOff,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { UserNotification } from '@/api/notifications';

interface NotificationModalProps {
  notifications: UserNotification[];
  acknowledgingIds?: string[];
  onAcknowledge: (notification?: UserNotification) => void;
  onClose: () => void;
  onDismissForever: () => void;
}

const getAccent = (kind: UserNotification['kind']) => {
  if (kind === 'benefit') {
    return {
      icon: Gift,
      ring: 'border-emerald-200',
      header: 'from-emerald-50 via-teal-50 to-cyan-50',
      iconBg: 'bg-emerald-500',
      title: 'text-emerald-950',
      text: 'text-emerald-800',
      button: 'bg-emerald-600 hover:bg-emerald-700',
    };
  }

  if (kind === 'whats_new') {
    return {
      icon: Sparkles,
      ring: 'border-amber-200',
      header: 'from-amber-50 via-orange-50 to-rose-50',
      iconBg: 'bg-amber-500',
      title: 'text-amber-950',
      text: 'text-amber-800',
      button: 'bg-amber-500 hover:bg-amber-600',
    };
  }

  return {
    icon: Bell,
    ring: 'border-blue-200',
    header: 'from-blue-50 via-sky-50 to-indigo-50',
    iconBg: 'bg-blue-500',
    title: 'text-blue-950',
    text: 'text-blue-800',
    button: 'bg-blue-600 hover:bg-blue-700',
  };
};

export default function NotificationModal({
  notifications,
  acknowledgingIds = [],
  onAcknowledge,
  onClose,
  onDismissForever,
}: NotificationModalProps) {
  const { t } = useTranslation('notification');
  const primaryNotification = notifications.find((item) => item.kind === 'benefit') ?? notifications[0];
  const accent = getAccent(primaryNotification.kind);
  const Icon = accent.icon;
  const isBusy = acknowledgingIds.length > 0;

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !isBusy) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isBusy, onClose]);

  const handleAction = (notification: UserNotification) => {
    const url = notification.primary_action?.url ?? notification.secondary_action?.url;
    if (url) {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
    onAcknowledge(notification);
  };

  return createPortal(
    <>
      <div className="fixed inset-0 z-[90] bg-black/30" onClick={onClose} />
      <div className="fixed inset-0 z-[100] flex items-center justify-center pointer-events-none">
        <div
          className={`pointer-events-auto w-full max-w-lg mx-4 overflow-hidden rounded-2xl border ${accent.ring} bg-white shadow-2xl`}
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-labelledby="notification-modal-title"
        >
          <div className={`flex items-start gap-3 bg-gradient-to-r ${accent.header} px-5 py-4`}>
            <span className={`mt-0.5 flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full ${accent.iconBg} text-white shadow-sm`}>
              <Icon className="h-5 w-5" />
            </span>
            <div className="min-w-0 flex-1">
              <div id="notification-modal-title" className={`text-base font-semibold ${accent.title}`}>{t('title')}</div>
              <p className={`mt-1 text-sm leading-6 ${accent.text}`}>{t('subtitle')}</p>
            </div>
            <button
              onClick={onClose}
              disabled={isBusy}
              className="rounded p-1 text-gray-400 transition-colors hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-50"
              aria-label={t('close')}
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="max-h-[70vh] overflow-y-auto px-5 py-4">
            <div className="space-y-4">
              {notifications.map((notification, index) => {
                const sectionAccent = getAccent(notification.kind);
                const SectionIcon = sectionAccent.icon;
                const primary = notification.primary_action;
                const secondary = notification.secondary_action;
                const action = primary ?? secondary;

                return (
                  <section
                    key={notification.id}
                    className={`rounded-2xl border ${sectionAccent.ring} bg-white p-4 ${index > 0 ? 'mt-4' : ''}`}
                  >
                    <div className="flex items-start gap-3">
                      <span className={`mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full ${sectionAccent.iconBg} text-white shadow-sm`}>
                        <SectionIcon className="h-4 w-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className={`text-sm font-semibold ${sectionAccent.title}`}>{notification.title}</div>
                        {notification.summary && (
                          <p className={`mt-1 text-xs leading-5 ${sectionAccent.text}`}>{notification.summary}</p>
                        )}
                      </div>
                    </div>

                    {notification.body && (
                      <p className="mt-3 text-sm leading-6 text-gray-600">{notification.body}</p>
                    )}

                    {notification.highlights.length > 0 && (
                      <div className="mt-3 space-y-2">
                        {notification.highlights.map((highlight) => (
                          <div key={highlight} className="flex items-start gap-2 text-sm text-gray-700">
                            <CheckCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-emerald-500" />
                            <span className="leading-5">{highlight}</span>
                          </div>
                        ))}
                      </div>
                    )}

                    {action?.url && (
                      <div className="mt-3 flex justify-end border-t border-gray-100 pt-3">
                        <button
                          onClick={() => handleAction(notification)}
                          disabled={isBusy}
                          className="flex items-center gap-1.5 rounded-lg bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-200 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {action.label}
                          <ExternalLink className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    )}
                  </section>
                );
              })}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 border-t border-gray-100 px-5 py-4">
            <button
              onClick={onDismissForever}
              disabled={isBusy}
              className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-50"
              title={t('dismissThis')}
            >
              {isBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <BellOff className="h-3.5 w-3.5" />
              )}
              {t('dismissThis')}
            </button>

            <button
              onClick={() => onAcknowledge()}
              disabled={isBusy}
              className={`ml-auto flex items-center gap-1.5 rounded-lg px-4 py-2 text-xs font-semibold text-white shadow-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${accent.button}`}
            >
              {isBusy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {t('gotIt')}
            </button>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}
