import { useState, createContext, useContext, useCallback, ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/utils/cn';

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

const confirmVariantStyles = {
  danger: 'bg-danger hover:bg-primary-700 text-white',
  warning: 'bg-warning hover:opacity-90 text-white',
  default: 'flocks-btn-primary',
};

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

  return (
    <ConfirmContext.Provider value={{ confirm }}>
      {children}
      {state && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 backdrop-blur-sm">
          <div className="flocks-float-panel mx-4 w-full max-w-sm p-6">
            <div className="flex items-start gap-3">
              {state.options.variant === 'danger' && (
                <div className="flex-shrink-0 rounded-lg bg-danger-muted p-2">
                  <AlertTriangle className="h-5 w-5 text-danger" />
                </div>
              )}
              <div>
                {state.options.title && (
                  <h3 className="mb-1 font-semibold text-ink">{state.options.title}</h3>
                )}
                <p className="text-sm text-ink-muted">{state.options.description}</p>
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-3">
              <button
                type="button"
                onClick={handleCancel}
                className="flocks-btn-secondary"
              >
                {state.options.cancelText ?? t('button.cancel')}
              </button>
              <button
                type="button"
                onClick={handleConfirm}
                className={cn(
                  'rounded-lg px-4 py-2 text-sm font-semibold transition-colors',
                  confirmVariantStyles[state.options.variant ?? 'default'],
                )}
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
