import { useState, useRef, useId, createContext, useContext, useCallback, ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useDialogFocus } from '@/hooks/useDialogFocus';

interface ConfirmOptions {
  title?: string;
  description: string;
  confirmText?: string;
  cancelText?: string;
  variant?: 'danger' | 'warning' | 'default';
}

interface ConfirmContextType {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
}

const ConfirmContext = createContext<ConfirmContextType | null>(null);

export function useConfirm() {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error('useConfirm must be used within ConfirmProvider');
  return ctx.confirm;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{
    options: ConfirmOptions;
    resolve: (value: boolean) => void;
  } | null>(null);

  const { t } = useTranslation('common');

  const confirm = useCallback((options: ConfirmOptions): Promise<boolean> => {
    return new Promise((resolve) => {
      setState({ options, resolve });
    });
  }, []);

  const handleConfirm = () => { state?.resolve(true); setState(null); };
  const handleCancel = () => { state?.resolve(false); setState(null); };
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  useDialogFocus(dialogRef, Boolean(state), handleCancel);

  const variantStyles = {
    danger: 'bg-red-600 hover:bg-red-700 text-white',
    warning: 'bg-yellow-500 hover:bg-yellow-600 text-white',
    default: 'bg-red-600 hover:bg-red-700 text-white',
  };

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      {state && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4" onClick={(event) => { if (event.target === event.currentTarget) handleCancel(); }}>
          <div
            ref={dialogRef}
            tabIndex={-1}
            role="dialog"
            aria-modal="true"
            aria-labelledby={state.options.title ? titleId : undefined}
            aria-label={state.options.title ? undefined : t('button.confirm')}
            aria-describedby={descriptionId}
            data-ui-overlay="dialog"
            className="flex max-h-[calc(100dvh-2rem)] w-full max-w-md flex-col overflow-hidden rounded-xl bg-white p-6 shadow-xl"
          >
            <div className="flex items-start gap-3">
              {state.options.variant === 'danger' && (
                <div className="p-2 bg-red-100 rounded-lg flex-shrink-0">
                  <AlertTriangle aria-hidden="true" className="w-5 h-5 text-red-600" />
                </div>
              )}
              <div className="min-w-0 flex-1">
                {state.options.title && (
                  <h2 id={titleId} className="font-semibold text-gray-900 mb-1">{state.options.title}</h2>
                )}
                <p id={descriptionId} className="max-h-[45vh] overflow-auto whitespace-pre-wrap break-words text-sm leading-6 text-gray-600">
                  {state.options.description}
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-3 mt-5">
              <button
                onClick={handleCancel}
                className="px-4 py-2 text-sm text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
              >
                {state.options.cancelText ?? t('button.cancel')}
              </button>
              <button
                onClick={handleConfirm}
                className={`px-4 py-2 text-sm rounded-lg transition-colors ${variantStyles[state.options.variant ?? 'default']}`}
              >
                {state.options.confirmText ?? t('button.confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}
