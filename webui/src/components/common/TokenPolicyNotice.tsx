import { createPortal } from 'react-dom';
import { useEffect, useRef } from 'react';
import { Bell, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import { NoticePrimaryAction } from './NoticeCard';
import { useDialogFocus } from '@/hooks/useDialogFocus';

export default function TokenPolicyNotice({
  onClose,
  onPresented,
}: {
  onClose: () => void;
  onPresented: () => void;
}) {
  const { t } = useTranslation('notification');
  const dialogRef = useRef<HTMLDivElement>(null);
  const close = () => {
    onPresented();
    onClose();
  };
  useDialogFocus(dialogRef, true, close);
  useEffect(() => {
    // A foreground paint, rather than module loading, confirms display.
    let frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => {
        if (document.visibilityState === 'visible') onPresented();
      });
    });
    return () => {
      cancelAnimationFrame(frame);
    };
  }, [onPresented]);
  return createPortal(
    <>
      <div className="fixed inset-0 bg-black/30 z-[90]" onClick={close} aria-hidden="true" />
      <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 pointer-events-none">
        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="token-policy-title"
          tabIndex={-1}
          data-ui-overlay="dialog"
          className="pointer-events-auto flex max-h-[calc(100dvh-2rem)] w-full max-w-3xl flex-col rounded-2xl border border-gray-200 bg-white shadow-2xl outline-none overflow-hidden"
        >
          <div className="flex items-start justify-between gap-3 border-b border-gray-100 px-5 py-4 shrink-0">
            <div className="flex min-w-0 items-start gap-2">
              <Bell aria-hidden="true" className="mt-0.5 h-5 w-5 text-amber-600 shrink-0" />
              <h2 id="token-policy-title" className="text-base font-semibold leading-6 text-gray-800">
                {t('tokenPolicyTitle')}
              </h2>
            </div>
            <button
              onClick={close}
              aria-label={t('close')}
              className="rounded p-1 text-gray-400 transition-colors hover:text-gray-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-amber-600 shrink-0"
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>
          <div
            tabIndex={0}
            role="region"
            aria-labelledby="token-policy-title"
            className="overscroll-contain focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-amber-600 min-h-0 overflow-y-auto px-5 py-4 sm:px-6"
          >
            <div className="prose prose-sm max-w-none text-gray-700 prose-p:my-3 prose-p:leading-7 prose-ol:my-3 prose-ol:pl-5 prose-li:my-2 prose-li:leading-7 prose-strong:text-gray-900 [&_p>strong:only-child]:text-red-600 prose-a:text-amber-800 prose-a:underline break-words prose-a:break-all">
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
          <div className="flex justify-end border-t border-gray-100 px-5 py-4 shrink-0">
            <NoticePrimaryAction onClick={close}>{t('gotIt')}</NoticePrimaryAction>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}
