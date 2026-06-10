import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import type { ComponentProps } from 'react';

import ChatTab from './ChatTab';
import { workflowAPI } from '@/api/workflow';

const {
  capturedSessionChatProps,
  capturedSessionOptions,
  mockCreate,
  mockCreateAndSend,
  mockReset,
  mockSendPrompt,
  mockUseAgents,
  mockUseProviders,
  mockDefaultModelGetResolved,
  mockModelListDefinitions,
} = vi.hoisted(() => ({
  capturedSessionChatProps: [] as any[],
  capturedSessionOptions: [] as any[],
  mockCreate: vi.fn(),
  mockCreateAndSend: vi.fn(),
  mockReset: vi.fn(),
  mockSendPrompt: vi.fn(),
  mockUseAgents: vi.fn(),
  mockUseProviders: vi.fn(),
  mockDefaultModelGetResolved: vi.fn(),
  mockModelListDefinitions: vi.fn(),
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

vi.mock('@/hooks/useAgents', () => ({
  useAgents: mockUseAgents,
}));

vi.mock('@/hooks/useProviders', () => ({
  useProviders: mockUseProviders,
}));

vi.mock('@/api/provider', () => ({
  defaultModelAPI: { getResolved: mockDefaultModelGetResolved },
  modelV2API: { listDefinitions: mockModelListDefinitions },
}));

vi.mock('@/components/common/SessionChat', () => ({
  buildInstructionDisplayText: (label: string) => `@@flocks-instruction:${label}`,
  default: (props: any) => {
    capturedSessionChatProps.push(props);
    return (
      <div data-testid="session-chat">
        {props.toolbarSlot}
        {props.centerToolbarSlot}
        {props.welcomeContent}
        {props.conversationBottomSlot?.({ sendPrompt: mockSendPrompt, sending: false })}
      </div>
    );
  },
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
          'MD 文件： {{mdPath}}',
          '工作流配置引导文件： {{guidePath}}',
          '配置工作流时必须先读取 guide.md；{{configSkillName}} 只提供交互协议。',
        ].join('\n'),
        'detail.chat.inputPlaceholder': '描述你想对工作流做的修改...',
        'detail.chat.newSession': '新建会话',
        'detail.chat.historyLabel': '历史会话',
        'detail.chat.currentLabel': '当前',
        'detail.chat.welcome.title': '{{name}} 当前状态',
        'detail.chat.welcome.descPart1': '你可以直接描述需求。',
        'detail.chat.welcome.descPart2': '。',
        'detail.chat.welcome.mdTabLabel': 'workflow.md',
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
        'detail.chat.welcome.guidePrompt': '用户点击了「智能配置」按钮。请从 {{guidePath}} 获取工作流有哪些配置，包括发布配置、工作流执行配置等。工作流 ID 是 {{id}}，工作流目录是 {{dir}}。',
        'detail.chat.welcome.guideInputModeShort': '输入模式',
        'detail.chat.welcome.guideInputModeDesc': '选择 API、Syslog 或其它输入',
        'detail.chat.welcome.guideInputModeInstruction': '不要要求 guide.md 存在按钮表；请围绕输入模式自动提取引导信息并发一个 question 卡片。',
        'detail.chat.welcome.guideSourceShapeShort': '来源形态',
        'detail.chat.welcome.guideSourceShapeDesc': '确认来源产品和数据格式',
        'detail.chat.welcome.guideSourceShapeInstruction': '请围绕来源形态发一个 question 卡片。',
        'detail.chat.welcome.guideOutputShort': '输出去向',
        'detail.chat.welcome.guideOutputDesc': '确认输出位置',
        'detail.chat.welcome.guideOutputInstruction': '请围绕输出去向发一个 question 卡片。',
        'detail.chat.welcome.guideFilterShort': '过滤规则',
        'detail.chat.welcome.guideFilterDesc': '确认过滤和去重规则',
        'detail.chat.welcome.guideFilterInstruction': '请围绕过滤规则发一个 question 卡片。',
        'detail.chat.welcome.guideApplyShort': '应用方式',
        'detail.chat.welcome.guideApplyDesc': '确认应用或保存草稿',
        'detail.chat.welcome.guideApplyInstruction': '请围绕应用方式发一个 question 卡片。',
        'detail.chat.welcome.guideSampleInstruction': '请围绕样例验证发一个 question 卡片。',
        'detail.chat.welcome.guideQuestionPrompt': '用户点击了「{{focus}}」按钮。这个按钮的意图是：{{instruction}} 第一步必须读取 {{guidePath}}，不要要求 guide.md 存在按钮表，请从全文自动提取相关引导信息。工作流 ID 是 {{id}}，工作流目录是 {{dir}}。必须调用 question 工具，并提供自定义输入，没有则填 none。',
        'detail.chat.welcome.guideAuditShort': '查配置',
        'detail.chat.welcome.guideAuditDesc': '检查缺失项',
        'detail.chat.welcome.auditPrompt': '请先读取 {{guidePath}} 后检查配置。工作流 ID 是 {{id}}，工作流目录是 {{dir}}。',
        'detail.chat.welcome.guideSampleShort': '样例验证',
        'detail.chat.welcome.guideSampleDesc': '验证输入输出',
        'detail.chat.welcome.samplePrompt': '请先读取 {{guidePath}} 后验证样例。工作流 ID 是 {{id}}，工作流目录是 {{dir}}。',
      };
      return (translations[key] ?? key).replace(/{{(\w+)}}/g, (_match, name: string) => (
        params?.[name] === undefined ? '' : String(params[name])
      ));
    },
    i18n: { language: 'zh-CN' },
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

function renderChatTab(props: Partial<ComponentProps<typeof ChatTab>> = {}) {
  return render(
    <MemoryRouter>
      <ChatTab workflow={workflow} {...props} />
    </MemoryRouter>,
  );
}

describe('WorkflowDetail ChatTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    capturedSessionChatProps.length = 0;
    capturedSessionOptions.length = 0;
    localStorage.clear();
    mockCreateAndSend.mockResolvedValue(undefined);
    mockUseAgents.mockReturnValue({
      agents: [
        {
          name: 'rex',
          description: 'Rex',
          mode: 'primary',
          native: true,
          permission: [],
          options: {},
          skills: [],
          tools: [],
        },
        {
          name: 'explore',
          description: 'Explore',
          mode: 'subagent',
          native: true,
          permission: [],
          options: {},
          skills: [],
          tools: [],
        },
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    mockUseProviders.mockReturnValue({
      providers: [],
      connectedIds: [],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    mockDefaultModelGetResolved.mockResolvedValue({ data: { provider_id: '', model_id: '' } });
    mockModelListDefinitions.mockResolvedValue({ data: { models: [] } });
  });

  it('passes the explicit workflow id into the AI session context', () => {
    renderChatTab();

    expect(capturedSessionOptions[0].contextMessage).toContain('工作流 ID： stream_alert_denoise');
    expect(capturedSessionOptions[0].contextMessage).toContain('工作流目录： ~/.flocks/plugins/workflows/stream_alert_denoise/');
    expect(capturedSessionOptions[0].contextMessage).toContain('workflow.md');
    expect(capturedSessionOptions[0].contextMessage).toContain('guide.md');
    expect(capturedSessionOptions[0].contextMessage).not.toContain('workflow.edit.md');
    expect(capturedSessionOptions[0].contextMessage).toContain('workflow-config-guide');
  });

  it('includes the workflow id in workflow configuration shortcut prompts', async () => {
    const user = userEvent.setup();
    renderChatTab();

    await user.click(screen.getByRole('button', { name: /智能配置/ }));

    expect(mockSendPrompt).toHaveBeenCalledWith(
      expect.stringContaining('工作流 ID 是 stream_alert_denoise'),
      expect.objectContaining({ displayText: expect.stringContaining('智能配置') }),
    );
    expect(mockSendPrompt).toHaveBeenCalledWith(
      expect.stringContaining('~/.flocks/plugins/workflows/stream_alert_denoise/'),
      expect.objectContaining({ displayText: expect.stringContaining('智能配置') }),
    );
    expect(mockSendPrompt).toHaveBeenCalledWith(
      expect.stringContaining('~/.flocks/plugins/workflows/stream_alert_denoise/guide.md'),
      expect.objectContaining({ displayText: expect.stringContaining('智能配置') }),
    );
    expect(mockSendPrompt).toHaveBeenCalledWith(
      expect.stringContaining('用户点击了「智能配置」按钮'),
      expect.objectContaining({ displayText: expect.stringContaining('智能配置') }),
    );
    expect(mockSendPrompt).toHaveBeenCalledWith(
      expect.stringContaining('发布配置、工作流执行配置'),
      expect.objectContaining({ displayText: expect.stringContaining('智能配置') }),
    );
  });

  it('offers focused workflow configuration questions as guide shortcuts', async () => {
    const user = userEvent.setup();
    renderChatTab();

    expect(screen.getByRole('button', { name: /输入模式/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /来源形态/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /输出去向/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /过滤规则/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /应用方式/ })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /输入模式/ }));

    expect(mockSendPrompt).toHaveBeenCalledWith(
      expect.stringContaining('第一步必须读取 ~/.flocks/plugins/workflows/stream_alert_denoise/guide.md'),
      expect.objectContaining({ displayText: expect.stringContaining('输入模式') }),
    );
    expect(mockSendPrompt).toHaveBeenCalledWith(
      expect.stringContaining('用户点击了「输入模式」按钮'),
      expect.objectContaining({ displayText: expect.stringContaining('输入模式') }),
    );
    expect(mockSendPrompt).toHaveBeenCalledWith(
      expect.stringContaining('不要要求 guide.md 存在按钮表'),
      expect.objectContaining({ displayText: expect.stringContaining('输入模式') }),
    );
    expect(mockSendPrompt).toHaveBeenCalledWith(
      expect.stringContaining('必须调用 question 工具'),
      expect.objectContaining({ displayText: expect.stringContaining('输入模式') }),
    );
  });

  it('routes launch requests through the current chat instead of directly creating a new session', async () => {
    const onLaunchRequestHandled = vi.fn();

    renderChatTab({
      launchRequest: {
        id: 1,
        prompt: '请引导我配置 API 发布。',
        displayLabel: '发布为 API',
      },
      onLaunchRequestHandled,
    });

    await waitFor(() => {
      expect(mockSendPrompt).toHaveBeenCalledWith(
        '请引导我配置 API 发布。',
        expect.objectContaining({
          displayText: '@@flocks-instruction:发布为 API',
        }),
      );
    });
    expect(mockReset).not.toHaveBeenCalled();
    expect(mockCreateAndSend).not.toHaveBeenCalled();
    expect(onLaunchRequestHandled).toHaveBeenCalledWith(1);
  });

  it('shows Rex as a read-only workflow chat agent', () => {
    renderChatTab();

    expect(capturedSessionChatProps[0].agentName).toBe('rex');
    expect(capturedSessionChatProps[0].mentionAgents.map((agent: any) => agent.name)).toEqual(['rex']);
    expect(screen.getByText(/Rex/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Rex/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Explore/i })).not.toBeInTheDocument();
  });

  it('keeps the workflow composer compact enough for guide shortcuts above it', () => {
    renderChatTab();

    expect(capturedSessionChatProps[0].composerTextareaMinHeight).toBe(48);
    expect(capturedSessionChatProps[0].composerTextareaMaxHeight).toBe(120);
  });

  it('keeps workflow guide descriptions behind info tooltips', async () => {
    const user = userEvent.setup();
    renderChatTab();

    expect(screen.getByRole('button', { name: /智能配置/ })).toBeInTheDocument();
    expect(screen.queryByText('配置工作流')).not.toBeInTheDocument();

    await user.hover(screen.getByTitle('配置工作流'));

    expect(screen.getByText('配置工作流')).toBeInTheDocument();
  });

  it('refreshes after a tool finishes when workflow.md content changed without updatedAt changing', async () => {
    const updatedWorkflow = {
      ...workflow,
      updatedAt: workflow.updatedAt,
      markdownContent: '# AI edited markdown\n',
    };
    vi.mocked(workflowAPI.get).mockResolvedValueOnce({ data: updatedWorkflow } as any);
    const onWorkflowUpdated = vi.fn();

    renderChatTab({
      workflow: { ...workflow, markdownContent: '# old markdown\n' },
      onWorkflowUpdated,
    });

    capturedSessionChatProps[0].onSSEEvent({
      type: 'message.part.updated',
      properties: {
        part: {
          type: 'tool',
          tool: 'apply_patch',
          state: { status: 'completed' },
        },
      },
    });

    await waitFor(() => {
      expect(workflowAPI.get).toHaveBeenCalledWith('stream_alert_denoise');
      expect(onWorkflowUpdated).toHaveBeenCalledWith(updatedWorkflow);
    });
  });
});
