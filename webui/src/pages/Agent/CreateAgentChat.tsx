import { pageActionLabel } from '../pageActionLabel';
import PageDialog from '../PageDialog';
import { useEffect } from 'react';
import { IconX, IconRobot } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import SessionChat from '@/components/common/SessionChat';
import { useSessionChat } from '@/hooks/useSessionChat';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';

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
  const supportsVision = useDefaultModelVision();

  const {
    sessionId,
    pendingOptimisticMessage,
    createAndSend,
    consumePendingOptimisticMessage,
    reset,
  } = useSessionChat({
    title: t('agent:chat.createTitle'),
    category: 'agent',
    contextMessage: buildContext(),
    welcomeMessage: WELCOME,
  });

  useEffect(() => {
    if (!open) reset();
  }, [open, reset]);


  if (!open) return null;

  return (
    <PageDialog onClose={onClose} className="fixed inset-0 bg-overlay-backdrop backdrop-blur-sm flex items-center justify-center z-50">
      <div
        className="bg-surface rounded-card shadow-overlay w-full mx-4 flex flex-col overflow-hidden max-w-modal"
        style={{ height: '80vh' }}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-line flex items-center justify-between flex-shrink-0 bg-gradient-to-r from-ai-soft to-subtle">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-card bg-ai-soft text-ai flex items-center justify-center shadow-sm">
              <IconRobot aria-hidden="true" className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-title-section font-semibold text-fg">{t('agent:chat.createTitle')}</h2>
              <p className="text-xs text-fg-muted mt-0.5">{t('agent:chat.subtitle')}</p>
            </div>
          </div>
          <button aria-label={pageActionLabel('close')}
            onClick={onClose}
            className="ui-btn-icon inline-flex items-center justify-center text-fg-muted hover:text-fg-secondary p-1.5 rounded-control hover:bg-surface transition-colors"
          >
            <IconX aria-hidden="true" className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <SessionChat
          sessionId={sessionId}
          live={!!sessionId}
          placeholder={t('agent:chat.placeholder')}
          className="flex-1 min-h-0"
          suggestions={SUGGESTIONS}
          supportsVision={supportsVision}
          initialOptimisticMessage={pendingOptimisticMessage}
          onInitialOptimisticMessageConsumed={consumePendingOptimisticMessage}
          onCreateAndSend={!sessionId ? (text, imageParts) => createAndSend({ text, imageParts }) : undefined}
          welcomeContent={!sessionId ? (
            <div className="text-center max-w-md">
              <IconRobot aria-hidden="true" className="w-8 h-8 text-ai mx-auto mb-3" />
              <h3 className="text-title-card font-semibold text-fg mb-2">{t('agent:chat.createTitle')}</h3>
              <p className="text-sm text-fg-muted">{t('agent:chat.subtitle')}</p>
            </div>
          ) : undefined}
        />
      </div>
    </PageDialog>
  );
}
