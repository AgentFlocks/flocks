import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { IconAlertTriangle, IconLoader2, IconShieldCheck } from '@tabler/icons-react';
import type { PendingPermission } from '@/api/permission';

export type PermissionDecision =
  | 'allow'
  | 'always'
  | 'deny'
  | 'trust_tool_network'
  | 'trust_network_target';

interface PermissionApprovalDialogProps {
  request: PendingPermission | null;
  submitting?: boolean;
  error?: string | null;
  onReply: (decision: PermissionDecision) => void;
}

export function PermissionApprovalDialog({
  request,
  submitting = false,
  error,
  onReply,
}: PermissionApprovalDialogProps) {
  const { t } = useTranslation('session');
  useEffect(() => {
    if (!request || submitting) return;

    const rejectOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onReply('deny');
    };
    window.addEventListener('keydown', rejectOnEscape);
    return () => window.removeEventListener('keydown', rejectOnEscape);
  }, [request, submitting, onReply]);

  if (!request) return null;

  const command = typeof request.metadata.command === 'string'
    ? request.metadata.command.trim()
    : '';
  const description = command || request.toolID || request.permission;
  const confirmOptions = Array.isArray(request.metadata.network_confirm_options)
    ? request.metadata.network_confirm_options
      .map((item) => (typeof item === 'string' ? item.trim().toLowerCase() : ''))
      .filter(Boolean)
    : [];
  const networkTrustEnabled = confirmOptions.includes('trust_tool_network');
  const networkTargetTrustEnabled = confirmOptions.includes('trust_network_target');

  return (
    <div
      className="mb-2 flex min-w-0 flex-wrap items-center gap-3 rounded-card border border-warning bg-warning-soft px-3 py-2.5"
      role="alertdialog"
      aria-labelledby="permission-approval-title"
    >
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-control bg-warning-soft">
        <IconAlertTriangle aria-hidden="true" className="h-4 w-4 text-warning dark:text-warning" />
      </div>
      <div className="min-w-0 flex-1">
        <div id="permission-approval-title" className="text-sm font-medium text-fg dark:text-fg">
          {t('chat.permission.title')}
        </div>
        <code className="mt-0.5 block truncate font-mono text-xs text-fg-secondary dark:text-fg-secondary" title={description}>
          $ {description}
        </code>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          autoFocus
          disabled={submitting}
          onClick={() => onReply('deny')}
          className="rounded-lg px-3 py-1.5 text-xs font-medium text-fg-secondary transition-colors hover:bg-warning-soft disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled dark:text-fg-secondary dark:hover:bg-warning-soft"
        >
          {t('chat.permission.deny')}
        </button>
        <button
          type="button"
          disabled={submitting}
          onClick={() => onReply(networkTargetTrustEnabled ? 'trust_network_target' : 'always')}
          className="inline-flex items-center gap-1 rounded-lg border border-warning px-3 py-1.5 text-xs font-medium text-warning transition-colors hover:bg-warning-soft disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled dark:text-warning dark:hover:bg-warning-soft"
        >
          <IconShieldCheck aria-hidden="true" className="h-4 w-4" />
          {networkTargetTrustEnabled ? t('chat.permission.alwaysTarget') : t('chat.permission.always')}
        </button>
        {networkTrustEnabled && (
          <button
            type="button"
            disabled={submitting}
            onClick={() => onReply('trust_tool_network')}
            className="inline-flex items-center gap-1 rounded-lg border border-line-control px-3 py-1.5 text-xs font-medium text-fg-secondary transition-colors hover:bg-hover disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled dark:border-line dark:text-fg-secondary dark:hover:bg-hover"
          >
            {t('chat.permission.trustToolNetwork')}
          </button>
        )}
        <button
          type="button"
          disabled={submitting}
          onClick={() => onReply('allow')}
          className="inline-flex items-center gap-1 rounded-control bg-action px-3 py-1.5 text-xs font-medium text-action-fg transition-colors hover:bg-action-hover disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled"
        >
          {submitting && <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
          {t('chat.permission.allowOnce')}
        </button>
      </div>
      {error && (
        <p role="alert" className="basis-full pl-11 text-xs text-danger dark:text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
