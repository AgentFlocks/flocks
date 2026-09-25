import { useState, useRef, useId, createContext, useContext, useCallback, ReactNode } from 'react';
import { IconAlertTriangle, IconX } from '@tabler/icons-react';
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
    danger: 'ui-btn-danger',
    warning: 'ui-btn-primary',
    default: 'ui-btn-primary',
  };

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      {state && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-overlay-backdrop p-4" onClick={(event) => { if (event.target === event.currentTarget) handleCancel(); }}>
          <div ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descriptionId} data-ui-overlay="dialog" className="flex max-h-[calc(100dvh-2rem)] w-full max-w-modal flex-col overflow-hidden rounded-overlay border border-line bg-surface p-6 shadow-overlay">
            <div className="flex shrink-0 items-start gap-3">
              {state.options.variant && state.options.variant !== 'default' && (
                <div className={`p-2 rounded-control flex-shrink-0 ${state.options.variant === 'danger' ? 'bg-danger-soft text-danger' : 'bg-warning-soft text-warning'}`}>
                  <IconAlertTriangle aria-hidden="true" className="w-5 h-5" />
                </div>
              )}
              <div className="min-w-0 flex-1">
                <h2 id={titleId} className="text-title-section text-fg">{state.options.title ?? t('button.confirm')}</h2>
              </div>
              <button type="button" className="ui-btn ui-btn-ghost ui-btn-icon shrink-0" onClick={handleCancel} aria-label={t('button.close')}><IconX aria-hidden="true" className="h-5 w-5" /></button>
            </div>
            <p id={descriptionId} className="mt-4 min-h-0 overflow-auto whitespace-pre-wrap break-words text-sm text-fg-secondary">{state.options.description}</p>
            <div className="ui-overlay-footer shrink-0">
              <button
                onClick={handleCancel}
                className="ui-btn"
              >
                {state.options.cancelText ?? t('button.cancel')}
              </button>
              <button
                onClick={handleConfirm}
                className={`ui-btn ${variantStyles[state.options.variant ?? 'default']}`}
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
