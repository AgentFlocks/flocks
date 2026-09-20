import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import MCPTabContent from './MCPTabContent';

vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: { role: 'admin' } }) }));

const { listAllToolPages, mcpAPI, mcpDetailProps } = vi.hoisted(() => ({
  listAllToolPages: vi.fn(),
  mcpAPI: {
    list: vi.fn(),
    update: vi.fn(),
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

  it('groups unified service rows without truncating drawers or changing alternate-view actions', async () => {
    mcpAPI.list.mockResolvedValue({ data: {
      'server-a': { status: 'connected', tools_count: 42, tools: [], resources: [], group: 'Alpha' },
    } });
    const entries = ['server-a', 'catalog-b'].map((id, index) => ({
      id, name: index ? 'Catalog B' : 'Server A', description: `${id} description`,
      category: 'siem', tool_type: 'mcp' as const, github: 'example/service', language: 'python',
      license: 'MIT', stars: 12, transport: 'stdio', install: {}, env_vars: {},
      system_deps: [], tags: [], official: false, requires_auth: false,
      group: index ? 'Beta' : 'Catalog default ignored',
    }));
    listAllToolPages.mockResolvedValue([{ name: 'outside-row-group', source: 'mcp', group: 'Beta' }]);
    const props = {
      tools: [], searchQuery: '', onSelectTool: vi.fn(), onRefreshTools: vi.fn().mockResolvedValue(undefined),
      catalogEntries: entries, catalogCategories: { siem: { label: 'SIEM', description: 'siem' } },
      catalogLoading: false, configuredIds: new Set(['server-a']), onConfiguredChange: vi.fn(),
    };
    const { rerender } = render(<MCPTabContent {...props} />);
    const sidebar = await screen.findByRole('complementary');
    await waitFor(() => expect(within(sidebar).getByRole('button', { name: 'all 2' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'SIEM (2)' })).toBeInTheDocument();
    const nativeRow = screen.getByText('Server A').closest('[draggable]') as HTMLElement;
    expect(nativeRow.children).toHaveLength(6);
    const originalText = nativeRow.textContent;
    const originalColumns = nativeRow.style.gridTemplateColumns;
    expect(within(nativeRow).getByText('42')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Alpha 1' }));
    expect(screen.queryByText('Catalog B')).not.toBeInTheDocument();
    rerender(<MCPTabContent {...props} viewMode="cards" />);
    const card = screen.getByText('Server A').closest('[draggable]') as HTMLElement;
    expect(card.textContent).toBe(originalText);
    expect(card.style.gridTemplateColumns).not.toBe(originalColumns);
    fireEvent.click(within(card).getByRole('button', { name: 'mcp.manage' }));
    expect(await screen.findByText('outside-row-group')).toBeInTheDocument();
    expect(listAllToolPages).toHaveBeenCalledWith({ source: 'mcp', sourceName: 'server-a', sortBy: 'name', sortDir: 'asc' });
    // Selecting a different business group never destroys or group-filters the open service drawer.
    fireEvent.click(screen.getByRole('button', { name: 'Beta 1' }));
    expect(screen.getByText('Catalog B')).toBeInTheDocument();
    expect(screen.getByText('outside-row-group')).toBeInTheDocument();
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
    expect(props.onRefreshTools).not.toHaveBeenCalled();
  });

  it('moves configured services with native metadata only and respects explicit clears over catalog defaults', async () => {
    mcpAPI.list.mockResolvedValue({ data: { 'server-a': { status: 'disabled', tools: [], resources: [], group: '', group_readonly: false } } });
    const entries = ['server-a', 'catalog-only'].map((id) => ({
      id, name: id, description: 'Native catalog', category: 'test', tool_type: 'mcp' as const,
      github: '', language: 'python', license: 'MIT', stars: 1, transport: 'stdio', install: {}, env_vars: {},
      system_deps: [], tags: [], official: false, requires_auth: false, group: 'Package', group_readonly: true,
    }));
    render(<MCPTabContent tools={[]} searchQuery="" onSelectTool={vi.fn()} onRefreshTools={vi.fn()}
      catalogEntries={entries} catalogCategories={{}} catalogLoading={false} configuredIds={new Set(['server-a'])} onConfiguredChange={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'ungrouped 1' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Package 1' })).toBeInTheDocument();
    expect(screen.getByText('catalog-only').closest('[draggable]')).toHaveAttribute('draggable', 'false');
    expect(screen.getByText('catalog-only').closest('[draggable]')).toHaveAttribute('title', 'pluginGroups:readOnly.system');
    const row = screen.getByText('server-a').closest('[draggable]')!;
    fireEvent.keyDown(row, { key: 'm', altKey: true });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Package' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
    await waitFor(() => expect(mcpAPI.update).toHaveBeenCalledExactlyOnceWith('server-a', { group: 'Package' }));
    expect(mcpAPI.list).toHaveBeenCalledTimes(3);
    expect(mcpAPI.connect).not.toHaveBeenCalled();
    expect(mcpAPI.catalogInstall).not.toHaveBeenCalled();
    expect(listAllToolPages).not.toHaveBeenCalled();
  });

  it('keeps the native rows and selected group on reload failure, then retries without reconnecting', async () => {
    const response = { data: { native: { status: 'connected', group: 'Ops', tools: [], resources: [] } } };
    mcpAPI.list.mockResolvedValueOnce(response).mockRejectedValueOnce(new Error('MCP list offline')).mockResolvedValue(response);
    const props = { tools: [], searchQuery: '', onSelectTool: vi.fn(), onRefreshTools: vi.fn(), catalogEntries: [],
      catalogCategories: {}, catalogLoading: false, configuredIds: new Set<string>(), onConfiguredChange: vi.fn() };
    const { rerender } = render(<MCPTabContent {...props} />);
    await screen.findByText('native');
    fireEvent.click(screen.getByRole('button', { name: 'Ops 1' }));
    rerender(<MCPTabContent {...props} refreshKey={1} />);
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('MCP list offline'));
    expect(screen.getByText('native')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ops 1' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'button.retry' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(mcpAPI.connect).not.toHaveBeenCalled();
    expect(mcpAPI.update).not.toHaveBeenCalled();
  });

  it.each(['create', 'rename'])('rejects fresh native MCP name collisions before %s writes', async (operation) => {
    const server = { status: 'connected', group: 'Ops', tools: [], resources: [] };
    mcpAPI.list.mockResolvedValueOnce({ data: { native: server } }).mockResolvedValue({ data: { native: server, new: { ...server, group: 'Existing' } } });
    render(<MCPTabContent tools={[]} searchQuery="" onSelectTool={vi.fn()} onRefreshTools={vi.fn()}
      catalogEntries={[]} catalogCategories={{}} catalogLoading={false} configuredIds={new Set()} onConfiguredChange={vi.fn()} />);
    await screen.findByText('native');
    fireEvent.click(screen.getByRole('button', { name: operation === 'create' ? 'create' : 'renameNamed' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'dialog.name' }), { target: { value: 'Existing' } });
    if (operation === 'create') fireEvent.change(screen.getByRole('combobox'), { target: { value: 'native' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('pluginGroups:validation.duplicate'));
    expect(mcpAPI.list).toHaveBeenCalledTimes(2);
    expect(mcpAPI.update).not.toHaveBeenCalled();
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
