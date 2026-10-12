import { useState, createContext, useContext, useCallback, useEffect, useId, useRef, ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface ConfirmOptions {
  title?: string;
  description: string;
  confirmText?: string;
  cancelText?: string;
  /** Information dialogs (e.g. shortcuts) only need the confirm button. */
  hideCancel?: boolean;
  variant?: 'danger' | 'warning' | 'default';
}

interface ConfirmContextType {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
}

const ConfirmContext = createContext<ConfirmContextType | null>(null);

// The mounted provider, so plain helpers outside React can use the product dialog too.
let activeConfirm: ((options: ConfirmOptions) => Promise<boolean>) | null = null;

export function useConfirm() {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error('useConfirm must be used within ConfirmProvider');
  return ctx.confirm;
}

/** Ask through the mounted product dialog; without a ConfirmProvider fall back to the browser prompt. */
export function requestConfirm(options: ConfirmOptions): Promise<boolean> {
  if (activeConfirm) return activeConfirm(options);
  return Promise.resolve(window.confirm([options.title, options.description].filter(Boolean).join('\n')));
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{
    options: ConfirmOptions;
    resolve: (value: boolean) => void;
  } | null>(null);
  const confirmButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();

  const { t } = useTranslation('common');

  const confirm = useCallback((options: ConfirmOptions): Promise<boolean> => {
    return new Promise((resolve) => {
      setState((current) => {
        // A newer request replaces an unanswered one; the older caller gets "cancel".
        current?.resolve(false);
        return { options, resolve };
      });
    });
  }, []);

  useEffect(() => {
    activeConfirm = confirm;
    return () => {
      if (activeConfirm === confirm) activeConfirm = null;
    };
  }, [confirm]);

  const handleConfirm = useCallback(() => { state?.resolve(true); setState(null); }, [state]);
  const handleCancel = useCallback(() => { state?.resolve(false); setState(null); }, [state]);

  useEffect(() => {
    if (!state) return undefined;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    // A native modal dialog sits in the browser's top layer, so the prompt stays on top of
    // (and usable over) another modal <dialog> that asked for it, e.g. the group editor.
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) {
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
    }
    confirmButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        handleCancel();
      }
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, [handleCancel, state]);

  const variantStyles = {
    danger: 'bg-red-600 hover:bg-red-700 text-white',
    warning: 'bg-yellow-500 hover:bg-yellow-600 text-white',
    default: 'bg-red-600 hover:bg-red-700 text-white',
  };
  const hideCancel = state?.options.hideCancel || state?.options.cancelText === '';

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      {state && (
        <dialog
          ref={dialogRef}
          role="alertdialog"
          aria-modal="true"
          aria-labelledby={state.options.title ? titleId : undefined}
          aria-label={state.options.title ? undefined : t('button.confirm')}
          aria-describedby={descriptionId}
          className="m-auto w-[calc(100%-2rem)] max-w-md overflow-visible bg-transparent p-0 backdrop:bg-black/40"
          onCancel={(event) => {
            event.preventDefault();
            handleCancel();
          }}
          onMouseDown={(event) => {
            // Only the backdrop reaches the <dialog> itself; the panel below covers the box.
            if (event.target === event.currentTarget) handleCancel();
          }}
        >
          {/* dvh: on phones 100vh includes the browser bars and pushed the buttons off-screen. */}
          <div
            data-ui-overlay="dialog"
            className="flex max-h-[calc(100dvh-2rem)] w-full flex-col overflow-hidden rounded-xl border border-zinc-200 bg-white p-6 shadow-xl dark:border-zinc-700 dark:bg-zinc-900"
          >
            <div className="flex items-start gap-3">
              {state.options.variant === 'danger' && (
                <div className="flex-shrink-0 rounded-lg bg-red-100 p-2 dark:bg-red-500/15">
                  <AlertTriangle aria-hidden="true" className="h-5 w-5 text-red-600 dark:text-red-300" />
                </div>
              )}
              <div className="min-w-0 flex-1">
                {state.options.title && (
                  <h3 id={titleId} className="mb-1 font-semibold text-gray-900 dark:text-zinc-100">{state.options.title}</h3>
                )}
                <p id={descriptionId} className="max-h-[45vh] overflow-auto whitespace-pre-wrap break-words text-sm leading-6 text-gray-600 dark:text-zinc-300">
                  {state.options.description}
                </p>
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-3">
              {!hideCancel && (
                <button
                  type="button"
                  onClick={handleCancel}
                  className="rounded-lg bg-gray-100 px-4 py-2 text-sm text-gray-700 transition-colors hover:bg-gray-200 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
                >
                  {state.options.cancelText || t('button.cancel')}
                </button>
              )}
              <button
                ref={confirmButtonRef}
                type="button"
                onClick={handleConfirm}
                className={`rounded-lg px-4 py-2 text-sm transition-colors ${variantStyles[state.options.variant ?? 'default']}`}
              >
                {state.options.confirmText ?? t('button.confirm')}
              </button>
            </div>
          </div>
        </dialog>
      )}
    </ConfirmContext.Provider>
  );
}
