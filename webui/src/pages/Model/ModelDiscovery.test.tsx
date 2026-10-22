import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import ModelDiscovery from './ModelDiscovery';
import type { DiscoveredModel, ModelDiscoveryResult } from '@/types';

const mocks = vi.hoisted(() => ({ discoverModels: vi.fn(), importModels: vi.fn() }));
vi.mock('@/api/provider', () => ({ providerAPI: mocks }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) => key + (values?.count !== undefined ? ` ${values.count}` : ''),
    i18n: { language: 'en-US' },
  }),
}));

function model(id: string, overrides: Partial<DiscoveredModel> = {}): DiscoveredModel {
  return { id, name: id, already_added: false, selectable: true, ...overrides };
}

function result(models: DiscoveredModel[], overrides: Partial<ModelDiscoveryResult> = {}) {
  return { data: { source: 'api', status: 'success', code: 'models_discovered', message: 'Ready', fetched_at: '2026-10-21T08:00:00Z', truncated: false, models, ...overrides } };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.discoverModels.mockResolvedValue(result([]));
  mocks.importModels.mockResolvedValue({ data: { added: [], skipped: [], failed: [] } });
});

it('fetches on demand with temporary credentials and stages only selectable new models', async () => {
  const user = userEvent.setup();
  const onSelectionChange = vi.fn();
  mocks.discoverModels.mockResolvedValue(result([
    model('owned', { already_added: true }), model('new-one'),
    model('embedding', { selectable: false, unavailable_reason: 'not_chat_model' }),
  ], { source: 'catalog', status: 'fallback', code: 'catalog_fallback' }));
  render(<ModelDiscovery providerId="custom-demo" credentials={{ api_key: 'draft-key', base_url: 'https://gateway/v1' }} mode="stage" onSelectionChange={onSelectionChange} />);
  expect(mocks.discoverModels).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'discovery.title' }));
  expect(mocks.discoverModels).toHaveBeenCalledWith('custom-demo', { api_key: 'draft-key', base_url: 'https://gateway/v1' });
  expect(await screen.findByText('discovery.catalogHint')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'owned' })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: 'owned' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'embedding' })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: 'embedding' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'new-one' })).not.toBeChecked();
  await user.click(screen.getByRole('button', { name: 'discovery.selectVisible' }));
  expect(onSelectionChange).toHaveBeenLastCalledWith([model('new-one')]);
  expect(mocks.importModels).not.toHaveBeenCalled();
  expect(screen.getByText('discovery.selected 2')).toBeInTheDocument();
  expect(screen.getByText('discovery.pending 1')).toBeInTheDocument();
});

it('selects only filtered results and reports partial imports without overwriting existing rows', async () => {
  const user = userEvent.setup();
  const onImported = vi.fn();
  mocks.discoverModels.mockResolvedValue(result([model('alpha-one', { context_window: 64000 }), model('alpha-two'), model('beta')]));
  mocks.importModels.mockResolvedValue({ data: { added: ['alpha-one'], skipped: [], failed: [{ id: 'alpha-two', error: 'Try again' }] } });
  render(<ModelDiscovery providerId="openai" mode="import" onImported={onImported} />);
  await user.click(screen.getByRole('button', { name: 'discovery.title' }));
  await user.type(await screen.findByRole('searchbox'), 'alpha');
  await user.click(screen.getByRole('button', { name: 'discovery.selectVisible' }));
  await user.click(screen.getByRole('button', { name: 'discovery.import 2' }));
  await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
  expect(mocks.importModels).toHaveBeenCalledWith('openai', [
    expect.objectContaining({ id: 'alpha-one', context_window: 64000 }), expect.objectContaining({ id: 'alpha-two' }),
  ]);
  const imported = mocks.importModels.mock.calls[0][1];
  expect(imported[0]).not.toHaveProperty('already_added');
  expect(imported[0]).not.toHaveProperty('selectable');
  expect(screen.getByRole('checkbox', { name: 'alpha-one' })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: 'alpha-one' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'alpha-two' })).toBeChecked();
  expect(screen.getByText('discovery.selected 2')).toBeInTheDocument();
  expect(screen.getByText('discovery.pending 1')).toBeInTheDocument();
  expect(screen.getByText(/Try again/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'discovery.import 1' })).toBeEnabled();
  await user.clear(screen.getByRole('searchbox'));
  expect(screen.getByRole('checkbox', { name: 'beta' })).not.toBeChecked();
});

it('invalidates selections and ignores a stale response after credentials change', async () => {
  const user = userEvent.setup();
  const onSelectionChange = vi.fn();
  let resolveOld: (value: ReturnType<typeof result>) => void = () => undefined;
  mocks.discoverModels.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
  const { rerender } = render(<ModelDiscovery providerId="openai" credentials={{ api_key: 'old' }} mode="stage" onSelectionChange={onSelectionChange} />);
  await user.click(screen.getByRole('button', { name: 'discovery.title' }));
  rerender(<ModelDiscovery providerId="openai" credentials={{ api_key: 'new' }} mode="stage" onSelectionChange={onSelectionChange} />);
  await act(async () => { resolveOld(result([model('stale')])); });
  expect(screen.queryByRole('checkbox', { name: 'stale' })).not.toBeInTheDocument();
  expect(onSelectionChange).toHaveBeenLastCalledWith([]);
  mocks.discoverModels.mockResolvedValueOnce(result([model('fresh')]));
  await user.click(screen.getByRole('button', { name: 'discovery.title' }));
  await user.click(await screen.findByRole('checkbox', { name: 'fresh' }));
  expect(onSelectionChange).toHaveBeenLastCalledWith([model('fresh')]);
  rerender(<ModelDiscovery providerId="other" credentials={{ api_key: 'new' }} mode="stage" onSelectionChange={onSelectionChange} />);
  expect(screen.queryByRole('checkbox', { name: 'fresh' })).not.toBeInTheDocument();
  expect(onSelectionChange).toHaveBeenLastCalledWith([]);
});

it('blocks repeated fetches and ignores a response after the results are closed', async () => {
  const user = userEvent.setup();
  let resolveRequest: (value: ReturnType<typeof result>) => void = () => undefined;
  mocks.discoverModels.mockImplementation(() => new Promise(resolve => { resolveRequest = resolve; }));
  render(<ModelDiscovery providerId="openai" mode="import" />);
  await user.dblClick(screen.getByRole('button', { name: 'discovery.title' }));
  expect(mocks.discoverModels).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole('button', { name: 'discovery.close' }));
  await act(async () => { resolveRequest(result([model('late')])); });
  expect(screen.queryByRole('checkbox', { name: 'late' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'discovery.title' })).toBeEnabled();
});

it('keeps authentication errors distinct from empty success and allows retry', async () => {
  const user = userEvent.setup();
  mocks.discoverModels.mockResolvedValueOnce(result([], { source: 'none', status: 'error', code: 'authentication_failed', message: 'Invalid key' }));
  render(<ModelDiscovery providerId="openai" mode="import" />);
  await user.click(screen.getByRole('button', { name: 'discovery.title' }));
  expect(await screen.findByText('discovery.codes.authentication_failed')).toBeInTheDocument();
  expect(screen.getByText('discovery.failed')).toBeInTheDocument();
  expect(screen.queryByText('discovery.catalogHint')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'discovery.refresh' }));
  expect(await screen.findByText('discovery.empty')).toBeInTheDocument();
});

it.each(['import', 'stage'] as const)('checks every row in the all-added state in %s mode without staging duplicates', async mode => {
  const user = userEvent.setup();
  const onSelectionChange = vi.fn();
  mocks.discoverModels.mockResolvedValue(result([
    model('existing', { already_added: true }), model('existing-two', { already_added: true }),
  ]));
  render(<ModelDiscovery providerId="openai" mode={mode} onSelectionChange={onSelectionChange} />);
  await user.click(screen.getByRole('button', { name: 'discovery.title' }));
  expect(await screen.findByText('discovery.allAdded')).toBeInTheDocument();
  screen.getAllByRole('checkbox').forEach(checkbox => {
    expect(checkbox).toBeChecked();
    expect(checkbox).toBeDisabled();
  });
  expect(screen.getByText('discovery.selected 2')).toBeInTheDocument();
  expect(screen.getByText('discovery.pending 0')).toBeInTheDocument();
  if (mode === 'import') expect(screen.getByRole('button', { name: 'discovery.import 0' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'discovery.selectVisible' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'discovery.invertVisible' })).toBeDisabled();
  expect(onSelectionChange).toHaveBeenLastCalledWith([]);
  expect(mocks.importModels).not.toHaveBeenCalled();
});

it('preserves a staged selection when results are folded and shows the pending count', async () => {
  const user = userEvent.setup();
  const onSelectionChange = vi.fn();
  mocks.discoverModels.mockResolvedValue(result([model('draft-model')]));
  render(<ModelDiscovery providerId="openai" mode="stage" onSelectionChange={onSelectionChange} />);
  await user.click(screen.getByRole('button', { name: 'discovery.title' }));
  await user.click(await screen.findByRole('checkbox', { name: 'draft-model' }));
  await user.click(screen.getByRole('button', { name: 'discovery.close' }));
  expect(onSelectionChange).toHaveBeenLastCalledWith([model('draft-model')]);
  expect(screen.getByText('discovery.pending 1')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'discovery.reviewSelection' }));
  expect(screen.getByRole('checkbox', { name: 'draft-model' })).toBeChecked();
  expect(mocks.discoverModels).toHaveBeenCalledTimes(1);
});

it('offers separate select-all, invert, and clear actions while keeping owned and unavailable rows unchanged', async () => {
  const user = userEvent.setup();
  const onSelectionChange = vi.fn();
  mocks.discoverModels.mockResolvedValue(result([
    model('owned', { already_added: true }), model('new-one'), model('new-two'),
    model('embedding', { selectable: false, unavailable_reason: 'not_chat_model' }),
  ]));
  render(<ModelDiscovery providerId="openai" mode="stage" onSelectionChange={onSelectionChange} />);
  await user.click(screen.getByRole('button', { name: 'discovery.title' }));
  expect(await screen.findByText('discovery.selected 1')).toBeInTheDocument();
  expect(screen.getByText('discovery.pending 0')).toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: 'discovery.selectVisible' }));
  expect(screen.getByText('discovery.selected 3')).toBeInTheDocument();
  expect(screen.getByText('discovery.pending 2')).toBeInTheDocument();
  // Select all stays a separate, idempotent action after every eligible row is selected.
  await user.click(screen.getByRole('button', { name: 'discovery.selectVisible' }));
  expect(screen.getByRole('checkbox', { name: 'new-one' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'new-two' })).toBeChecked();

  await user.click(screen.getByRole('button', { name: 'discovery.invertVisible' }));
  expect(screen.getByText('discovery.selected 1')).toBeInTheDocument();
  expect(screen.getByText('discovery.pending 0')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'owned' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'embedding' })).not.toBeChecked();
  expect(onSelectionChange).toHaveBeenLastCalledWith([]);

  await user.click(screen.getByRole('button', { name: 'discovery.invertVisible' }));
  await user.click(screen.getByRole('button', { name: 'discovery.deselectVisible' }));
  expect(screen.getByRole('checkbox', { name: 'owned' })).toBeChecked();
  expect(screen.getByText('discovery.pending 0')).toBeInTheDocument();
  expect(mocks.importModels).not.toHaveBeenCalled();
});

it('inverts only eligible filtered rows and preserves pending selections outside the filter', async () => {
  const user = userEvent.setup();
  const onSelectionChange = vi.fn();
  mocks.discoverModels.mockResolvedValue(result([
    model('alpha-owned', { already_added: true }), model('alpha-one'), model('alpha-two'), model('beta'),
    model('alpha-embedding', { selectable: false, unavailable_reason: 'not_chat_model' }),
  ]));
  render(<ModelDiscovery providerId="openai" mode="stage" onSelectionChange={onSelectionChange} />);
  await user.click(screen.getByRole('button', { name: 'discovery.title' }));
  await user.click(await screen.findByRole('checkbox', { name: 'alpha-one' }));
  await user.click(screen.getByRole('checkbox', { name: 'beta' }));
  await user.type(screen.getByRole('searchbox'), 'alpha');
  await user.click(screen.getByRole('button', { name: 'discovery.invertVisible' }));

  expect(screen.getByRole('checkbox', { name: 'alpha-owned' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'alpha-one' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'alpha-two' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'alpha-embedding' })).not.toBeChecked();
  expect(screen.getByText('discovery.selected 3')).toBeInTheDocument();
  expect(screen.getByText('discovery.pending 2')).toBeInTheDocument();
  expect(onSelectionChange).toHaveBeenLastCalledWith([model('alpha-two'), model('beta')]);

  await user.clear(screen.getByRole('searchbox'));
  expect(screen.getByRole('checkbox', { name: 'beta' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'alpha-one' })).not.toBeChecked();
});

it('checks added and skipped imports immediately, retains failed selections, and accepts later inventory changes', async () => {
  const user = userEvent.setup();
  const onSelectionChange = vi.fn();
  mocks.discoverModels.mockResolvedValue(result([
    model('owned', { already_added: true }), model('added'), model('skipped'), model('failed'),
  ]));
  mocks.importModels.mockResolvedValue({ data: {
    added: ['added'], skipped: ['skipped'], failed: [{ id: 'failed', error: 'Retry this model' }],
  } });
  const { rerender } = render(<ModelDiscovery providerId="openai" mode="import" existingModelIds={['owned']} onSelectionChange={onSelectionChange} />);
  await user.click(screen.getByRole('button', { name: 'discovery.title' }));
  await user.click(await screen.findByRole('button', { name: 'discovery.selectVisible' }));
  await user.click(screen.getByRole('button', { name: 'discovery.import 3' }));

  await waitFor(() => expect(screen.getByRole('checkbox', { name: 'added' })).toBeDisabled());
  for (const id of ['owned', 'added', 'skipped', 'failed']) expect(screen.getByRole('checkbox', { name: id })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'skipped' })).toBeDisabled();
  expect(screen.getByRole('checkbox', { name: 'failed' })).toBeEnabled();
  expect(screen.getByText('discovery.selected 4')).toBeInTheDocument();
  expect(screen.getByText('discovery.pending 1')).toBeInTheDocument();
  expect(onSelectionChange).toHaveBeenLastCalledWith([model('failed')]);
  expect(mocks.importModels).toHaveBeenCalledWith('openai', [
    expect.objectContaining({ id: 'added' }), expect.objectContaining({ id: 'skipped' }), expect.objectContaining({ id: 'failed' }),
  ]);

  // Allocating the same pre-refresh snapshot must not undo the import acknowledgement.
  rerender(<ModelDiscovery providerId="openai" mode="import" existingModelIds={['owned']} onSelectionChange={onSelectionChange} />);
  expect(screen.getByRole('checkbox', { name: 'added' })).toBeChecked();
  rerender(<ModelDiscovery providerId="openai" mode="import" existingModelIds={['owned', 'added', 'skipped']} onSelectionChange={onSelectionChange} />);
  rerender(<ModelDiscovery providerId="openai" mode="import" existingModelIds={['owned', 'skipped']} onSelectionChange={onSelectionChange} />);
  expect(screen.getByRole('checkbox', { name: 'added' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'added' })).toBeEnabled();
  expect(screen.getByRole('checkbox', { name: 'skipped' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'failed' })).toBeChecked();
  expect(screen.getByText('discovery.selected 3')).toBeInTheDocument();
  expect(screen.getByText('discovery.pending 1')).toBeInTheDocument();
  expect(mocks.discoverModels).toHaveBeenCalledTimes(1);
});

it('uses the latest inventory instead of stale discovery flags and forgets pending selections that become owned', async () => {
  const user = userEvent.setup();
  const onSelectionChange = vi.fn();
  mocks.discoverModels.mockResolvedValue(result([
    model('stale-owned', { already_added: true }), model('fresh-owned'), model('candidate'),
  ]));
  const { rerender } = render(<ModelDiscovery providerId="openai" mode="stage" existingModelIds={['fresh-owned']} onSelectionChange={onSelectionChange} />);
  await user.click(screen.getByRole('button', { name: 'discovery.title' }));
  expect(await screen.findByRole('checkbox', { name: 'stale-owned' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'stale-owned' })).toBeEnabled();
  expect(screen.getByRole('checkbox', { name: 'fresh-owned' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'fresh-owned' })).toBeDisabled();
  await user.click(screen.getByRole('checkbox', { name: 'candidate' }));
  expect(screen.getByText('discovery.selected 2')).toBeInTheDocument();
  expect(screen.getByText('discovery.pending 1')).toBeInTheDocument();

  rerender(<ModelDiscovery providerId="openai" mode="stage" existingModelIds={['candidate', 'fresh-owned']} onSelectionChange={onSelectionChange} />);
  expect(screen.getByRole('checkbox', { name: 'candidate' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'candidate' })).toBeDisabled();
  expect(screen.getByText('discovery.pending 0')).toBeInTheDocument();
  expect(onSelectionChange).toHaveBeenLastCalledWith([]);
  rerender(<ModelDiscovery providerId="openai" mode="stage" existingModelIds={[]} onSelectionChange={onSelectionChange} />);
  screen.getAllByRole('checkbox').forEach(checkbox => {
    expect(checkbox).not.toBeChecked();
    expect(checkbox).toBeEnabled();
  });
  expect(screen.getByText('discovery.selected 0')).toBeInTheDocument();
  expect(screen.getByText('discovery.pending 0')).toBeInTheDocument();
  expect(mocks.discoverModels).toHaveBeenCalledTimes(1);
});

it('treats a new instance as a separate inventory even when the catalog type already owns its models', async () => {
  const user = userEvent.setup();
  const onSelectionChange = vi.fn();
  mocks.discoverModels.mockResolvedValue(result([model('shared-model', { already_added: true })]));
  render(<ModelDiscovery providerId="openai-compatible" mode="stage" newInstance existingModelIds={['shared-model']} onSelectionChange={onSelectionChange} />);
  await user.click(screen.getByRole('button', { name: 'discovery.title' }));
  const checkbox = await screen.findByRole('checkbox', { name: 'shared-model' });
  expect(checkbox).not.toBeChecked();
  expect(checkbox).toBeEnabled();
  expect(screen.getByText('discovery.selected 0')).toBeInTheDocument();
  await user.click(checkbox);
  expect(onSelectionChange).toHaveBeenLastCalledWith([model('shared-model')]);
});

it('uses an inventory update that arrives while discovery is still in flight', async () => {
  const user = userEvent.setup();
  let resolveRequest: (value: ReturnType<typeof result>) => void = () => undefined;
  mocks.discoverModels.mockImplementationOnce(() => new Promise(resolve => { resolveRequest = resolve; }));
  const { rerender } = render(<ModelDiscovery providerId="openai" mode="import" existingModelIds={[]} />);
  await user.click(screen.getByRole('button', { name: 'discovery.title' }));
  rerender(<ModelDiscovery providerId="openai" mode="import" existingModelIds={['arrived']} />);
  await act(async () => { resolveRequest(result([model('arrived')])); });
  expect(screen.getByRole('checkbox', { name: 'arrived' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'arrived' })).toBeDisabled();
  expect(screen.getByText('discovery.selected 1')).toBeInTheDocument();
  expect(screen.getByText('discovery.pending 0')).toBeInTheDocument();
});
