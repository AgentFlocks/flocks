import { createPortal } from 'react-dom';
import { useId, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  IconBell,
  IconCircleCheck,
  IconExternalLink,
  IconGift,
  IconLoader2,
  IconSparkles,
  IconX,
  IconBellOff,
} from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import type { UserNotification } from '@/api/notifications';
import { useDialogFocus } from '@/hooks/useDialogFocus';

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
      icon: IconGift,
      ring: 'border-success',
      header: 'bg-success-soft',
      iconBg: 'bg-success-soft text-success',
      title: 'text-success',
      text: 'text-success',
    };
  }

  if (kind === 'whats_new') {
    return {
      icon: IconSparkles,
      ring: 'border-warning',
      header: 'bg-warning-soft',
      iconBg: 'bg-warning-soft text-warning',
      title: 'text-warning',
      text: 'text-warning',
    };
  }

  return {
    icon: IconBell,
    ring: 'border-info',
    header: 'bg-info-soft',
    iconBg: 'bg-info-soft text-info',
    title: 'text-info',
    text: 'text-info',
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
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const primaryNotification = notifications.find((item) => item.kind === 'benefit') ?? notifications[0];
  const accent = getAccent(primaryNotification?.kind);
  const Icon = accent.icon;
  const isBusy = acknowledgingIds.length > 0;

  useDialogFocus(dialogRef, notifications.length > 0, isBusy ? undefined : onClose);

  const handleAction = (notification: UserNotification) => {
    const url = notification.primary_action?.url ?? notification.secondary_action?.url;
    if (url) {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
    onAcknowledge(notification);
  };

  if (!primaryNotification) return null;

  return createPortal(
    <>
      <div className="fixed inset-0 z-[90] bg-overlay-backdrop" onClick={() => { if (!isBusy) onClose(); }} aria-hidden="true" />
      <div className="fixed inset-0 z-[100] flex items-center justify-center pointer-events-none">
        <div
          ref={dialogRef}
          tabIndex={-1}
          data-ui-overlay="dialog"
          className={`pointer-events-auto flex max-h-[calc(100dvh-2rem)] w-full max-w-modal mx-4 flex-col overflow-hidden rounded-overlay border ${accent.ring} bg-surface shadow-overlay`}
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
        >
          <div className={`flex shrink-0 items-start gap-3 ${accent.header} px-4 sm:px-6 py-4`}>
            <span className={`mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-control ${accent.iconBg}`}>
              <Icon aria-hidden="true" className="h-5 w-5" />
            </span>
            <div className="min-w-0 flex-1">
              <h2 id={titleId} className={`text-title-section ${accent.title}`}>{t('title')}</h2>
              <p className={`mt-1 text-sm ${accent.text}`}>{t('subtitle')}</p>
            </div>
            <button
              onClick={() => onClose()}
              disabled={isBusy}
              className="ui-btn ui-btn-ghost ui-btn-icon shrink-0 text-fg-muted"
              aria-label={t('close')}
            >
              <IconX aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 sm:px-6 py-4">
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
                    className={`rounded-card border ${sectionAccent.ring} bg-surface p-4 ${index > 0 ? 'mt-4' : ''}`}
                  >
                    <div className="flex items-start gap-3">
                      <span className={`mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-control ${sectionAccent.iconBg}`}>
                        <SectionIcon aria-hidden="true" className="h-4 w-4" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className={`text-sm font-semibold ${sectionAccent.title}`}>{notification.title}</div>
                        {notification.summary && (
                          <p className={`mt-1 text-xs ${sectionAccent.text}`}>{notification.summary}</p>
                        )}
                      </div>
                    </div>

                    {notification.body && (
                      notification.kind === 'whats_new' ? (
                        <div className="prose prose-sm mt-3 max-w-none break-words text-fg-secondary prose-headings:my-3 prose-headings:text-fg prose-p:my-2 prose-ul:my-2 prose-ol:my-2 prose-li:my-1 prose-a:break-all prose-a:text-fg-link prose-pre:overflow-x-auto">
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                              a: ({ children, ...props }) => (
                                <a {...props} target="_blank" rel="noopener noreferrer">{children}</a>
                              ),
                            }}
                          >
                            {notification.body}
                          </ReactMarkdown>
                        </div>
                      ) : (
                        <p className="mt-3 whitespace-pre-wrap text-sm text-fg-secondary">{notification.body}</p>
                      )
                    )}

                    {notification.highlights.length > 0 && (
                      <div className="mt-3 space-y-2">
                        {notification.highlights.map((highlight) => (
                          <div key={highlight} className="flex items-start gap-2 text-sm text-fg-secondary">
                            <IconCircleCheck aria-hidden="true" className="mt-0.5 h-4 w-4 flex-shrink-0 text-success" />
                            <span>{highlight}</span>
                          </div>
                        ))}
                      </div>
                    )}

                    {action?.url && (
                      <div className="mt-3 flex justify-end border-t border-line pt-3">
                        <button
                          onClick={() => handleAction(notification)}
                          disabled={isBusy}
                          className="ui-btn"
                        >
                          {action.label}
                          <IconExternalLink aria-hidden="true" className="h-4 w-4" />
                        </button>
                      </div>
                    )}
                  </section>
                );
              })}
            </div>
          </div>

          <div className="flex shrink-0 flex-wrap items-center gap-2 border-t border-line px-4 sm:px-6 py-4">
            <button
              onClick={onDismissForever}
              disabled={isBusy}
              className="ui-btn ui-btn-ghost text-fg-muted"
              title={t('dismissThis')}
            >
              {isBusy ? (
                <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
              ) : (
                <IconBellOff aria-hidden="true" className="h-4 w-4" />
              )}
              {t('dismissThis')}
            </button>

            <button
              onClick={() => onAcknowledge()}
              disabled={isBusy}
              className="ui-btn ui-btn-primary ml-auto"
            >
              {isBusy && <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
              {t('gotIt')}
            </button>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}
