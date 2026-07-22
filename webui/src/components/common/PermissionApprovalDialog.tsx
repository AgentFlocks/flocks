import { useEffect } from 'react';
import { AlertTriangle, Loader2, ShieldCheck } from 'lucide-react';
import type { PendingPermission } from '@/api/permission';

export type PermissionDecision = 'allow' | 'always' | 'deny';

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

  return (
    <div
      className="mb-2 flex min-w-0 items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 shadow-sm dark:border-amber-500/35 dark:bg-amber-950/25"
      role="alertdialog"
      aria-labelledby="permission-approval-title"
    >
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-amber-100 dark:bg-amber-950/50">
        <AlertTriangle className="h-4 w-4 text-amber-700 dark:text-amber-300" />
      </div>
      <div className="min-w-0 flex-1">
        <div id="permission-approval-title" className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
          需要确认执行
        </div>
        <code className="mt-0.5 block truncate font-mono text-xs text-zinc-700 dark:text-zinc-200" title={description}>
          $ {description}
        </code>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <button
          type="button"
          autoFocus
          disabled={submitting}
          onClick={() => onReply('deny')}
          className="rounded-lg px-3 py-1.5 text-xs font-medium text-zinc-700 transition-colors hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60 dark:text-zinc-200 dark:hover:bg-amber-900/40"
        >
          拒绝
        </button>
        <button
          type="button"
          disabled={submitting}
          onClick={() => onReply('always')}
          className="inline-flex items-center gap-1 rounded-lg border border-amber-400 px-3 py-1.5 text-xs font-medium text-amber-800 transition-colors hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60 dark:text-amber-200 dark:hover:bg-amber-900/40"
        >
          <ShieldCheck className="h-3.5 w-3.5" />
          始终允许
        </button>
        <button
          type="button"
          disabled={submitting}
          onClick={() => onReply('allow')}
          className="inline-flex items-center gap-1 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {submitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          允许一次
        </button>
      </div>
      {error && (
        <p role="alert" className="basis-full pl-11 text-xs text-red-700 dark:text-red-300">
          {error}
        </p>
      )}
    </div>
  );
}
