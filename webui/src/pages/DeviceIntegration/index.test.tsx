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
  getServiceMetadata: vi.fn(),
  listTools: vi.fn(),
  setToolEnabled: vi.fn(),
  refreshTools: vi.fn(),
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
        'config.nameLabel': '设备名称',
        'config.roomLabel': '所属机房',
        'config.saveBtn': '保存配置',
        'config.addBtn': '添加设备',
        'config.testBtn': '连通测试',
        'config.showSecretAction': '显示',
        'config.hideSecretAction': '隐藏',
        'wizard.selectVendorTitle': `选择 ${String(params?.vendor ?? '')} 设备`,
        'wizard.customCardTitle': '自定义设备',
        'wizard.customModes.api.title': 'API 接入',
        'wizard.customModes.webcli.title': 'WebCLI 接入',
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
    placeholder,
  }: {
    sessionId?: string | null;
    welcomeContent?: React.ReactNode;
    onCreateAndSend?: (text: string, imageParts: []) => void;
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
    </div>
  ),
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
    mocks.getServiceMetadata.mockResolvedValue({ data: { credential_schema: [] } });
    mocks.revealDeviceCredentials.mockResolvedValue({ data: { fields: {} } });
    mocks.listTools.mockResolvedValue({ data: [] });
    mocks.setToolEnabled.mockResolvedValue({ data: {} });
    mocks.listDeviceTools.mockResolvedValue({ data: [] });
    mocks.updateDeviceTool.mockResolvedValue({ data: {} });
    mocks.refreshTools.mockResolvedValue({ data: { ok: true } });
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

  it('shows custom device option and access modes', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));

    expect(screen.getByText('API 接入')).toBeInTheDocument();
    expect(screen.getByText('WebCLI 接入')).toBeInTheDocument();
    expect(screen.getByText('Workflow 接入')).toBeInTheDocument();
  });

  it('navigates unavailable templates to FlockHub', async () => {
    const user = userEvent.setup();
    mocks.listTemplates.mockResolvedValueOnce({
      data: [
        buildTemplate({
          plugin_id: 'onesig_v2_5_3_D20250710',
          storage_key: 'onesig_v2_5_3_D20250710_api_v2_5_3_D20250710',
          service_id: 'onesig_v2_5_3_D20250710_api',
          name: 'onesig',
          version: '2.5.3 D20250710',
          installed: false,
          state: 'available',
        }),
      ],
    });

    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByText('微步'));
    await user.click(screen.getByText('onesig'));

    expect(mocks.navigate).toHaveBeenCalledWith(
      '/hub?type=device&plugin=onesig_v2_5_3_D20250710&q=onesig_v2_5_3_D20250710',
    );
  });

  it('opens api mode directly in Rex chat with built-in guidance', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));
    await user.click(screen.getByRole('button', { name: /API 接入/ }));

    expect(screen.queryByLabelText('设备产品名')).toBeNull();
    expect(screen.queryByLabelText('Base URL')).toBeNull();
    expect(screen.queryByRole('button', { name: /提交给 Rex/ })).toBeNull();
    expect(await screen.findByText('SessionChat:pending')).toBeInTheDocument();
    expect(screen.getByText('Placeholder:请提供产品 API 文档')).toBeInTheDocument();
    expect(screen.getByText(/请提供待接入设备的 API 资料。/)).toBeInTheDocument();
    expect(mocks.createAndSend).not.toHaveBeenCalled();
    const options = mocks.useSessionChatOptions.mock.calls.at(-1)?.[0];
    expect(options).toEqual(
      expect.objectContaining({
        category: 'entity-config',
        welcomeMessage: expect.stringContaining('API 文档链接'),
      }),
    );
    expect(options.contextMessage).toContain('本次接入方式是 API 接入');
    expect(options.contextMessage).toContain('在正式开始构建设备插件之前');
    expect(options.contextMessage).toContain('使用 `question` 工具明确');
    expect(options.welcomeMessage).toContain('请提供待接入设备的 API 资料。');
    expect(options.welcomeMessage).toContain('资料确认后，Rex 将生成');
  });

  it('opens webcli mode directly in Rex chat with skill-first guidance', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));
    await user.click(screen.getByRole('button', { name: /WebCLI 接入/ }));

    expect(screen.queryByLabelText('登录说明')).toBeNull();
    expect(screen.queryByLabelText('产品 URL')).toBeNull();
    expect(screen.queryByLabelText('需要获取的接口或页面行为')).toBeNull();
    expect(screen.queryByRole('button', { name: /提交给 Rex/ })).toBeNull();
    expect(await screen.findByText('SessionChat:pending')).toBeInTheDocument();
    expect(screen.getByText('Placeholder:请提供网站地址')).toBeInTheDocument();
    expect(screen.getByText(/请提供待接入设备的 Web 控制台资料。/)).toBeInTheDocument();
    expect(mocks.createAndSend).not.toHaveBeenCalled();
    const options = mocks.useSessionChatOptions.mock.calls.at(-1)?.[0];
    expect(options).toEqual(
      expect.objectContaining({
        welcomeMessage: expect.stringContaining('登录 URL'),
      }),
    );
    expect(options.contextMessage).toContain('本次接入方式是 WebCLI 接入');
    expect(options.contextMessage).toContain('向用户提出必要问题');
    expect(options.contextMessage).toContain('使用 `question` 工具明确');
    expect(options.welcomeMessage).toContain('请提供待接入设备的 Web 控制台资料。');
    expect(options.welcomeMessage).toContain('资料确认后，Rex 将沉淀 WebCLI 资产');
  });

  it('creates custom device session only after the user sends a message', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));
    await user.click(screen.getByRole('button', { name: /API 接入/ }));

    expect(await screen.findByText('SessionChat:pending')).toBeInTheDocument();
    expect(mocks.createAndSend).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /mock send/ }));

    await waitFor(() => expect(mocks.createAndSend).toHaveBeenCalledTimes(1));
    expect(mocks.createAndSend).toHaveBeenCalledWith({
      text: '用户补充资料',
      imageParts: [],
    });
  });

  it('hides refresh action and rex footer hint in chat view', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));
    await user.click(screen.getByRole('button', { name: /API 接入/ }));

    await screen.findByText('SessionChat:pending');
    expect(screen.queryByRole('button', { name: /刷新设备模板/ })).toBeNull();
    expect(screen.queryByText(/已进入 Rex 对话/)).toBeNull();
  });

  it('navigates to the matching session from rex chat view', async () => {
    const user = userEvent.setup();
    mocks.sessionId = 'session-1';
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));
    await user.click(screen.getByRole('button', { name: /API 接入/ }));

    await screen.findByText('SessionChat:session-1');
    await user.click(screen.getByRole('button', { name: /前往会话列表查看/ }));

    expect(mocks.navigate).toHaveBeenCalledWith('/sessions?session=session-1');
  });

  it('redirects workflow integration flow to workflows page', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));
    await user.click(screen.getByRole('button', { name: /Workflow 接入/ }));
    expect(screen.queryByRole('button', { name: /新建工作流/ })).toBeNull();
    await user.click(screen.getByRole('button', { name: /前往工作流列表/ }));

    expect(mocks.navigate).toHaveBeenCalledWith('/workflows');
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
