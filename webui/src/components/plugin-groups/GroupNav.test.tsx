import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createInstance } from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import GroupNav, { useGroupDrag } from './GroupNav';
import PluginGroupButton from './PluginGroupButton';
import { ToastProvider, useToast } from '@/components/common/Toast';
import { deriveGroupNav, type GroupNavItem, type GroupSelection } from './groupView';
import messages from '@/locales/en-US/pluginGroups.json';
import zhMessages from '@/locales/zh-CN/pluginGroups.json';

const move = vi.fn();
const rename = vi.fn();
const remove = vi.fn();
const notify = vi.fn();
const originalClick = vi.fn();
function Example({ inventory, showItems = true, onCreate, onReadOnly = notify }: { inventory: GroupNavItem[]; showItems?: boolean; onCreate?: (key: string, group: string) => Promise<void>; onReadOnly?: (message: string) => void }) {
  const [items, setItems] = useState(inventory);
  const [selection, setSelection] = useState<GroupSelection>(null);
  const drag = useGroupDrag(items, onReadOnly);
  return <>
    <GroupNav inventoryComplete preferenceKey="test" {...deriveGroupNav(items)} items={items} selection={selection} onSelect={setSelection}
      onMove={async (key, group) => { await move(key, group); setItems((current) => current.map((item) => item.key === key ? { ...item, group } : item)); }}
      onCreate={onCreate} onRename={rename} onDelete={remove} {...drag} />
    <output data-testid="selection">{JSON.stringify(selection)}</output>
    {showItems && items.map((item) => <article key={item.key} data-testid={item.key} onClick={originalClick} {...drag.dragProps(item.key)}>
      {item.name}<button>Original action {item.name}</button>
      <PluginGroupButton grouping={drag} itemKey={item.key} />
    </article>)}
  </>;
}
async function mount(items: GroupNavItem[], onCreate?: (key: string, group: string) => Promise<void>) {
  const i18n = createInstance();
  await i18n.use(initReactI18next).init({ lng: 'en-US', resources: { 'en-US': { pluginGroups: messages }, 'zh-CN': { pluginGroups: zhMessages } }, interpolation: { escapeValue: false } });
  const view = render(<I18nextProvider i18n={i18n}><Example inventory={items} onCreate={onCreate} /></I18nextProvider>);
  return { ...view, i18n, showItems: (showItems: boolean) => view.rerender(<I18nextProvider i18n={i18n}><Example inventory={items} showItems={showItems} onCreate={onCreate} /></I18nextProvider>) };
}
function transfer() {
  const values = new Map<string, string>();
  return { types: [] as string[], effectAllowed: '',
    setData(type: string, value: string) { values.set(type, value); this.types.push(type); },
    getData(type: string) { return values.get(type) ?? ''; },
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  localStorage.clear();
  move.mockResolvedValue(undefined);
  rename.mockResolvedValue(undefined);
  remove.mockResolvedValue(undefined);
});

describe('pure native GroupNav', () => {
  it('localizes default names while keeping custom names and the selected native value', async () => {
    const { i18n } = await mount([
      { key: 'a', name: 'A', group: '平台集成' },
      { key: 'b', name: 'B', group: '安全研判' },
      { key: 'c', name: 'C', group: '系统辅助' },
      { key: 'custom', name: 'Custom', group: '客户业务组' },
    ]);
    expect(screen.getByRole('button', { name: 'Platform Integrations 1' })).toHaveAttribute('title', 'Platform Integrations');
    expect(screen.getByRole('button', { name: 'Security Analysis 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'System Utilities 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '客户业务组 1' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Platform Integrations 1' }));
    expect(screen.getByTestId('selection')).toHaveTextContent('"平台集成"');
    await act(async () => { await i18n.changeLanguage('zh-CN'); });
    expect(screen.getByRole('button', { name: '平台集成 1' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '安全研判 1' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '系统辅助 1' })).toBeInTheDocument();
    expect(move).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);
  });

  it('shows translated picker labels but sends native values for picker saves and drops', async () => {
    await mount([
      { key: 'a', name: 'Alpha' }, { key: 'b', name: 'Beta' },
      { key: 'builtin', name: 'Built-in', group: '平台集成', readOnlyReason: messages.readOnly.system },
    ]);
    fireEvent.click(within(screen.getByTestId('a')).getByRole('button', { name: 'Edit group' }));
    expect(screen.getByRole('option', { name: 'Platform Integrations' })).toHaveValue('平台集成');
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '平台集成' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(move).toHaveBeenCalledExactlyOnceWith('a', '平台集成'));
    await screen.findByRole('button', { name: 'Platform Integrations 2' });
    const dataTransfer = transfer();
    fireEvent.dragStart(screen.getByTestId('b'), { dataTransfer });
    fireEvent.drop(screen.getByRole('button', { name: 'Platform Integrations 2' }).parentElement!, { dataTransfer });
    await waitFor(() => expect(move).toHaveBeenLastCalledWith('b', '平台集成'));
  });

  it('uses translated action labels without storing translated ordering keys', async () => {
    await mount([{ key: 'a', name: 'A', group: '平台集成' }, { key: 'b', name: 'B', group: '安全研判' }]);
    expect(screen.getByRole('button', { name: 'Rename group Platform Integrations' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete group Platform Integrations' })).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('button', { name: 'Reorder group Security Analysis' }), { key: 'ArrowUp' });
    expect(JSON.parse(localStorage.getItem('flocks:plugin-group-order:test')!)).toEqual(['安全研判', '平台集成']);
    expect(move).not.toHaveBeenCalled();
  });

  it('only shows a short drag hint, without permanent readonly or implementation explanations', async () => {
    await mount([{ key: 'builtin', name: 'Built-in', group: 'Fixed', readOnlyReason: messages.readOnly.system }]);
    const sidebar = screen.getByRole('complementary');
    expect(within(sidebar).getByText(messages.countsDescription)).toBeInTheDocument();
    expect(sidebar).not.toHaveTextContent(messages.readOnly.system);
    expect(sidebar).not.toHaveTextContent(/derived|administrators|personal|native group/i);
    expect(notify).not.toHaveBeenCalled();
  });

  it('opens the existing group picker from the item button without triggering the original card action', async () => {
    await mount([{ key: 'a', name: 'Alpha', group: 'First' }, { key: 'b', name: 'Beta', group: 'Second' }]);
    fireEvent.click(within(screen.getByTestId('a')).getByRole('button', { name: 'Edit group' }));
    expect(screen.getByRole('dialog', { name: 'Edit group for Alpha' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: 'Destination group' })).toHaveValue('First');
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'Second' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(move).toHaveBeenCalledExactlyOnceWith('a', 'Second'));
    expect(originalClick).not.toHaveBeenCalled();
    expect(notify).not.toHaveBeenCalled();
  });

  it('warns on the readonly item button without opening a picker or saving', async () => {
    await mount([{ key: 'builtin', name: 'Built-in', group: 'Fixed', readOnlyReason: messages.readOnly.system }]);
    const button = within(screen.getByTestId('builtin')).getByRole('button', { name: 'Edit group' });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(notify).toHaveBeenCalledExactlyOnceWith(messages.readOnly.system);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(move).not.toHaveBeenCalled();
    expect(originalClick).not.toHaveBeenCalled();
  });

  it('uses the existing warning toast for an attempted builtin drag and never creates a drag payload', async () => {
    const i18n = createInstance();
    await i18n.use(initReactI18next).init({ lng: 'en-US', resources: { 'en-US': { pluginGroups: messages } } });
    function ToastExample() {
      const { warning } = useToast();
      return <Example inventory={[{ key: 'builtin', name: 'Built-in', group: 'Fixed', readOnlyReason: messages.readOnly.system }]} onReadOnly={warning} />;
    }
    render(<I18nextProvider i18n={i18n}><ToastProvider><ToastExample /></ToastProvider></I18nextProvider>);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    const dataTransfer = transfer();
    expect(fireEvent.dragStart(screen.getByTestId('builtin'), { dataTransfer })).toBe(false);
    expect(dataTransfer.types).toEqual([]);
    expect(screen.getByRole('alert')).toHaveTextContent(messages.readOnly.system);
    fireEvent.drop(screen.getByRole('button', { name: 'Ungrouped 0' }).parentElement!, { dataTransfer });
    expect(move).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('only clears a missing selection for an authoritative unfiltered inventory', async () => {
    const i18n = createInstance();
    await i18n.use(initReactI18next).init({ lng: 'en-US', resources: { 'en-US': { pluginGroups: messages } } });
    const select = vi.fn();
    const props = { preferenceKey: 'facets', selection: 'Alpha', onSelect: select,
      onMove: move, onRename: rename, onDelete: remove, items: [], total: 1, ungroupedCount: 0 };
    const { rerender } = render(<I18nextProvider i18n={i18n}><GroupNav {...props} groups={[{ name: 'Beta', count: 1 }]} /></I18nextProvider>);
    expect(screen.getByRole('button', { name: 'Alpha 0' })).toHaveAttribute('aria-pressed', 'true');
    expect(select).not.toHaveBeenCalled();
    rerender(<I18nextProvider i18n={i18n}><GroupNav {...props} groups={[]} total={0} /></I18nextProvider>);
    expect(select).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Alpha 0' })).toHaveAttribute('aria-pressed', 'true');
    rerender(<I18nextProvider i18n={i18n}><GroupNav {...props} groups={[]} total={0} inventoryComplete /></I18nextProvider>);
    expect(select).toHaveBeenCalledExactlyOnceWith(null);
  });

  it('requires the first editable plugin and never persists an empty group', async () => {
    await mount([{ key: 'a', name: 'Alpha' }, { key: 'builtin', name: 'Built-in', readOnlyReason: 'Read-only definition' }]);
    fireEvent.click(screen.getByRole('button', { name: 'New group' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Group name' }), { target: { value: '  Ops  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(screen.getByRole('alert')).toHaveTextContent(messages.validation.firstPlugin);
    expect(move).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);
    const select = screen.getByRole('combobox', { name: 'First editable plugin' });
    expect(within(select).queryByRole('option', { name: 'Built-in' })).not.toBeInTheDocument();
    fireEvent.change(select, { target: { value: 'a' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByRole('button', { name: 'Ops 1' });
    expect(move).toHaveBeenCalledExactlyOnceWith('a', 'Ops');
    expect(localStorage.length).toBe(0);
    fireEvent.click(screen.getByRole('button', { name: 'Ops 1' }));
    fireEvent.keyDown(screen.getByTestId('a'), { key: 'm', altKey: true });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Ops 1' })).not.toBeInTheDocument());
    expect(screen.getByTestId('selection')).toHaveTextContent('null');
    expect(move).toHaveBeenLastCalledWith('a', null);
  });

  it('lets the native page reject a new name outside the displayed group scope', async () => {
    const create = vi.fn().mockRejectedValue(new Error(messages.validation.duplicate));
    await mount([{ key: 'a', name: 'Alpha' }], create);
    fireEvent.click(screen.getByRole('button', { name: 'New group' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Outside current filters' } });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'a' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(messages.validation.duplicate));
    expect(create).toHaveBeenCalledExactlyOnceWith('a', 'Outside current filters');
    expect(move).not.toHaveBeenCalled();
    expect(within(screen.getByRole('complementary')).queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('rejects duplicate names without merging or issuing native writes', async () => {
    await mount([{ key: 'a', name: 'A', group: 'Alpha' }, { key: 'b', name: 'B', group: 'Beta' }]);
    fireEvent.click(screen.getByRole('button', { name: 'Rename group Alpha' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: ' Beta ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(screen.getByRole('alert')).toHaveTextContent(messages.validation.duplicate);
    expect(rename).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'New group' }));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Alpha' } });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'b' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(screen.getByRole('alert')).toHaveTextContent(messages.validation.duplicate);
    expect(move).not.toHaveBeenCalled();
  });

  it('keeps a failed native save visible and only reports success after a successful retry', async () => {
    move.mockRejectedValueOnce(new Error('Alpha: disk full'));
    await mount([{ key: 'a', name: 'Alpha', group: 'Operations' }]);
    fireEvent.keyDown(screen.getByTestId('a'), { key: 'm', altKey: true });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Alpha: disk full'));
    expect(within(screen.getByRole('complementary')).queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Operations 1' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(within(screen.getByRole('complementary')).getByRole('status')).toHaveTextContent(messages.saved);
    expect(screen.queryByRole('button', { name: 'Operations 1' })).not.toBeInTheDocument();
    expect(move).toHaveBeenCalledTimes(2);
  });

  it('sorts only current names as a personal preference without native writes', async () => {
    localStorage.setItem('flocks:plugin-group-order:test', JSON.stringify(['Ghost', 'Beta']));
    await mount([{ key: 'a', name: 'A', group: 'Alpha' }, { key: 'b', name: 'B', group: 'Beta' }]);
    expect(screen.queryByText('Ghost')).not.toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('button', { name: 'Reorder group Alpha' }), { key: 'ArrowUp' });
    expect(JSON.parse(localStorage.getItem('flocks:plugin-group-order:test')!)).toEqual(['Alpha', 'Beta']);
    const dataTransfer = transfer();
    fireEvent.dragStart(screen.getByRole('button', { name: 'Reorder group Beta' }), { dataTransfer });
    fireEvent.drop(screen.getByRole('button', { name: 'Alpha 1' }).parentElement!, { dataTransfer });
    expect(JSON.parse(localStorage.getItem('flocks:plugin-group-order:test')!)).toEqual(['Beta', 'Alpha']);
    expect(move).not.toHaveBeenCalled();
    expect(rename).not.toHaveBeenCalled();
    expect(remove).not.toHaveBeenCalled();
  });

  it('keeps All distinct from Ungrouped and groups literally called all/new', async () => {
    await mount([{ key: 'a', name: 'A', group: 'all' }, { key: 'b', name: 'B', group: 'new' }, { key: 'c', name: 'C' }]);
    fireEvent.click(screen.getByRole('button', { name: 'Ungrouped 1' }));
    expect(screen.getByTestId('selection')).toHaveTextContent('""');
    fireEvent.click(screen.getByRole('button', { name: 'all 1' }));
    expect(screen.getByTestId('selection')).toHaveTextContent('"all"');
    fireEvent.click(screen.getByRole('button', { name: 'All 3' }));
    expect(screen.getByTestId('selection')).toHaveTextContent('null');
    const dataTransfer = transfer();
    fireEvent.dragStart(screen.getByTestId('c'), { dataTransfer });
    fireEvent.drop(screen.getByRole('button', { name: 'new 1' }).parentElement!, { dataTransfer });
    await waitFor(() => expect(move).toHaveBeenCalledWith('c', 'new'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('supports dropping onto New group, ignores external IDs and preserves native controls', async () => {
    await mount([{ key: 'a', name: 'A' }]);
    const dataTransfer = transfer();
    dataTransfer.setData('application/x-flocks-visible-plugin', 'not-visible');
    fireEvent.drop(screen.getByRole('button', { name: 'Ungrouped 1' }).parentElement!, { dataTransfer });
    expect(move).not.toHaveBeenCalled();
    expect(fireEvent.dragStart(screen.getByRole('button', { name: 'Original action A' }), { dataTransfer })).toBe(false);
    fireEvent.dragStart(screen.getByTestId('a'), { dataTransfer });
    fireEvent.drop(screen.getByRole('button', { name: 'New group' }), { dataTransfer });
    expect(screen.getByRole('combobox', { name: 'First editable plugin' })).toHaveValue('a');
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'First' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(move).toHaveBeenCalledWith('a', 'First'));
  });

  it('rejects forged visible keys, cancelled drags and unmounted sources', async () => {
    const view = await mount([{ key: 'a', name: 'A', group: 'Alpha' }, { key: 'b', name: 'B', group: 'Beta' }]);
    const target = screen.getByRole('button', { name: 'Beta 1' }).parentElement!;
    const forged = transfer();
    forged.setData('application/x-flocks-visible-plugin', 'a');
    fireEvent.drop(target, { dataTransfer: forged });
    const reorderForgery = transfer();
    reorderForgery.setData('application/x-flocks-group-order', 'Alpha');
    fireEvent.drop(target, { dataTransfer: reorderForgery });
    expect(localStorage.length).toBe(0);
    const cancelled = transfer();
    fireEvent.dragStart(screen.getByTestId('a'), { dataTransfer: cancelled });
    fireEvent.dragEnd(screen.getByTestId('a'));
    fireEvent.drop(target, { dataTransfer: cancelled });
    const unmounted = transfer();
    fireEvent.dragStart(screen.getByTestId('a'), { dataTransfer: unmounted });
    view.showItems(false);
    fireEvent.drop(target, { dataTransfer: unmounted });
    expect(move).not.toHaveBeenCalled();
    expect(rename).not.toHaveBeenCalled();
  });

  it('traps modal keyboard focus and restores it on Escape without saving', async () => {
    await mount([{ key: 'a', name: 'A', group: 'Alpha' }]);
    const source = screen.getByTestId('a');
    source.focus();
    fireEvent.keyDown(source, { key: 'm', altKey: true });
    const dialog = screen.getByRole('dialog');
    expect(screen.getByRole('combobox')).toHaveFocus();
    const close = within(dialog).getByRole('button', { name: 'Close' });
    const save = within(dialog).getByRole('button', { name: 'Save' });
    save.focus();
    fireEvent.keyDown(save, { key: 'Tab' });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: 'Tab', shiftKey: true });
    expect(save).toHaveFocus();
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(source).toHaveFocus();
    expect(move).not.toHaveBeenCalled();
  });

  it('supports Option+M on macOS and focuses the name when switching to group creation', async () => {
    await mount([{ key: 'a', name: 'Alpha' }]);
    const source = screen.getByTestId('a');
    const input = document.createElement('input');
    source.appendChild(input);
    input.focus();
    expect(fireEvent.keyDown(input, { key: 'µ', code: 'KeyM', altKey: true })).toBe(true);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    source.focus();
    fireEvent.keyDown(source, { key: 'µ', code: 'KeyM', altKey: true });
    expect(screen.getByRole('combobox', { name: 'Destination group' })).toHaveFocus();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'New group' }));
    expect(screen.getByRole('textbox', { name: 'Group name' })).toHaveFocus();
    expect(screen.getByRole('combobox', { name: 'First editable plugin' })).toHaveValue('a');
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(source).toHaveFocus();
    expect(move).not.toHaveBeenCalled();
  });

  it('blocks whole-group edits with read-only members but permits moving editable ones', async () => {
    await mount([{ key: 'builtin', name: 'Built-in', group: 'Mixed', readOnlyReason: 'Built-in even for admin' }, { key: 'custom', name: 'Custom', group: 'Mixed' }]);
    expect(screen.getByRole('button', { name: 'Rename group Mixed' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Delete group Mixed' })).toBeDisabled();
    const dataTransfer = transfer();
    expect(fireEvent.dragStart(screen.getByTestId('builtin'), { dataTransfer })).toBe(false);
    expect(dataTransfer.types).toEqual([]);
    expect(notify).toHaveBeenCalledWith('Built-in even for admin');
    fireEvent.keyDown(screen.getByTestId('builtin'), { key: 'm', altKey: true });
    expect(notify).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.keyDown(screen.getByTestId('custom'), { key: 'm', altKey: true });
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(move).toHaveBeenCalledExactlyOnceWith('custom', null));
  });
});
