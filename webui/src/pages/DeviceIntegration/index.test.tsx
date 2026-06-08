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
  resetSession: vi.fn(),
  listDevices: vi.fn(),
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
        'config.showSecretAction': '显示',
        'config.hideSecretAction': '隐藏',
        'wizard.selectVendorTitle': `选择 ${String(params?.vendor ?? '')} 设备`,
        'wizard.customCardTitle': '自定义设备',
        'wizard.customModes.api.title': 'API 接入',
        'wizard.customModes.webcli.title': 'WebCLI 接入',
        'wizard.customModes.workflow.title': 'Workflow 接入',
        'custom.actions.submit': '提交给 Rex',
        'custom.actions.openSessionList': '前往会话列表查看',
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
  default: ({ sessionId }: { sessionId?: string | null }) => (
    <div>SessionChat:{sessionId ?? 'pending'}</div>
  ),
}));

vi.mock('@/hooks/useSessionChat', () => ({
  useSessionChat: () => ({
    sessionId: 'session-1',
    loading: false,
    error: null,
    create: vi.fn().mockResolvedValue('session-1'),
    createAndSend: mocks.createAndSend,
    retry: vi.fn(),
    reset: mocks.resetSession,
  }),
}));

vi.mock('@/api/device', () => ({
  deviceAPI: {
    list: (...args: unknown[]) => mocks.listDevices(...args),
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
    mocks.listDevices.mockResolvedValue({ data: [] });
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

  it('submits api draft to Rex with device plugin prompt', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));
    await user.click(screen.getByRole('button', { name: /API 接入/ }));

    await user.type(screen.getByLabelText('设备产品名'), 'Acme Guard');
    await user.type(screen.getByLabelText('厂商名称'), 'Acme Security');
    await user.type(screen.getByLabelText('Base URL'), 'https://device.example.com/api');
    await user.type(screen.getByLabelText('API 文档链接'), 'https://device.example.com/openapi');
    expect(screen.queryByLabelText('API 文档内容')).toBeNull();
    expect(screen.queryByLabelText('认证方式')).toBeNull();
    expect(screen.queryByText('Rex 对话')).toBeNull();
    await user.click(screen.getByRole('button', { name: /提交给 Rex/ }));

    await waitFor(() => expect(mocks.createAndSend).toHaveBeenCalledTimes(1));
    const arg = mocks.createAndSend.mock.calls[0][0];
    expect(arg.text).toContain('Acme Guard');
    expect(arg.text).toContain('https://device.example.com/openapi');
    expect(arg.text).toContain('integration_type: device');
    expect(arg.text).toContain('~/.flocks/plugins/tools/device/<plugin_id>/');
    expect(arg.text).toContain('期望能力范围：全部 API');
    expect(arg.text).toContain('tool-builder skill');
    expect(await screen.findByText('SessionChat:session-1')).toBeInTheDocument();
  });

  it('shows webcli form without login hint field', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));
    await user.click(screen.getByRole('button', { name: /WebCLI 接入/ }));

    expect(screen.queryByLabelText('登录说明')).toBeNull();
    expect(screen.getByText('产品 URL')).toBeInTheDocument();
    expect(screen.getByLabelText('产品 URL')).toBeInTheDocument();
    expect(screen.getByLabelText('需要获取的接口或页面行为')).toBeInTheDocument();
  });

  it('submits webcli draft to Rex with skill-first device prompt', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));
    await user.click(screen.getByRole('button', { name: /WebCLI 接入/ }));

    await user.type(screen.getByLabelText('设备产品名'), 'Acme Portal');
    await user.type(screen.getByLabelText('厂商名称'), 'Acme Security');
    await user.type(screen.getByLabelText('产品 URL'), 'https://portal.example.com');
    await user.type(screen.getByLabelText('需要获取的接口或页面行为'), '告警列表和资产详情');
    await user.type(screen.getByLabelText('认证/权限提示'), 'Cookie + CSRF Token');
    await user.click(screen.getByRole('button', { name: /提交给 Rex/ }));

    await waitFor(() => expect(mocks.createAndSend).toHaveBeenCalledTimes(1));
    const arg = mocks.createAndSend.mock.calls[0][0];
    expect(arg.text).toContain('接入方式：WebCLI');
    expect(arg.text).toContain('https://portal.example.com');
    expect(arg.text).toContain('references/cli-in-skill.md');
    expect(arg.text).toContain('integration_type: device');
    expect(arg.text).toContain('~/.flocks/plugins/tools/device/<plugin_id>/');
    expect(arg.text).toContain('默认认证方式为 `cookie/auth-state`');
    expect(await screen.findByText('SessionChat:session-1')).toBeInTheDocument();
  });

  it('hides refresh action and rex footer hint in chat view', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));
    await user.click(screen.getByRole('button', { name: /API 接入/ }));
    await user.type(screen.getByLabelText('设备产品名'), 'Acme Guard');
    await user.type(screen.getByLabelText('厂商名称'), 'Acme Security');
    await user.type(screen.getByLabelText('Base URL'), 'https://device.example.com/api');
    await user.type(screen.getByLabelText('API 文档链接'), 'https://device.example.com/openapi');
    await user.click(screen.getByRole('button', { name: /提交给 Rex/ }));

    await screen.findByText('SessionChat:session-1');
    expect(screen.queryByRole('button', { name: /刷新设备模板/ })).toBeNull();
    expect(screen.queryByText(/已进入 Rex 对话/)).toBeNull();
  });

  it('navigates to the matching session from rex chat view', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));
    await user.click(screen.getByRole('button', { name: /API 接入/ }));
    await user.type(screen.getByLabelText('设备产品名'), 'Acme Guard');
    await user.type(screen.getByLabelText('厂商名称'), 'Acme Security');
    await user.type(screen.getByLabelText('Base URL'), 'https://device.example.com/api');
    await user.click(screen.getByRole('button', { name: /提交给 Rex/ }));

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
