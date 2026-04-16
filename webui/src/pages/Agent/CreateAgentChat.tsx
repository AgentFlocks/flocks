import { useEffect, useCallback } from 'react';
import { X, Bot } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import SessionChat from '@/components/common/SessionChat';
import { useSessionChat } from '@/hooks/useSessionChat';

const SUGGESTIONS = [
  '创建一个威胁情报分析 Agent，能够查询 IP/域名/哈希的信誉并输出分析报告',
  '创建一个代码审计 Agent，分析代码中的安全漏洞和潜在风险',
  '创建一个日志分析 Agent，解析安全日志并提取关键事件和异常行为',
  '创建一个漏洞评估 Agent，评估 CVE 漏洞的影响范围并给出修复建议',
];

const WELCOME = `你好！我来帮你创建一个新的子 Agent。

请告诉我你需要什么样的 Agent，比如：

- **名称**：如 \`threat-analyst\`（小写 + 短横线）
- **职责**：这个 Agent 负责做什么
- **能力范围**：它需要访问哪些工具（只读分析 / 代码执行 / 网络搜索等）

描述越清晰，生成的 Agent 越准确。你也可以点击下方的示例快速开始！`;

function buildContext(): string {
  return `你是 Agent 创建助手。用户希望通过对话来创建一个新的子 Agent。

请使用 agent-builder skill 根据用户的需求生成子 Agent 配置文件（YAML + prompt 文件），保存到 ~/.flocks/plugins/agents/ 目录。

**创建流程：**
1. 先确认用户需求：Agent 名称、职责、能力边界、执行模式
2. 生成 prompt 文件（.prompt.md）和配置文件（.yaml）
3. 验证文件正确性

**重要约束：**
- Agent 名称必须是 kebab-case 格式
- mode 固定为 subagent
- 文件必须写入 ~/.flocks/plugins/agents/
- 不要与内置 Agent 名称冲突

请先引导用户描述需求，如果信息不够清晰可适当追问，然后一次性生成所有文件。`;
}

interface CreateAgentChatProps {
  open: boolean;
  onClose: () => void;
}

export default function CreateAgentChat({ open, onClose }: CreateAgentChatProps) {
  const { t } = useTranslation(['agent', 'common']);

  const { sessionId, createAndSend, reset } = useSessionChat({
    title: t('agent:chat.createTitle'),
    category: 'agent',
    contextMessage: buildContext(),
    welcomeMessage: WELCOME,
  });

  useEffect(() => {
    if (!open) reset();
  }, [open, reset]);

  const handleCreateAndSend = useCallback(
    async (text: string) => {
      await createAndSend(text);
    },
    [createAndSend],
  );

  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
      <div
        className="bg-white rounded-2xl shadow-2xl w-full mx-4 flex flex-col overflow-hidden max-w-2xl"
        style={{ height: '80vh' }}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between flex-shrink-0 bg-gradient-to-r from-purple-50 to-slate-50">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-purple-600 to-slate-700 flex items-center justify-center shadow-sm">
              <Bot className="w-5 h-5 text-white" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">{t('agent:chat.createTitle')}</h2>
              <p className="text-xs text-gray-500 mt-0.5">{t('agent:chat.subtitle')}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 p-1.5 rounded-lg hover:bg-white/80 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <SessionChat
          sessionId={sessionId}
          live={!!sessionId}
          placeholder={t('agent:chat.placeholder')}
          className="flex-1 min-h-0"
          suggestions={SUGGESTIONS}
          onCreateAndSend={!sessionId ? handleCreateAndSend : undefined}
          welcomeContent={!sessionId ? (
            <div className="text-center max-w-md">
              <Bot className="w-10 h-10 text-purple-500 mx-auto mb-3" />
              <h3 className="text-lg font-semibold text-gray-900 mb-2">{t('agent:chat.createTitle')}</h3>
              <p className="text-sm text-gray-500">{t('agent:chat.subtitle')}</p>
            </div>
          ) : undefined}
        />
      </div>
    </div>
  );
}
