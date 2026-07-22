import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import type { ComponentProps } from 'react';

import ChatTab from './ChatTab';
import { workflowAPI } from '@/api/workflow';
import { setStoredSessions } from '../sessionStorage';
import { __resetChatModelOptionsResourcesForTesting } from '@/components/common/ChatPromptSelectors';

const {
  capturedSessionChatProps,
  capturedSessionOptions,
  mockClientGet,
  mockClientPatch,
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
  mockClientGet: vi.fn(),
  mockClientPatch: vi.fn(),
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
  default: { get: mockClientGet, patch: mockClientPatch },
}));

vi.mock('@/api/workflow', () => ({
  workflowAPI: { get: vi.fn() },
  workflowAPIEndpoints: (id: string, triggerId = '{triggerId}') => {
    const workflowBase = `/api/workflow/${id}`;
    const triggerBase = `${workflowBase}/triggers`;
    const triggerRecord = `${triggerBase}/${triggerId}`;
    return {
      config: {
        read: `GET ${workflowBase}/config`,
        write: `PUT ${workflowBase}/config`,
        syncFallback: `POST ${workflowBase}/config/sync`,
      },
      apiService: {
        read: `GET ${workflowBase}/service`,
        publish: `POST ${workflowBase}/publish`,
        unpublish: `POST ${workflowBase}/unpublish`,
        delete: `DELETE ${workflowBase}/service`,
      },
      triggers: {
        list: `GET ${triggerBase}`,
        create: `POST ${triggerBase}`,
        update: `PUT ${triggerRecord}`,
        delete: `DELETE ${triggerRecord}`,
        status: `GET ${triggerRecord}/status`,
        previewMapping: `POST ${triggerRecord}/preview-mapping`,
        test: `POST ${triggerRecord}/test`,
        invokeWebhook: `/webhook/workflows/${id}/${triggerId}`,
        plugins: 'GET /api/workflow-trigger-plugins',
      },
      legacyAdapters: {
        kafkaConfig: `GET/POST ${workflowBase}/kafka-config`,
        kafkaStatus: `GET ${workflowBase}/kafka-status`,
        pollerConfig: `GET/POST ${workflowBase}/poller-config`,
        pollerStatus: `GET ${workflowBase}/poller-status`,
        pollerRunOnce: `POST ${workflowBase}/poller-run-once`,
        syslogConfig: `GET/POST ${workflowBase}/syslog-config`,
        syslogStatus: `GET ${workflowBase}/syslog-status`,
      },
    };
  },
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
        'detail.chat.backendConfigAccessGuide': '后端配置库认证方式：使用 server_api_token，并通过 Authorization: Bearer 访问 {{configEndpoint}}；兜底迁移接口是 {{configSyncEndpoint}}。',
        'detail.chat.contextMessage': [
          '工作流 ID： {{id}}',
          '工作流名称： {{name}}',
          '工作流目录： {{dir}}',
          'MD 文件： {{mdPath}}',
          '工作流配置引导文件： {{guidePath}}',
          '前端当前 API 清单：',
          '{{apiEndpoints}}',
          '配置工作流时必须先读取 guide.md；{{configSkillName}} 只提供交互协议。',
        ].join('\n'),
        'detail.chat.inputPlaceholder': '描述你想对工作流做的修改...',
        'detail.chat.newSession': '新建会话',
        'detail.chat.historyLabel': '历史会话',
        'detail.chat.currentLabel': '当前',
        'detail.chat.welcome.title': '{{name}} 当前状态',
        'detail.chat.welcome.descPart1': '你可以直接描述需求。',
        'detail.chat.welcome.descPart2': '。',
        'detail.chat.welcome.mdTabLabel': '流程说明',
        'detail.chat.welcome.editPanelTitle': 'Rex 辅助修改',
        'detail.chat.welcome.editPanelDesc': '选择一个修改入口，Rex 会先读取 {{name}}。',
        'detail.chat.welcome.editSectionTitle': '辅助修改',
        'detail.chat.welcome.configSectionTitle': '辅助配置',
        'detail.chat.welcome.publishSectionTitle': '辅助发布',
        'detail.chat.welcome.editRequirementShort': '修改功能需求',
        'detail.chat.welcome.editRequirementDesc': '整理功能修改需求',
        'detail.chat.welcome.editRequirementPrompt': '用户点击了「修改功能需求」按钮。工作流 ID 是 {{id}}，工作流目录是 {{dir}}，MD 文件是 {{mdPath}}。',
        'detail.chat.welcome.editNodeFunctionShort': '修改节点功能',
        'detail.chat.welcome.editNodeFunctionDesc': '调整节点做什么',
        'detail.chat.welcome.editNodeFunctionPrompt': '用户点击了「修改节点功能」按钮。工作流 ID 是 {{id}}，工作流目录是 {{dir}}，MD 文件是 {{mdPath}}。',
        'detail.chat.welcome.editNodeShort': '编辑节点实现',
        'detail.chat.welcome.editNodeDesc': '调整节点代码和连接',
        'detail.chat.welcome.editNodePrompt': '用户点击了「编辑节点实现」按钮。工作流 ID 是 {{id}}，工作流目录是 {{dir}}，MD 文件是 {{mdPath}}。',
        'detail.chat.welcome.editFlowShort': '调整流程结构',
        'detail.chat.welcome.editFlowDesc': '调整节点和边',
        'detail.chat.welcome.editFlowPrompt': '用户点击了「调整流程结构」按钮。工作流 ID 是 {{id}}，工作流目录是 {{dir}}，MD 文件是 {{mdPath}}。',
        'detail.chat.welcome.editRegenerateShort': '生成工作流',
        'detail.chat.welcome.editRegenerateDesc': '基于 workflow.md 生成 workflow.json',
        'detail.chat.welcome.editRegeneratePrompt': '用户点击了「生成工作流」按钮。基于 workflow.md 生成 workflow.json。工作流 ID 是 {{id}}，工作流目录是 {{dir}}，MD 文件是 {{mdPath}}。',
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
        'detail.chat.welcome.guidePrimaryShort': '帮我智能配置',
        'detail.chat.welcome.guidePrimaryDesc': '配置工作流',
        'detail.chat.welcome.guidePrompt': '用户点击了「帮我智能配置」按钮。请从 {{guidePath}} 获取工作流有哪些配置，包括发布配置、工作流执行配置等。工作流 ID 是 {{id}}，工作流目录是 {{dir}}。配置模板接口是 GET/PUT {{configEndpoint}}，兜底迁移接口是 {{configSyncEndpoint}}。前端当前 API 清单：{{apiEndpoints}}。config.json 和 workflow.json 只能作为迁移兜底；后端接口不可用时必须停止配置流程。',
        'detail.chat.welcome.guideInputModeShort': '配置输入方式',
        'detail.chat.welcome.guideInputModeDesc': '选择 API、Syslog 或其它输入',
        'detail.chat.welcome.guideInputModeInstruction': '不要要求 guide.md 存在按钮表；请围绕输入模式自动提取引导信息并发一个 question 卡片。',
        'detail.chat.welcome.guideSourceShapeShort': '确认来源数据',
        'detail.chat.welcome.guideSourceShapeDesc': '确认来源产品和数据格式',
        'detail.chat.welcome.guideSourceShapeInstruction': '请围绕来源形态发一个 question 卡片。',
        'detail.chat.welcome.guideOutputShort': '设置输出去向',
        'detail.chat.welcome.guideOutputDesc': '确认输出位置',
        'detail.chat.welcome.guideOutputInstruction': '请围绕输出去向发一个 question 卡片。',
        'detail.chat.welcome.guideFilterShort': '调整过滤规则',
        'detail.chat.welcome.guideFilterDesc': '确认过滤和去重规则',
        'detail.chat.welcome.guideFilterInstruction': '请围绕过滤规则发一个 question 卡片。',
        'detail.chat.welcome.guideApplyShort': '应用配置方案',
        'detail.chat.welcome.guideApplyDesc': '确认应用或保存草稿',
        'detail.chat.welcome.guideApplyInstruction': '请围绕应用方式发一个 question 卡片。',
        'detail.chat.welcome.guideSampleInstruction': '请围绕样例验证发一个 question 卡片。',
        'detail.chat.welcome.guideQuestionPrompt': '用户点击了「{{focus}}」按钮。这个按钮的意图是：{{instruction}} 第一步必须读取 {{guidePath}}，不要要求 guide.md 存在按钮表，请从全文自动提取相关引导信息。工作流 ID 是 {{id}}，工作流目录是 {{dir}}。配置模板接口是 GET/PUT {{configEndpoint}}，兜底迁移接口是 {{configSyncEndpoint}}。前端当前 API 清单：{{apiEndpoints}}。config.json 和 workflow.json 不能直接写。后端接口不可用时必须停止配置流程，不要追问用户要对 workflow.json 模板触发器做什么。必须调用 question 工具，并提供自定义输入，没有则填 none。',
        'detail.chat.welcome.guideAuditShort': '检查当前配置',
        'detail.chat.welcome.guideAuditDesc': '检查缺失项',
        'detail.chat.welcome.auditPrompt': '请先读取 {{guidePath}} 后检查配置。工作流 ID 是 {{id}}，工作流目录是 {{dir}}。',
        'detail.chat.welcome.guideSampleShort': '验证样例数据',
        'detail.chat.welcome.guideSampleDesc': '验证输入输出',
        'detail.chat.welcome.samplePrompt': '请先读取 {{guidePath}} 后验证样例。工作流 ID 是 {{id}}，工作流目录是 {{dir}}。',
        'detail.run.guideApiShort': '发布为 API',
        'detail.run.guideApiDesc': '配置 API 发布',
        'detail.run.guideApiInstruction': '围绕 API 发布读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 和 workflow.json 不是直接写入目标；后端接口不可用时必须停止配置流程',
        'detail.run.guideSyslogShort': 'Syslog 接入',
        'detail.run.guideSyslogDesc': '配置 Syslog 接入',
        'detail.run.guideSyslogInstruction': '围绕 Syslog 接入读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 和 workflow.json 不是直接写入目标；后端接口不可用时必须停止配置流程',
        'detail.run.guideKafkaShort': 'Kafka 接入',
        'detail.run.guideKafkaDesc': '配置 Kafka 接入',
        'detail.run.guideKafkaInstruction': '围绕 Kafka 接入读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 和 workflow.json 不是直接写入目标；后端接口不可用时必须停止配置流程',
        'detail.run.guideWebhookShort': 'Webhook 接入',
        'detail.run.guideWebhookDesc': '配置 Webhook 接入',
        'detail.run.guideWebhookInstruction': '围绕 Webhook 接入读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 和 workflow.json 不是直接写入目标；后端接口不可用时必须停止配置流程',
        'detail.run.guideScheduleShort': '定时触发',
        'detail.run.guideScheduleDesc': '配置定时触发',
        'detail.run.guideScheduleInstruction': '围绕定时触发读取 guide.md，先 GET {{configEndpoint}}，确认后 PUT {{configEndpoint}}，config.json 和 workflow.json 不是直接写入目标；后端接口不可用时必须停止配置流程',
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
    __resetChatModelOptionsResourcesForTesting();
    capturedSessionChatProps.length = 0;
    capturedSessionOptions.length = 0;
    localStorage.clear();
    mockClientGet.mockResolvedValue({ data: {} });
    mockClientPatch.mockResolvedValue({ data: {} });
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
    expect(capturedSessionOptions[0].contextMessage).toContain('前端当前 API 清单');
    expect(capturedSessionOptions[0].contextMessage).toContain('GET /api/workflow/stream_alert_denoise/service');
    expect(capturedSessionOptions[0].contextMessage).toContain('DELETE /api/workflow/stream_alert_denoise/triggers/{triggerId}');
    expect(capturedSessionOptions[0].contextMessage).toContain('server_api_token');
    expect(capturedSessionOptions[0].contextMessage).toContain('Authorization: Bearer');
  });

  it('includes the workflow id in workflow configuration shortcut prompts', async () => {
    const user = userEvent.setup();
    renderChatTab();

    await user.click(screen.getByRole('button', { name: /帮我智能配置/ }));

    await waitFor(() => {
      expect(mockCreateAndSend).toHaveBeenCalledWith(
        expect.objectContaining({
          text: expect.stringContaining('工作流 ID 是 stream_alert_denoise'),
          displayText: '@@flocks-instruction:帮我智能配置',
        }),
      );
    });
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('~/.flocks/plugins/workflows/stream_alert_denoise/'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('~/.flocks/plugins/workflows/stream_alert_denoise/guide.md'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('用户点击了「帮我智能配置」按钮'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('发布配置、工作流执行配置'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('/api/workflow/stream_alert_denoise/config'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('server_api_token'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('Authorization: Bearer'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('GET /api/workflow/stream_alert_denoise/service'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('DELETE /api/workflow/stream_alert_denoise/triggers/{triggerId}'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('config.json 和 workflow.json 只能作为迁移兜底'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('后端接口不可用时必须停止配置流程'),
      }),
    );
  });

  it('offers focused workflow configuration questions as guide shortcuts', async () => {
    const user = userEvent.setup();
    renderChatTab();

    expect(screen.getByRole('button', { name: /配置输入方式/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /确认来源数据/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /设置输出去向/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /调整过滤规则/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /应用配置方案/ })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /配置输入方式/ }));

    await waitFor(() => {
      expect(mockCreateAndSend).toHaveBeenCalledWith(
        expect.objectContaining({
          text: expect.stringContaining('第一步必须读取 ~/.flocks/plugins/workflows/stream_alert_denoise/guide.md'),
          displayText: '@@flocks-instruction:配置输入方式',
        }),
      );
    });
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('用户点击了「配置输入方式」按钮'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('不要要求 guide.md 存在按钮表'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('必须调用 question 工具'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('server_api_token'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('config.json 和 workflow.json 不能直接写'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('不要追问用户要对 workflow.json 模板触发器做什么'),
      }),
    );
  });

  it('offers workflow editing guides in the empty workbench', async () => {
    const user = userEvent.setup();
    renderChatTab();

    expect(screen.getByText('Rex 辅助修改')).toBeInTheDocument();
    expect(screen.getByText('辅助修改')).toBeInTheDocument();
    expect(screen.getByText('辅助配置')).toBeInTheDocument();
    expect(screen.getByText('辅助发布')).toBeInTheDocument();
    expect(screen.getByTestId('workflow-edit-guide-scroll')).toHaveClass('overflow-y-auto');
    expect(screen.getByRole('button', { name: /修改功能需求/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /修改节点功能/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /编辑节点实现/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /调整流程结构/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /生成工作流/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /发布为 API/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Syslog 接入/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Kafka 接入/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Webhook 接入/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /定时触发/ })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /修改节点功能/ }));

    await waitFor(() => {
      expect(mockCreateAndSend).toHaveBeenCalledWith(
        expect.objectContaining({
          text: expect.stringContaining('用户点击了「修改节点功能」按钮'),
          displayText: '@@flocks-instruction:修改节点功能',
        }),
      );
    });
  });

  it('offers publish guide shortcuts from the workflow workbench', async () => {
    const user = userEvent.setup();
    renderChatTab();

    await user.click(screen.getByRole('button', { name: /发布为 API/ }));

    await waitFor(() => {
      expect(mockCreateAndSend).toHaveBeenCalledWith(
        expect.objectContaining({
          text: expect.stringContaining('用户点击了「发布为 API」按钮'),
          displayText: '@@flocks-instruction:发布为 API',
        }),
      );
    });
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('围绕 API 发布读取 guide.md'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('/api/workflow/stream_alert_denoise/config'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('Authorization: Bearer'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('POST /api/workflow/stream_alert_denoise/publish'),
      }),
    );
    expect(mockCreateAndSend).toHaveBeenCalledWith(
      expect.objectContaining({
        text: expect.stringContaining('后端接口不可用时必须停止配置流程'),
      }),
    );
  });

  it('routes launch requests through the current chat instead of directly creating a new session', async () => {
    const onLaunchRequestHandled = vi.fn();
    setStoredSessions(workflow.id, [
      { id: 'existing-workflow-session', title: 'Existing', createdAt: Date.now() },
    ]);

    renderChatTab({
      launchRequest: {
        id: 1,
        prompt: '请引导我配置 API 发布。',
        displayLabel: '发布为 API',
      },
      onLaunchRequestHandled,
    });

    await waitFor(() => {
      expect(capturedSessionChatProps[capturedSessionChatProps.length - 1]?.sessionId).toBe('existing-workflow-session');
      expect(mockSendPrompt).toHaveBeenCalledWith(
        '请引导我配置 API 发布。',
        expect.objectContaining({
          displayText: '@@flocks-instruction:发布为 API',
        }),
      );
    });
    expect(mockClientGet).toHaveBeenCalledWith('/api/session/existing-workflow-session');
    expect(mockReset).not.toHaveBeenCalled();
    expect(mockCreateAndSend).not.toHaveBeenCalled();
    expect(onLaunchRequestHandled).toHaveBeenCalledWith(1);
  });

  it('shows Rex as a read-only workflow chat agent', () => {
    renderChatTab();

    expect(capturedSessionChatProps[0].agentName).toBe('rex');
    expect(capturedSessionChatProps[0].mentionAgents.map((agent: any) => agent.name)).toEqual(['rex']);
    expect(capturedSessionChatProps[0].display).toEqual({
      collapseIntermediateSteps: true,
      processGroupsDefaultOpen: false,
    });
    expect(screen.getAllByText(/Rex/i).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: /Rex/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Explore/i })).not.toBeInTheDocument();
  });

  it('hydrates the workflow workbench model picker from the active session pinned model', async () => {
    setStoredSessions(workflow.id, [
      { id: 'workflow-session-kimi', title: 'Existing', createdAt: Date.now() },
    ]);
    mockUseProviders.mockReturnValue({
      providers: [{ id: 'moonshot', name: 'Moonshot', configured: true }],
      connectedIds: ['moonshot'],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    mockModelListDefinitions.mockResolvedValue({
      data: {
        models: [{
          provider_id: 'moonshot',
          id: 'kimi-k2.6',
          name: 'kimi-k2.6',
          model_type: 'chat',
          capabilities: { supports_vision: false },
          limits: { context_window: 128000 },
        }],
      },
    });
    mockClientGet.mockResolvedValue({
      data: {
        id: 'workflow-session-kimi',
        provider: 'moonshot',
        model: 'kimi-k2.6',
        model_pinned: true,
      },
    });

    renderChatTab();

    await waitFor(() => {
      const latestProps = capturedSessionChatProps[capturedSessionChatProps.length - 1];
      expect(latestProps.sessionId).toBe('workflow-session-kimi');
      expect(latestProps.model).toEqual({ providerID: 'moonshot', modelID: 'kimi-k2.6' });
    });
    expect(await screen.findByText('kimi-k2.6')).toBeInTheDocument();
  });

  it('falls back consistently when the active session pinned model is no longer available', async () => {
    setStoredSessions(workflow.id, [
      { id: 'workflow-session-stale-model', title: 'Existing', createdAt: Date.now() },
    ]);
    mockUseProviders.mockReturnValue({
      providers: [{ id: 'minimax', name: 'MiniMax', configured: true }],
      connectedIds: ['minimax'],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    mockModelListDefinitions.mockResolvedValue({
      data: {
        models: [{
          provider_id: 'minimax',
          id: 'minimax-m3',
          name: 'minimax-m3',
          model_type: 'chat',
          capabilities: { supports_vision: false },
          limits: { context_window: 64000 },
        }],
      },
    });
    mockClientGet.mockResolvedValue({
      data: {
        id: 'workflow-session-stale-model',
        provider: 'moonshot',
        model: 'kimi-k2.6',
        model_pinned: true,
      },
    });

    renderChatTab();

    await waitFor(() => {
      const latestProps = capturedSessionChatProps[capturedSessionChatProps.length - 1];
      expect(latestProps.sessionId).toBe('workflow-session-stale-model');
      expect(latestProps.model).toEqual({ providerID: 'minimax', modelID: 'minimax-m3' });
    });
    expect(await screen.findByText('minimax-m3')).toBeInTheDocument();
  });

  it('pins model picker changes back to the active workflow chat session', async () => {
    const user = userEvent.setup();
    setStoredSessions(workflow.id, [
      { id: 'workflow-session-model-switch', title: 'Existing', createdAt: Date.now() },
    ]);
    mockUseProviders.mockReturnValue({
      providers: [
        { id: 'minimax', name: 'MiniMax', configured: true },
        { id: 'moonshot', name: 'Moonshot', configured: true },
      ],
      connectedIds: ['minimax', 'moonshot'],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    mockModelListDefinitions.mockResolvedValue({
      data: {
        models: [
          {
            provider_id: 'moonshot',
            id: 'kimi-k2.6',
            name: 'kimi-k2.6',
            model_type: 'chat',
            capabilities: { supports_vision: false },
            limits: { context_window: 128000 },
          },
          {
            provider_id: 'minimax',
            id: 'minimax-m3',
            name: 'minimax-m3',
            model_type: 'chat',
            capabilities: { supports_vision: false },
            limits: { context_window: 64000 },
          },
        ],
      },
    });
    mockClientGet.mockResolvedValue({
      data: {
        id: 'workflow-session-model-switch',
        provider: 'moonshot',
        model: 'kimi-k2.6',
        model_pinned: true,
      },
    });

    renderChatTab();

    await user.click(await screen.findByRole('button', { name: /kimi-k2\.6/i }));
    await user.click(await screen.findByRole('button', { name: /minimax-m3/i }));

    expect(mockClientPatch).toHaveBeenCalledWith('/api/session/workflow-session-model-switch', {
      provider: 'minimax',
      model: 'minimax-m3',
      model_pinned: true,
    });
    await waitFor(() => {
      const latestProps = capturedSessionChatProps[capturedSessionChatProps.length - 1];
      expect(latestProps.model).toEqual({ providerID: 'minimax', modelID: 'minimax-m3' });
    });
  });

  it('keeps the workflow composer compact enough for guide shortcuts above it', () => {
    renderChatTab();

    expect(capturedSessionChatProps[0].composerTextareaMinHeight).toBe(48);
    expect(capturedSessionChatProps[0].composerTextareaMaxHeight).toBe(120);
  });

  it('keeps workflow guide descriptions behind info tooltips', async () => {
    const user = userEvent.setup();
    renderChatTab();

    expect(screen.getByRole('button', { name: /帮我智能配置/ })).toBeInTheDocument();
    expect(screen.queryByText('配置工作流')).not.toBeInTheDocument();

    const infoIcon = screen.getAllByRole('img', { name: '帮我智能配置说明' })[0];
    await user.hover(infoIcon);

    expect(await screen.findByText('配置工作流')).toBeInTheDocument();

    await user.unhover(infoIcon);
    await waitFor(() => {
      expect(screen.queryByText('配置工作流')).not.toBeInTheDocument();
    });
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
