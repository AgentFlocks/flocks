import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ChatTab from './ChatTab';

const {
  capturedSessionOptions,
  mockCreate,
  mockCreateAndSend,
  mockReset,
  mockSendPrompt,
} = vi.hoisted(() => ({
  capturedSessionOptions: [] as any[],
  mockCreate: vi.fn(),
  mockCreateAndSend: vi.fn(),
  mockReset: vi.fn(),
  mockSendPrompt: vi.fn(),
}));

vi.mock('@/hooks/useDefaultModelVision', () => ({
  useDefaultModelVision: () => false,
}));

vi.mock('@/hooks/useSessionChat', () => ({
  useSessionChat: (options: any) => {
    capturedSessionOptions.push(options);
    return {
      sessionId: null,
      loading: false,
      error: null,
      create: mockCreate,
      createAndSend: mockCreateAndSend,
      reset: mockReset,
    };
  },
}));

vi.mock('@/api/client', () => ({
  default: { get: vi.fn() },
}));

vi.mock('@/api/workflow', () => ({
  workflowAPI: { get: vi.fn() },
}));

vi.mock('@/components/common/SessionChat', () => ({
  default: ({ conversationBottomSlot, welcomeContent }: any) => (
    <div data-testid="session-chat">
      {welcomeContent}
      {conversationBottomSlot?.({ sendPrompt: mockSendPrompt, sending: false })}
    </div>
  ),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const translations: Record<string, string> = {
        'detail.chat.sessionTitle': '修改工作流「{{name}}」',
        'detail.chat.contextMessage': [
          '工作流 ID： {{id}}',
          '工作流名称： {{name}}',
          '工作流目录： {{dir}}',
          '编辑文档： {{editDocPath}}',
          '配置工作流时请加载 {{configSkillName}} skill。',
        ].join('\n'),
        'detail.chat.inputPlaceholder': '描述你想对工作流做的修改...',
        'detail.chat.newSession': '新建会话',
        'detail.chat.historyLabel': '历史会话',
        'detail.chat.currentLabel': '当前',
        'detail.chat.welcome.title': '{{name}} 当前状态',
        'detail.chat.welcome.descPart1': '你可以直接描述需求。',
        'detail.chat.welcome.descPart2': '。',
        'detail.chat.welcome.mdTabLabel': '编辑文档',
        'detail.chat.welcome.canHelp': '我可以帮你：',
        'detail.chat.welcome.bullet1': '修改节点',
        'detail.chat.welcome.bullet2': '调整流转',
        'detail.chat.welcome.bullet3': '新增节点',
        'detail.chat.welcome.bullet4': '重构结构',
        'detail.chat.welcome.tipPart1': '先看左侧',
        'detail.chat.welcome.tipPart2': '。',
        'detail.chat.welcome.retry': '重试',
        'detail.chat.welcome.guideExpand': '展开',
        'detail.chat.welcome.guideCollapse': '收起',
        'detail.chat.welcome.guidePrimaryShort': '智能配置',
        'detail.chat.welcome.guidePrimaryDesc': '配置工作流',
        'detail.chat.welcome.guidePrompt': '请按 {{configSkillName}} 配置。工作流 ID 是 {{id}}，工作流目录是 {{dir}}。',
        'detail.chat.welcome.guideAuditShort': '查配置',
        'detail.chat.welcome.guideAuditDesc': '检查缺失项',
        'detail.chat.welcome.auditPrompt': '请检查配置。工作流 ID 是 {{id}}，工作流目录是 {{dir}}。',
        'detail.chat.welcome.guideSampleShort': '样例验证',
        'detail.chat.welcome.guideSampleDesc': '验证输入输出',
        'detail.chat.welcome.samplePrompt': '请验证样例。工作流 ID 是 {{id}}，工作流目录是 {{dir}}。',
      };
      return (translations[key] ?? key).replace(/{{(\w+)}}/g, (_match, name: string) => (
        params?.[name] === undefined ? '' : String(params[name])
      ));
    },
  }),
}));

const workflow = {
  id: 'stream_alert_denoise',
  name: 'Stream Alert Denoise',
  category: 'default',
  source: 'global' as const,
  status: 'active' as const,
  createdAt: 0,
  updatedAt: 0,
  markdownContent: '',
  workflowJson: {
    start: 'receive_alert',
    nodes: [],
    edges: [],
  },
  stats: {
    callCount: 0,
    successCount: 0,
    errorCount: 0,
    totalRuntime: 0,
    avgRuntime: 0,
    thumbsUp: 0,
    thumbsDown: 0,
  },
};

describe('WorkflowDetail ChatTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    capturedSessionOptions.length = 0;
    localStorage.clear();
  });

  it('passes the explicit workflow id into the AI session context', () => {
    render(<ChatTab workflow={workflow} />);

    expect(capturedSessionOptions[0].contextMessage).toContain('工作流 ID： stream_alert_denoise');
    expect(capturedSessionOptions[0].contextMessage).toContain('工作流目录： ~/.flocks/plugins/workflows/stream_alert_denoise/');
    expect(capturedSessionOptions[0].contextMessage).toContain('workflow-config-guide');
  });

  it('includes the workflow id in workflow configuration shortcut prompts', async () => {
    const user = userEvent.setup();
    render(<ChatTab workflow={workflow} />);

    await user.click(screen.getByRole('button', { name: /智能配置/ }));

    expect(mockSendPrompt).toHaveBeenCalledWith(expect.stringContaining('工作流 ID 是 stream_alert_denoise'));
    expect(mockSendPrompt).toHaveBeenCalledWith(expect.stringContaining('~/.flocks/plugins/workflows/stream_alert_denoise/'));
  });
});
