import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import APITabContent from './APITabContent';

const { apiDetailProps, listAllToolPages, mcpAPI, providerAPI } = vi.hoisted(() => ({
  apiDetailProps: vi.fn(),
  listAllToolPages: vi.fn(),
  mcpAPI: {
    catalogInstall: vi.fn(),
    connect: vi.fn(),
  },
  providerAPI: {
    listApiServices: vi.fn(),
    updateApiService: vi.fn(),
    deleteApiService: vi.fn(),
  },
}));

vi.mock('@/api/provider', () => ({ providerAPI }));
vi.mock('@/api/mcp', () => ({ mcpAPI }));
vi.mock('@/api/tool', () => ({ listAllToolPages }));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading</div>,
}));

vi.mock('@/components/common/EmptyState', () => ({
  default: ({ title }: { title: string }) => <div>{title}</div>,
}));

vi.mock('./ServiceDetailPanel', () => ({
  APIServiceDetailPanel: (props: { serviceTools: Array<{ name: string }> }) => {
    apiDetailProps(props);
    return (
      <div>
        detail-panel
        {props.serviceTools.map((tool) => <span key={tool.name}>{tool.name}</span>)}
      </div>
    );
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN' },
  }),
}));

describe('APITabContent', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    providerAPI.listApiServices.mockResolvedValue({ data: [] });
    listAllToolPages.mockResolvedValue([]);
  });

  it('loads the complete tool list when a service detail drawer opens', async () => {
    const user = userEvent.setup();
    providerAPI.listApiServices.mockResolvedValue({
      data: [
        {
          id: 'service-a',
          name: 'Service A',
          description: 'Service A API',
          enabled: true,
          status: 'connected',
          tool_count: 2,
          verify_ssl: false,
        },
      ],
    });
    listAllToolPages.mockResolvedValue([
      {
        name: 'complete-api-tool',
        description: 'Loaded beyond the current page',
        category: 'custom',
        source: 'api',
        source_name: 'service-a',
        enabled: true,
      },
    ]);

    render(
      <APITabContent
        tools={[]}
        onSelectTool={vi.fn()}
        onRefreshTools={vi.fn().mockResolvedValue(undefined)}
        catalogEntries={[]}
        catalogCategories={{}}
        catalogLoading={false}
        configuredIds={new Set()}
        onConfiguredChange={vi.fn()}
      />,
    );

    await user.click((await screen.findByText('Service A')).closest('button')!);

    await waitFor(() => {
      expect(listAllToolPages).toHaveBeenCalledWith({
        source: 'api',
        sourceName: 'service-a',
        sortBy: 'name',
        sortDir: 'asc',
      });
    });
    expect(await screen.findByText('complete-api-tool')).toBeInTheDocument();
    expect(apiDetailProps).toHaveBeenLastCalledWith(
      expect.objectContaining({
        serviceTools: [expect.objectContaining({ name: 'complete-api-tool' })],
      }),
    );
  });
});
