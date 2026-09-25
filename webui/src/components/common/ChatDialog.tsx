/**
 * ChatDialog - 统一的对话弹窗组件
 *
 * 使用 useSessionChat 创建会话，通过 SessionChat 展示对话并支持追问。
 * 会话在用户首次发送消息时才创建，避免空会话污染会话列表。
 */
import { useEffect, useId, useRef } from 'react';
import { IconX, IconSparkles } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import SessionChat from './SessionChat';
import { useSessionChat } from '@/hooks/useSessionChat';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';
import { useDialogFocus } from '@/hooks/useDialogFocus';

interface ChatDialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  /** Initial prompt auto-sent to AI via SessionChat's initialMessage */
  initialPrompt?: string;
  placeholder?: string;
  suggestions?: string[];
  onComplete?: () => void;
  className?: string;
  width?: string;
}

export default function ChatDialog({
  open,
  onClose,
  title,
  subtitle,
  initialPrompt,
  placeholder,
  suggestions,
  width = 'max-w-modal',
}: ChatDialogProps) {
  const { t } = useTranslation('common');
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialogFocus(dialogRef, open, onClose);
  const supportsVision = useDefaultModelVision();
  const {
    sessionId,
    pendingOptimisticMessage,
    createAndSend,
    consumePendingOptimisticMessage,
    reset,
  } = useSessionChat({
    title,
  });

  useEffect(() => {
    if (open && initialPrompt) {
      createAndSend({ text: initialPrompt }).catch(() => {});
    }
    if (!open) reset();
  }, [open, reset, initialPrompt, createAndSend]);


  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-overlay-backdrop flex items-center justify-center z-50" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} data-ui-overlay="dialog" className={`bg-surface rounded-overlay shadow-overlay ${width} w-full mx-4 flex flex-col`} style={{ height: '75vh' }}>
        {/* Header */}
        <div className="px-4 sm:px-6 py-4 border-b border-line flex-shrink-0">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-control bg-ai-soft text-ai flex items-center justify-center">
                <IconSparkles aria-hidden="true" className="w-5 h-5" />
              </div>
              <div>
                <h2 id={titleId} className="text-title-section text-fg">{title}</h2>
                {subtitle && <p className="text-xs text-fg-muted mt-0.5">{subtitle}</p>}
              </div>
            </div>
            <button
              onClick={onClose}
              aria-label={t('button.close')}
              className="ui-btn ui-btn-ghost ui-btn-icon shrink-0 text-fg-muted"
            >
              <IconX aria-hidden="true" className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Body */}
        <SessionChat
          sessionId={sessionId}
          live={!!sessionId}
          placeholder={placeholder ?? t('chat.inputPlaceholder')}
          className="flex-1 min-h-0 rounded-b-xl"
          emptyText={t('chat.starting')}
          suggestions={suggestions}
          supportsVision={supportsVision}
          initialOptimisticMessage={pendingOptimisticMessage}
          onInitialOptimisticMessageConsumed={consumePendingOptimisticMessage}
          onCreateAndSend={!sessionId ? (text, imageParts) => createAndSend({ text, imageParts }) : undefined}
          welcomeContent={!sessionId ? (
            <div className="text-center max-w-md">
              <IconSparkles aria-hidden="true" className="w-8 h-8 text-ai mx-auto mb-3" />
              <h3 className="text-title-card font-semibold text-fg mb-2">{t('chat.talkToRex')}</h3>
              <p className="text-sm text-fg-muted">{t('chat.talkToRexHint')}</p>
            </div>
          ) : undefined}
        />
      </div>
    </div>
  );
}
