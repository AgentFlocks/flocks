import { useEffect, useId, useRef } from 'react';
import type { RefObject } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import './VoiceErrorDialog.css';

interface VoiceErrorDialogProps {
  message: string;
  onClose: () => void;
  returnFocusRef?: RefObject<HTMLButtonElement | null>;
}

/** The native top layer escapes composer clipping and makes the page inert. */
export default function VoiceErrorDialog({ message, onClose, returnFocusRef }: VoiceErrorDialogProps) {
  const { t } = useTranslation('voice');
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const bodyId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const previousFocus = returnFocusRef?.current ?? document.activeElement;
    dialog.showModal();
    closeRef.current?.focus({ preventScroll: true });
    return () => {
      if (dialog.open) dialog.close();
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) {
        previousFocus.focus({ preventScroll: true });
      }
    };
  }, [returnFocusRef]);

  return createPortal(
    <dialog
      ref={dialogRef}
      role="alertdialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      data-flocks-voice-error
      className="flocks-voice-error-dialog"
      onCancel={(event) => { event.preventDefault(); onClose(); }}
    >
      <header className="flocks-voice-error-header">
        <h2 id={titleId}>{t('errorDialog.title')}</h2>
        <button ref={closeRef} type="button" onClick={onClose} className="flocks-voice-error-close" aria-label={t('errorDialog.close')}>
          <X aria-hidden="true" size={18} />
        </button>
      </header>
      <div id={bodyId} className="flocks-voice-error-body" tabIndex={0}>
        <p dir="auto">{message}</p>
        <p className="flocks-voice-error-help">{t('errorDialog.draftKept')}</p>
      </div>
      <footer className="flocks-voice-error-footer">
        <button type="button" onClick={onClose}>{t('dismiss')}</button>
      </footer>
    </dialog>,
    document.body,
  );
}
