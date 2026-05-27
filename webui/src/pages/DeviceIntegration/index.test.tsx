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
  updateGroup: vi.fn(),
  createDevice: vi.fn(),
  updateDevice: vi.fn(),
  deleteDevice: vi.fn(),
  testDevice: vi.fn(),
  listApiServices: vi.fn(),
  getServiceMetadata: vi.fn(),
  listTools: vi.fn(),
  setToolEnabled: vi.fn(),
  refreshTools: vi.fn(),
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
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
    listGroups: (...args: unknown[]) => mocks.listGroups(...args),
    updateGroup: (...args: unknown[]) => mocks.updateGroup(...args),
    create: (...args: unknown[]) => mocks.createDevice(...args),
    update: (...args: unknown[]) => mocks.updateDevice(...args),
    delete: (...args: unknown[]) => mocks.deleteDevice(...args),
    test: (...args: unknown[]) => mocks.testDevice(...args),
  },
}));

vi.mock('@/api/provider', () => ({
  providerAPI: {
    listApiServices: (...args: unknown[]) => mocks.listApiServices(...args),
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
    id: 'existing_device_v1',
    name: 'Existing Device',
    enabled: true,
    status: 'unknown',
    tool_count: 1,
    verify_ssl: false,
    integration_type: 'device',
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
    mocks.listApiServices.mockResolvedValue({ data: [buildTemplate()] });
    mocks.getServiceMetadata.mockResolvedValue({ data: { credential_schema: [] } });
    mocks.listTools.mockResolvedValue({ data: [] });
    mocks.setToolEnabled.mockResolvedValue({ data: {} });
    mocks.refreshTools.mockResolvedValue({ data: { ok: true } });
  });

  it('shows custom device option and access modes', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));

    expect(screen.getByText('API 接入')).toBeInTheDocument();
    expect(screen.getByText('WebCLI 接入')).toBeInTheDocument();
    expect(screen.getByText('Syslog 接入')).toBeInTheDocument();
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
    expect(arg.text).toContain('默认认证方式为 `auth-state`');
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

  it('redirects syslog flow to workflows page', async () => {
    const user = userEvent.setup();
    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));
    await user.click(screen.getByRole('button', { name: /Syslog 接入/ }));
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
    mocks.listApiServices.mockResolvedValueOnce({
      data: [
        {
          id: 'tdp_api_v3_3_10',
          name: 'TDP',
          enabled: true,
          status: 'ready',
          tool_count: 21,
          verify_ssl: false,
          integration_type: 'device',
          vendor: 'threatbook',
        },
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
});
