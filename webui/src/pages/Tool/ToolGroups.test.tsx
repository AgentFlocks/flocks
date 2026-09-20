import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createInstance } from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import ToolPage from './index';
import { __resetToolsResourceForTesting } from '@/hooks/useTools';
import type { Tool } from '@/api/tool';
import zhTool from '@/locales/zh-CN/tool.json';
import enTool from '@/locales/en-US/tool.json';
import zhGroups from '@/locales/zh-CN/pluginGroups.json';
import enGroups from '@/locales/en-US/pluginGroups.json';

const mocks = vi.hoisted(() => ({
  listPage: vi.fn(), get: vi.fn(), patch: vi.fn(), refresh: vi.fn(), listServices: vi.fn(),
  auth: { role: 'admin' },
}));
vi.mock('@/api/client', () => ({ default: {
  get: (url: string, config?: { params?: Record<string, unknown> }) => {
    if (url === '/api/tools/page') return mocks.listPage(config?.params);
    if (url.endsWith('/fixtures')) return Promise.resolve({ data: [] });
    return mocks.get(url);
  },
  patch: (...args: unknown[]) => mocks.patch(...args),
  post: (...args: unknown[]) => mocks.refresh(...args),
} }));
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: mocks.auth }) }));
vi.mock('@/api/provider', () => ({ providerAPI: { listApiServices: mocks.listServices } }));
vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({ error: vi.fn(), warning: vi.fn(), success: vi.fn() }),
}));

let inventory: Tool[];
const nativeRow = (name: string) => screen.getByRole('button', { name: new RegExp(`^${name}(?: |$)`) }).closest('[draggable]') as HTMLElement;

async function mount(language = 'zh-CN') {
  const i18n = createInstance();
  await i18n.use(initReactI18next).init({
    lng: language,
    resources: {
      'zh-CN': { tool: zhTool, pluginGroups: zhGroups },
      'en-US': { tool: enTool, pluginGroups: enGroups },
    },
    interpolation: { escapeValue: false },
  });
  const result = render(<I18nextProvider i18n={i18n}><ToolPage /></I18nextProvider>);
  await screen.findByRole('button', { name: 'tool_00' });
  return result;
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  __resetToolsResourceForTesting();
  mocks.auth.role = 'admin';
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  inventory = Array.from({ length: 61 }, (_, index) => ({
    name: `tool_${String(index).padStart(2, '0')}`,
    description: `Complete description ${index}`,
    category: index % 2 ? 'file' : 'custom',
    source: 'plugin_py', source_name: 'Native provider', enabled: true,
    parameters: [], requires_confirmation: false,
    group: index < 30 ? 'Alpha' : index < 60 ? 'Beta' : null,
  }));
  mocks.listServices.mockResolvedValue({ data: [] });
  mocks.get.mockImplementation(async (url: string) => ({ data: inventory.find((tool) => url === `/api/tools/${tool.name}`) }));
  mocks.listPage.mockImplementation(async (params: Record<string, any>) => {
    const eligible = inventory.filter((tool) => (!params.q || tool.name.includes(params.q))
      && (!params.source || params.source.split(',').includes(tool.source))
      && (!params.category || params.category.split(',').includes(tool.category))
      && (!params.enabled || params.enabled.split(',').includes(String(tool.enabled))));
    const group: Record<string, number> = {};
    eligible.forEach((tool) => { const name = tool.group?.trim() || ''; group[name] = (group[name] ?? 0) + 1; });
    const selected = eligible.filter((tool) => params.group === undefined || (tool.group?.trim() || '') === params.group);
    return { data: {
      items: selected.slice(params.offset ?? 0, (params.offset ?? 0) + (params.limit ?? 25)),
      total: selected.length, offset: params.offset ?? 0, limit: params.limit ?? 25,
      facets: { group, category: { custom: 31, file: 30 }, source: { plugin_py: selected.length }, source_groups: {}, source_name: { 'Native provider': selected.length }, enabled: { true: selected.length } },
    } };
  });
  mocks.patch.mockImplementation(async (url: string, request: { group: string | null }) => {
    const item = inventory.find((tool) => url === `/api/tools/${encodeURIComponent(tool.name)}`)!;
    item.group = request.group;
    return { data: item };
  });
});

describe('Tools native group attributes and original view preservation', () => {
  it.each([
    ['zh-CN', ['工具名称', '来源', '供应商', '状态', '操作'], '管理'],
    ['en-US', ['Tool Name', 'Source', 'Provider', 'Status', 'Actions'], 'Manage'],
  ] as const)('keeps all native list columns, grids and actions in %s', async (language, labels, manage) => {
    await mount(language);
    const header = screen.getByText(labels[0]).parentElement!;
    expect(Array.from(header.children).map((cell) => cell.textContent)).toEqual(['', ...labels]);
    expect(header.style.gridTemplateColumns).toBe('32px minmax(220px, 3fr) minmax(80px, 1fr) minmax(140px, 1.6fr) minmax(80px, 1fr) minmax(90px, 1fr)');
    const row = nativeRow('tool_00');
    expect(row.children).toHaveLength(6);
    expect(row.style.gridTemplateColumns).toBe(header.style.gridTemplateColumns);
    expect(within(row).getByText('Native provider')).toBeInTheDocument();
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
    expect(screen.getAllByText('Alpha')).toHaveLength(1);
    fireEvent.click(within(row).getByRole('button', { name: manage }));
    await waitFor(() => expect(mocks.get).toHaveBeenCalledWith('/api/tools/tool_00'));
    expect(await screen.findByText('Complete description 0')).toBeInTheDocument();
  });

  it('uses full facets, scalar group filters, distinct All/Ungrouped cache keys and offset zero', async () => {
    await mount();
    const nav = screen.getByRole('complementary', { name: '业务分组' });
    expect(within(nav).getByRole('button', { name: '全部 61' })).toBeInTheDocument();
    expect(within(nav).getByRole('button', { name: '未分组 1' })).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(zhTool.search.placeholder), { target: { value: 'tool_' } });
    await waitFor(() => expect(mocks.listPage).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'tool_' })));
    fireEvent.click(screen.getByRole('button', { name: '2' }));
    await screen.findByRole('button', { name: 'tool_25' });
    fireEvent.click(screen.getByRole('button', { name: 'Beta 30' }));
    await screen.findByRole('button', { name: 'tool_30' });
    const betaCalls = mocks.listPage.mock.calls.filter(([params]) => params.group === 'Beta');
    expect(betaCalls).toHaveLength(1);
    expect(betaCalls[0][0]).toEqual(expect.objectContaining({ group: 'Beta', offset: 0, q: 'tool_', sort_by: 'source', sort_dir: 'asc' }));
    fireEvent.click(screen.getByRole('button', { name: '2' }));
    await screen.findByRole('button', { name: 'tool_55' });
    const originalText = nativeRow('tool_55').textContent;
    const requests = mocks.listPage.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: '卡片视图' }));
    expect(nativeRow('tool_55').textContent).toBe(originalText);
    expect(mocks.listPage).toHaveBeenCalledTimes(requests);
    fireEvent.click(screen.getByRole('button', { name: '全部 61' }));
    await screen.findByRole('button', { name: 'tool_00' });
    expect(mocks.listPage).toHaveBeenCalledWith(expect.objectContaining({ group: undefined, offset: 0, q: 'tool_' }));
    fireEvent.click(screen.getByRole('button', { name: '未分组 1' }));
    await screen.findByRole('button', { name: 'tool_60' });
    expect(mocks.listPage).toHaveBeenLastCalledWith(expect.objectContaining({ group: '', offset: 0, q: 'tool_' }));
  });

  it.each(['tool_30', 'no-matching-tools'])('retains Alpha when search facets only show Beta or none (%s)', async (query) => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Alpha 30' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Alpha 30' })).toHaveAttribute('aria-pressed', 'true'));
    fireEvent.change(screen.getByPlaceholderText(zhTool.search.placeholder), { target: { value: query } });
    const selected = await screen.findByRole('button', { name: 'Alpha 0' });
    expect(selected).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: 'tool_30' })).not.toBeInTheDocument();
    expect(mocks.listPage).toHaveBeenLastCalledWith(expect.objectContaining({ group: 'Alpha', q: query }));
    fireEvent.change(screen.getByPlaceholderText(zhTool.search.placeholder), { target: { value: '' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Alpha 30' })).toHaveAttribute('aria-pressed', 'true'));
    expect(await screen.findByRole('button', { name: 'tool_00' })).toBeInTheDocument();
  });

  it('retains Alpha when a column filter only matches Beta', async () => {
    inventory.forEach((tool) => { tool.enabled = tool.group !== 'Alpha'; });
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Alpha 30' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Alpha 30' })).toHaveAttribute('aria-pressed', 'true'));
    const header = screen.getByRole('button', { name: '状态' }).parentElement!;
    fireEvent.click(within(header).getAllByRole('button')[1]);
    fireEvent.click(screen.getByRole('checkbox', { name: zhTool.table.enabledLabel }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Alpha 0' })).toHaveAttribute('aria-pressed', 'true'));
    expect(mocks.listPage).toHaveBeenLastCalledWith(expect.objectContaining({ group: 'Alpha', enabled: 'true' }));
  });

  it('retains the selected group across Local category and zero-match query filters', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: /^本地工具/ }));
    await screen.findByRole('button', { name: /^tool_00 / });
    fireEvent.click(screen.getByRole('button', { name: 'Alpha 30' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Alpha 30' })).toHaveAttribute('aria-pressed', 'true'));
    fireEvent.click(screen.getByRole('button', { name: `${zhTool.category.file} (12)` }));
    expect(screen.getByRole('button', { name: 'Alpha 30' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: /^tool_00 / })).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(zhTool.search.placeholder), { target: { value: 'tool_30' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Alpha 0' })).toHaveAttribute('aria-pressed', 'true'));
    expect(mocks.listPage).toHaveBeenLastCalledWith(expect.objectContaining({ group: 'Alpha', q: 'tool_30' }));
  });

  it('preserves a selected group after its first page request fails and retries that group', async () => {
    await mount();
    const originalPage = mocks.listPage.getMockImplementation()!;
    mocks.listPage.mockImplementation((params) => params.group === 'Beta' ? Promise.reject(new Error('group page offline')) : originalPage(params));
    fireEvent.click(screen.getByRole('button', { name: 'Beta 30' }));
    await screen.findByText('group page offline');
    expect(screen.queryByRole('button', { name: 'tool_00' })).not.toBeInTheDocument();
    expect(mocks.listPage).toHaveBeenLastCalledWith(expect.objectContaining({ group: 'Beta' }));
    mocks.listPage.mockImplementation(originalPage);
    mocks.refresh.mockResolvedValue({ data: { status: 'success', tool_count: inventory.length, message: '', stages: {}, errors: [] } });
    fireEvent.click(screen.getByTitle(zhTool.button.refreshList));
    await screen.findByRole('button', { name: 'tool_30' });
    expect(screen.getByRole('button', { name: 'Beta 30' })).toHaveAttribute('aria-pressed', 'true');
    expect(mocks.listPage).toHaveBeenLastCalledWith(expect.objectContaining({ group: 'Beta', offset: 0 }));
  });

  it('creates a group only after query-independent name validation with a single-field native save', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: '新建分组' }));
    fireEvent.change(screen.getByRole('textbox', { name: '分组名称' }), { target: { value: 'New native group' } });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'tool_00' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(mocks.patch).toHaveBeenCalledExactlyOnceWith('/api/tools/tool_00', { group: 'New native group' }));
    await screen.findByRole('button', { name: 'New native group 1' });
    expect(mocks.listPage).toHaveBeenCalledWith(expect.objectContaining({ limit: 1, offset: 0, q: undefined }));
    expect(mocks.refresh).not.toHaveBeenCalled();
  });

  it.each(['create', 'rename'])('rejects %s collisions outside filtered facets before any native writes', async (operation) => {
    await mount();
    fireEvent.change(screen.getByPlaceholderText(zhTool.search.placeholder), { target: { value: 'tool_00' } });
    await waitFor(() => expect(mocks.listPage).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'tool_00' })));
    await screen.findByRole('button', { name: 'Alpha 1' });
    expect(screen.queryByRole('button', { name: 'Beta 30' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: operation === 'create' ? '新建分组' : '重命名分组 Alpha' }));
    fireEvent.change(screen.getByRole('textbox', { name: '分组名称' }), { target: { value: 'Beta' } });
    if (operation === 'create') fireEvent.change(screen.getByRole('combobox'), { target: { value: 'tool_00' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(zhGroups.validation.duplicate));
    expect(mocks.listPage).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 1, offset: 0, q: undefined, source: undefined, group: undefined }));
    expect(mocks.patch).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it.each(['api', 'mcp'])('keeps the native %s group selected while a child-tool search loads', async (tab) => {
    mocks.listServices.mockResolvedValue({ data: [
      { id: 'native-a', name: 'Native service A', group: 'Services', enabled: true, status: 'connected', tool_count: 0 },
      { id: 'native-b', name: 'Native service B', group: null, enabled: true, status: 'connected', tool_count: 0 },
    ] });
    const originalGet = mocks.get.getMockImplementation()!;
    mocks.get.mockImplementation((url: string) => {
      if (url === '/api/mcp') return Promise.resolve({ data: {
        'native-a': { status: 'connected', group: 'Services', tools: [], resources: [] },
        'native-b': { status: 'connected', group: null, tools: [], resources: [] },
      } });
      if (url.startsWith('/api/mcp/catalog/')) return Promise.resolve({ data: url.endsWith('/categories') ? {} : [] });
      return originalGet(url);
    });
    await mount();
    fireEvent.click(screen.getByRole('button', { name: tab === 'api' ? /^API 集成/ : /^MCP 服务/ }));
    const groupButton = await screen.findByRole('button', { name: 'Services 1' });
    fireEvent.click(groupButton);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Services 1' })).toHaveAttribute('aria-pressed', 'true'));
    const originalPage = mocks.listPage.getMockImplementation()!;
    let resolvePage!: (result: unknown) => void;
    mocks.listPage.mockImplementation((params) => params.q ? new Promise((resolve) => { resolvePage = resolve; }) : originalPage(params));
    fireEvent.change(screen.getByPlaceholderText(zhTool.search.placeholder), { target: { value: 'native' } });
    await waitFor(() => expect(resolvePage).toBeDefined());
    expect(screen.getByRole('button', { name: 'Services 1' })).toBe(groupButton);
    expect(groupButton).toHaveAttribute('aria-pressed', 'true');
    resolvePage(await originalPage({ q: 'native', source: tab, offset: 0, limit: 25 }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Services 1' })).toHaveAttribute('aria-pressed', 'true'));
    expect(screen.queryByText(tab === 'api' ? 'Native service B' : 'native-b')).not.toBeInTheDocument();
  });

  it('moves one native row with the keyboard equivalent and only reloads metadata', async () => {
    await mount();
    fireEvent.keyDown(nativeRow('tool_00'), { key: 'm', altKey: true });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Beta' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(mocks.patch).toHaveBeenCalledExactlyOnceWith('/api/tools/tool_00', { group: 'Beta' }));
    await screen.findByRole('button', { name: 'Beta 31' });
    expect(mocks.refresh).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('loads every matching native page before confirming and renaming, not just 25 rows', async () => {
    inventory = Array.from({ length: 205 }, (_, index) => ({ ...inventory[0], name: `tool_${String(index).padStart(2, '0')}`, group: 'Alpha' }));
    await mount();
    fireEvent.click(screen.getByRole('button', { name: '重命名分组 Alpha' }));
    fireEvent.change(screen.getByRole('textbox', { name: '分组名称' }), { target: { value: 'Renamed' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(mocks.patch).toHaveBeenCalledTimes(205));
    expect(mocks.listPage).toHaveBeenCalledWith(expect.objectContaining({ group: 'Alpha', offset: 0, limit: 200 }));
    expect(mocks.listPage).toHaveBeenCalledWith(expect.objectContaining({ group: 'Alpha', offset: 200, limit: 200 }));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('205'));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('tool_204'));
    expect(mocks.patch).toHaveBeenLastCalledWith('/api/tools/tool_204', { group: 'Renamed' });
    expect(mocks.refresh).not.toHaveBeenCalled();
  });

  it('locks shipped rows even when their source is not builtin, while custom members remain movable', async () => {
    inventory[0].group_readonly = true;
    inventory[1].group_readonly = false;
    await mount();
    expect(nativeRow('tool_00')).toHaveAttribute('draggable', 'false');
    expect(nativeRow('tool_00')).toHaveAttribute('title', zhGroups.readOnly.system);
    expect(screen.getByRole('button', { name: '重命名分组 Alpha' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '删除分组 Alpha' })).toBeDisabled();
    fireEvent.keyDown(nativeRow('tool_00'), { key: 'm', altKey: true });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '新建分组' }));
    expect(within(screen.getByRole('combobox')).queryByRole('option', { name: 'tool_00' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    fireEvent.keyDown(nativeRow('tool_01'), { key: 'm', altKey: true });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(mocks.patch).toHaveBeenCalledExactlyOnceWith('/api/tools/tool_01', { group: null }));
  });

  it.each(['move', 'create'])('checks fresh tool ownership before a selected-row %s', async (operation) => {
    await mount();
    mocks.get.mockResolvedValue({ data: { ...inventory[0], group_readonly: true } });
    if (operation === 'move') fireEvent.keyDown(nativeRow('tool_00'), { key: 'm', altKey: true });
    else {
      fireEvent.click(screen.getByRole('button', { name: '新建分组' }));
      fireEvent.change(screen.getByRole('textbox', { name: '分组名称' }), { target: { value: 'New' } });
      fireEvent.change(screen.getByRole('combobox'), { target: { value: 'tool_00' } });
    }
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(zhGroups.readOnly.system));
    expect(mocks.patch).not.toHaveBeenCalled();
  });

  it.each(['rename', 'delete'])('finds a locked tool beyond page 200 before whole-group %s', async (operation) => {
    inventory = Array.from({ length: 205 }, (_, index) => ({ ...inventory[0], name: `tool_${String(index).padStart(2, '0')}`, group: 'Alpha', group_readonly: index === 204 }));
    await mount();
    fireEvent.click(screen.getByRole('button', { name: operation === 'rename' ? '重命名分组 Alpha' : '删除分组 Alpha' }));
    if (operation === 'rename') {
      fireEvent.change(screen.getByRole('textbox', { name: '分组名称' }), { target: { value: 'New' } });
      fireEvent.click(screen.getByRole('button', { name: '保存' }));
    }
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('tool_204'));
    expect(mocks.listPage).toHaveBeenCalledWith(expect.objectContaining({ group: 'Alpha', offset: 200, limit: 200 }));
    expect(mocks.patch).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it.each([
    ['rename', 'search'], ['delete', 'search'], ['rename', 'local'], ['delete', 'local'],
  ])('audits hidden locked members before %s under the %s filter', async (operation, filter) => {
    inventory[29].group_readonly = true;
    inventory[29].source = 'api';
    await mount();
    if (filter === 'search') {
      fireEvent.change(screen.getByPlaceholderText(zhTool.search.placeholder), { target: { value: 'tool_00' } });
      await screen.findByRole('button', { name: 'Alpha 1' });
    } else {
      fireEvent.click(screen.getByRole('button', { name: /^本地工具/ }));
      await screen.findByRole('button', { name: 'Alpha 29' });
    }
    fireEvent.click(screen.getByRole('button', { name: operation === 'rename' ? '重命名分组 Alpha' : '删除分组 Alpha' }));
    if (operation === 'rename') {
      fireEvent.change(screen.getByRole('textbox', { name: '分组名称' }), { target: { value: 'New' } });
      fireEvent.click(screen.getByRole('button', { name: '保存' }));
    }
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('tool_29'));
    expect(mocks.listPage).toHaveBeenCalledWith(expect.objectContaining({ group: 'Alpha', q: undefined, source: undefined, limit: 200 }));
    expect(mocks.patch).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
  });

  it('deletes with one explicit filtered-scope confirmation and preserves a still-existing selection', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Alpha 30' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Alpha 30' })).toHaveAttribute('aria-pressed', 'true'));
    fireEvent.change(screen.getByPlaceholderText(zhTool.search.placeholder), { target: { value: 'tool_00' } });
    await screen.findByRole('button', { name: 'Alpha 1' });
    fireEvent.click(screen.getByRole('button', { name: '删除分组 Alpha' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(mocks.patch).toHaveBeenCalledExactlyOnceWith('/api/tools/tool_00', { group: null }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Alpha 0' })).toHaveAttribute('aria-pressed', 'true'));
    expect(window.confirm).toHaveBeenCalledTimes(1);
    const confirmation = vi.mocked(window.confirm).mock.calls[0][0]!;
    expect(confirmation).toContain('tool_00');
    expect(confirmation).not.toContain('tool_01');
    expect(mocks.listPage).toHaveBeenCalledWith(expect.objectContaining({ limit: 1, group: undefined, q: undefined }));
    expect(inventory[1].group).toBe('Alpha');
  });

  it('clears a filtered selection only after the unfiltered summary confirms the last member left', async () => {
    inventory.forEach((tool, index) => { if (index > 0 && tool.group === 'Alpha') tool.group = 'Beta'; });
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Alpha 1' }));
    fireEvent.change(screen.getByPlaceholderText(zhTool.search.placeholder), { target: { value: 'tool_00' } });
    await waitFor(() => expect(mocks.listPage).toHaveBeenLastCalledWith(expect.objectContaining({ group: 'Alpha', q: 'tool_00' })));
    await screen.findByRole('button', { name: 'Alpha 1' });
    fireEvent.click(screen.getByRole('button', { name: '删除分组 Alpha' }));
    await waitFor(() => expect(mocks.patch).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole('button', { name: '全部 1' })).toHaveAttribute('aria-pressed', 'true'));
    expect(mocks.listPage).toHaveBeenCalledWith(expect.objectContaining({ limit: 1, group: undefined, q: undefined }));
  });

  it('cancels the single native delete confirmation without a preview dialog or writes', async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    await mount();
    fireEvent.click(screen.getByRole('button', { name: '删除分组 Alpha' }));
    await waitFor(() => expect(window.confirm).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(mocks.patch).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Alpha 30' })).toBeInTheDocument();
  });

  it('reports actual partial failures and recomputes both group counts', async () => {
    const original = mocks.patch.getMockImplementation()!;
    mocks.patch.mockImplementation((url, body) => url === '/api/tools/tool_02' ? Promise.reject(new Error('read-only filesystem')) : original(url, body));
    await mount();
    fireEvent.click(screen.getByRole('button', { name: '删除分组 Alpha' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('tool_02: read-only filesystem'));
    expect(mocks.patch).toHaveBeenCalledTimes(30);
    expect(screen.getByRole('button', { name: 'Alpha 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '未分组 30' })).toBeInTheDocument();
    expect(inventory[0].group).toBeNull();
    expect(inventory[2].group).toBe('Alpha');
  });

  it('permits filtering but gates native settings writes for non-admins', async () => {
    mocks.auth.role = 'member';
    await mount();
    expect(nativeRow('tool_00')).toHaveAttribute('draggable', 'false');
    expect(screen.getByRole('button', { name: '新建分组' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '重命名分组 Alpha' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Beta 30' }));
    await screen.findByRole('button', { name: 'tool_30' });
    expect(mocks.patch).not.toHaveBeenCalled();
  });

  it('keeps Local category filtering and tab-specific view preferences', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: '卡片视图' }));
    fireEvent.click(screen.getByRole('button', { name: /^本地工具/ }));
    await screen.findByRole('button', { name: /^tool_00 / });
    expect(screen.getByRole('button', { name: '列表视图' })).toHaveAttribute('aria-pressed', 'true');
    const requests = mocks.listPage.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: `${zhTool.category.file} (12)` }));
    expect(screen.queryByRole('button', { name: /^tool_00 / })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^tool_01 / })).toBeInTheDocument();
    expect(mocks.listPage).toHaveBeenCalledTimes(requests);
    const text = nativeRow('tool_01').textContent;
    fireEvent.click(screen.getByRole('button', { name: '卡片视图' }));
    expect(nativeRow('tool_01').textContent).toBe(text);
    fireEvent.click(screen.getByRole('button', { name: /^全量工具/ }));
    await screen.findByRole('button', { name: 'tool_00' });
    expect(screen.getByRole('button', { name: '卡片视图' })).toHaveAttribute('aria-pressed', 'true');
  });
});
