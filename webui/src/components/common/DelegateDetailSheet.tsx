/**
 * DelegateDetailSheet — 子 Agent 委派详情侧边抽屉
 *
 * 点击 DelegateTaskCard 的「查看对话框」后弹出，
 * 内嵌只读 SessionChat 展示子 Session 的完整对话。
 * 左边缘可拖拽调整宽度，交互风格与 workflow RightPanel 一致。
 */

import { useId, useRef, useState, useCallback } from 'react';
import { IconX } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import SessionChat from './SessionChat';
import { useDialogFocus } from '@/hooks/useDialogFocus';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface DelegateDetailSheetProps {
  open: boolean;
  onClose: () => void;
  sessionId: string;
  agentName: string;
  description: string;
  status: string;
}

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------

const STATUS_DOT: Record<string, string> = {
  pending:   'bg-selected',
  running:   'bg-info animate-pulse',
  completed: 'bg-success',
  error:     'bg-action',
};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MIN_WIDTH = 360;
const DEFAULT_WIDTH = 480;
const STORAGE_KEY = 'delegate-sheet-width';

function getInitialWidth(): number {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      const w = Number(saved);
      if (w >= MIN_WIDTH && w <= window.innerWidth) return w;
    }
  } catch { /* ignore */ }
  return Math.min(DEFAULT_WIDTH, window.innerWidth);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function DelegateDetailSheet({
  open,
  onClose,
  sessionId,
  agentName,
  description,
  status,
}: DelegateDetailSheetProps) {
  const { t } = useTranslation('common');
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialogFocus(dialogRef, open, onClose);
  const dot = STATUS_DOT[status] ?? STATUS_DOT.pending;
  const statusLabel = t(`delegate.${status}`, { defaultValue: status });

  const [width, setWidth] = useState(getInitialWidth);
  const dragging = useRef(false);
  const dragStartX = useRef(0);
  const dragStartW = useRef(0);

  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    dragStartX.current = e.clientX;
    dragStartW.current = width;

    const maxW = Math.round(window.innerWidth * 0.85);

    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const delta = dragStartX.current - ev.clientX;
      setWidth(Math.min(maxW, Math.max(MIN_WIDTH, dragStartW.current + delta)));
    };
    const onUp = () => {
      dragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      setWidth((w) => {
        try { localStorage.setItem(STORAGE_KEY, String(w)); } catch { /* ignore */ }
        return w;
      });
    };

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [width]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-overlay-backdrop backdrop-blur-[2px] transition-opacity"
        onClick={onClose}
      />

      {/* Drag separator — matching workflow RightPanel style */}
      <div
        onMouseDown={onDragStart}
        className="relative w-1 flex-shrink-0 bg-selected hover:bg-action-hover active:bg-action-pressed cursor-col-resize transition-colors duration-150 z-[51]"
      >
        <div className="absolute inset-y-0 -left-1.5 -right-1.5" />
      </div>

      {/* Sheet */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        data-ui-overlay="drawer"
        className="relative max-w-full bg-surface shadow-overlay flex flex-col border-l border-line overflow-hidden z-[51]"
        style={{ width }}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-4 sm:px-6 py-4 border-b border-line bg-canvas flex-shrink-0">
          <span className="inline-flex items-center justify-center w-8 h-8 rounded-control bg-ai-soft text-ai text-sm font-semibold">
            {agentName.charAt(0)}
          </span>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h2 id={titleId} className="text-title-section text-fg truncate">{agentName}</h2>
              <span className="flex items-center gap-1 text-xs font-medium text-fg-muted">
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${dot}`} />
                {statusLabel}
              </span>
            </div>
            <p className="text-xs text-fg-muted truncate mt-0.5">{description}</p>
          </div>

          <button
            onClick={onClose}
            aria-label={t('button.close')}
            className="ui-btn ui-btn-ghost ui-btn-icon shrink-0 text-fg-muted"
          >
            <IconX aria-hidden="true" className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-hidden">
          <SessionChat
            sessionId={sessionId}
            live
            hideInput
            display={{
              compact: false,
              pageCanvas: true,
              showTimestamp: true,
              collapseIntermediateSteps: true,
              processGroupsDefaultOpen: false,
              processGroupsOpenWhileActive: true,
            }}
            agentName={agentName}
            className="h-full"
            emptyText={t('delegate.emptyChat')}
          />
        </div>
      </div>
    </div>
  );
}
