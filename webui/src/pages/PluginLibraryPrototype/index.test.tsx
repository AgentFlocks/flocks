import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createInstance, type i18n } from 'i18next';
import { initReactI18next, I18nextProvider } from 'react-i18next';
import { BrowserRouter, MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import { ToastProvider } from '@/components/common/Toast';
import commonZh from '@/locales/zh-CN/common.json';
import pluginLibraryZh from '@/locales/zh-CN/pluginLibrary.json';
import PluginLibraryPrototype from './index';
import { INITIAL_GROUPS, MOCK_ITEMS, SCOPE_CONFIG } from './mockData';
import { createInitialState, STORAGE_KEY } from './model';
import type { CatalogScope, Collection, FacetDefinition, OrganizationState, PluginItem, PluginType } from './types';

const GROUP_PAGE_SIZE = 6;
const pluginTypes: PluginType[] = ['workflow', 'agent', 'skill', 'tool', 'device'];
let instance: i18n;
let user: ReturnType<typeof userEvent.setup>;
let fetchSpy: ReturnType<typeof vi.fn>;
let xhrOpenSpy: ReturnType<typeof vi.spyOn>;
const originalDialogMethods = {
  showModal: Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'showModal'),
  close: Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, 'close'),
};

function text(key: string, values: Record<string, string | number> = {}) {
  return String(instance.t(key, { ns: 'pluginLibrary', ...values }));
}

function countedName(label: string) {
  return new RegExp(`^${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\d+$`);
}

function scopeItems(scope: CatalogScope) {
  return MOCK_ITEMS.filter((item) => item.scope === scope);
}

function scopeGroups(scope: CatalogScope) {
  return INITIAL_GROUPS.filter((group) => group.scope === scope);
}

// Independent expected results: display text comes from fixtures, never English
// literals or the production selector. Pagination is per group, not per page.
function byUpdated(items: PluginItem[]) {
  return [...items].sort((left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt)
    || (left.id < right.id ? -1 : left.id > right.id ? 1 : 0));
}

function groupPage(items: PluginItem[], id: string, page = 1) {
  return byUpdated(items.filter((item) => (item.collectionId || 'ungrouped') === id))
    .slice((page - 1) * GROUP_PAGE_SIZE, page * GROUP_PAGE_SIZE);
}

function allDisplayed(items: PluginItem[]) {
  return [...new Set(items.map((item) => item.collectionId || 'ungrouped'))]
    .flatMap((id) => groupPage(items, id));
}

function initiallyDisplayed(scope: CatalogScope) {
  return allDisplayed(scopeItems(scope));
}

function matchesQuery(item: PluginItem, query: string) {
  const searchable = [item.name, item.identifier, item.id, item.description, ...item.tags].join(' ').toLowerCase();
  return query.trim().toLowerCase().split(/\s+/u).every((word) => searchable.includes(word));
}

function savedState(): OrganizationState {
  const raw = localStorage.getItem(STORAGE_KEY);
  expect(raw).not.toBeNull();
  return JSON.parse(raw!);
}

function searchInput() {
  return screen.getByRole('textbox', { name: text('searchLabel') });
}

function groupNavigation() {
  return screen.getByRole('navigation', { name: text('groupNavigation') });
}

function groupNav(id: string) {
  const button = groupNavigation().querySelector<HTMLButtonElement>(`[data-group-nav-id="${id}"]`);
  expect(button).toBeInTheDocument();
  return button!;
}

async function navigateGroup(id: string) {
  await user.click(groupNav(id));
  expect(groupNav(id)).toHaveAttribute('aria-current', 'page');
}

function expectNavigation(scope: CatalogScope, items = scopeItems(scope)) {
  const entries = [
    { id: 'all', name: text('allGroups'), count: items.length },
    ...scopeGroups(scope).map((group) => ({ ...group, count: items.filter((item) => item.collectionId === group.id).length })),
    { id: 'ungrouped', name: text('ungrouped'), count: items.filter((item) => !item.collectionId).length },
  ];
  expect([...groupNavigation().querySelectorAll('[data-group-nav-id]')].map((button) => button.getAttribute('data-group-nav-id')))
    .toEqual(entries.map((entry) => entry.id));
  for (const entry of entries) {
    expect(groupNav(entry.id)).toHaveAccessibleName(text('navigateGroup', { name: entry.name }));
    expect(within(groupNav(entry.id)).getByText(String(entry.count), { selector: 'span' })).toBeVisible();
  }
}

function filterTrigger() {
  return screen.getByRole('button', { name: text('filters') });
}

async function openFilters() {
  if (filterTrigger().getAttribute('aria-expanded') === 'false') await user.click(filterTrigger());
  return screen.getByRole('dialog', { name: text('filters') });
}

function selectVisible() {
  return screen.getByRole('checkbox', { name: text('selectVisible') });
}

function itemCheckbox(item: PluginItem) {
  return screen.getByRole('checkbox', { name: text('selectItem', { name: item.name }) });
}

function visibleItemCheckboxes() {
  const labels = new Set(MOCK_ITEMS.map((item) => text('selectItem', { name: item.name })));
  return screen.queryAllByRole('checkbox').filter((input) => labels.has(input.getAttribute('aria-label') || ''));
}

function expectVisibleItems(expected: PluginItem[]) {
  expect(visibleItemCheckboxes().map((input) => input.getAttribute('aria-label')).sort())
    .toEqual(expected.map((item) => text('selectItem', { name: item.name })).sort());
  for (const item of expected) expect(screen.getByRole('button', { name: item.name })).toBeVisible();
}

function groupHeader(id: string) {
  const header = document.querySelector<HTMLElement>(`[data-group-id="${id}"]`);
  expect(header).toBeInTheDocument();
  return header!;
}

function groupToggle(id: string) {
  return groupHeader(id).querySelector<HTMLButtonElement>('button[aria-expanded]')!;
}

function groupContent(id: string) {
  return document.getElementById(groupToggle(id).getAttribute('aria-controls')!)!;
}

function itemElement(item: PluginItem) {
  return itemCheckbox(item).closest<HTMLElement>('[data-plugin-id]')!;
}

function expectNoSelection() {
  expect(screen.queryByRole('region', { name: text('batchActions') })).not.toBeInTheDocument();
  expect(selectVisible()).not.toBeChecked();
  expect(selectVisible()).not.toBePartiallyChecked();
  for (const input of visibleItemCheckboxes()) expect(input).not.toBeChecked();
}

function dialog(kind: 'create' | 'rename' | 'delete' | 'move' | 'reset') {
  return screen.getByRole('dialog', { name: text(`dialog.${kind}Title`) });
}

async function confirm(kind: 'create' | 'rename' | 'delete' | 'move' | 'reset') {
  await user.click(within(dialog(kind)).getByRole('button', { name: text(`dialog.${kind}Confirm`) }));
}

async function createGroupThroughUI(name: string) {
  await user.click(screen.getByRole('button', { name: text('createGroup') }));
  await user.type(within(dialog('create')).getByRole('textbox', { name: text('dialog.groupName') }), name);
  await confirm('create');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  const created = savedState().groups.find((group) => group.name === name);
  expect(created).toBeDefined();
  return created!;
}

async function moveThroughDialog(target: string | null, expectedScope: CatalogScope) {
  const destination = within(dialog('move')).getByRole('combobox', { name: text('dialog.destination') });
  expect(within(destination).getAllByRole('option').map((option) => (option as HTMLOptionElement).value))
    .toEqual(['', ...savedState().groups.filter((group) => group.scope === expectedScope).map((group) => group.id)]);
  await user.selectOptions(destination, target || '');
  await confirm('move');
}

async function toggleFacet(facet: FacetDefinition, value: string) {
  const panel = await openFilters();
  const fieldset = within(panel).getByRole('group', { name: facet.label.zh });
  const option = facet.options.find((entry) => entry.value === value)!;
  await user.click(within(fieldset).getByRole('checkbox', { name: option.label.zh }));
  await user.keyboard('{Escape}');
  expect(filterTrigger()).toHaveAttribute('aria-expanded', 'false');
}

function expectOnlyMoved(before: OrganizationState, ids: string[], target: string | null) {
  const selected = new Set(ids);
  const after = savedState();
  expect(after.memberships).toEqual(Object.fromEntries(Object.entries(before.memberships)
    .map(([id, membership]) => [id, selected.has(id) ? target : membership])));
  expect(Object.keys(after.memberships).sort()).toEqual(MOCK_ITEMS.map((item) => item.id).sort());
  expect(after.groups).toEqual(before.groups);
  expect(after.favorites).toEqual(before.favorites);
}

function dragTransfer() {
  const values = new Map<string, string>();
  return {
    effectAllowed: 'uninitialized',
    dropEffect: 'none',
    setData: vi.fn((format: string, value: string) => { values.set(format, value); }),
    getData: vi.fn((format: string) => values.get(format) || ''),
  };
}

function NavigationProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return <div>
    <div data-testid="router-location">{location.pathname}{location.search}</div>
    <button type="button" onClick={() => navigate(-1)}>Test history back</button>
  </div>;
}

function renderPage(options: { pluginType?: PluginType; url?: string; browser?: boolean } = {}) {
  const tree = (pluginType = options.pluginType) => {
    const content = <I18nextProvider i18n={instance}>
      <ToastProvider><PluginLibraryPrototype pluginType={pluginType} /><NavigationProbe /></ToastProvider>
    </I18nextProvider>;
    return options.browser ? <BrowserRouter>{content}</BrowserRouter>
      : <MemoryRouter initialEntries={[options.url || '/workflows?preview=groups']}>{content}</MemoryRouter>;
  };
  const rendered = render(tree());
  return { ...rendered, rerenderType: (pluginType: PluginType) => rendered.rerender(tree(pluginType)) };
}

function installStorageFailure(readFails: boolean, writeFails: boolean) {
  const storage = localStorage;
  const denied = () => { throw new DOMException('Storage is unavailable for this test', 'SecurityError'); };
  const getItem = vi.fn(readFails ? denied : (key: string) => storage.getItem(key));
  const setItem = vi.fn(writeFails ? denied : (key: string, value: string) => storage.setItem(key, value));
  vi.stubGlobal('localStorage', {
    get length() { return storage.length; },
    clear: () => storage.clear(),
    key: (index: number) => storage.key(index),
    removeItem: (key: string) => storage.removeItem(key),
    getItem,
    setItem,
  } satisfies Storage);
  return { storage, setItem };
}

beforeEach(async () => {
  localStorage.clear();
  window.history.replaceState({}, '', '/');
  instance = createInstance();
  await instance.use(initReactI18next).init({
    lng: 'zh-CN', fallbackLng: false,
    ns: ['pluginLibrary', 'common'], defaultNS: 'pluginLibrary',
    resources: { 'zh-CN': { pluginLibrary: pluginLibraryZh, common: commonZh } },
    interpolation: { escapeValue: false },
  });
  user = userEvent.setup();
  // Emulate only the native open attribute; focus trapping/inertness are browser tests.
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true, value: vi.fn(function (this: HTMLDialogElement) { this.setAttribute('open', ''); }),
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true, value: vi.fn(function (this: HTMLDialogElement) { this.removeAttribute('open'); }),
  });
  fetchSpy = vi.fn(() => Promise.reject(new Error('Grouped-list mocks must not fetch real resources')));
  vi.stubGlobal('fetch', fetchSpy);
  // Intercept axios's browser transport without importing any app APIs.
  xhrOpenSpy = vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(() => {
    throw new Error('Grouped-list mocks must not send XMLHttpRequests');
  });
});

afterEach(() => {
  cleanup();
  try {
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(xhrOpenSpy).not.toHaveBeenCalled();
  } finally {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    for (const method of ['showModal', 'close'] as const) {
      const original = originalDialogMethods[method];
      if (original) Object.defineProperty(HTMLDialogElement.prototype, method, original);
      else Reflect.deleteProperty(HTMLDialogElement.prototype, method);
    }
    localStorage.clear();
    instance.off();
  }
});

describe('page-local one-level plugin groups', () => {
  it('renders each of the five page titles from its explicit type, overriding query type, without centralized navigation', async () => {
    for (const pluginType of pluginTypes) {
      const scope: CatalogScope = pluginType === 'device' ? 'device-instance' : pluginType;
      const mounted = renderPage({ pluginType, url: `/example?preview=groups&type=${pluginType === 'tool' ? 'workflow' : 'tool'}` });
      const header = screen.getByTestId('compact-page-header');
      expect(within(header).getByRole('heading', { level: 1, name: text(`pages.${pluginType}.title`) })).toBeVisible();
      expect(within(header).getByLabelText(text('pageCount', { count: scopeItems(scope).length }))).toBeVisible();
      expect(within(header).getByText('Mock')).toBeVisible();
      expect(within(header).getByRole('button', { name: text('reset') })).toBeVisible();
      expect(header).toContainElement(searchInput());
      expect(header).toContainElement(filterTrigger());
      expect(header.querySelector('p')).toBeNull();
      expect(screen.queryByText(text(`pages.${pluginType}.description`))).not.toBeInTheDocument();
      expectVisibleItems(initiallyDisplayed(scope));
      expectNavigation(scope);
      expect(groupNav('all')).toHaveAttribute('aria-current', 'page');
      for (const id of [...scopeGroups(scope).map((group) => group.id), 'ungrouped']) {
        expect(groupToggle(id)).toHaveAttribute('aria-expanded', 'true');
      }
      expect(screen.getAllByRole('navigation')).toEqual([groupNavigation()]);
      expect(within(groupNavigation()).getByRole('button', { name: text('createGroup') })).toBeVisible();
      expect(screen.queryByRole('combobox', { name: text('groupFilter') })).not.toBeInTheDocument();
      expect(document.getElementById('plugin-group-filter')).toBeNull();
      expect(filterTrigger()).toHaveAttribute('aria-expanded', 'false');
      expect(screen.queryByRole('dialog', { name: text('filters') })).not.toBeInTheDocument();
      expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
      expect(screen.queryByRole('tree')).not.toBeInTheDocument();
      expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /我的收藏|分组卡片视图|紧凑列表视图/ })).not.toBeInTheDocument();
      if (pluginType === 'workflow' || pluginType === 'agent' || pluginType === 'device') {
        expect(screen.queryByRole('table')).not.toBeInTheDocument();
        for (const item of initiallyDisplayed(scope)) expect(itemElement(item).tagName).toBe('ARTICLE');
      }
      mounted.unmount();
    }
  });

  it('keeps facets in a closed-by-default header overlay with a selected badge, clear action and Escape dismissal', async () => {
    renderPage({ pluginType: 'workflow' });
    const header = screen.getByTestId('compact-page-header');
    const list = screen.getByRole('region', { name: text('resourceList') });
    const [status, trigger] = SCOPE_CONFIG.workflow.facets;
    const anchor = initiallyDisplayed('workflow')[0];
    expect(filterTrigger()).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('group', { name: status.label.zh })).not.toBeInTheDocument();
    expect(document.querySelector('summary')).toBeNull();
    const panel = await openFilters();
    expect(panel).toHaveClass('absolute');
    expect(header).toContainElement(panel);
    expect(list).not.toContainElement(panel);
    expect(filterTrigger()).toHaveAttribute('aria-controls', panel.id);
    expect(within(panel).getAllByRole('group')).toHaveLength(SCOPE_CONFIG.workflow.facets.length);
    expect(within(panel).getByRole('button', { name: text('clearFilters') })).toBeDisabled();
    const selectedLabels: string[] = [];
    for (const facet of [status, trigger]) {
      const option = facet.options.find((entry) => entry.value === anchor.attributes[facet.key])!;
      await user.click(within(within(panel).getByRole('group', { name: facet.label.zh })).getByRole('checkbox', { name: option.label.zh }));
      selectedLabels.push(`${facet.label.zh}: ${option.label.zh}`);
    }
    expect(within(filterTrigger()).getByText('2')).toBeVisible();
    expect(filterTrigger()).toHaveAccessibleDescription(`${text('filterCount', { count: 2 })} · ${selectedLabels.join(' · ')}`);
    expect(filterTrigger()).toHaveAttribute('aria-expanded', 'true');
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: text('filters') })).not.toBeInTheDocument();
    expect(filterTrigger()).toHaveFocus();
    expect(filterTrigger()).toHaveAttribute('aria-expanded', 'false');
    // Selected conditions remain summarized on the trigger, not in a second row.
    expect(screen.queryByRole('button', { name: text('removeFilter', { label: status.options.find((entry) => entry.value === anchor.attributes[status.key])!.label.zh }) })).not.toBeInTheDocument();
    await user.type(searchInput(), anchor.identifier);
    expectVisibleItems([anchor]);
    const reopened = await openFilters();
    await user.click(within(reopened).getByRole('button', { name: text('clearFilters') }));
    expect(searchInput()).toHaveValue('');
    expect(filterTrigger()).not.toHaveAttribute('aria-describedby');
    expect(within(filterTrigger()).queryByText('2')).not.toBeInTheDocument();
    for (const checkbox of within(reopened).getAllByRole('checkbox')) expect(checkbox).not.toBeChecked();
    expect(within(reopened).getByRole('button', { name: text('clearFilters') })).toBeDisabled();
    expectVisibleItems(initiallyDisplayed('workflow'));
    await user.click(within(reopened).getByRole('button', { name: text('doneFiltering') }));
    expect(filterTrigger()).toHaveFocus();
    expect(screen.queryByRole('dialog', { name: text('filters') })).not.toBeInTheDocument();
  });

  it('renders Skill and Tools as one real table with one column header row and flat group rows', async () => {
    for (const pluginType of ['skill', 'tool'] as const) {
      const mounted = renderPage({ pluginType });
      const table = screen.getByRole('table', { name: text('resourceList') });
      expect(screen.getAllByRole('table')).toHaveLength(1);
      expect(table.querySelectorAll('thead')).toHaveLength(1);
      const keys = pluginType === 'skill' ? ['source', 'eligibility', 'enabled'] : ['source', 'enabled', 'confirmation'];
      expect(within(table).getAllByRole('columnheader').map((cell) => cell.textContent))
        .toEqual([text('selection'), text('name'), ...keys.map((key) => SCOPE_CONFIG[pluginType].facets.find((facet) => facet.key === key)!.label.zh), text('actions')]);
      for (const id of [...scopeGroups(pluginType).map((group) => group.id), 'ungrouped']) {
        const header = groupHeader(id);
        expect(header.closest('table')).toBe(table);
        expect(header.closest('tr')!.children).toHaveLength(1);
        expect(header.closest('td')).toHaveAttribute('colspan', '6');
        expect(groupContent(id).tagName).toBe('TBODY');
        expect(groupContent(id).parentElement).toBe(table);
        expect(header.querySelector('[data-group-id]')).toBeNull();
      }
      expectVisibleItems(allDisplayed(scopeItems(pluginType)));
      for (const item of allDisplayed(scopeItems(pluginType))) {
        expect(itemElement(item).tagName).toBe('TR');
        expect(itemElement(item)).toHaveAttribute('draggable', 'true');
      }
      mounted.unmount();
    }
  });

  it('defaults Device to instances and switches templates/back without losing preview or unrelated query parameters', async () => {
    window.history.replaceState({}, '', '/devices?preview=groups&type=workflow&keep=demo');
    renderPage({ pluginType: 'device', browser: true });
    expectVisibleItems(initiallyDisplayed('device-instance'));
    expect(screen.getByRole('button', { name: countedName(text('deviceInstances')) })).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', { name: countedName(text('deviceTemplates')) }));
    const params = new URLSearchParams(window.location.search);
    expect(params.get('preview')).toBe('groups');
    expect(params.get('keep')).toBe('demo');
    expect(params.get('type')).toBe('workflow');
    expect(params.get('deviceView')).toBe('templates');
    expectVisibleItems(initiallyDisplayed('device-template'));
    const panel = await openFilters();
    expect(within(panel).getByRole('group', { name: SCOPE_CONFIG['device-template'].facets[0].label.zh })).toBeVisible();
    expect(within(panel).queryByRole('group', { name: SCOPE_CONFIG['device-instance'].facets[0].label.zh })).not.toBeInTheDocument();
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: 'Test history back' }));
    await waitFor(() => expect(screen.getByTestId('router-location').textContent).toBe('/devices?preview=groups&type=workflow&keep=demo'));
    expectVisibleItems(initiallyDisplayed('device-instance'));
  });

  it('searches fixture names, identifiers and tags with ANDed words and expands only matching group contents', async () => {
    renderPage({ pluginType: 'workflow' });
    const anchor = initiallyDisplayed('workflow')[0];
    for (const query of [anchor.name, anchor.identifier, anchor.tags[0], `${anchor.tags[0]} ${anchor.identifier}`]) {
      await user.clear(searchInput());
      await user.type(searchInput(), query);
      const expected = scopeItems('workflow').filter((item) => matchesQuery(item, query));
      expect(expected).toContainEqual(anchor);
      expectVisibleItems(allDisplayed(expected));
      // Navigation counts describe the full page, not only the search matches.
      expectNavigation('workflow');
      const groupIds = [...new Set(expected.map((item) => item.collectionId || 'ungrouped'))];
      expect([...document.querySelectorAll('[data-group-id]')].map((node) => node.getAttribute('data-group-id')).sort()).toEqual(groupIds.sort());
      for (const id of groupIds) expect(groupToggle(id)).toHaveAttribute('aria-expanded', 'true');
    }
    await user.click(screen.getByRole('button', { name: text('clearSearch') }));
    expectVisibleItems(allDisplayed(scopeItems('workflow')));
  });

  it('combines OR values within a facet, AND across all three facets and search, then clears an empty result', async () => {
    renderPage({ pluginType: 'workflow' });
    const items = scopeItems('workflow');
    const anchor = initiallyDisplayed('workflow')[0];
    const [status, trigger, source] = SCOPE_CONFIG.workflow.facets;
    const statuses = [anchor.attributes[status.key], status.options.find((option) => option.value !== anchor.attributes[status.key])!.value];
    await toggleFacet(status, statuses[0]);
    expectVisibleItems(allDisplayed(items.filter((item) => item.attributes[status.key] === statuses[0])));
    await toggleFacet(status, statuses[1]);
    let expected = items.filter((item) => statuses.includes(item.attributes[status.key]));
    expectVisibleItems(allDisplayed(expected));
    for (const facet of [trigger, source]) {
      await toggleFacet(facet, anchor.attributes[facet.key]);
      expected = expected.filter((item) => item.attributes[facet.key] === anchor.attributes[facet.key]);
      expectVisibleItems(allDisplayed(expected));
    }
    await user.type(searchInput(), anchor.identifier);
    expectVisibleItems([anchor]);
    await user.clear(searchInput());
    await user.type(searchInput(), 'no-such-page-local-plugin-zzzz');
    expect(screen.getByText(text('noResults'))).toBeVisible();
    expectVisibleItems([]);
    expect(selectVisible()).toBeDisabled();
    // With the filter overlay closed, the empty state owns this clearing action.
    await user.click(screen.getByRole('button', { name: text('clearFilters') }));
    expect(searchInput()).toHaveValue('');
    expect(filterTrigger()).not.toHaveAttribute('aria-describedby');
    expectVisibleItems(allDisplayed(items));
  });

  it('validates blank, duplicate and overlong names, then creates an expanded empty one-level group', async () => {
    renderPage({ pluginType: 'workflow' });
    const before = savedState();
    await user.click(screen.getByRole('button', { name: text('createGroup') }));
    const input = within(dialog('create')).getByRole('textbox', { name: text('dialog.groupName') });
    expect(within(dialog('create')).queryByRole('combobox')).not.toBeInTheDocument();
    for (const [value, error] of [['   ', 'emptyName'], [scopeGroups('workflow')[0].name, 'duplicateName'], ['测'.repeat(33), 'longName']]) {
      await user.clear(input);
      await user.type(input, value);
      await confirm('create');
      expect(within(dialog('create')).getByRole('alert')).toHaveTextContent(text(`errors.${error}`));
      expect(input).toHaveAttribute('aria-invalid', 'true');
      expect(input).toHaveAccessibleDescription(text(`errors.${error}`));
      expect(savedState()).toEqual(before);
    }
    await user.clear(input);
    await user.type(input, '页面内测试分组');
    expect(input).toHaveAttribute('aria-invalid', 'false');
    await confirm('create');
    const created = savedState().groups.find((group) => group.name === '页面内测试分组')!;
    expect(created).toEqual({ id: expect.stringMatching(/^collection-/), scope: 'workflow', name: '页面内测试分组' });
    expect(groupNav('all')).toHaveAttribute('aria-current', 'page');
    expect(groupToggle(created.id)).toHaveAttribute('aria-expanded', 'true');
    expect(within(groupContent(created.id)).getByText(text('emptyGroupInline'))).toBeVisible();
    expect(savedState().memberships).toEqual(before.memberships);
    expect(savedState().favorites).toEqual(before.favorites);
    expect(groupHeader(created.id).closest('[draggable="true"]')).toBeNull();
  });

  it('renames a group while retaining stable ID, membership and its flat navigation entry', async () => {
    renderPage({ pluginType: 'workflow' });
    const before = savedState();
    const group = scopeGroups('workflow')[0];
    await user.click(within(groupHeader(group.id)).getByRole('button', { name: text('renameGroup', { name: group.name }) }));
    const input = within(dialog('rename')).getByRole('textbox', { name: text('dialog.groupName') });
    expect(input).toHaveValue(group.name);
    await user.clear(input);
    await user.type(input, '重命名后的响应组');
    await confirm('rename');
    const after = savedState();
    expect(after.groups).toEqual(before.groups.map((entry) => entry.id === group.id ? { ...entry, name: '重命名后的响应组' } : entry));
    expect(after.memberships).toEqual(before.memberships);
    expect(after.favorites).toEqual(before.favorites);
    expect(groupToggle(group.id)).toHaveAccessibleName(countedName('重命名后的响应组'));
    await navigateGroup(group.id);
    expect(groupNav(group.id)).toHaveAttribute('aria-current', 'page');
    expect(groupNav(group.id)).toHaveAccessibleName(text('navigateGroup', { name: '重命名后的响应组' }));
    expectVisibleItems(groupPage(scopeItems('workflow'), group.id));
  });

  it('cancels then confirms deletion of a populated group, keeping every plugin and returning its members to ungrouped', async () => {
    renderPage({ pluginType: 'workflow' });
    const before = savedState();
    const group = scopeGroups('workflow')[0];
    const members = scopeItems('workflow').filter((item) => item.collectionId === group.id);
    await navigateGroup(group.id);
    const openDelete = () => user.click(within(groupHeader(group.id)).getByRole('button', { name: text('deleteGroup', { name: group.name }) }));
    await openDelete();
    expect(within(dialog('delete')).getByText(text('dialog.deleteDescription', { name: group.name, count: members.length }))).toBeVisible();
    await user.click(within(dialog('delete')).getByRole('button', { name: text('cancel') }));
    expect(savedState()).toEqual(before);
    await openDelete();
    await confirm('delete');
    const after = savedState();
    expect(after.groups).toEqual(before.groups.filter((entry) => entry.id !== group.id));
    expect(after.memberships).toEqual(Object.fromEntries(Object.entries(before.memberships)
      .map(([id, membership]) => [id, membership === group.id ? null : membership])));
    expect(Object.keys(after.memberships).sort()).toEqual(MOCK_ITEMS.map((item) => item.id).sort());
    expect(after.favorites).toEqual(before.favorites);
    expect(groupNav('ungrouped')).toHaveAttribute('aria-current', 'page');
    expect(groupToggle('ungrouped')).toHaveAttribute('aria-expanded', 'true');
    expectVisibleItems(byUpdated(scopeItems('workflow').filter((item) => item.collectionId === null || item.collectionId === group.id)).slice(0, GROUP_PAGE_SIZE));
    expect(within(groupNavigation()).queryByRole('button', { name: text('navigateGroup', { name: group.name }) })).not.toBeInTheDocument();
  });

  it('keeps selection actions in the existing toolbar and excludes collapsed groups from select-visible and moves', async () => {
    renderPage({ pluginType: 'workflow' });
    const all = initiallyDisplayed('workflow');
    const toolbar = screen.getByTestId('list-toolbar');
    const list = screen.getByRole('region', { name: text('resourceList') });
    const listChildren = [...list.children];
    await user.click(itemCheckbox(all[0]));
    expect(selectVisible()).toBePartiallyChecked();
    expect(toolbar).toContainElement(screen.getByRole('region', { name: text('batchActions') }));
    expect([...list.children]).toEqual(listChildren);
    await user.click(selectVisible());
    expect(within(toolbar).getByText(text('selectedCount', { count: all.length }))).toBeVisible();
    await user.click(within(toolbar).getByRole('button', { name: text('cancelSelection') }));
    expectNoSelection();
    // One toggle changes its label instead of reserving two toolbar controls.
    expect(screen.queryByRole('button', { name: text('expandAll') })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: text('collapseAll') }));
    expectVisibleItems([]);
    expect(selectVisible()).toBeDisabled();
    expect(screen.queryByRole('button', { name: text('collapseAll') })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: text('expandAll') }));
    expectVisibleItems(all);
    await user.click(selectVisible());
    await user.click(groupToggle(scopeGroups('workflow')[0].id));
    expectNoSelection();
    const remaining = all.filter((item) => item.collectionId !== scopeGroups('workflow')[0].id);
    expectVisibleItems(remaining);
    await user.click(selectVisible());
    expect(within(toolbar).getByText(text('selectedCount', { count: remaining.length }))).toBeVisible();
    expect(toolbar).toContainElement(screen.getByRole('region', { name: text('batchActions') }));
    await user.click(within(toolbar).getByRole('button', { name: text('moveTo') }));
    const before = savedState();
    await moveThroughDialog(null, 'workflow');
    expectOnlyMoved(before, remaining.map((item) => item.id), null);
    await user.click(screen.getByRole('button', { name: text('collapseAll') }));
    expectVisibleItems([]);
    expect(selectVisible()).toBeDisabled();
    expectNoSelection();
  });

  it('clears selection on query, facet, group navigation, collapse and sort changes', async () => {
    for (const change of ['query', 'facet', 'group', 'collapse', 'sort']) {
      const mounted = renderPage({ pluginType: 'workflow' });
      await user.click(selectVisible());
      expect(screen.getByRole('region', { name: text('batchActions') })).toBeVisible();
      if (change === 'query') await user.type(searchInput(), initiallyDisplayed('workflow')[0].identifier);
      if (change === 'facet') {
        const facet = SCOPE_CONFIG.workflow.facets[0];
        await toggleFacet(facet, initiallyDisplayed('workflow')[0].attributes[facet.key]);
      }
      if (change === 'group') await navigateGroup(scopeGroups('workflow')[1].id);
      if (change === 'collapse') await user.click(groupToggle(scopeGroups('workflow')[0].id));
      if (change === 'sort') await user.selectOptions(screen.getByRole('combobox', { name: text('sort') }), 'name');
      expectNoSelection();
      mounted.unmount();
    }
  });

  it('clears query, grouping and selection when the explicit page type changes, even with a conflicting URL type', async () => {
    const mounted = renderPage({ pluginType: 'workflow', url: '/workflows?preview=groups&type=workflow' });
    const item = initiallyDisplayed('workflow')[0];
    await navigateGroup(item.collectionId!);
    await user.type(searchInput(), item.identifier);
    await user.click(itemCheckbox(item));
    mounted.rerenderType('tool');
    expect(screen.getByRole('heading', { level: 1, name: text('pages.tool.title') })).toBeVisible();
    expect(searchInput()).toHaveValue('');
    expect(groupNav('all')).toHaveAttribute('aria-current', 'page');
    expectVisibleItems(initiallyDisplayed('tool'));
    expectNoSelection();
    expect(groupToggle(scopeGroups('tool')[1].id)).toHaveAttribute('aria-expanded', 'true');
  });

  it('moves an individual device instance to a collapsed same-scope group without changing original room or resource count', async () => {
    renderPage({ pluginType: 'device' });
    const item = initiallyDisplayed('device-instance')[0];
    const target = scopeGroups('device-instance')[1];
    const before = savedState();
    await user.click(groupToggle(target.id));
    expect(groupToggle(target.id)).toHaveAttribute('aria-expanded', 'false');
    await user.click(screen.getByRole('button', { name: text('moveItem', { name: item.name }) }));
    expect(within(dialog('move')).getByText(text('dialog.moveDescription', { count: 1 }))).toBeVisible();
    await moveThroughDialog(target.id, 'device-instance');
    expectOnlyMoved(before, [item.id], target.id);
    expect(groupToggle(target.id)).toHaveAttribute('aria-expanded', 'true');
    await user.type(searchInput(), item.identifier);
    expectVisibleItems([item]);
    await user.click(screen.getByRole('button', { name: text('previewItem', { name: item.name }) }));
    const drawer = screen.getByRole('dialog', { name: text('detail.title') });
    const room = SCOPE_CONFIG['device-instance'].facets.find((facet) => facet.key === 'room')!;
    expect(within(drawer).getByText(room.options.find((option) => option.value === item.attributes.room)!.label.zh)).toBeVisible();
    expect(within(drawer).getByText(target.name)).toBeVisible();
  });

  it('bulk moves displayed plugins into one group, paginates that group by six, clears selection and clamps pages after moving its last page out', async () => {
    renderPage({ pluginType: 'workflow' });
    const target = await createGroupThroughUI('分页归组测试');
    const selected = allDisplayed(scopeItems('workflow'));
    expect(selected.length).toBeGreaterThan(GROUP_PAGE_SIZE * 2);
    await user.click(selectVisible());
    const before = savedState();
    await user.click(screen.getByRole('button', { name: text('moveTo') }));
    await moveThroughDialog(target.id, 'workflow');
    expectOnlyMoved(before, selected.map((item) => item.id), target.id);
    expectNoSelection();
    await navigateGroup(target.id);
    const ordered = byUpdated(selected);
    expectVisibleItems(ordered.slice(0, GROUP_PAGE_SIZE));
    const next = () => screen.getByRole('button', { name: text('nextGroupPage', { name: target.name }) });
    const previous = () => screen.getByRole('button', { name: text('previousGroupPage', { name: target.name }) });
    expect(previous()).toBeDisabled();
    await user.click(itemCheckbox(ordered[0]));
    await user.click(next());
    expectNoSelection();
    expectVisibleItems(ordered.slice(GROUP_PAGE_SIZE, GROUP_PAGE_SIZE * 2));
    // Querying from page two must restart the matching group's pagination.
    await user.type(searchInput(), ordered[0].identifier);
    expectVisibleItems([ordered[0]]);
    expect(screen.queryByRole('button', { name: text('nextGroupPage', { name: target.name }) })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: text('clearSearch') }));
    expectVisibleItems(ordered.slice(0, GROUP_PAGE_SIZE));
    const pages = Math.ceil(ordered.length / GROUP_PAGE_SIZE);
    for (let page = 1; page < pages; page++) await user.click(next());
    const lastPage = ordered.slice((pages - 1) * GROUP_PAGE_SIZE);
    expectVisibleItems(lastPage);
    expect(next()).toBeDisabled();
    await user.click(selectVisible());
    await user.click(screen.getByRole('button', { name: text('moveTo') }));
    const beforeLastMove = savedState();
    await moveThroughDialog(null, 'workflow');
    expectOnlyMoved(beforeLastMove, lastPage.map((item) => item.id), null);
    const remaining = ordered.slice(0, (pages - 1) * GROUP_PAGE_SIZE);
    expectVisibleItems(remaining.slice(-GROUP_PAGE_SIZE));
    expect(next()).toBeDisabled();
    expect(screen.getByText(text('groupPage', { page: pages - 1, pages: pages - 1, count: remaining.length }))).toBeVisible();
    expectNoSelection();
  });

  it('drags selected plugins onto a collapsed group header, expanding the target without making groups draggable or nested', async () => {
    renderPage({ pluginType: 'workflow' });
    const selected = initiallyDisplayed('workflow').slice(0, 2);
    const target = scopeGroups('workflow')[1];
    const before = savedState();
    await user.click(groupToggle(target.id));
    for (const item of selected) await user.click(itemCheckbox(item));
    const transfer = dragTransfer();
    fireEvent.dragStart(itemElement(selected[0]), { dataTransfer: transfer });
    expect(transfer.effectAllowed).toBe('move');
    expect(transfer.setData).toHaveBeenCalledWith('text/plain', selected.map((item) => item.id).join(','));
    expect(groupToggle(target.id)).toHaveAttribute('aria-expanded', 'false');
    fireEvent.dragOver(groupHeader(target.id), { dataTransfer: transfer });
    expect(transfer.dropEffect).toBe('move');
    expect(within(groupHeader(target.id)).getByText(text('dropHere'))).toBeVisible();
    fireEvent.drop(groupHeader(target.id), { dataTransfer: transfer });
    expectOnlyMoved(before, selected.map((item) => item.id), target.id);
    expect(groupToggle(target.id)).toHaveAttribute('aria-expanded', 'true');
    expect(screen.queryByText(text('dropHere'))).not.toBeInTheDocument();
    expectNoSelection();
    for (const header of document.querySelectorAll('[data-group-id]')) {
      expect(header.closest('[draggable="true"]')).toBeNull();
      expect(header.querySelector('[data-group-id]')).toBeNull();
    }
  });

  it('drops onto navigation groups and ungrouped without changing the active source, while All and external drags never move plugins', async () => {
    renderPage({ pluginType: 'workflow' });
    const [source, target] = scopeGroups('workflow');
    const [first, second] = groupPage(scopeItems('workflow'), source.id);
    await navigateGroup(source.id);
    const before = savedState();
    const external = dragTransfer();
    external.setData('text/plain', first.id);
    fireEvent.dragOver(groupNav(target.id), { dataTransfer: external });
    fireEvent.drop(groupNav(target.id), { dataTransfer: external });
    expect(savedState()).toEqual(before);
    const ignored = dragTransfer();
    const firstElement = itemElement(first);
    fireEvent.dragStart(firstElement, { dataTransfer: ignored });
    fireEvent.dragOver(groupNav('all'), { dataTransfer: ignored });
    fireEvent.drop(groupNav('all'), { dataTransfer: ignored });
    expect(ignored.dropEffect).toBe('none');
    expect(savedState()).toEqual(before);
    expect(groupNav(source.id)).toHaveAttribute('aria-current', 'page');
    fireEvent.dragEnd(firstElement, { dataTransfer: ignored });
    const toGroup = dragTransfer();
    fireEvent.dragStart(firstElement, { dataTransfer: toGroup });
    fireEvent.dragOver(groupNav(target.id), { dataTransfer: toGroup });
    expect(toGroup.dropEffect).toBe('move');
    fireEvent.drop(groupNav(target.id), { dataTransfer: toGroup });
    expectOnlyMoved(before, [first.id], target.id);
    expect(groupNav(source.id)).toHaveAttribute('aria-current', 'page');
    expect(groupNav(target.id)).not.toHaveAttribute('aria-current');
    expect(document.querySelector(`[data-group-id="${target.id}"]`)).toBeNull();
    expectVisibleItems(groupPage(scopeItems('workflow').filter((item) => item.id !== first.id), source.id));
    const beforeUngrouping = savedState();
    const toUngrouped = dragTransfer();
    fireEvent.dragStart(itemElement(second), { dataTransfer: toUngrouped });
    fireEvent.dragOver(groupNav('ungrouped'), { dataTransfer: toUngrouped });
    expect(toUngrouped.dropEffect).toBe('move');
    fireEvent.drop(groupNav('ungrouped'), { dataTransfer: toUngrouped });
    expectOnlyMoved(beforeUngrouping, [second.id], null);
    expect(groupNav(source.id)).toHaveAttribute('aria-current', 'page');
    expectNavigation('workflow', scopeItems('workflow').map((item) => ({
      ...item, collectionId: item.id === first.id ? target.id : item.id === second.id ? null : item.collectionId,
    })));
    for (const button of groupNavigation().querySelectorAll('[data-group-nav-id]')) {
      expect(button).not.toHaveAttribute('draggable', 'true');
      expect(button.querySelector('[data-group-nav-id]')).toBeNull();
    }
  });

  it('ignores external and cancelled drags, and dragging an unselected plugin does not move the current selection', async () => {
    renderPage({ pluginType: 'workflow' });
    const [selected, dragged, cancelled] = initiallyDisplayed('workflow');
    const target = scopeGroups('workflow')[1];
    const before = savedState();
    await user.click(groupToggle(target.id));
    const external = dragTransfer();
    external.setData('text/plain', dragged.id);
    fireEvent.dragOver(groupHeader(target.id), { dataTransfer: external });
    fireEvent.drop(groupHeader(target.id), { dataTransfer: external });
    expect(savedState()).toEqual(before);
    expect(groupToggle(target.id)).toHaveAttribute('aria-expanded', 'false');
    await user.click(itemCheckbox(selected));
    const transfer = dragTransfer();
    fireEvent.dragStart(itemElement(dragged), { dataTransfer: transfer });
    expect(transfer.setData).toHaveBeenCalledWith('text/plain', dragged.id);
    fireEvent.drop(groupHeader(target.id), { dataTransfer: transfer });
    expectOnlyMoved(before, [dragged.id], target.id);
    const afterMove = savedState();
    const cancelledElement = itemElement(cancelled);
    fireEvent.dragStart(cancelledElement, { dataTransfer: transfer });
    fireEvent.dragOver(groupHeader('ungrouped'), { dataTransfer: transfer });
    fireEvent.dragEnd(cancelledElement, { dataTransfer: transfer });
    expect(screen.queryByText(text('dropHere'))).not.toBeInTheDocument();
    fireEvent.drop(groupHeader('ungrouped'), { dataTransfer: transfer });
    expect(savedState()).toEqual(afterMove);
  });

  it('reloads persisted one-level groups and membership, and confirms reset without changing other localStorage keys', async () => {
    localStorage.setItem('other-app:preferences', 'preserve-me');
    const mounted = renderPage({ pluginType: 'workflow' });
    const item = initiallyDisplayed('workflow')[0];
    const target = await createGroupThroughUI('刷新保留分组');
    await user.click(screen.getByRole('button', { name: text('moveItem', { name: item.name }) }));
    await moveThroughDialog(target.id, 'workflow');
    const persisted = savedState();
    mounted.unmount();
    const refreshed = renderPage({ pluginType: 'workflow' });
    expect(savedState()).toEqual(persisted);
    await navigateGroup(target.id);
    expectVisibleItems([item]);
    await user.click(screen.getByRole('button', { name: text('reset') }));
    await user.click(within(dialog('reset')).getByRole('button', { name: text('cancel') }));
    expect(savedState()).toEqual(persisted);
    await user.click(screen.getByRole('button', { name: text('reset') }));
    await confirm('reset');
    expect(savedState()).toEqual(createInitialState());
    expect(localStorage.getItem('other-app:preferences')).toBe('preserve-me');
    expectVisibleItems(initiallyDisplayed('workflow'));
    refreshed.unmount();
    renderPage({ pluginType: 'workflow' });
    expect(savedState()).toEqual(createInitialState());
    expect(document.querySelector(`[data-group-id="${target.id}"]`)).toBeNull();
    expect(localStorage.getItem('other-app:preferences')).toBe('preserve-me');
  });

  it('previews all six scopes as mock-only details with close/move actions, no execution or favorite controls, and native cancel', async () => {
    for (const scope of Object.keys(SCOPE_CONFIG) as CatalogScope[]) {
      const pluginType = SCOPE_CONFIG[scope].type;
      const mounted = renderPage({ pluginType, url: `/example?preview=groups&deviceView=${scope === 'device-template' ? 'templates' : 'instances'}` });
      const item = initiallyDisplayed(scope)[0];
      await user.click(screen.getByRole('button', { name: text('previewItem', { name: item.name }) }));
      const drawer = screen.getByRole('dialog', { name: text('detail.title') });
      expect(drawer).toHaveAttribute('open');
      expect(within(drawer).getByRole('heading', { name: item.name })).toBeVisible();
      expect(within(drawer).getByText(item.identifier)).toBeVisible();
      expect(within(drawer).getByText('MOCK')).toBeVisible();
      expect(within(drawer).getByText(text(pluginType === 'device' ? 'detail.deviceNotice' : 'detail.mockNotice'))).toBeVisible();
      expect(within(drawer).getAllByRole('button')).toHaveLength(2);
      for (const name of [text('close'), text('moveTo')]) expect(within(drawer).getByRole('button', { name })).toBeEnabled();
      expect(within(drawer).queryByRole('link')).not.toBeInTheDocument();
      fireEvent(drawer, new Event('cancel', { bubbles: false, cancelable: true }));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(savedState()).toEqual(createInitialState());
      mounted.unmount();
    }
  });

  it('repairs corrupted and obsolete storage with a dismissible warning, retaining other application data', async () => {
    for (const raw of ['{not-json', JSON.stringify({ version: 0, groups: [] })]) {
      localStorage.setItem(STORAGE_KEY, raw);
      localStorage.setItem('other-app:preferences', 'preserved');
      const mounted = renderPage({ pluginType: 'workflow' });
      const notice = screen.getByRole('status');
      expect(notice).toHaveTextContent(text('storageRecovered'));
      expectVisibleItems(initiallyDisplayed('workflow'));
      expect(savedState()).toEqual(createInitialState());
      await user.click(within(notice).getByRole('button', { name: text('close') }));
      expect(screen.queryByRole('status')).not.toBeInTheDocument();
      mounted.unmount();
      const repaired = renderPage({ pluginType: 'workflow' });
      expect(screen.queryByRole('status')).not.toBeInTheDocument();
      expect(localStorage.getItem('other-app:preferences')).toBe('preserved');
      repaired.unmount();
    }
  });

  it.each([
    { fault: 'blocked reads and writes', readFails: true, writeFails: true },
    { fault: 'failed writes', readFails: false, writeFails: true },
    { fault: 'failed reads', readFails: true, writeFails: false },
  ])('warns about $fault while allowing in-memory groups and never overwriting unread records', async ({ readFails, writeFails }) => {
    const persisted = createInitialState();
    persisted.groups[0].name = '不可覆盖的现有分组';
    const raw = JSON.stringify(persisted);
    localStorage.setItem(STORAGE_KEY, raw);
    const { storage, setItem } = installStorageFailure(readFails, writeFails);
    renderPage({ pluginType: 'workflow' });
    expect(screen.getByRole('status')).toHaveTextContent(text('storageUnavailable'));
    if (readFails) expect(setItem).not.toHaveBeenCalled();
    else expect(setItem).toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: text('createGroup') }));
    await user.type(within(dialog('create')).getByRole('textbox', { name: text('dialog.groupName') }), '仅内存中的新分组');
    await confirm('create');
    expect(screen.getByRole('heading', { level: 3, name: countedName('仅内存中的新分组') })).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent(text('storageUnavailable'));
    if (readFails) expect(setItem).not.toHaveBeenCalled();
    expect(storage.getItem(STORAGE_KEY)).toBe(raw);
  });
});
