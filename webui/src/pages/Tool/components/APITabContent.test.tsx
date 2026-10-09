import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import APITabContent from './APITabContent';

vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: { role: 'admin' } }) }));

const { apiDetailProps, listAllToolPages, mcpAPI, providerAPI, toastWarning } = vi.hoisted(() => ({
  toastWarning: vi.fn(),
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
vi.mock('@/components/common/Toast', () => ({ useToast: () => ({ warning: toastWarning }) }));
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

  it.each([' Alpha ', 'QA-ALPHA_V2', 'unique English', '独特描述'])('searches service metadata independently of child tools (%s)', async (searchQuery) => {
    providerAPI.listApiServices.mockResolvedValue({ data: [
      { id: 'qa-alpha_v2', name: 'QA Alpha', description: 'unique English description', description_cn: '独特描述', group: 'Ops', enabled: false, tool_count: 0 },
      { id: 'qa-beta_v1', name: 'QA Beta', description: 'Other description', group: 'Ops', enabled: false, tool_count: 0 },
    ] });
    render(<APITabContent tools={[]} searchQuery={searchQuery} matchingToolServices={{}}
      onSelectTool={vi.fn()} onRefreshTools={vi.fn()} catalogEntries={[]} catalogCategories={{}}
      catalogLoading={false} configuredIds={new Set()} onConfiguredChange={vi.fn()} />);
    expect(await screen.findByText('QA Alpha')).toBeInTheDocument();
    expect(screen.queryByText('QA Beta')).not.toBeInTheDocument();
    expect(listAllToolPages).not.toHaveBeenCalled();
  });

  it('composes search with group selection, shows no matches, and restores rows without closing details', async () => {
    providerAPI.listApiServices.mockResolvedValue({ data: [
      { id: 'qa-alpha', name: 'QA Alpha', group: 'Ops', enabled: false, tool_count: 0 },
      { id: 'qa-beta', name: 'QA Beta', group: 'Ops', enabled: false, tool_count: 0 },
      { id: 'other-alpha', name: 'Outside Alpha', group: 'Other', enabled: false, tool_count: 0 },
    ] });
    listAllToolPages.mockResolvedValue([{ name: 'complete-drawer-tool' }]);
    const props = { tools: [], matchingToolServices: {}, onSelectTool: vi.fn(), onRefreshTools: vi.fn(),
      catalogEntries: [], catalogCategories: {}, catalogLoading: false, configuredIds: new Set<string>(), onConfiguredChange: vi.fn() };
    const { rerender } = render(<APITabContent {...props} />);
    await screen.findByText('QA Alpha');
    fireEvent.click(screen.getByRole('button', { name: 'Ops 2' }));
    fireEvent.click(screen.getByText('QA Alpha', { selector: 'span' }).closest('button')!);
    expect(await screen.findByText('complete-drawer-tool')).toBeInTheDocument();
    rerender(<APITabContent {...props} searchQuery="Alpha" />);
    expect(screen.getByText('QA Alpha', { selector: 'span' })).toBeInTheDocument();
    expect(screen.queryByText('QA Beta')).not.toBeInTheDocument();
    expect(screen.queryByText('Outside Alpha')).not.toBeInTheDocument();
    rerender(<APITabContent {...props} searchQuery="zz-no-match-765" />);
    expect(screen.getByText('api.noTools')).toBeInTheDocument();
    expect(screen.queryByText('QA Alpha', { selector: 'span' })).not.toBeInTheDocument();
    expect(screen.queryByText('QA Beta')).not.toBeInTheDocument();
    expect(screen.getByText('complete-drawer-tool')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ops 2' })).toHaveAttribute('aria-pressed', 'true');
    rerender(<APITabContent {...props} searchQuery="" />);
    expect(screen.getByText('QA Alpha', { selector: 'span' })).toBeInTheDocument();
    expect(screen.getByText('QA Beta')).toBeInTheDocument();
    expect(screen.queryByText('Outside Alpha')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ops 2' })).toHaveAttribute('aria-pressed', 'true');
    expect(listAllToolPages).toHaveBeenCalledExactlyOnceWith({ source: 'api', sourceName: 'qa-alpha', sortBy: 'name', sortDir: 'asc' });
  });

  it('uses complete tool facets, not the current page, and waits for pending tool matches', async () => {
    providerAPI.listApiServices.mockResolvedValue({ data: [
      { id: 'alpha_v1', name: 'QA Alpha', enabled: true, tool_count: 25 },
      { id: 'beta_v2', name: 'QA Beta', enabled: true, tool_count: 1 },
      { id: 'beta_v1', name: 'Other Beta version', enabled: true, tool_count: 0 },
    ] });
    const tools = Array.from({ length: 25 }, (_, index) => ({ name: `needle_${index}`, description: 'needle',
      source: 'api' as const, source_name: 'alpha_v1', category: 'custom', parameters: [], enabled: true, requires_confirmation: false }));
    const props = { tools, searchQuery: 'needle', onSelectTool: vi.fn(), onRefreshTools: vi.fn(),
      catalogEntries: [], catalogCategories: {}, catalogLoading: false, configuredIds: new Set<string>(), onConfiguredChange: vi.fn() };
    const { rerender } = render(<APITabContent {...props} matchingToolServices={{ alpha_v1: 25, beta_v2: 1 }} toolSearchPending />);
    await waitFor(() => expect(providerAPI.listApiServices).toHaveBeenCalledOnce());
    expect(screen.queryByText('QA Alpha', { selector: 'span' })).not.toBeInTheDocument();
    expect(screen.queryByText('QA Beta')).not.toBeInTheDocument();
    expect(screen.queryByText('api.noTools')).not.toBeInTheDocument();
    expect(screen.getByText('loading')).toBeInTheDocument();
    rerender(<APITabContent {...props} matchingToolServices={{ alpha_v1: 25, beta_v2: 1 }} />);
    expect(screen.getByText('QA Alpha', { selector: 'span' })).toBeInTheDocument();
    expect(screen.getByText('QA Beta')).toBeInTheDocument();
    expect(screen.queryByText('Other Beta version')).not.toBeInTheDocument();
    // A failed/missing summary must not fall back to matching the 25-row sample.
    rerender(<APITabContent {...props} />);
    expect(screen.queryByText('QA Alpha', { selector: 'span' })).not.toBeInTheDocument();
    expect(screen.getByText('api.noTools')).toBeInTheDocument();
    expect(listAllToolPages).not.toHaveBeenCalled();
  });

  it('keeps catalog keyword search independent of service and child-tool matches', async () => {
    providerAPI.listApiServices.mockResolvedValue({ data: [{ id: 'service', name: 'Native service', enabled: false, tool_count: 0 }] });
    const props = { tools: [], onSelectTool: vi.fn(), onRefreshTools: vi.fn(),
      catalogEntries: [{ id: 'catalog', name: 'Catalog API', description: 'Catalog description', category: 'intel', tool_type: 'api' as const,
        github: '', language: 'python', license: 'MIT', stars: 1, transport: 'stdio', install: {}, env_vars: {},
        system_deps: [], tags: ['catalog-keyword'], official: false, requires_auth: false }],
      catalogCategories: {}, catalogLoading: false, configuredIds: new Set<string>(), onConfiguredChange: vi.fn() };
    const { rerender } = render(<APITabContent {...props} searchQuery="catalog-keyword" matchingToolServices={{}} />);
    expect(await screen.findByText('Catalog API')).toBeInTheDocument();
    expect(screen.queryByText('Native service')).not.toBeInTheDocument();
    rerender(<APITabContent {...props} searchQuery="no-catalog-match" matchingToolServices={{}} />);
    expect(screen.queryByText('Catalog API')).not.toBeInTheDocument();
    expect(screen.getByText('api.noTools')).toBeInTheDocument();
    expect(mcpAPI.catalogInstall).not.toHaveBeenCalled();
  });

  it('reports the full API inventory count across filters and native reloads', async () => {
    const service = { id: 'api', name: 'API service', enabled: false, tool_count: 0, group: 'Ops' };
    providerAPI.listApiServices.mockResolvedValue({ data: [service, { ...service, id: 'device', integration_type: 'device' }] });
    const onServiceCountChange = vi.fn();
    const props = { tools: [], onSelectTool: vi.fn(), onRefreshTools: vi.fn(), onServiceCountChange,
      catalogEntries: [], catalogCategories: {}, catalogLoading: false, configuredIds: new Set<string>(), onConfiguredChange: vi.fn() };
    const { rerender } = render(<APITabContent {...props} />);
    await waitFor(() => expect(onServiceCountChange).toHaveBeenLastCalledWith(1));
    fireEvent.click(screen.getByRole('button', { name: 'Ops 1' }));
    rerender(<APITabContent {...props} searchQuery="no-match" />);
    expect(screen.getByText('api.noTools')).toBeInTheDocument();
    expect(onServiceCountChange).toHaveBeenLastCalledWith(1);
    providerAPI.listApiServices.mockResolvedValue({ data: [service, { ...service, id: 'added' }] });
    rerender(<APITabContent {...props} searchQuery="no-match" refreshKey={1} />);
    await waitFor(() => expect(onServiceCountChange).toHaveBeenLastCalledWith(2));
    expect(screen.getByRole('button', { name: 'Ops 2' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('keeps versioned service identities, full group counts, card fields/actions and independent drawer tools', async () => {
    providerAPI.listApiServices.mockResolvedValue({ data: [
      { id: 'service-a__v9_2', name: 'Service A', version: '9.2', description: 'Service A description', enabled: true, status: 'connected', tool_count: 40, latency_ms: 12, verify_ssl: false, group: 'Alpha' },
      { id: 'service-b', name: 'Service B', enabled: false, status: 'disabled', tool_count: 0, verify_ssl: true, group: null, builtin: true, group_readonly: false },
      { id: 'device-hidden', name: 'Not an API row', integration_type: 'device', enabled: true, tool_count: 10, verify_ssl: false },
    ] });
    listAllToolPages.mockResolvedValue([{ name: 'ungrouped-child-tool', source: 'api', group: null }]);
    const props = {
      tools: [], onSelectTool: vi.fn(), onRefreshTools: vi.fn().mockResolvedValue(undefined),
      catalogEntries: [], catalogCategories: {}, catalogLoading: false, configuredIds: new Set<string>(), onConfiguredChange: vi.fn(),
    };
    const { rerender } = render(<APITabContent {...props} />);
    await screen.findByText('Service A');
    await waitFor(() => expect(screen.getByRole('button', { name: 'all 2' })).toBeInTheDocument());
    expect(screen.queryByText('Not an API row')).not.toBeInTheDocument();
    const nativeRow = screen.getByText('Service A').closest('[draggable]') as HTMLElement;
    expect(nativeRow.children).toHaveLength(6);
    const originalText = nativeRow.textContent;
    expect(within(nativeRow).getByText('v9.2')).toBeInTheDocument();
    expect(within(nativeRow).getByText('40')).toBeInTheDocument();
    expect(within(nativeRow).getByText('12ms')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Alpha 1' }));
    expect(screen.queryByText('Service B')).not.toBeInTheDocument();
    rerender(<APITabContent {...props} viewMode="cards" />);
    const card = screen.getByText('Service A').closest('[draggable]') as HTMLElement;
    expect(card.textContent).toBe(originalText);
    fireEvent.click(within(card).getByRole('button', { name: 'mcp.manage' }));
    expect(await screen.findByText('ungrouped-child-tool')).toBeInTheDocument();
    expect(listAllToolPages).toHaveBeenCalledWith({ source: 'api', sourceName: 'service-a__v9_2', sortBy: 'name', sortDir: 'asc' });
    fireEvent.click(screen.getByRole('button', { name: 'ungrouped 1' }));
    expect(screen.getByText('Service B')).toBeInTheDocument();
    expect(screen.getByText('ungrouped-child-tool')).toBeInTheDocument();

    const ungroupedRow = screen.getByText('Service B').closest('[draggable]') as HTMLElement;
    fireEvent.click(within(ungroupedRow).getByRole('button', { name: 'editGroup' }));
    expect(screen.getByText('ungrouped-child-tool')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Alpha' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
    await waitFor(() => expect(providerAPI.updateApiService).toHaveBeenCalledWith('service-b', { group: 'Alpha' }));
    await waitFor(() => expect(providerAPI.listApiServices).toHaveBeenCalledWith({ force: true }));
    expect(props.onRefreshTools).not.toHaveBeenCalled();
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
  });

  it('locks definition-only API rows but leaves builtin configured instances editable', async () => {
    providerAPI.listApiServices.mockResolvedValue({ data: [
      { id: 'definition', name: 'System API definition', builtin: true, group_readonly: true, group: 'Mixed', enabled: false, tool_count: 0 },
      { id: 'configured', name: 'Configured API', builtin: true, group_readonly: false, group: 'Mixed', enabled: false, tool_count: 0 },
    ] });
    render(<APITabContent tools={[]} onSelectTool={vi.fn()} onRefreshTools={vi.fn()}
      catalogEntries={[]} catalogCategories={{}} catalogLoading={false} configuredIds={new Set()} onConfiguredChange={vi.fn()} />);
    await screen.findByText('System API definition');
    const definition = screen.getByText('System API definition').closest('[draggable]')!;
    const dataTransfer = { setData: vi.fn() };
    expect(fireEvent.dragStart(definition, { dataTransfer })).toBe(false);
    expect(dataTransfer.setData).not.toHaveBeenCalled();
    fireEvent.click(within(definition as HTMLElement).getByRole('button', { name: 'editGroup' }));
    expect(toastWarning).toHaveBeenCalledTimes(2);
    expect(toastWarning).toHaveBeenLastCalledWith('pluginGroups:readOnly.system');
    expect(providerAPI.updateApiService).not.toHaveBeenCalled();
    expect(providerAPI.listApiServices).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'renameNamed' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'deleteNamed' })).toBeDisabled();
    fireEvent.keyDown(definition, { key: 'm', altKey: true });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'create' }));
    expect(within(screen.getByRole('combobox')).queryByRole('option', { name: /System API definition/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'cancel' }));
    fireEvent.click(within(screen.getByText('Configured API').closest('[draggable]') as HTMLElement).getByRole('button', { name: 'editGroup' }));
    expect(apiDetailProps).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'save' }));
    await waitFor(() => expect(providerAPI.updateApiService).toHaveBeenCalledExactlyOnceWith('configured', { group: null }));
  });

  it.each(['move', 'create', 'rename', 'delete'])('rechecks fresh definition-only API rows before %s', async (operation) => {
    const service = { id: 'service', name: 'Native API', group: 'Ops', group_readonly: false, enabled: false, tool_count: 0 };
    providerAPI.listApiServices.mockResolvedValueOnce({ data: [service] }).mockResolvedValue({ data: [{ ...service, group_readonly: true }] });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<APITabContent tools={[]} onSelectTool={vi.fn()} onRefreshTools={vi.fn()}
      catalogEntries={[]} catalogCategories={{}} catalogLoading={false} configuredIds={new Set()} onConfiguredChange={vi.fn()} />);
    await screen.findByText('Native API');
    if (operation === 'move') fireEvent.keyDown(screen.getByText('Native API').closest('[draggable]')!, { key: 'm', altKey: true });
    else fireEvent.click(screen.getByRole('button', { name: operation === 'create' ? 'create' : `${operation}Named` }));
    if (operation === 'create' || operation === 'rename') fireEvent.change(screen.getByRole('textbox'), { target: { value: 'New' } });
    if (operation === 'create') fireEvent.change(screen.getByRole('combobox'), { target: { value: 'service' } });
    if (operation !== 'delete') fireEvent.click(screen.getByRole('button', { name: 'save' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('pluginGroups:errors.readOnlyMembers'));
    expect(providerAPI.updateApiService).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it('keeps unconfigured catalog attributes read-only, with no artificial service creation', async () => {
    render(<APITabContent
      tools={[]} onSelectTool={vi.fn()} onRefreshTools={vi.fn().mockResolvedValue(undefined)}
      catalogEntries={[{
        id: 'catalog-native', name: 'Catalog API', description: 'Catalog description', category: 'intel', tool_type: 'api',
        github: '', language: 'python', license: 'MIT', stars: 7, transport: 'stdio', install: {}, env_vars: {},
        system_deps: [], tags: [], official: false, requires_auth: false, group: 'Pack default', group_readonly: true,
      }]}
      catalogCategories={{ intel: { label: 'Intel', description: 'intel' } }} catalogLoading={false}
      configuredIds={new Set()} onConfiguredChange={vi.fn()}
    />);
    await screen.findByText('Catalog API');
    const row = screen.getByText('Catalog API').closest('[draggable]') as HTMLElement;
    const dataTransfer = { setData: vi.fn() };
    expect(fireEvent.dragStart(row, { dataTransfer })).toBe(false);
    expect(dataTransfer.setData).not.toHaveBeenCalled();
    fireEvent.click(within(row).getByRole('button', { name: 'editGroup' }));
    expect(toastWarning).toHaveBeenLastCalledWith('pluginGroups:readOnly.system');
    fireEvent.keyDown(row, { key: 'm', altKey: true });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'renameNamed' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'deleteNamed' })).toBeDisabled();
    expect(providerAPI.updateApiService).not.toHaveBeenCalled();
    expect(mcpAPI.catalogInstall).not.toHaveBeenCalled();
  });

  it('keeps the native rows and selected group when reloading after an action fails', async () => {
    const response = { data: [{ id: 'service', name: 'Native API', group: 'Ops', enabled: true, tool_count: 0 }] };
    providerAPI.listApiServices.mockResolvedValueOnce(response).mockRejectedValueOnce(new Error('API list offline')).mockResolvedValue(response);
    providerAPI.updateApiService.mockResolvedValue({ data: {} });
    render(<APITabContent tools={[]} onSelectTool={vi.fn()} onRefreshTools={vi.fn()}
      catalogEntries={[]} catalogCategories={{}} catalogLoading={false} configuredIds={new Set()} onConfiguredChange={vi.fn()} />);
    await screen.findByText('Native API');
    fireEvent.click(screen.getByRole('button', { name: 'Ops 1' }));
    fireEvent.click(screen.getByTitle('detail.disableServer'));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('API list offline'));
    expect(screen.getByText('Native API')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ops 1' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'button.retry' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(providerAPI.updateApiService).toHaveBeenCalledExactlyOnceWith('service', { enabled: false });
  });

  it.each(['create', 'rename'])('rejects fresh native API name collisions before %s writes', async (operation) => {
    const service = { id: 'service__v1', name: 'Native API', group: 'Ops', enabled: true, tool_count: 0 };
    providerAPI.listApiServices.mockResolvedValueOnce({ data: [service] }).mockResolvedValue({ data: [service, { ...service, id: 'other__v2', group: 'Existing' }] });
    render(<APITabContent tools={[]} onSelectTool={vi.fn()} onRefreshTools={vi.fn()}
      catalogEntries={[]} catalogCategories={{}} catalogLoading={false} configuredIds={new Set()} onConfiguredChange={vi.fn()} />);
    await screen.findByText('Native API');
    fireEvent.click(screen.getByRole('button', { name: operation === 'create' ? 'create' : 'renameNamed' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'dialog.name' }), { target: { value: 'Existing' } });
    if (operation === 'create') fireEvent.change(screen.getByRole('combobox'), { target: { value: 'service__v1' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'save' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('pluginGroups:validation.duplicate'));
    expect(providerAPI.listApiServices).toHaveBeenLastCalledWith({ force: true });
    expect(providerAPI.updateApiService).not.toHaveBeenCalled();
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
