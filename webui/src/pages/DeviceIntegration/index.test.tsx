import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import DeviceIntegrationPage from './index';

const mocks = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  toastInfo: vi.fn(),
  navigate: vi.fn(),
  createAndSend: vi.fn().mockResolvedValue('session-1'),
  useSessionChatOptions: vi.fn(),
  sessionId: null as string | null,
  resetSession: vi.fn(),
  listDevices: vi.fn(),
  syncDevices: vi.fn(),
  getDevice: vi.fn(),
  listGroups: vi.fn(),
  createGroup: vi.fn(),
  updateGroup: vi.fn(),
  deleteGroup: vi.fn(),
  createDevice: vi.fn(),
  updateDevice: vi.fn(),
  deleteDevice: vi.fn(),
  testDevice: vi.fn(),
  revealDeviceCredentials: vi.fn(),
  listDeviceTools: vi.fn(),
  updateDeviceTool: vi.fn(),
  listTemplates: vi.fn(),
  hubInstall: vi.fn(),
  hubUpdate: vi.fn(),
  getServiceMetadata: vi.fn(),
  listTools: vi.fn(),
  setToolEnabled: vi.fn(),
  refreshTools: vi.fn(),
  getSessionMessagesPage: vi.fn(),
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const translations: Record<string, string> = {
        pageTitle: '设备接入',
        pageDescription: '配置安全设备 API 连接，使 Flocks 能够直接调用和控制这些设备',
        'toolbar.refresh': '刷新',
        'toolbar.addDevice': '立即添加设备',
        'empty.addNow': '立即添加设备',
        'config.closeAriaLabel': '关闭设备配置面板',
        'config.newDeviceTitle': '填写配置',
        'config.nameLabel': '设备名称',
        'config.roomLabel': '所属机房',
        'config.saveBtn': '保存配置',
        'config.addBtn': '添加设备',
        'config.testBtn': '连通测试',
        'config.showSecretAction': '显示',
        'config.hideSecretAction': '隐藏',
        'wizard.selectVendorTitle': `选择 ${String(params?.vendor ?? '')} 设备`,
        'wizard.tabs.rex': 'Rex 接入',
        'wizard.tabs.manual': '手动接入',
        'wizard.guide.workbenchTab': '工作台',
        'wizard.guide.title': 'Rex 辅助接入',
        'wizard.guide.subtitle': '选择一个引导或案例',
        'wizard.guide.customTitle': '自定义设备接入',
        'wizard.guide.caseTitle': '创建案例',
        'wizard.guide.examples.supported': '我想接入一台已支持的安全设备',
        'wizard.guide.examples.addressOnly': '我只有设备地址和登录方式',
        'wizard.guide.examples.noApi': '这台设备没有开放 API',
        'wizard.guide.prompts.api': '我已选择 API 接入，请按 API 接入继续',
        'wizard.guide.prompts.browser': '我已选择浏览器接入，请按浏览器接入继续',
        'wizard.guide.prompts.addressOnly': '我只有待接入设备的地址和登录方式，请先帮我判断它是否已有模板可用',
        'wizard.guide.prompts.tdp': '我已选择 TDP 接入案例，请引导我接入微步 TDP 设备',
        'wizard.guide.prompts.onesec': '我已选择 OneSEC 接入案例，请引导我接入 OneSEC 设备',
        'wizard.guide.actions.api': 'API 接入',
        'wizard.guide.actions.browser': '浏览器接入',
        'wizard.guide.cases.tdp': 'TDP 接入',
        'wizard.guide.cases.onesec': 'OneSEC 接入',
        'wizard.guide.cases.more': '查看更多',
        'wizard.supportedList.back': '返回',
        'wizard.supportedList.title': '已支持设备列表',
        'wizard.supportedList.subtitle': '先选择厂商，再选择要接入的设备',
        'wizard.supportedList.deviceCount': `${String(params?.count ?? '')} 款设备`,
        'wizard.supportedList.integratedCount': `已接入 ${String(params?.count ?? '')} 台`,
        'wizard.installState.installed': '已安装',
        'wizard.installState.available': '可安装',
        'wizard.installState.updateAvailable': '可更新',
        'wizard.installState.brokenShort': '不可用',
        'wizard.installState.installing': '安装中',
        'wizard.installState.updating': '更新中',
        'wizard.installState.installingTemplate': `正在安装设备模板「${String(params?.name ?? '')}」`,
        'wizard.installState.updatingTemplate': `正在更新设备模板「${String(params?.name ?? '')}」`,
        'wizard.installState.installDone': `设备模板「${String(params?.name ?? '')}」已安装`,
        'wizard.installState.updateDone': `设备模板「${String(params?.name ?? '')}」已更新`,
        'wizard.installState.installFailed': `设备模板「${String(params?.name ?? '')}」安装失败`,
        'wizard.installState.updateFailed': `设备模板「${String(params?.name ?? '')}」更新失败`,
        'wizard.rex.title': 'Rex 引导添加设备',
        'wizard.rex.heading': 'Rex 引导接入',
        'wizard.rex.subtitle': '描述设备型号、接入方式和已有资料',
        'wizard.rex.welcome': '请告诉我你要接入的设备厂商、型号、版本。信息足够后，我会输出一段 ```json 配置草稿。',
        'wizard.rex.placeholder': '描述要接入的设备、地址、认证方式或上传相关资料',
        'wizard.rex.pending': 'Rex 准备中...',
        'wizard.rex.manualAction': '切换到手动接入',
        'wizard.rex.applyDraft': '应用到表单',
        'wizard.rex.applyDone': '已填充设备配置表单',
        'wizard.rex.extracting': '提取中...',
        'wizard.rex.extractEmpty': '还没有可提取的 Rex 输出',
        'wizard.rex.extractNoTemplate': '未能从 Rex 输出中匹配到设备模板',
        'wizard.rex.extractFailed': '提取失败，请让 Rex 输出 ```json 设备配置草稿后重试',
        'wizard.rex.installFirst': '该模板尚未安装，先前往 FlockHub 安装',
        'wizard.rex.detectedDraft': '检测到可填充的设备配置草稿',
        'wizard.rex.detectedInstall': '检测到设备模板，但该模板尚未安装',
        'wizard.rex.applyDetected': '填充表单',
        'wizard.rex.installDetected': '前往安装',
        'wizard.rex.dismissDraft': '忽略',
        'wizard.rex.guides.existing.title': '已有模板',
        'wizard.rex.guides.existing.desc': '整理已支持设备的手动接入字段',
        'wizard.rex.guides.existing.prompt': '我要接入一个已有模板支持的安全设备',
        'wizard.rex.guides.api.title': '自定义 API',
        'wizard.rex.guides.api.desc': '通过 API 文档创建自定义 device 插件',
        'wizard.rex.guides.api.prompt': '我要接入一个暂未支持的 API 设备',
        'wizard.rex.guides.webcli.title': '浏览器接入',
        'wizard.rex.guides.webcli.desc': '通过 Web 控制台页面创建接入能力',
        'wizard.rex.guides.webcli.prompt': '我要接入一个没有开放 API 的 Web 控制台设备',
        'wizard.customCardTitle': '自定义设备',
        'wizard.customModes.api.title': 'API 接入',
        'wizard.customModes.webcli.title': '浏览器接入',
        'wizard.customModes.workflow.title': 'Workflow 接入',
        'custom.actions.submit': '提交给 Rex',
        'custom.actions.openSessionList': '前往会话列表查看',
        'custom.rex.apiPlaceholder': '请提供产品 API 文档',
        'custom.rex.webcliPlaceholder': '请提供网站地址',
        'custom.workflow.goToWorkflows': '前往工作流列表',
        'custom.form.api.deviceNameLabel': '设备产品名',
        'custom.form.api.vendorNameLabel': '厂商名称',
        'custom.form.api.baseUrlLabel': 'Base URL',
        'custom.form.api.docsUrlLabel': 'API 文档链接',
        'custom.form.webcli.deviceNameLabel': '设备产品名',
        'custom.form.webcli.vendorNameLabel': '厂商名称',
        'custom.form.webcli.productUrlLabel': '产品 URL',
        'custom.form.webcli.targetInterfacesLabel': '需要获取的接口或页面行为',
        'custom.form.webcli.authHintLabel': '认证/权限提示',
      };
      if (key === 'config.showSecretAria') return `显示${String(params?.label ?? '')}`;
      if (key === 'config.hideSecretAria') return `隐藏${String(params?.label ?? '')}`;
      return translations[key] ?? key;
    },
    i18n: { language: 'zh-CN' },
  }),
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({
    success: mocks.toastSuccess,
    error: mocks.toastError,
    info: mocks.toastInfo,
    warning: vi.fn(),
  }),
}));

vi.mock('@/components/common/PageHeader', () => ({
  default: ({
    title,
    description,
    action,
  }: {
    title: string;
    description?: string;
    action?: React.ReactNode;
  }) => (
    <div>
      <h1>{title}</h1>
      {description ? <p>{description}</p> : null}
      <div>{action}</div>
    </div>
  ),
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading</div>,
}));

vi.mock('../Tool/components/ToolDetailModal', () => ({
  default: () => null,
}));

vi.mock('@/components/common/SessionChat', () => ({
  default: ({
    sessionId,
    welcomeContent,
    onCreateAndSend,
    onStreamingDone,
    placeholder,
  }: {
    sessionId?: string | null;
    welcomeContent?: React.ReactNode;
    onCreateAndSend?: (text: string, imageParts: []) => void;
    onStreamingDone?: () => void;
    placeholder?: string;
  }) => (
    <div>
      <div>SessionChat:{sessionId ?? 'pending'}</div>
      <div>Placeholder:{placeholder}</div>
      {!sessionId && welcomeContent}
      {!sessionId && (
        <button type="button" onClick={() => onCreateAndSend?.('用户补充资料', [])}>
          mock send
        </button>
      )}
      {onStreamingDone && (
        <button type="button" onClick={onStreamingDone}>
          mock stream done
        </button>
      )}
    </div>
  ),
}));

vi.mock('@/components/common/useRexComposerControls', () => ({
  useRexComposerControls: () => ({
    rexAgentName: 'rex',
    rexMentionAgents: [{ name: 'rex', description: 'Rex' }],
    rexModel: { providerID: 'openai', modelID: 'gpt-4.1' },
    rexSupportsVision: true,
    rexContextWindowTokens: 128000,
    rexComposerTextareaMinHeight: 48,
    rexComposerTextareaMaxHeight: 120,
    rexToolbarSlot: <div>RexAgentDisplay</div>,
    rexCenterToolbarSlot: <div>RexModelPicker</div>,
  }),
}));

vi.mock('@/hooks/useSessionChat', () => ({
  useSessionChat: (options: Record<string, unknown>) => {
    mocks.useSessionChatOptions(options);
    return {
    sessionId: mocks.sessionId,
    loading: false,
    error: null,
    create: vi.fn().mockResolvedValue('session-1'),
    createAndSend: mocks.createAndSend,
    retry: vi.fn(),
    reset: mocks.resetSession,
  };
  },
}));

vi.mock('@/api/device', () => ({
  deviceAPI: {
    list: (...args: unknown[]) => mocks.listDevices(...args),
    sync: (...args: unknown[]) => mocks.syncDevices(...args),
    get: (...args: unknown[]) => mocks.getDevice(...args),
    revealCredentials: (...args: unknown[]) => mocks.revealDeviceCredentials(...args),
    listGroups: (...args: unknown[]) => mocks.listGroups(...args),
    createGroup: (...args: unknown[]) => mocks.createGroup(...args),
    updateGroup: (...args: unknown[]) => mocks.updateGroup(...args),
    deleteGroup: (...args: unknown[]) => mocks.deleteGroup(...args),
    create: (...args: unknown[]) => mocks.createDevice(...args),
    update: (...args: unknown[]) => mocks.updateDevice(...args),
    delete: (...args: unknown[]) => mocks.deleteDevice(...args),
    test: (...args: unknown[]) => mocks.testDevice(...args),
    listTemplates: (...args: unknown[]) => mocks.listTemplates(...args),
    listDeviceTools: (...args: unknown[]) => mocks.listDeviceTools(...args),
    updateDeviceTool: (...args: unknown[]) => mocks.updateDeviceTool(...args),
  },
}));

vi.mock('@/api/hub', () => ({
  hubAPI: {
    install: (...args: unknown[]) => mocks.hubInstall(...args),
    update: (...args: unknown[]) => mocks.hubUpdate(...args),
  },
}));

vi.mock('@/api/provider', () => ({
  providerAPI: {
    getServiceMetadata: (...args: unknown[]) => mocks.getServiceMetadata(...args),
  },
}));

vi.mock('@/api/tool', () => ({
  toolAPI: {
    list: (...args: unknown[]) => mocks.listTools(...args),
    setEnabled: (...args: unknown[]) => mocks.setToolEnabled(...args),
    refresh: (...args: unknown[]) => mocks.refreshTools(...args),
  },
}));

vi.mock('@/api/session', () => ({
  sessionApi: {
    getMessagesPage: (...args: unknown[]) => mocks.getSessionMessagesPage(...args),
  },
}));

function buildTemplate(overrides: Record<string, unknown> = {}) {
  return {
    plugin_id: 'existing_device_v1',
    storage_key: 'existing_device_v1',
    service_id: 'existing_device',
    name: 'Existing Device',
    credential_schema: [],
    tool_count: 1,
    installed: true,
    state: 'installed',
    source: 'project',
    vendor: 'threatbook',
    ...overrides,
  };
}

async function openManualAddWizard(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
}

async function openSupportedDeviceList(user: ReturnType<typeof userEvent.setup>) {
  await openManualAddWizard(user);
  await user.click(screen.getByRole('button', { name: /查看更多/ }));
}

async function openApiDeviceGuidance(user: ReturnType<typeof userEvent.setup>) {
  await openManualAddWizard(user);
  await user.click(screen.getByRole('button', { name: /^API 接入$/ }));
}

async function openBrowserDeviceGuidance(user: ReturnType<typeof userEvent.setup>) {
  await openManualAddWizard(user);
  await user.click(screen.getByRole('button', { name: /^浏览器接入$/ }));
}

describe('DeviceIntegrationPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.sessionId = null;
    mocks.listDevices.mockResolvedValue({ data: [] });
    mocks.syncDevices.mockResolvedValue({ data: { created: 0 } });
    mocks.getDevice.mockResolvedValue({
      data: {
        id: 'device-1',
        group_id: 'group-1',
        name: 'TDP-test-02',
        storage_key: 'tdp_api_v3_3_10',
        service_id: 'tdp',
        enabled: true,
        verify_ssl: false,
        fields: { base_url: 'https://tdp.example.com' },
        fields_set: { api_key: true, secret: true, base_url: true },
        status: 'connected',
        created_at: 0,
        updated_at: 0,
      },
    });
    mocks.testDevice.mockResolvedValue({
      data: { success: true, message: 'HTTP 200, 163ms', latency_ms: 163 },
    });
    mocks.listGroups.mockResolvedValue({
      data: [{ id: 'default', name: '默认机房', sort_order: 0, created_at: 0, updated_at: 0 }],
    });
    mocks.listTemplates.mockResolvedValue({ data: [buildTemplate()] });
    mocks.hubInstall.mockResolvedValue({ data: {} });
    mocks.hubUpdate.mockResolvedValue({ data: {} });
    mocks.getServiceMetadata.mockResolvedValue({ data: { credential_schema: [] } });
    mocks.revealDeviceCredentials.mockResolvedValue({ data: { fields: {} } });
    mocks.listTools.mockResolvedValue({ data: [] });
    mocks.setToolEnabled.mockResolvedValue({ data: {} });
    mocks.listDeviceTools.mockResolvedValue({ data: [] });
    mocks.updateDeviceTool.mockResolvedValue({ data: {} });
    mocks.refreshTools.mockResolvedValue({ data: { ok: true } });
    mocks.getSessionMessagesPage.mockResolvedValue({ items: [] });
  });

  it('refreshes devices and templates without syncing when the window regains focus', async () => {
    render(<DeviceIntegrationPage />);

    await screen.findByText('设备接入');
    await waitFor(() => {
      expect(mocks.listDevices).toHaveBeenCalledTimes(1);
    });
    mocks.listDevices.mockClear();
    mocks.listTemplates.mockClear();
    mocks.listGroups.mockClear();

    window.dispatchEvent(new Event('focus'));

    await waitFor(() => {
      expect(mocks.listDevices).toHaveBeenCalledWith();
      expect(mocks.listTemplates).toHaveBeenCalledWith();
      expect(mocks.listGroups).toHaveBeenCalled();
    });
    expect(mocks.syncDevices).not.toHaveBeenCalled();
  });

  it('shows custom guidance and example entries on the add-device workbench', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await openManualAddWizard(user);

    expect(screen.getByText('API 接入')).toBeInTheDocument();
    expect(screen.getByText('浏览器接入')).toBeInTheDocument();
    expect(screen.getByText('TDP 接入')).toBeInTheDocument();
    expect(screen.getByText('OneSEC 接入')).toBeInTheDocument();
    expect(screen.getByText('查看更多')).toBeInTheDocument();
  });

  it('opens the add-device panel on the Rex-guided tab by default', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));

    expect(await screen.findByText('SessionChat:pending')).toBeInTheDocument();
    expect(screen.getByText('SessionChat:pending')).toBeInTheDocument();
    expect(screen.getByText('Rex 辅助接入')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^API 接入$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^浏览器接入$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^查看更多$/ })).toBeInTheDocument();
    expect(screen.getByText('Placeholder:描述要接入的设备、地址、认证方式或上传相关资料')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^填充表单$/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /自定义设备/ })).toBeNull();
    expect(screen.queryByText('Rex 引导接入')).toBeNull();
    expect(mocks.useSessionChatOptions.mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        title: 'Rex 引导添加设备',
        category: 'entity-config',
        contextMessage: expect.stringContaining('当前可见设备模板'),
      }),
    );
    const contextMessage = mocks.useSessionChatOptions.mock.calls.at(-1)?.[0].contextMessage;
    expect(contextMessage).toContain('```json');
    expect(contextMessage).toContain('API 接入');
    expect(contextMessage).toContain('浏览器接入');
    expect(contextMessage).toContain('Workflow 接入');
    expect(contextMessage).toContain('Syslog、Kafka 或 Webhook');
    expect(contextMessage).toContain('不要继续输出设备配置 JSON');
    expect(contextMessage).toContain('只有用户没有明确选择接入方式时，才使用 `question` 工具询问用户选择接入方式');
    expect(contextMessage).toContain('如果用户当前消息已经明确写了「API 接入」或「浏览器接入」，不要再询问接入方式');
    expect(contextMessage).toContain('用户确认接入方式后，必须使用下方对应规则继续澄清和推进');
  });

  it('starts a guided Rex prompt from the add-device welcome card', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /TDP 接入/ }));

    expect(mocks.createAndSend).toHaveBeenCalledWith({
      text: expect.stringContaining('我已选择 TDP 接入案例'),
      agent: 'rex',
      model: { providerID: 'openai', modelID: 'gpt-4.1' },
    });
  });

  it('sends the custom API guidance prompt from the add-device welcome card', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /^API 接入$/ }));

    expect(mocks.createAndSend).toHaveBeenCalledWith({
      text: expect.stringContaining('我已选择 API 接入'),
      agent: 'rex',
      model: { providerID: 'openai', modelID: 'gpt-4.1' },
    });
    expect(screen.getByText('Placeholder:描述要接入的设备、地址、认证方式或上传相关资料')).toBeInTheDocument();
  });

  it('applies Rex device draft to the add-device config form', async () => {
    const user = userEvent.setup();
    mocks.sessionId = 'session-1';
    mocks.listGroups.mockResolvedValue({
      data: [
        { id: 'group-1', name: '默认机房', sort_order: 0, created_at: 0, updated_at: 0 },
        { id: 'group-2', name: '北京机房', sort_order: 1, created_at: 0, updated_at: 0 },
      ],
    });
    mocks.listTemplates.mockResolvedValue({
      data: [
        buildTemplate({
          storage_key: 'qingteng_v3_4_1_66',
          service_id: 'qingteng',
          name: '青藤云安全',
          vendor: 'qingteng',
          credential_schema: [
            {
              key: 'base_url',
              label: 'Base URL',
              storage: 'config',
              sensitive: false,
              required: true,
              input_type: 'url',
              config_key: 'base_url',
            },
            {
              key: 'username',
              label: 'Username',
              storage: 'config',
              sensitive: false,
              required: true,
              input_type: 'text',
              config_key: 'username',
            },
          ],
        }),
      ],
    });
    mocks.getSessionMessagesPage.mockResolvedValue({
      items: [
        {
          info: { role: 'assistant' },
          parts: [
            {
              type: 'text',
              text: '```json\n{"storage_key":"qingteng_v3_4_1_66","device_name":"青藤万相","fields":{"base_url":"https://example.com","username":"admin"},"verify_ssl":false}\n```',
            },
          ],
        },
      ],
    });
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(await screen.findByRole('button', { name: /mock stream done/ }));
    expect(await screen.findByText('检测到可填充的设备配置草稿')).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: /^填充表单$/ }));

    expect(await screen.findByDisplayValue('青藤万相')).toBeInTheDocument();
    expect(screen.getByDisplayValue('https://example.com')).toBeInTheDocument();
    expect(screen.getByDisplayValue('admin')).toBeInTheDocument();
    expect(screen.getByRole('combobox')).toHaveValue('group-1');
    expect(screen.getAllByText('北京机房').length).toBeGreaterThan(0);
    expect(mocks.toastSuccess).toHaveBeenCalledWith('已填充设备配置表单');
  });

  it('does not detect Rex prose as a fillable device draft', async () => {
    const user = userEvent.setup();
    mocks.sessionId = 'session-1';
    mocks.listTemplates.mockResolvedValue({
      data: [
        buildTemplate({
          storage_key: 'qingteng_v3_4_1_66',
          service_id: 'qingteng',
          name: '青藤云安全',
          vendor: 'qingteng',
        }),
      ],
    });
    mocks.getSessionMessagesPage.mockResolvedValue({
      items: [
        {
          info: { role: 'assistant' },
          parts: [
            {
              type: 'text',
              text: '模板：`qingteng_v3_4_1_66`\n设备名称：青藤万相\n所属机房：北京机房\nBase URL：https://example.com',
            },
          ],
        },
      ],
    });

    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(await screen.findByRole('button', { name: /mock stream done/ }));

    await waitFor(() => {
      expect(mocks.getSessionMessagesPage).toHaveBeenCalledWith('session-1', { limit: 50 });
    });
    expect(screen.queryByText('检测到可填充的设备配置草稿')).toBeNull();
    expect(screen.queryByRole('button', { name: /^填充表单$/ })).toBeNull();
    expect(mocks.toastError).not.toHaveBeenCalled();
  });

  it('installs unavailable supported templates and then sends them to Rex', async () => {
    const user = userEvent.setup();
    const availableTemplate = buildTemplate({
      plugin_id: 'onesig_v2_5_3_D20250710',
      storage_key: 'onesig_v2_5_3_D20250710_api_v2_5_3_D20250710',
      service_id: 'onesig_v2_5_3_D20250710_api',
      name: 'onesig',
      version: '2.5.3 D20250710',
      installed: false,
      state: 'available',
    });
    mocks.listTemplates.mockResolvedValueOnce({
      data: [availableTemplate],
    });
    mocks.listTemplates.mockResolvedValueOnce({
      data: [{ ...availableTemplate, installed: true, state: 'installed' }],
    });

    render(<DeviceIntegrationPage />);

    await openSupportedDeviceList(user);
    await user.click(screen.getByText('微步'));
    await user.click(screen.getByText('onesig'));

    await waitFor(() => {
      expect(mocks.hubInstall).toHaveBeenCalledWith('device', 'onesig_v2_5_3_D20250710');
    });
    expect(mocks.syncDevices).not.toHaveBeenCalled();
    expect(mocks.listTemplates).toHaveBeenLastCalledWith({ refresh: true });
    expect(mocks.navigate).not.toHaveBeenCalled();
    await waitFor(() => expect(mocks.createAndSend).toHaveBeenCalledWith(expect.objectContaining({
      text: expect.stringContaining('我要接入设备「onesig」'),
      agent: 'rex',
      model: { providerID: 'openai', modelID: 'gpt-4.1' },
    })));
    expect(mocks.createAndSend.mock.calls[0][0].text).toContain('storage_key=onesig_v2_5_3_D20250710_api_v2_5_3_D20250710');
  });

  it('sends api guidance directly to Rex without opening a custom form', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await openApiDeviceGuidance(user);

    expect(screen.queryByLabelText('设备产品名')).toBeNull();
    expect(screen.queryByLabelText('Base URL')).toBeNull();
    expect(screen.queryByRole('button', { name: /提交给 Rex/ })).toBeNull();
    expect(await screen.findByText('SessionChat:pending')).toBeInTheDocument();
    expect(mocks.createAndSend).toHaveBeenCalledWith({
      text: expect.stringContaining('我已选择 API 接入'),
      agent: 'rex',
      model: { providerID: 'openai', modelID: 'gpt-4.1' },
    });
  });

  it('sends browser guidance directly to Rex without opening a custom form', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await openBrowserDeviceGuidance(user);

    expect(screen.queryByLabelText('登录说明')).toBeNull();
    expect(screen.queryByLabelText('产品 URL')).toBeNull();
    expect(screen.queryByLabelText('需要获取的接口或页面行为')).toBeNull();
    expect(screen.queryByRole('button', { name: /提交给 Rex/ })).toBeNull();
    expect(await screen.findByText('SessionChat:pending')).toBeInTheDocument();
    expect(mocks.createAndSend).toHaveBeenCalledWith({
      text: expect.stringContaining('我已选择浏览器接入'),
      agent: 'rex',
      model: { providerID: 'openai', modelID: 'gpt-4.1' },
    });
  });

  it('creates a Rex device-add session when the user sends a message', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await openManualAddWizard(user);

    expect(await screen.findByText('SessionChat:pending')).toBeInTheDocument();
    expect(mocks.createAndSend).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /mock send/ }));

    await waitFor(() => expect(mocks.createAndSend).toHaveBeenCalledTimes(1));
    expect(mocks.createAndSend).toHaveBeenCalledWith({
      text: '用户补充资料',
      imageParts: [],
      agent: 'rex',
      model: { providerID: 'openai', modelID: 'gpt-4.1' },
    });
  });

  it('hides refresh action and rex footer hint in chat view', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await openApiDeviceGuidance(user);

    await screen.findByText('SessionChat:pending');
    expect(screen.queryByRole('button', { name: /刷新设备模板/ })).toBeNull();
    expect(screen.queryByText(/已进入 Rex 对话/)).toBeNull();
  });

  it('sends the selected supported device template to Rex from the vendor accordion', async () => {
    const user = userEvent.setup();
    mocks.listTemplates.mockResolvedValueOnce({
      data: [
        buildTemplate({
          plugin_id: 'tdp_v3_3_10',
          storage_key: 'tdp_api_v3_3_10',
          service_id: 'tdp_api',
          name: 'TDP',
          vendor: 'threatbook',
          installed: true,
          state: 'installed',
          credential_schema: [
            {
              key: 'base_url',
              label: 'Base URL',
              storage: 'config',
              sensitive: false,
              required: true,
              input_type: 'url',
              config_key: 'base_url',
            },
          ],
        }),
      ],
    });
    render(<DeviceIntegrationPage />);

    await openSupportedDeviceList(user);
    await user.click(screen.getByRole('button', { name: /微步/ }));
    await user.click(screen.getByRole('button', { name: /TDP/ }));

    expect(screen.queryByText('填写配置')).toBeNull();
    expect(mocks.createAndSend).toHaveBeenCalledWith(expect.objectContaining({
      text: expect.stringContaining('我要接入设备「TDP」'),
      agent: 'rex',
      model: { providerID: 'openai', modelID: 'gpt-4.1' },
    }));
    expect(mocks.createAndSend.mock.calls[0][0].text).toContain('storage_key=tdp_api_v3_3_10');
    expect(mocks.createAndSend.mock.calls[0][0].text).toContain('base_url* (Base URL)');
  });

  it('clicking the blank backdrop closes the config panel', async () => {
    const user = userEvent.setup();
    mocks.listDevices.mockResolvedValueOnce({
      data: [
        {
          id: 'device-1',
          group_id: 'group-1',
          name: 'TDP-test-02',
          storage_key: 'tdp_api_v3_3_10',
          service_id: 'tdp',
          enabled: true,
          verify_ssl: false,
          fields: { base_url: 'https://tdp.example.com' },
          fields_set: { api_key: true, secret: true, base_url: true },
          status: 'connected',
          created_at: 0,
          updated_at: 0,
        },
      ],
    });
    mocks.listTemplates.mockResolvedValueOnce({
      data: [
        buildTemplate({
          plugin_id: 'tdp_v3_3_10',
          storage_key: 'tdp_api_v3_3_10',
          service_id: 'tdp_api',
          name: 'TDP',
          tool_count: 21,
          vendor: 'threatbook',
        }),
      ],
    });
    mocks.listGroups.mockResolvedValueOnce({
      data: [
        {
          id: 'group-1',
          name: '默认机房',
          sort_order: 0,
          created_at: 0,
          updated_at: 0,
        },
      ],
    });
    mocks.getServiceMetadata.mockResolvedValueOnce({
      data: {
        name: 'TDP',
        credential_schema: [
          {
            key: 'api_key',
            label: 'API Key',
            storage: 'secret',
            sensitive: true,
            required: true,
            input_type: 'password',
            config_key: 'api_key',
          },
          {
            key: 'secret',
            label: 'Secret',
            storage: 'secret',
            sensitive: true,
            required: true,
            input_type: 'password',
            config_key: 'secret',
          },
          {
            key: 'base_url',
            label: 'Base URL',
            storage: 'config',
            sensitive: false,
            required: true,
            input_type: 'url',
            config_key: 'base_url',
          },
        ],
      },
    });

    render(<DeviceIntegrationPage />);

    const cardTitle = await screen.findByText('TDP-test-02');
    await user.click(cardTitle);

    expect(await screen.findByRole('button', { name: '关闭设备配置面板' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '关闭设备配置面板' }));

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '关闭设备配置面板' })).not.toBeInTheDocument();
    });
  });

  it('allows editing an existing device room from a selected room view', async () => {
    const user = userEvent.setup();
    const initialDevice = {
      id: 'device-1',
      group_id: 'group-1',
      name: 'TDP-test-02',
      storage_key: 'tdp_api_v3_3_10',
      service_id: 'tdp',
      enabled: true,
      verify_ssl: false,
      fields: { base_url: 'https://tdp.example.com' },
      fields_set: { api_key: true, secret: true, base_url: true },
      status: 'connected',
      created_at: 0,
      updated_at: 0,
    };
    mocks.listDevices.mockResolvedValue({ data: [initialDevice] });
    mocks.listTemplates.mockResolvedValue({
      data: [
        buildTemplate({
          plugin_id: 'tdp_v3_3_10',
          storage_key: 'tdp_api_v3_3_10',
          service_id: 'tdp_api',
          name: 'TDP',
          tool_count: 21,
          vendor: 'threatbook',
        }),
      ],
    });
    mocks.listGroups.mockResolvedValue({
      data: [
        { id: 'group-1', name: '默认机房', sort_order: 0, created_at: 0, updated_at: 0 },
        { id: 'group-2', name: '测试', sort_order: 1, created_at: 0, updated_at: 0 },
      ],
    });
    mocks.getDevice.mockResolvedValue({
      data: { ...initialDevice, group_id: 'group-2' },
    });

    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByText('TDP-test-02'));
    const roomSelect = await screen.findByRole('combobox');
    await user.selectOptions(roomSelect, 'group-2');
    await user.click(screen.getByRole('button', { name: /保存配置/ }));

    await waitFor(() => {
      expect(mocks.updateDevice).toHaveBeenCalledWith(
        'device-1',
        expect.objectContaining({ group_id: 'group-2' }),
      );
    });
  });

  it('tests connectivity with draft fields without replacing the form', async () => {
    const user = userEvent.setup();
    const initialDevice = {
      id: 'device-1',
      group_id: 'group-1',
      name: 'onesig-02',
      storage_key: 'onesig_api_v2_5_3',
      service_id: 'onesig_api',
      enabled: true,
      verify_ssl: false,
      fields: {
        base_url: 'https://persisted.example.com',
        api_prefix: '/api',
        username: 'admin',
        password: 'p***word',
      },
      fields_set: { base_url: true, api_prefix: true, username: true, password: true },
      status: 'connected',
      created_at: 0,
      updated_at: 0,
    };
    mocks.listDevices.mockResolvedValue({ data: [initialDevice] });
    mocks.listTemplates.mockResolvedValue({
      data: [
        buildTemplate({
          plugin_id: 'onesig_v2_5_3',
          storage_key: 'onesig_api_v2_5_3',
          service_id: 'onesig_api',
          name: 'OneSIG',
          vendor: 'threatbook',
        }),
      ],
    });
    mocks.getServiceMetadata.mockResolvedValueOnce({
      data: {
        name: 'OneSIG',
        credential_schema: [
          {
            key: 'base_url',
            label: 'Base URL',
            storage: 'config',
            sensitive: false,
            required: true,
            input_type: 'url',
            config_key: 'base_url',
          },
          {
            key: 'api_prefix',
            label: 'API Prefix',
            storage: 'config',
            sensitive: false,
            required: false,
            input_type: 'text',
            config_key: 'api_prefix',
          },
          {
            key: 'username',
            label: 'Username',
            storage: 'config',
            sensitive: false,
            required: true,
            input_type: 'text',
            config_key: 'username',
          },
          {
            key: 'password',
            label: 'Password',
            storage: 'secret',
            sensitive: true,
            required: true,
            input_type: 'password',
            config_key: 'password',
          },
        ],
      },
    });

    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByText('onesig-02'));
    const baseUrl = await screen.findByDisplayValue('https://persisted.example.com');
    await user.clear(baseUrl);
    await user.type(baseUrl, 'https://draft.example.com');
    await user.click(screen.getByRole('button', { name: /连通测试/ }));

    await waitFor(() => {
      expect(mocks.testDevice).toHaveBeenCalledWith('device-1', {
        fields: expect.objectContaining({
          base_url: 'https://draft.example.com',
          api_prefix: '/api',
          username: 'admin',
          password: 'p***word',
        }),
        verify_ssl: false,
        base_url: 'https://draft.example.com',
      });
    });
    expect(mocks.getDevice).not.toHaveBeenCalled();
    expect(mocks.listDevices).toHaveBeenCalledTimes(1);
    expect(screen.getByDisplayValue('https://draft.example.com')).toBeInTheDocument();
    expect(await screen.findByText('HTTP 200, 163ms')).toBeInTheDocument();
  });

  it('reveals the full persisted secret when clicking show', async () => {
    const user = userEvent.setup();
    mocks.listDevices.mockResolvedValueOnce({
      data: [
        {
          id: 'device-1',
          group_id: 'group-1',
          name: 'onesec-02',
          storage_key: 'onesec_api_v2_8_2',
          service_id: 'onesec',
          enabled: true,
          verify_ssl: false,
          fields: {
            api_key: 'l***Cd4Y',
            secret: 's***7890',
            base_url: 'https://console.onesec.net',
          },
          fields_set: { api_key: true, secret: true, base_url: true },
          status: 'connected',
          created_at: 0,
          updated_at: 0,
        },
      ],
    });
    mocks.listTemplates.mockResolvedValueOnce({
      data: [
        buildTemplate({
          plugin_id: 'onesec_v2_8_2',
          storage_key: 'onesec_api_v2_8_2',
          service_id: 'onesec_api',
          name: 'OneSEC',
          tool_count: 5,
          vendor: 'threatbook',
        }),
      ],
    });
    mocks.getServiceMetadata.mockResolvedValueOnce({
      data: {
        name: 'OneSEC',
        credential_schema: [
          {
            key: 'api_key',
            label: 'API Key',
            storage: 'secret',
            sensitive: true,
            required: true,
            input_type: 'password',
            config_key: 'api_key',
          },
          {
            key: 'secret',
            label: 'Secret',
            storage: 'secret',
            sensitive: true,
            required: true,
            input_type: 'password',
            config_key: 'secret',
          },
        ],
      },
    });
    mocks.revealDeviceCredentials.mockResolvedValueOnce({
      data: {
        fields: {
          api_key: 'long-real-onesec-api-key-Cd4Y',
          secret: 'long-real-onesec-secret-7890',
        },
      },
    });

    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByText('onesec-02'));
    expect(await screen.findByDisplayValue('l***Cd4Y')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '显示API Key' }));

    await waitFor(() => {
      expect(mocks.revealDeviceCredentials).toHaveBeenCalledWith('device-1', 'api_key');
      expect(screen.getByDisplayValue('long-real-onesec-api-key-Cd4Y')).toBeInTheDocument();
    });
  });
});
