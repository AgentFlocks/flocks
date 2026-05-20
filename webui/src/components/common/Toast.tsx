import { useState, useEffect, createContext, useContext, useCallback, ReactNode } from 'react';
import { X, CheckCircle, AlertCircle, AlertTriangle, Info } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export type ToastType = 'success' | 'error' | 'warning' | 'info';

export interface ToastMessage {
  id: string;
  type: ToastType;
  title: string;
  description?: string;
  duration?: number;
}

interface ToastContextType {
  toasts: ToastMessage[];
  addToast: (toast: Omit<ToastMessage, 'id'>) => void;
  removeToast: (id: string) => void;
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
  warning: (title: string, description?: string) => void;
  info: (title: string, description?: string) => void;
}

const ToastContext = createContext<ToastContextType | null>(null);

const generateId = () => `toast-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const addToast = useCallback((toast: Omit<ToastMessage, 'id'>) => {
    const id = generateId();
    const newToast: ToastMessage = {
      ...toast,
      id,
      duration: toast.duration ?? 5000,
    };
    setToasts((prev) => [...prev, newToast]);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const success = useCallback((title: string, description?: string) => {
    addToast({ type: 'success', title, description });
  }, [addToast]);

  const error = useCallback((title: string, description?: string) => {
    addToast({ type: 'error', title, description, duration: 8000 });
  }, [addToast]);

  const warning = useCallback((title: string, description?: string) => {
    addToast({ type: 'warning', title, description });
  }, [addToast]);

  const info = useCallback((title: string, description?: string) => {
    addToast({ type: 'info', title, description });
  }, [addToast]);

  return (
    <ToastContext.Provider value={{ toasts, addToast, removeToast, success, error, warning, info }}>
      {children}
      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
}

const toastConfig: Record<ToastType, { icon: typeof CheckCircle; className: string }> = {
  success: {
    icon: CheckCircle,
    className: 'border-success-muted bg-success-muted text-success',
  },
  error: {
    icon: AlertCircle,
    className: 'border-danger-muted bg-danger-muted text-danger',
  },
  warning: {
    icon: AlertTriangle,
    className: 'border-warning-muted bg-warning-muted text-warning',
  },
  info: {
    icon: Info,
    className: 'border-accent-muted bg-accent-muted text-accent',
  },
};

function Toast({ toast, onRemove }: { toast: ToastMessage; onRemove: () => void }) {
  const { t } = useTranslation('common');
  const config = toastConfig[toast.type];
  const Icon = config.icon;

  useEffect(() => {
    if (toast.duration && toast.duration > 0) {
      const timer = setTimeout(onRemove, toast.duration);
      return () => clearTimeout(timer);
    }
  }, [toast.duration, onRemove]);

  return (
    <div
      className={`flex animate-slide-in items-start gap-3 rounded-panel border p-4 shadow-float ${config.className}`}
      role="alert"
    >
      <Icon className="mt-0.5 h-5 w-5 flex-shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{toast.title}</p>
        {toast.description && (
          <p className="mt-1 text-sm opacity-80">{toast.description}</p>
        )}
      </div>
      <button
        type="button"
        onClick={onRemove}
        className="flex-shrink-0 rounded p-1 transition-colors hover:bg-black/5"
        aria-label={t('button.close')}
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

function ToastContainer({
  toasts,
  removeToast,
}: {
  toasts: ToastMessage[];
  removeToast: (id: string) => void;
}) {
  if (toasts.length === 0) return null;

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2">
      {toasts.map((toast) => (
        <div key={toast.id} className="pointer-events-auto">
          <Toast toast={toast} onRemove={() => removeToast(toast.id)} />
        </div>
      ))}
    </div>
  );
}
