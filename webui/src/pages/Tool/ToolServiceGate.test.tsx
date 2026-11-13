vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: { role: 'admin' } }) }));
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ToolPage from './index';

// INC-F5C08C1C: a tool whose API service is disabled cannot be switched on from
// its own drawer. The drawer says so and takes the user to the service.

const { getToolMock, setEnabledMock, listApiServicesMock } = vi.hoisted(() => ({
  getToolMock: vi.fn(),
  setEnabledMock: vi.fn(),
  listApiServicesMock: vi.fn(),
}));

const blockedTool = {
  name: 'af_api_app_traffic_top',
  description: 'AF app traffic top',
  source: 'api',
  source_name: 'af_api',
  category: 'custom',
  enabled: false,
  enabled_default: true,
  enabled_customized: false,
  service_enabled: false,
  parameters: [],
};

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      typeof options?.defaultValue === 'string' ? options.defaultValue : key,
    i18n: { language: 'zh-CN' },
  }),
}));

vi.mock('@/components/common/Toast', async (importOriginal) => ({
  // Keep the module's other exports (GroupNav uses useOptionalToast).
  ...(await importOriginal<typeof import('@/components/common/Toast')>()),
  useToast: () => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }),
}));

vi.mock('@/hooks/useTools', () => ({
  useToolPage: () => ({
    tools: [blockedTool],
    total: 1,
    facets: {
      category: { custom: 1 },
      source: { api: 1 },
      source_groups: { api: 1 },
      source_name: { af_api: 1 },
      enabled: { false: 1 },
    },
    loading: false,
    error: null,
    initialized: true,
    refetch: vi.fn(),
    reload: vi.fn(),
  }),
}));

vi.mock('@/api/tool', () => ({
  canDirectlyTestTool: vi.fn(() => true),
  listAllToolPages: vi.fn(() => Promise.resolve([])),
  toolAPI: {
    get: getToolMock,
    setEnabled: setEnabledMock,
    listFixtures: vi.fn(() => Promise.resolve({ data: [] })),
  },
}));

vi.mock('@/api/mcp', () => ({
  mcpAPI: {
    list: vi.fn(() => Promise.resolve({ data: {} })),
    catalogList: vi.fn(() => Promise.resolve({ data: [] })),
    catalogCategories: vi.fn(() => Promise.resolve({ data: {} })),
    catalogConfigured: vi.fn(() => Promise.resolve({ data: [] })),
  },
}));

vi.mock('@/api/provider', () => ({ providerAPI: { listApiServices: listApiServicesMock } }));

vi.mock('./components/ServiceDetailPanel', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./components/ServiceDetailPanel')>()),
  APIServiceDetailPanel: ({ serviceName, enabled }: { serviceName: string; enabled?: boolean }) => (
    <div>service-panel:{serviceName}:{enabled ? 'on' : 'off'}</div>
  ),
}));

describe('ToolPage: tool gated by a disabled API service', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getToolMock.mockResolvedValue({ data: blockedTool });
    listApiServicesMock.mockResolvedValue({ data: [
      { id: 'af_api', name: 'af_api', enabled: false, status: 'disabled', tool_count: 1 },
      { id: 'other_api', name: 'other_api', enabled: true, status: 'unknown', tool_count: 0 },
    ] });
  });

  const openBlockedTool = async () => {
    fireEvent.click(screen.getByRole('button', { name: blockedTool.name }));
    await screen.findByRole('note');
  };
  const tab = (key: string) => screen.getByRole('button', { name: new RegExp(`^${key.replace('.', '\\.')}`) });

  it('goes from the drawer to the service panel on the API tab', async () => {
    render(<ToolPage />);
    await openBlockedTool();
    expect(screen.getByRole('switch', { name: 'toolDetail.status' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'toolDetail.goToApiService' }));

    // The tool drawer is gone and the service's own panel is open instead.
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(await screen.findByText('service-panel:af_api:off')).toBeInTheDocument();
    expect(setEnabledMock).not.toHaveBeenCalled();
  });

  it('keeps what a refused enable said about the service when the page re-renders the drawer', async () => {
    // The row and the detail know nothing about the service yet.
    const { service_enabled: _flag, service_integration_type: _type, ...staleTool } = { ...blockedTool, service_integration_type: null };
    getToolMock.mockResolvedValue({ data: staleTool });
    setEnabledMock.mockResolvedValue({ data: { ...blockedTool, service_integration_type: 'device' } });
    render(<MemoryRouter><ToolPage /></MemoryRouter>);

    fireEvent.click(screen.getByRole('button', { name: blockedTool.name }));
    const toggle = await screen.findByRole('switch', { name: 'toolDetail.status' });
    await waitFor(() => expect(getToolMock).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole('note')).toBeNull());
    fireEvent.click(toggle);

    // The page merges the answer into its copy of the tool and re-renders the
    // drawer: the device wording and link must survive that prop sync.
    expect(await screen.findByRole('link', { name: 'toolDetail.goToDevices' })).toHaveAttribute('href', '/devices');
    expect(screen.getByRole('note')).toHaveTextContent('toolDetail.serviceDisabledDevice');
    expect(screen.queryByRole('button', { name: 'toolDetail.goToApiService' })).toBeNull();
    expect(screen.getByRole('switch', { name: 'toolDetail.status' })).toBeDisabled();
    expect(setEnabledMock).toHaveBeenCalledExactlyOnceWith(blockedTool.name, true);
  });

  it('does not reopen the service panel when the API tab is visited again', async () => {
    render(<ToolPage />);
    await openBlockedTool();
    fireEvent.click(screen.getByRole('button', { name: 'toolDetail.goToApiService' }));
    await screen.findByText('service-panel:af_api:off');

    fireEvent.click(tab('tabs.all'));
    await waitFor(() => expect(screen.queryByText('service-panel:af_api:off')).toBeNull());
    fireEvent.click(tab('tabs.api'));
    // The service row is back, its panel is not.
    await screen.findByText('other_api', { selector: 'span' });
    expect(screen.queryByText('service-panel:af_api:off')).toBeNull();

    // Asking again from the tool does open it again.
    fireEvent.click(tab('tabs.all'));
    await openBlockedTool();
    fireEvent.click(screen.getByRole('button', { name: 'toolDetail.goToApiService' }));
    expect(await screen.findByText('service-panel:af_api:off')).toBeInTheDocument();
  });
});
