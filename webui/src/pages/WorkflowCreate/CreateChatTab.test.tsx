import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';

import CreateChatTab from './CreateChatTab';
import { workflowAPI } from '@/api/workflow';

const {
  capturedSessionChatProps,
  capturedSessionOptions,
  capturedModelPickerProps,
  mockCreateAndSend,
  mockClientGet,
  mockClientPatch,
  mockSendPrompt,
  mockSetSelectedModelAuto,
  mockSetSelectedModelKey,
  workflowModelState,
} = vi.hoisted(() => ({
  capturedSessionChatProps: [] as any[],
  capturedSessionOptions: [] as any[],
  capturedModelPickerProps: [] as any[],
  mockCreateAndSend: vi.fn(),
  mockClientGet: vi.fn(),
  mockClientPatch: vi.fn(),
  mockSendPrompt: vi.fn(),
  mockSetSelectedModelAuto: vi.fn(),
  mockSetSelectedModelKey: vi.fn(),
  workflowModelState: { auto: false },
}));

const selectedPromptModel = { providerID: 'provider-1', modelID: 'model-1' };
const selectedModelOption = {
  key: 'provider-1:model-1',
  providerID: 'provider-1',
  providerName: 'Provider',
  modelID: 'model-1',
  label: 'model-1',
  pricingLabel: '',
  contextLabel: '',
  contextWindowTokens: 128000,
  supportsVision: true,
};

vi.mock('@/hooks/useDefaultModelVision', () => ({
  useDefaultModelVision: () => false,
}));

vi.mock('@/hooks/useSessionChat', () => ({
  useSessionChat: (options: any) => {
    capturedSessionOptions.push(options);
    return {
      sessionId: options.initialSessionId ?? null,
      error: null,
      createAndSend: mockCreateAndSend,
      retry: vi.fn(),
    };
  },
}));

vi.mock('@/api/client', () => ({
  default: { get: mockClientGet, patch: mockClientPatch },
}));

vi.mock('@/api/workflow', () => ({
  workflowAPI: {
    list: vi.fn().mockResolvedValue({ data: [] }),
    get: vi.fn().mockResolvedValue({ data: null }),
  },
}));

vi.mock('@/components/common/ChatPromptSelectors', () => ({
  useChatAgentOptions: () => ({
    agents: [{ name: 'rex', description: 'Rex', mode: 'primary', native: true }],
    loading: false,
  }),
  useChatModelOptions: () => ({
    groupedOptions: [],
    loading: false,
    effectiveModelOption: selectedModelOption,
    modelPickerAutoOption: {
      selected: workflowModelState.auto,
      disabled: false,
      statusLabel: 'Auto',
      onSelect: () => { workflowModelState.auto = true; },
    },
    selectModelKey: (key: string) => {
      workflowModelState.auto = false;
      mockSetSelectedModelKey(key);
    },
    selectedModelAuto: workflowModelState.auto,
    selectedModelOption,
    selectedPromptModel: workflowModelState.auto ? null : selectedPromptModel,
    setSelectedModelAuto: mockSetSelectedModelAuto,
    setSelectedModelKey: mockSetSelectedModelKey,
  }),
  ChatAgentDisplay: ({ selectedAgent }: { selectedAgent: string }) => (
    <div>Agent:{selectedAgent}</div>
  ),
  ChatModelPicker: (props: any) => {
    capturedModelPickerProps.push(props);
    return <div>ModelPicker</div>;
  },
}));

vi.mock('@/components/common/SessionChat', () => ({
  buildInstructionDisplayText: (label: string) => `@@flocks-instruction:${label}`,
  default: (props: any) => {
    capturedSessionChatProps.push(props);
    return (
      <div data-testid="session-chat">
        {typeof props.welcomeContent === 'function'
          ? props.welcomeContent(vi.fn())
          : props.welcomeContent}
        {props.toolbarSlot}
        {props.centerToolbarSlot}
        {props.conversationBottomSlot?.({ sendPrompt: mockSendPrompt, sending: false })}
      </div>
    );
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      const translations: Record<string, any> = {
        'create.chat.sessionTitle': '新建工作流',
        'create.chat.inputPlaceholder': '描述您想创建的工作流...',
        'create.chat.contextMessage': '用户希望创建一个 Flocks 工作流，请使用 workflow-builder skill 来完成。',
        'create.chat.welcomeMessage': '欢迎创建工作流',
        'create.chat.emptyStateTitle': '暂无执行记录',
        'create.chat.guidePanelTitle': 'Rex 辅助创建',
        'create.chat.guidePanelDesc': '选择一个引导或创建案例，Rex 会按 workflow-builder skill 先确认场景。',
        'create.chat.guideSectionTitle': '创建引导',
        'create.chat.caseSectionTitle': '创建案例',
        'create.chat.guideActions': [
          {
            label: '如何创建工作流',
            description: '从零开始梳理业务目标、输入输出、节点流程、样例和生成步骤。',
            prompt: '请按 workflow-builder skill 引导我从零创建一个 Flocks 工作流。',
          },
          {
            label: '编辑工作流节点',
            description: '调整节点职责、输入输出、代码或连接关系。',
            prompt: '请帮我编辑工作流节点。',
          },
        ],
        'create.chat.exampleQuestions': [
          '帮我创建一个 IP 威胁情报查询工作流，输入 IP 地址，查询多个情报源并汇总生成报告',
          '创建一个域名分析工作流，对域名进行 WHOIS 查询、DNS 解析和历史记录交叉分析',
        ],
        'create.chat.exampleQuestionLabels': [
          '创建 IP 情报工作流',
          '创建域名分析工作流',
        ],
        'detail.chat.welcome.guideCollapse': '收起',
        'detail.chat.welcome.guideExpand': '展开',
      };
      const value = translations[key] ?? key;
      return options?.returnObjects ? value : String(value);
    },
  }),
}));

function renderCreateChatTab(props?: Partial<ComponentProps<typeof CreateChatTab>>) {
  return render(
    <MemoryRouter>
      <CreateChatTab onWorkflowCreated={vi.fn()} {...props} />
    </MemoryRouter>,
  );
}

describe('WorkflowCreate CreateChatTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    capturedSessionChatProps.length = 0;
    capturedSessionOptions.length = 0;
    capturedModelPickerProps.length = 0;
    workflowModelState.auto = false;
    mockSetSelectedModelAuto.mockImplementation((enabled: boolean) => {
      workflowModelState.auto = enabled;
    });
    mockCreateAndSend.mockResolvedValue('session-1');
    mockClientGet.mockResolvedValue({ data: {} });
    mockClientPatch.mockResolvedValue({ data: {} });
    vi.mocked(workflowAPI.list).mockResolvedValue({ data: [] });
    vi.mocked(workflowAPI.get).mockResolvedValue({ data: null as any });
  });

  it('renders creation guides and case examples in a centered guide panel', async () => {
    const user = userEvent.setup();
    renderCreateChatTab();

    expect(capturedSessionChatProps[0].suggestions).toBeUndefined();
    expect(screen.getByText('暂无执行记录')).toBeInTheDocument();
    expect(screen.getByText('Rex 辅助创建')).toBeInTheDocument();
    expect(screen.getByText('创建引导')).toBeInTheDocument();
    expect(screen.getByText('创建案例')).toBeInTheDocument();
    expect(screen.getByTestId('create-workflow-guide-scroll')).toHaveClass('overflow-y-auto');
    expect(screen.getByRole('button', { name: /如何创建工作流/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /编辑工作流节点/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /创建 IP 情报工作流/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /创建域名分析工作流/ })).toBeInTheDocument();
    expect(screen.queryByText('帮我创建一个 IP 威胁情报查询工作流，输入 IP 地址，查询多个情报源并汇总生成报告')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /如何创建工作流/ }));

    await waitFor(() => {
      expect(mockCreateAndSend).toHaveBeenCalledWith({
        text: '请按 workflow-builder skill 引导我从零创建一个 Flocks 工作流。',
        imageParts: [],
        agent: 'rex',
        model: selectedPromptModel,
        modelAuto: false,
        displayText: '@@flocks-instruction:如何创建工作流',
      });
    });
    expect(mockSendPrompt).not.toHaveBeenCalled();
  });

  it('shows the guide dock above the composer once creation starts', () => {
    renderCreateChatTab();

    const slot = capturedSessionChatProps[0].conversationBottomSlot?.({
      sendPrompt: mockSendPrompt,
      setInput: vi.fn(),
      focusInput: vi.fn(),
      sending: true,
      streaming: false,
      sessionId: null,
    });
    const { container } = render(<>{slot}</>);

    expect(within(container).getByRole('button', { name: /如何创建工作流/ })).toBeDisabled();
    expect(within(container).getByRole('button', { name: /创建 IP 情报工作流/ })).toBeDisabled();
  });

  it('reuses the workflow workbench chat controls', async () => {
    renderCreateChatTab();

    expect(screen.getByText('Agent:rex')).toBeInTheDocument();
    expect(screen.getByText('ModelPicker')).toBeInTheDocument();
    expect(capturedSessionChatProps[0].agentName).toBe('rex');
    expect(capturedSessionChatProps[0].mentionAgents.map((agent: any) => agent.name)).toEqual(['rex']);
    expect(capturedSessionChatProps[0].model).toEqual(selectedPromptModel);
    expect(capturedSessionChatProps[0].modelAuto).toBe(false);
    expect(capturedSessionChatProps[0].supportsVision).toBe(true);
    expect(capturedSessionChatProps[0].contextWindowTokens).toBe(128000);
    expect(capturedSessionChatProps[0].display).toEqual({
      collapseIntermediateSteps: true,
      processGroupsDefaultOpen: false,
    });
    expect(capturedSessionChatProps[0].composerTextareaMinHeight).toBe(48);
    expect(capturedSessionChatProps[0].composerTextareaMaxHeight).toBe(120);

    await capturedSessionChatProps[0].onCreateAndSend(
      '创建一个工作流',
      [],
      undefined,
      undefined,
      { displayText: '@@flocks-instruction:IP 情报' },
    );

    await waitFor(() => {
      expect(mockCreateAndSend).toHaveBeenCalledWith({
        text: '创建一个工作流',
        imageParts: [],
        agent: 'rex',
        model: selectedPromptModel,
        modelAuto: false,
        displayText: '@@flocks-instruction:IP 情报',
      });
    });
    expect(capturedSessionOptions[0]).toEqual(expect.objectContaining({
      category: 'workflow',
      modelAuto: false,
    }));
    expect(capturedModelPickerProps[0].autoOption).toBeDefined();
  });

  it('uses Auto only after manual selection and sends no concrete model', async () => {
    const view = renderCreateChatTab();

    act(() => {
      capturedModelPickerProps[capturedModelPickerProps.length - 1].autoOption.onSelect();
    });
    view.rerender(
      <MemoryRouter>
        <CreateChatTab onWorkflowCreated={vi.fn()} />
      </MemoryRouter>,
    );

    const latestProps = capturedSessionChatProps[capturedSessionChatProps.length - 1];
    expect(latestProps.model).toBeNull();
    expect(latestProps.modelAuto).toBe(true);
    expect(latestProps.supportsVision).toBe(true);
    expect(capturedSessionOptions[capturedSessionOptions.length - 1].modelAuto).toBe(true);

    await latestProps.onCreateAndSend('创建一个工作流', []);

    expect(mockCreateAndSend).toHaveBeenLastCalledWith({
      text: '创建一个工作流',
      imageParts: [],
      agent: 'rex',
      model: null,
      modelAuto: true,
      displayText: undefined,
    });
  });

  it('resumes a persisted create session', () => {
    const onSessionChange = vi.fn();
    renderCreateChatTab({
      initialSessionId: 'session-restored',
      onSessionChange,
    });

    expect(capturedSessionChatProps[0].sessionId).toBe('session-restored');
    expect(capturedSessionChatProps[0].welcomeContent).toBeUndefined();
    expect(onSessionChange).toHaveBeenCalledWith('session-restored');
  });

  it('restores Auto from a persisted create session', async () => {
    mockClientGet.mockResolvedValueOnce({ data: { model_auto: true } });

    renderCreateChatTab({ initialSessionId: 'session-auto' });

    await waitFor(() => {
      expect(mockSetSelectedModelAuto).toHaveBeenCalledWith(true);
      const latestProps = capturedSessionChatProps[capturedSessionChatProps.length - 1];
      expect(latestProps.model).toBeNull();
      expect(latestProps.modelAuto).toBe(true);
    });
  });

  it('persists picker changes for a resumed create session', async () => {
    renderCreateChatTab({ initialSessionId: 'session-picker' });

    await waitFor(() => {
      expect(mockClientGet).toHaveBeenCalledWith('/api/session/session-picker');
    });
    act(() => {
      capturedModelPickerProps[capturedModelPickerProps.length - 1].autoOption.onSelect();
    });
    expect(mockClientPatch).toHaveBeenCalledWith('/api/session/session-picker', {
      model_auto: true,
      model_pinned: false,
    });

    act(() => {
      capturedModelPickerProps[capturedModelPickerProps.length - 1]
        .onSelectModel(selectedModelOption);
    });
    expect(mockClientPatch).toHaveBeenCalledWith('/api/session/session-picker', {
      provider: 'provider-1',
      model: 'model-1',
      model_pinned: true,
      model_auto: false,
    });
  });

  it('does not attach an already-known workflow just because it was created recently', async () => {
    const creationStartedAt = Date.now();
    const recentKnownWorkflow = {
      id: 'previous-workflow',
      name: 'Previous Workflow',
      workflowJson: { start: 'n1', nodes: [], edges: [] },
      status: 'active',
      source: 'global',
      createdAt: creationStartedAt - 1000,
      updatedAt: creationStartedAt - 1000,
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
    vi.mocked(workflowAPI.list).mockResolvedValue({ data: [recentKnownWorkflow] });
    const onWorkflowCreated = vi.fn();

    renderCreateChatTab({
      initialSessionId: 'session-active',
      creationStartedAt,
      onWorkflowCreated,
    });

    await waitFor(() => {
      expect(workflowAPI.list).toHaveBeenCalledTimes(1);
    });

    capturedSessionChatProps[capturedSessionChatProps.length - 1]?.onStreamingDone?.();

    await waitFor(() => {
      expect(workflowAPI.list).toHaveBeenCalledTimes(2);
    });
    expect(onWorkflowCreated).not.toHaveBeenCalled();
  });

  it('attaches the workflow identified by a workflow.created SSE event', async () => {
    const creationStartedAt = Date.now();
    const createdWorkflow = {
      id: 'created-by-this-event',
      name: 'Created By This Event',
      workflowJson: { start: 'n1', nodes: [], edges: [] },
      status: 'active',
      source: 'global',
      createdAt: creationStartedAt + 100,
      updatedAt: creationStartedAt + 100,
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
    vi.mocked(workflowAPI.get).mockResolvedValue({ data: createdWorkflow });
    const onWorkflowCreated = vi.fn();

    renderCreateChatTab({
      initialSessionId: 'session-active',
      creationStartedAt,
      onWorkflowCreated,
    });

    await waitFor(() => {
      expect(workflowAPI.list).toHaveBeenCalledTimes(1);
    });

    capturedSessionChatProps[capturedSessionChatProps.length - 1]?.onSSEEvent?.({
      type: 'workflow.created',
      properties: { id: 'created-by-this-event' },
    });

    await waitFor(() => {
      expect(workflowAPI.get).toHaveBeenCalledWith('created-by-this-event');
    });
    expect(onWorkflowCreated).toHaveBeenCalledWith(createdWorkflow);
  });

  it('replays workflow.created events that arrive before the initial snapshot is ready', async () => {
    const creationStartedAt = Date.now();
    const createdWorkflow = {
      id: 'created-before-snapshot-ready',
      name: 'Created Before Snapshot Ready',
      workflowJson: { start: 'n1', nodes: [], edges: [] },
      status: 'active',
      source: 'global',
      createdAt: creationStartedAt + 100,
      updatedAt: creationStartedAt + 100,
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
    let resolveSnapshot: ((value: { data: typeof createdWorkflow[] }) => void) | undefined;
    vi.mocked(workflowAPI.list).mockReturnValueOnce(new Promise((resolve) => {
      resolveSnapshot = resolve;
    }) as any);
    vi.mocked(workflowAPI.get).mockResolvedValue({ data: createdWorkflow });
    const onWorkflowCreated = vi.fn();

    renderCreateChatTab({
      initialSessionId: 'session-active',
      creationStartedAt,
      onWorkflowCreated,
    });

    capturedSessionChatProps[capturedSessionChatProps.length - 1]?.onSSEEvent?.({
      type: 'workflow.created',
      properties: { id: 'created-before-snapshot-ready' },
    });

    expect(workflowAPI.get).not.toHaveBeenCalled();

    await act(async () => {
      resolveSnapshot?.({ data: [createdWorkflow] });
    });

    await waitFor(() => {
      expect(workflowAPI.get).toHaveBeenCalledWith('created-before-snapshot-ready');
    });
    expect(onWorkflowCreated).toHaveBeenCalledWith(createdWorkflow);
  });

  it('does not guess when fallback polling sees multiple fresh workflows', async () => {
    const creationStartedAt = Date.now();
    const makeWorkflow = (id: string) => ({
      id,
      name: id,
      workflowJson: { start: 'n1', nodes: [], edges: [] },
      status: 'active',
      source: 'global',
      createdAt: creationStartedAt + 100,
      updatedAt: creationStartedAt + 100,
      stats: {
        callCount: 0,
        successCount: 0,
        errorCount: 0,
        totalRuntime: 0,
        avgRuntime: 0,
        thumbsUp: 0,
        thumbsDown: 0,
      },
    });
    vi.mocked(workflowAPI.list)
      .mockResolvedValueOnce({ data: [] })
      .mockResolvedValueOnce({ data: [makeWorkflow('first'), makeWorkflow('second')] });
    const onWorkflowCreated = vi.fn();

    renderCreateChatTab({
      initialSessionId: 'session-active',
      creationStartedAt,
      onWorkflowCreated,
    });

    await waitFor(() => {
      expect(workflowAPI.list).toHaveBeenCalledTimes(1);
    });

    capturedSessionChatProps[capturedSessionChatProps.length - 1]?.onStreamingDone?.();

    await waitFor(() => {
      expect(workflowAPI.list).toHaveBeenCalledTimes(2);
    });
    expect(onWorkflowCreated).not.toHaveBeenCalled();
  });
});
