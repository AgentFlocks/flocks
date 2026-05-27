import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
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
      {description && <p>{description}</p>}
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
    mocks.listGroups.mockResolvedValue({
      data: [{ id: 'default', name: '默认机房', sort_order: 0, created_at: 0, updated_at: 0 }],
    });
    mocks.listApiServices.mockResolvedValue({ data: [buildTemplate()] });
    mocks.getServiceMetadata.mockResolvedValue({ data: { credential_schema: [] } });
    mocks.listTools.mockResolvedValue({ data: [] });
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

  it('refreshes templates and enters device config when matching template appears', async () => {
    const user = userEvent.setup();
    mocks.listApiServices
      .mockResolvedValueOnce({ data: [buildTemplate()] })
      .mockResolvedValueOnce({
        data: [
          buildTemplate(),
          buildTemplate({
            id: 'acme_guard_device_v1',
            name: 'Acme Guard',
            vendor: 'acme_security',
            description_cn: '自定义接入设备',
          }),
        ],
      });

    render(<DeviceIntegrationPage />);

    await user.click(await screen.findByRole('button', { name: /立即添加设备/ }));
    await user.click(screen.getByRole('button', { name: /自定义设备/ }));
    await user.click(screen.getByRole('button', { name: /API 接入/ }));
    await user.type(screen.getByLabelText('设备产品名'), 'Acme Guard');
    await user.type(screen.getByLabelText('厂商名称'), 'Acme Security');
    await user.type(screen.getByLabelText('Base URL'), 'https://device.example.com/api');
    await user.type(screen.getByLabelText('API 文档链接'), 'https://device.example.com/openapi');
    await user.click(screen.getByRole('button', { name: /提交给 Rex/ }));

    await user.click(screen.getByRole('button', { name: /刷新设备模板/ }));

    await waitFor(() => expect(mocks.refreshTools).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('填写配置')).toBeInTheDocument();
    expect(screen.getByText('acme_guard_device_v1')).toBeInTheDocument();
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
});
