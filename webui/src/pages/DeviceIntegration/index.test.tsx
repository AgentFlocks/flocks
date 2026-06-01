import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import DeviceIntegrationPage from './index';

const mocks = vi.hoisted(() => ({
  listDevices: vi.fn(),
  getDevice: vi.fn(),
  createDevice: vi.fn(),
  updateDevice: vi.fn(),
  deleteDevice: vi.fn(),
  testDevice: vi.fn(),
  listGroups: vi.fn(),
  updateGroup: vi.fn(),
  listApiServices: vi.fn(),
  getServiceMetadata: vi.fn(),
  listTools: vi.fn(),
  setToolEnabled: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock('@/api/device', () => ({
  deviceAPI: {
    list: mocks.listDevices,
    get: mocks.getDevice,
    create: mocks.createDevice,
    update: mocks.updateDevice,
    delete: mocks.deleteDevice,
    test: mocks.testDevice,
    listGroups: mocks.listGroups,
    updateGroup: mocks.updateGroup,
  },
}));

vi.mock('@/api/provider', () => ({
  providerAPI: {
    listApiServices: mocks.listApiServices,
    getServiceMetadata: mocks.getServiceMetadata,
  },
}));

vi.mock('@/api/tool', () => ({
  toolAPI: {
    list: mocks.listTools,
    setEnabled: mocks.setToolEnabled,
  },
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({
    success: mocks.toastSuccess,
    error: mocks.toastError,
    info: vi.fn(),
    warning: vi.fn(),
  }),
}));

vi.mock('@/components/common/PageHeader', () => ({
  default: ({ title, description }: { title: string; description?: string }) => (
    <div>
      <h1>{title}</h1>
      {description ? <p>{description}</p> : null}
    </div>
  ),
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading...</div>,
}));

vi.mock('../Tool/components/ToolDetailModal', () => ({
  default: () => null,
}));

describe('DeviceIntegrationPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    mocks.listDevices.mockResolvedValue({
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
    mocks.listApiServices.mockResolvedValue({
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
    mocks.listGroups.mockResolvedValue({
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
    mocks.getServiceMetadata.mockResolvedValue({
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
    mocks.listTools.mockResolvedValue({ data: [] });
    mocks.setToolEnabled.mockResolvedValue({ data: {} });
  });

  it('clicking the blank backdrop closes the config panel', async () => {
    const user = userEvent.setup();

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
