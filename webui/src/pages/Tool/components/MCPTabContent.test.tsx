import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import MCPTabContent from './MCPTabContent';

const { listAllToolPages, mcpAPI, mcpDetailProps } = vi.hoisted(() => ({
  listAllToolPages: vi.fn(),
  mcpAPI: {
    list: vi.fn(),
    catalogInstall: vi.fn(),
    connect: vi.fn(),
  },
  mcpDetailProps: vi.fn(),
}));

vi.mock('@/api/mcp', () => ({
  mcpAPI,
}));

vi.mock('@/api/tool', () => ({
  listAllToolPages,
  toolAPI: {
    test: vi.fn(),
  },
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading</div>,
}));

vi.mock('@/components/common/EmptyState', () => ({
  default: ({ title }: { title: string }) => <div>{title}</div>,
}));

vi.mock('./ServiceDetailPanel', () => ({
  MCPServerDetailPanel: (props: { serverTools: Array<{ name: string }> }) => {
    mcpDetailProps(props);
    return (
      <div>
        detail-panel
        {props.serverTools.map((tool) => <span key={tool.name}>{tool.name}</span>)}
      </div>
    );
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      if (key === 'credentials.enterField') {
        return `请输入 ${String(options?.field || '')}`;
      }
      const translations: Record<string, string> = {
        'catalog.all': '全部',
        'mcp.refreshStatus': '刷新状态',
        'button.install': '安装',
        'button.cancel': '取消',
        'button.confirmConfig': '确认配置',
        'credentials.configNote': '配置必要的 API 密钥后即可使用',
        'mcp.configuring': '配置中...',
        'alert.fillAllRequired': '请填写所有必填字段',
        'alert.mcpConfiguredDisabled': '已添加但未启用',
        'mcp.noServers': '暂无 MCP 服务',
      };
      return translations[key] ?? key;
    },
    i18n: { language: 'zh-CN' },
  }),
}));

describe('MCPTabContent', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, 'alert').mockImplementation(() => {});
    mcpAPI.list.mockResolvedValue({ data: {} });
    mcpAPI.catalogInstall.mockResolvedValue({
      data: {
        config: { enabled: false },
      },
    });
    mcpAPI.connect.mockResolvedValue({ data: true });
    listAllToolPages.mockResolvedValue([]);
  });

  it('collects credentials and env vars before installing protected catalog entries', async () => {
    const user = userEvent.setup();
    const onConfiguredChange = vi.fn();
    const onRefreshTools = vi.fn().mockResolvedValue(undefined);

    render(
      <MCPTabContent
        tools={[]}
        searchQuery=""
        onSelectTool={vi.fn()}
        onRefreshTools={onRefreshTools}
        catalogEntries={[
          {
            id: 'panther',
            name: 'Panther',
            description: 'Panther SIEM',
            category: 'siem',
            tool_type: 'mcp',
            github: 'panther-labs/mcp-panther',
            language: 'python',
            license: 'MIT',
            stars: 10,
            transport: 'local',
            install: { local_command: ['python', '-m', 'mcp_panther'] },
            env_vars: {
              PANTHER_API_TOKEN: {
                required: true,
                description: 'API token',
                secret: true,
              },
              PANTHER_API_HOST: {
                required: true,
                description: 'API host',
                secret: false,
              },
            },
            system_deps: [],
            tags: ['siem'],
            official: false,
            requires_auth: true,
          },
        ]}
        catalogCategories={{ siem: { label: 'SIEM', description: 'siem' } }}
        catalogLoading={false}
        configuredIds={new Set()}
        onConfiguredChange={onConfiguredChange}
      />,
    );

    await user.click(screen.getByRole('button', { name: '安装' }));

    expect(mcpAPI.catalogInstall).not.toHaveBeenCalled();

    const modal = screen.getByText('配置必要的 API 密钥后即可使用').closest('div')?.parentElement?.parentElement;
    expect(modal).toBeTruthy();

    await user.type(within(modal as HTMLElement).getByPlaceholderText('请输入 PANTHER_API_TOKEN'), 'secret-token');
    await user.type(within(modal as HTMLElement).getByPlaceholderText('请输入 PANTHER_API_HOST'), 'https://panther.example.com');
    await user.click(within(modal as HTMLElement).getByRole('button', { name: '确认配置' }));

    await waitFor(() => {
      expect(mcpAPI.catalogInstall).toHaveBeenCalledWith('panther', {
        credentials: {
          PANTHER_API_TOKEN: 'secret-token',
        },
        env_overrides: {
          PANTHER_API_HOST: 'https://panther.example.com',
        },
      });
    });

    expect(onConfiguredChange).toHaveBeenCalledWith('panther');
    expect(onRefreshTools).toHaveBeenCalled();
  });

  it('loads the complete tool list when a server detail drawer opens', async () => {
    const user = userEvent.setup();
    mcpAPI.list.mockResolvedValue({
      data: [
        {
          name: 'server-a',
          status: 'connected',
          tools: [],
          resources: [],
          tools_count: 2,
          resources_count: 0,
        },
      ],
    });
    listAllToolPages.mockResolvedValue([
      {
        name: 'complete-tool',
        description: 'Loaded beyond the current page',
        category: 'custom',
        source: 'mcp',
        source_name: 'server-a',
        enabled: true,
      },
    ]);

    render(
      <MCPTabContent
        tools={[]}
        searchQuery=""
        onSelectTool={vi.fn()}
        onRefreshTools={vi.fn().mockResolvedValue(undefined)}
        catalogEntries={[]}
        catalogCategories={{}}
        catalogLoading={false}
        configuredIds={new Set(['server-a'])}
        onConfiguredChange={vi.fn()}
      />,
    );

    await user.click(await screen.findByText('server-a'));

    await waitFor(() => {
      expect(listAllToolPages).toHaveBeenCalledWith({
        source: 'mcp',
        sourceName: 'server-a',
        sortBy: 'name',
        sortDir: 'asc',
      });
    });
    expect(await screen.findByText('complete-tool')).toBeInTheDocument();
    expect(mcpDetailProps).toHaveBeenLastCalledWith(
      expect.objectContaining({
        serverTools: [expect.objectContaining({ name: 'complete-tool' })],
      }),
    );
  });
});
