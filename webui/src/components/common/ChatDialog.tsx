/**
 * ChatDialog - 统一的对话弹窗组件
 *
 * 使用 useSessionChat 创建会话，通过 SessionChat 展示对话并支持追问。
 * 会话在用户首次发送消息时才创建，避免空会话污染会话列表。
 */
import { useEffect, useCallback } from 'react';
import { X, Sparkles } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import SessionChat from './SessionChat';
import { useSessionChat } from '@/hooks/useSessionChat';

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
  width = 'max-w-2xl',
}: ChatDialogProps) {
  const { t } = useTranslation('common');
  const { sessionId, createAndSend, reset } = useSessionChat({
    title,
  });

  useEffect(() => {
    if (open && initialPrompt) {
      createAndSend(initialPrompt).catch(() => {});
    }
    if (!open) reset();
  }, [open, reset, initialPrompt, createAndSend]);

  const handleCreateAndSend = useCallback(
    async (text: string) => {
      await createAndSend(text);
    },
    [createAndSend],
  );

  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-gray-600/75 flex items-center justify-center z-50">
      <div className={`bg-white rounded-xl shadow-2xl ${width} w-full mx-4 flex flex-col`} style={{ height: '75vh' }}>
        {/* Header */}
        <div className="px-5 py-4 border-b border-gray-200 flex-shrink-0">
          <div className="flex items-center justify-center">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg bg-gradient-to-br from-red-500 to-purple-600 flex items-center justify-center">
                <Sparkles className="w-5 h-5 text-white" />
              </div>
              <div>
                <h2 className="text-base font-semibold text-gray-900">{title}</h2>
                {subtitle && <p className="text-xs text-gray-500 mt-0.5">{subtitle}</p>}
              </div>
            </div>
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 p-1.5 rounded-lg hover:bg-gray-100 transition-colors"
            >
              <X className="w-5 h-5" />
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
          onCreateAndSend={!sessionId ? handleCreateAndSend : undefined}
          welcomeContent={!sessionId ? (
            <div className="text-center max-w-md">
              <Sparkles className="w-10 h-10 text-red-500 mx-auto mb-3" />
              <h3 className="text-lg font-semibold text-gray-900 mb-2">{t('chat.talkToRex')}</h3>
              <p className="text-sm text-gray-500">{t('chat.talkToRexHint')}</p>
            </div>
          ) : undefined}
        />
      </div>
    </div>
  );
}
