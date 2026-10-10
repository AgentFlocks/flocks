import { createPortal } from 'react-dom';
import { useId, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Bell,
  BellOff,
  CheckCircle,
  ExternalLink,
  Gift,
  Loader2,
  Sparkles,
  X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { NotificationQRCode, UserNotification } from '@/api/notifications';
import { useDialogFocus } from '@/hooks/useDialogFocus';

interface NotificationModalProps {
  notifications: UserNotification[];
  acknowledgingIds?: string[];
  onAcknowledge: (notification?: UserNotification) => void;
  onClose: () => void;
  onDismissForever: () => void;
}

const getAccent = (kind: UserNotification['kind'], hasQRCode = false) => {
  if (kind === 'benefit' && !hasQRCode) {
    return {
      icon: Gift,
      ring: 'border-emerald-200',
      header: 'from-emerald-50 via-teal-50 to-cyan-50 dark:from-emerald-950/40 dark:via-teal-950/30 dark:to-cyan-950/30',
      iconBg: 'bg-emerald-500',
      title: 'text-emerald-950 dark:text-emerald-100',
      text: 'text-emerald-800',
      button: 'bg-emerald-600 hover:bg-emerald-700',
    };
  }

  if (kind === 'whats_new' || hasQRCode) {
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
    header: 'from-blue-50 via-sky-50 to-indigo-50 dark:from-blue-950/40 dark:via-sky-950/30 dark:to-indigo-950/30',
    iconBg: 'bg-blue-500',
    title: 'text-blue-950 dark:text-blue-100',
    text: 'text-blue-800',
    button: 'bg-blue-600 hover:bg-blue-700',
  };
};

function NoticeQRCode({ code }: { code: NotificationQRCode }) {
  const { t } = useTranslation('notification');
  const [failed, setFailed] = useState(false);
  return (
    <figure className="mx-auto w-[136px] max-w-full text-center sm:mx-0">
      {failed ? (
        <div role="status" className="flex items-center rounded-lg border border-amber-200 bg-white p-2 text-xs leading-5 text-gray-600 min-h-32">
          {t('qrUnavailable')}
        </div>
      ) : (
        <img
          src={code.src}
          alt={code.alt}
          width={128}
          height={128}
          onError={() => setFailed(true)}
          className="block h-32 w-32 rounded-lg bg-white object-contain mx-auto max-w-full"
        />
      )}
      {code.caption && <figcaption className="mt-2 text-sm font-semibold leading-6 text-amber-800">{code.caption}</figcaption>}
    </figure>
  );
}

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
  const accent = getAccent(primaryNotification?.kind, !!primaryNotification?.qr_code);
  const Icon = primaryNotification?.qr_code ? Sparkles : accent.icon;
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
      <div className="fixed inset-0 bg-black/30 z-[90]" onClick={() => { if (!isBusy) onClose(); }} aria-hidden="true" />
      <div className="fixed inset-0 flex items-center justify-center p-4 pointer-events-none z-[100]">
        <div
          ref={dialogRef}
          tabIndex={-1}
          data-ui-overlay="dialog"
          className={`pointer-events-auto flex max-h-[calc(100dvh-2rem)] w-full max-w-2xl flex-col rounded-2xl border overflow-hidden ${accent.ring} bg-white shadow-2xl outline-none`}
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
        >
          <div className={`flex items-start gap-3 bg-gradient-to-r shrink-0 ${accent.header} px-4 sm:px-6 py-4`}>
            <span className={`mt-0.5 flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full ${accent.iconBg} text-white shadow-sm`}>
              <Icon aria-hidden="true" className="h-5 w-5" />
            </span>
            <div className="min-w-0 flex-1">
              <h2 id={titleId} className={`text-base font-semibold ${accent.title}`}>{t('title')}</h2>
              <p className={`mt-1 text-sm leading-6 ${accent.text}`}>{t('subtitle')}</p>
            </div>
            <button
              onClick={() => onClose()}
              disabled={isBusy}
              className="rounded p-1 text-gray-400 transition-colors hover:text-gray-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600 disabled:cursor-not-allowed disabled:opacity-50 shrink-0"
              aria-label={t('close')}
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>

          {/* Focusable so keyboard users can scroll a long notice; the dialog's
              focus handling recaptures focus here when every button is disabled. */}
          <div
            tabIndex={0}
            role="region"
            aria-labelledby={titleId}
            className="overscroll-contain focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-amber-600 min-h-0 overflow-y-auto px-4 sm:px-6 py-4"
          >
            <div className="space-y-4">
              {notifications.map((notification, index) => {
                const sectionAccent = getAccent(notification.kind, !!notification.qr_code);
                const SectionIcon = notification.kind === 'benefit' ? Gift : sectionAccent.icon;
                const primary = notification.primary_action;
                const secondary = notification.secondary_action;
                const action = primary ?? secondary;

                return (
                  <section
                    key={notification.id}
                    className={`rounded-2xl border ${sectionAccent.ring} ${notification.qr_code ? 'bg-gradient-to-br from-amber-50 to-orange-50' : 'bg-white'} p-4 ${index > 0 ? 'mt-4' : ''}`}
                  >
                    <div className="flex items-start gap-3">
                      <span className={`mt-0.5 flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full ${sectionAccent.iconBg} text-white shadow-sm`}>
                        <SectionIcon aria-hidden="true" className="h-5 w-5" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className={`text-sm font-semibold ${sectionAccent.title}`}>{notification.title}</div>
                        {notification.summary && (
                          <p className={`mt-1 text-sm leading-6 ${sectionAccent.text}`}>{notification.summary}</p>
                        )}
                      </div>
                    </div>

                    <div className={notification.qr_code ? 'mt-4 grid grid-cols-1 items-start gap-4 sm:grid-cols-[minmax(0,1fr)_136px]' : ''}>
                      {notification.body && (
                        notification.kind === 'whats_new' || notification.qr_code ? (
                          <div className={`prose prose-sm text-gray-600 prose-headings:my-3 prose-headings:text-sm prose-headings:font-semibold prose-headings:text-amber-950 prose-p:my-2 prose-p:leading-6 prose-ul:my-2 prose-ol:my-2 prose-li:my-1 prose-a:text-amber-800 prose-a:underline min-w-0 max-w-none break-words prose-a:break-all prose-pre:overflow-x-auto ${notification.qr_code ? 'prose-p:first:mt-0 prose-h3:text-amber-800 prose-strong:text-amber-800' : 'mt-4'}`}>
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
                          <p className="mt-3 text-sm leading-6 text-gray-600 whitespace-pre-wrap">{notification.body}</p>
                        )
                      )}
                      {notification.qr_code && <NoticeQRCode key={notification.qr_code.src} code={notification.qr_code} />}
                    </div>

                    {notification.highlights.length > 0 && (
                      <div className="mt-3 space-y-2">
                        {notification.highlights.map((highlight) => (
                          <div key={highlight} className="flex items-start gap-2 text-sm text-gray-700">
                            <CheckCircle aria-hidden="true" className="mt-0.5 h-4 w-4 text-emerald-500 flex-shrink-0" />
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
                          <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    )}
                  </section>
                );
              })}
            </div>
          </div>

          <div className="flex items-center gap-2 border-t border-gray-100 shrink-0 flex-wrap px-4 sm:px-6 py-4">
            <button
              onClick={onDismissForever}
              disabled={isBusy}
              className="flex items-center gap-1.5 rounded-lg px-2 py-2 text-sm font-medium text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600 disabled:cursor-not-allowed disabled:opacity-50"
              title={t('dismissThis')}
            >
              {isBusy ? (
                <Loader2 aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <BellOff aria-hidden="true" className="h-3.5 w-3.5" />
              )}
              {t('dismissThis')}
            </button>

            <button
              onClick={() => onAcknowledge()}
              disabled={isBusy}
              className={`ml-auto flex items-center gap-1.5 rounded-xl px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-600 disabled:cursor-not-allowed disabled:opacity-50 ${accent.button}`}
            >
              {isBusy && <Loader2 aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />}
              {t('gotIt')}
            </button>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}
