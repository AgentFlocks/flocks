import { useCallback } from 'react';
import { useToast } from '@/components/common/Toast';
import { useConfirm } from '@/components/common/ConfirmDialog';

/** Unified success/error toasts and confirm dialogs (replaces window.alert/confirm). */
export function useAppFeedback() {
  const toast = useToast();
  const confirm = useConfirm();

  const notifySuccess = useCallback(
    (title: string, description?: string) => toast.success(title, description),
    [toast],
  );

  const notifyError = useCallback(
    (title: string, description?: string) => toast.error(title, description),
    [toast],
  );

  const notifyWarning = useCallback(
    (title: string, description?: string) => toast.warning(title, description),
    [toast],
  );

  const notifyInfo = useCallback(
    (title: string, description?: string) => toast.info(title, description),
    [toast],
  );

  const askConfirm = useCallback(
    (options: {
      description: string;
      title?: string;
      confirmText?: string;
      cancelText?: string;
      variant?: 'danger' | 'warning' | 'default';
    }) => confirm(options),
    [confirm],
  );

  return {
    notifySuccess,
    notifyError,
    notifyWarning,
    notifyInfo,
    askConfirm,
  };
}
