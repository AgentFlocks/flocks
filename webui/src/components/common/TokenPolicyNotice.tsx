import { createPortal } from 'react-dom';
import { useEffect, useRef } from 'react';
import { IconBell, IconX } from '@tabler/icons-react';
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
      <div className="fixed inset-0 z-[90] bg-overlay-backdrop" onClick={close} aria-hidden="true" />
      <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 pointer-events-none">
        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="token-policy-title"
          tabIndex={-1}
          data-ui-overlay="dialog"
          className="pointer-events-auto flex max-h-[calc(100dvh-2rem)] w-full max-w-modal flex-col overflow-hidden rounded-overlay border border-line bg-surface shadow-overlay"
        >
          <div className="flex shrink-0 items-start justify-between gap-3 border-b border-line px-5 py-4">
            <div className="flex min-w-0 items-start gap-2">
              <IconBell aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-warning" />
              <h2 id="token-policy-title" className="text-title-section text-fg">
                {t('tokenPolicyTitle')}
              </h2>
            </div>
            <button
              onClick={close}
              aria-label={t('close')}
              className="ui-btn ui-btn-ghost ui-btn-icon shrink-0 text-fg-muted"
            >
              <IconX aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>
          <div
            tabIndex={0}
            role="region"
            aria-labelledby="token-policy-title"
            className="min-h-0 overflow-y-auto overscroll-contain px-5 py-4 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus sm:px-6"
          >
            <div className="prose prose-sm max-w-none break-words text-fg-secondary prose-p:my-3 prose-ol:my-3 prose-ol:pl-5 prose-li:my-2 prose-strong:text-fg [&_p>strong:only-child]:text-brand prose-a:break-all prose-a:text-fg-link prose-a:underline">
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
          <div className="flex shrink-0 justify-end border-t border-line px-5 py-4">
            <NoticePrimaryAction onClick={close}>{t('gotIt')}</NoticePrimaryAction>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}
