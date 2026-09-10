import { createPortal } from 'react-dom';
import { useEffect, useRef } from 'react';
import { Bell, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import { NoticePrimaryAction } from './NoticeCard';

export default function TokenPolicyNotice({
  onClose,
  onPresented,
}: {
  onClose: () => void;
  onPresented: () => void;
}) {
  const { t } = useTranslation('notification');
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialogRef.current?.focus();
    // A foreground paint, rather than module loading, confirms display.
    let frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => {
        if (document.visibilityState === 'visible') onPresented();
      });
    });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        onPresented();
        onClose();
      }
      if (event.key === 'Tab') {
        const controls = dialogRef.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], [tabindex="0"]',
        );
        const first = controls?.[0];
        const last = controls?.[controls.length - 1];
        if (
          event.shiftKey &&
          (document.activeElement === first || document.activeElement === dialogRef.current)
        ) {
          event.preventDefault();
          last?.focus();
        } else if (
          !event.shiftKey &&
          (document.activeElement === last || document.activeElement === dialogRef.current)
        ) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [onClose, onPresented]);
  const close = () => {
    onPresented();
    onClose();
  };
  return createPortal(
    <>
      <div className="fixed inset-0 z-[90] bg-black/30" onClick={close} aria-hidden="true" />
      <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 pointer-events-none">
        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="token-policy-title"
          tabIndex={-1}
          className="pointer-events-auto flex max-h-[calc(100dvh-2rem)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl outline-none"
        >
          <div className="flex shrink-0 items-start justify-between gap-3 border-b border-gray-100 px-5 py-4">
            <div className="flex min-w-0 items-start gap-2">
              <Bell aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
              <h2 id="token-policy-title" className="text-base font-semibold leading-6 text-gray-800">
                {t('tokenPolicyTitle')}
              </h2>
            </div>
            <button
              onClick={close}
              aria-label={t('close')}
              className="shrink-0 rounded p-1 text-gray-400 transition-colors hover:text-gray-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600"
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>
          <div
            tabIndex={0}
            role="region"
            aria-labelledby="token-policy-title"
            className="min-h-0 overflow-y-auto overscroll-contain px-5 py-4 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-amber-600 sm:px-6"
          >
            <div className="prose prose-sm max-w-none break-words text-gray-700 prose-p:my-3 prose-p:leading-7 prose-ol:my-3 prose-ol:pl-5 prose-li:my-2 prose-li:leading-7 prose-strong:text-gray-900 [&_p>strong:only-child]:text-red-600 prose-a:break-all prose-a:text-amber-800 prose-a:underline">
              <ReactMarkdown
                components={{
                  a: ({ children, ...props }) => (
                    <a {...props} target="_blank" rel="noopener noreferrer">
                      {children}
                    </a>
                  ),
                }}
              >
                {t('tokenPolicyBody')}
              </ReactMarkdown>
            </div>
          </div>
          <div className="flex shrink-0 justify-end border-t border-gray-100 px-5 py-4">
            <NoticePrimaryAction onClick={close}>{t('gotIt')}</NoticePrimaryAction>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}
