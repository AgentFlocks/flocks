import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import ModelDiscovery from './ModelDiscovery';
import ProviderKeyCheck from './ProviderKeyCheck';
const api = vi.hoisted(() => ({ discoverModels: vi.fn(), getModelInventory: vi.fn(), saveModelInventory: vi.fn(), checkKey: vi.fn() }));
vi.mock('@/api/provider', () => ({ providerAPI: api }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en-US' } }) }));
const remote = (id: string) => ({ id, name: id, selectable: true, already_added: false, context_window: 32000, max_output_tokens: 4000 });
const result = (...ids: string[]) => ({ data: { source: 'api', status: 'success', code: 'models_discovered', message: 'Ready', models: ids.map(remote) } });
beforeEach(() => {
  vi.clearAllMocks();
  api.getModelInventory.mockResolvedValue({ data: { models: [{ id: 'saved', name: 'Saved', enabled: false, supports_tools: false }] } });
  api.discoverModels.mockResolvedValue(result('new-one', 'new-two'));
  api.saveModelInventory.mockResolvedValue({ data: { added: ['new-one'], skipped: [], failed: [] } });
  api.checkKey.mockResolvedValue({ data: { status: 'valid', code: 'key_valid' } });
});
const fetchButton = () => screen.getByRole('button', { name: /discovery.title|compact.refresh/ });

it('shows compact editable switches with only select all and select none; new limits remain Auto', async () => {
  const user = userEvent.setup();
  render(<ModelDiscovery providerId="openai" mode="stage" />);
  await user.click(fetchButton());
  expect(await screen.findByRole('switch', { name: 'compact.enableModel new-one' })).toBeChecked();
  expect(screen.queryByText('discovery.filtersTitle')).not.toBeInTheDocument();
  expect(screen.queryByText('discovery.invertVisible')).not.toBeInTheDocument();
  expect(screen.queryByText('32000')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'compact.selectNone' }));
  screen.getAllByRole('switch').forEach(toggle => expect(toggle).not.toBeChecked());
  await user.type(screen.getByRole('searchbox'), 'new-one');
  await user.click(screen.getByRole('button', { name: 'compact.selectAll' }));
  await user.clear(screen.getByRole('searchbox'));
  screen.getAllByRole('switch').forEach(toggle => expect(toggle).toBeChecked());
  await user.click(screen.getByRole('button', { name: 'compact.editModel new-one' }));
  const editor = within(screen.getByRole('group', { name: 'compact.editModel' }));
  expect(editor.getAllByPlaceholderText('Auto')).toHaveLength(2);
  editor.getAllByRole('spinbutton').forEach(input => expect(input).toHaveValue(null));
  expect(editor.getByRole('switch', { name: 'compact.supports_streaming' })).toBeChecked();
  expect(editor.getByRole('switch', { name: 'compact.supports_reasoning' })).toBeChecked();
  expect(api.saveModelInventory).not.toHaveBeenCalled();
});

it('lets existing models participate in both bulk switch operations without deleting them', async () => {
  const user = userEvent.setup(); const onInventoryChange = vi.fn();
  render(<ModelDiscovery providerId="openai" mode="stage" existingModelIds={['saved']} onInventoryChange={onInventoryChange} />);
  expect(await screen.findByRole('switch', { name: 'compact.enableModel saved' })).not.toBeChecked();
  await user.click(screen.getByRole('button', { name: 'compact.selectAll' }));
  expect(screen.getByRole('switch', { name: 'compact.enableModel saved' })).toBeChecked();
  await user.click(screen.getByRole('button', { name: 'compact.selectNone' }));
  expect(onInventoryChange).toHaveBeenLastCalledWith({ models: [expect.objectContaining({ id: 'saved', enabled: false })], deleted: [] });
  expect(api.saveModelInventory).not.toHaveBeenCalled();
});

it('validates editor values and saves Auto as an explicit cleared override', async () => {
  const user = userEvent.setup(); const ready = vi.fn();
  render(<ModelDiscovery providerId="openai" mode="import" existingModelIds={['saved']} onReadyChange={ready} />);
  await user.click(await screen.findByRole('button', { name: 'compact.editModel saved' }));
  expect(ready).toHaveBeenLastCalledWith(false);
  const editor = within(screen.getByRole('group', { name: 'compact.editModel' }));
  await user.type(editor.getByRole('spinbutton', { name: 'form.contextWindow' }), '0');
  expect(editor.getByRole('button', { name: 'compact.apply' })).toBeDisabled();
  await user.clear(editor.getByRole('spinbutton', { name: 'form.contextWindow' }));
  await user.click(editor.getByRole('switch', { name: 'compact.supports_tools' }));
  await user.click(editor.getByRole('button', { name: 'compact.apply' }));
  expect(ready).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByRole('button', { name: 'compact.saveModels' }));
  expect(api.saveModelInventory).toHaveBeenCalledWith('openai', { models: [expect.objectContaining({ id: 'saved', edited: true, enabled: false, context_window: null, max_output_tokens: null, supports_tools: true })], deleted: [] });
});

it('only explicit trash actions delete saved models and apply on save', async () => {
  const user = userEvent.setup(); render(<ModelDiscovery providerId="openai" mode="import" existingModelIds={['saved']} />);
  await user.click(await screen.findByRole('button', { name: 'compact.deleteModel saved' }));
  expect(api.saveModelInventory).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'compact.saveModels' }));
  expect(api.saveModelInventory).toHaveBeenCalledWith('openai', { models: [], deleted: ['saved'] });
});

it('retains unselected candidates after saving none so they can be selected later', async () => {
  const user = userEvent.setup(); render(<ModelDiscovery providerId="openai" mode="import" />);
  await user.click(fetchButton());
  await user.click(screen.getByRole('button', { name: 'compact.selectNone' }));
  await user.click(screen.getByRole('button', { name: 'compact.saveModels' }));
  expect(api.saveModelInventory).toHaveBeenCalledWith('openai', { models: [], deleted: [] });
  expect(screen.getAllByRole('switch')).toHaveLength(2);
  screen.getAllByRole('switch').forEach(toggle => expect(toggle).not.toBeChecked());
});

it('keeps initial inventory loading alive when credentials are supplied at mount', async () => {
  let resolve!: (value: unknown) => void;
  api.getModelInventory.mockReturnValue(new Promise(r => { resolve = r; }));
  render(<ModelDiscovery providerId="openai" mode="stage" credentials={{ api_key: 'draft' }} existingModelIds={['saved']} />);
  await act(async () => { resolve({ data: { models: [{ id: 'saved', name: 'Saved', enabled: true }] } }); });
  expect(await screen.findByRole('switch', { name: 'compact.enableModel saved' })).toBeChecked();
});

it('ignores late model responses after credential changes and retains saved rows', async () => {
  let resolve!: (value: unknown) => void;
  api.discoverModels.mockReturnValue(new Promise(r => { resolve = r; }));
  const user = userEvent.setup();
  const { rerender } = render(<ModelDiscovery providerId="openai" mode="stage" credentials={{ api_key: 'old' }} existingModelIds={['saved']} />);
  await screen.findByRole('switch', { name: 'compact.enableModel saved' }); await user.click(fetchButton());
  rerender(<ModelDiscovery providerId="openai" mode="stage" credentials={{ api_key: 'new' }} existingModelIds={['saved']} />);
  await act(async () => { resolve(result('late')); });
  expect(screen.queryByRole('switch', { name: 'compact.enableModel late' })).not.toBeInTheDocument();
  expect(screen.getByRole('switch', { name: 'compact.enableModel saved' })).toBeInTheDocument();
});

it('merges a manually added model without discarding unsaved switches', async () => {
  const user = userEvent.setup(); const onInventoryChange = vi.fn();
  const { rerender } = render(<ModelDiscovery providerId="openai" mode="stage" existingModelIds={['saved']} onInventoryChange={onInventoryChange} />);
  await user.click(await screen.findByRole('switch', { name: 'compact.enableModel saved' }));
  api.getModelInventory.mockResolvedValue({ data: { models: [
    { id: 'saved', name: 'Saved', enabled: false },
    { id: 'manual', name: 'Manual', enabled: true },
  ] } });
  rerender(<ModelDiscovery providerId="openai" mode="stage" existingModelIds={['saved', 'manual']} onInventoryChange={onInventoryChange} />);
  expect(await screen.findByRole('switch', { name: 'compact.enableModel manual' })).toBeChecked();
  expect(screen.getByRole('switch', { name: 'compact.enableModel saved' })).toBeChecked();
  expect(onInventoryChange).toHaveBeenLastCalledWith({ models: [expect.objectContaining({ id: 'saved', enabled: true }), expect.objectContaining({ id: 'manual', enabled: true })], deleted: [] });
});

it('checks only the key without populating, saving or importing models and resets stale results', async () => {
  const user = userEvent.setup();
  const { rerender } = render(<ProviderKeyCheck providerId="openai" credentials={{ api_key: 'key-a' }} />);
  await user.click(screen.getByRole('button', { name: 'compact.check' }));
  expect(await screen.findByRole('status')).toHaveTextContent('compact.keyCodes.key_valid');
  expect(api.checkKey).toHaveBeenCalledWith('openai', { api_key: 'key-a' });
  expect(api.discoverModels).not.toHaveBeenCalled(); expect(api.saveModelInventory).not.toHaveBeenCalled();
  rerender(<ProviderKeyCheck providerId="openai" credentials={{ api_key: 'key-b' }} />);
  await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
});
