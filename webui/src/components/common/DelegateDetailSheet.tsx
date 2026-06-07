/**
 * DelegateDetailSheet — 子 Agent 委派详情侧边抽屉
 *
 * 点击 DelegateTaskCard 的「查看对话框」后弹出，
 * 内嵌只读 SessionChat 展示子 Session 的完整对话。
 * 左边缘可拖拽调整宽度，交互风格与 workflow RightPanel 一致。
 */

import { useEffect, useRef, useState, useCallback } from 'react';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import SessionChat from './SessionChat';

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
  pending:   'bg-gray-300',
  running:   'bg-slate-400 animate-pulse',
  completed: 'bg-zinc-400',
  error:     'bg-red-400',
};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MIN_WIDTH = 360;
const DEFAULT_WIDTH_RATIO = 0.55;
const STORAGE_KEY = 'delegate-sheet-width';

function getInitialWidth(): number {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      const w = Number(saved);
      if (w >= MIN_WIDTH && w <= window.innerWidth) return w;
    }
  } catch { /* ignore */ }
  return Math.max(MIN_WIDTH, Math.round(window.innerWidth * DEFAULT_WIDTH_RATIO));
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

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onClose]);

  // Prevent body scroll when open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden';
      return () => { document.body.style.overflow = ''; };
    }
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-[2px] transition-opacity"
        onClick={onClose}
      />

      {/* Drag separator — matching workflow RightPanel style */}
      <div
        onMouseDown={onDragStart}
        className="relative w-1 flex-shrink-0 bg-gray-200 hover:bg-red-400 active:bg-red-500 cursor-col-resize transition-colors duration-150 z-[51]"
      >
        <div className="absolute inset-y-0 -left-1.5 -right-1.5" />
      </div>

      {/* Sheet */}
      <div
        className="relative bg-white shadow-2xl flex flex-col border-l border-gray-200 overflow-hidden z-[51]"
        style={{ width }}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-gray-200 bg-gray-50/80 flex-shrink-0">
          <span className="inline-flex items-center justify-center w-8 h-8 rounded-full bg-gradient-to-br from-violet-500 to-red-500 text-white text-sm font-bold shadow-sm">
            {agentName.charAt(0)}
          </span>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-gray-900 truncate">{agentName}</h2>
              <span className="flex items-center gap-1 text-[10px] font-medium text-gray-500">
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${dot}`} />
                {statusLabel}
              </span>
            </div>
            <p className="text-xs text-gray-500 truncate mt-0.5">{description}</p>
          </div>

          <button
            onClick={onClose}
            className="p-1.5 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-200/60 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-hidden">
          <SessionChat
            sessionId={sessionId}
            live
            hideInput
            display={{ compact: false, showTimestamp: true }}
            className="h-full"
            emptyText={t('delegate.emptyChat')}
          />
        </div>
      </div>
    </div>
  );
}
