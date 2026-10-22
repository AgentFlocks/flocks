import { act, render, screen, waitFor, within } from '@testing-library/react';
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
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  expect(mocks.discoverModels).toHaveBeenCalledWith('custom-demo', { api_key: 'draft-key', base_url: 'https://gateway/v1' });
  expect(await screen.findByText('discovery.catalogHint')).toBeInTheDocument();
  expect(screen.getByText('discovery.catalogOnly')).toBeInTheDocument();
  expect(screen.queryByText('discovery.listReachable')).not.toBeInTheDocument();
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
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
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
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  rerender(<ModelDiscovery providerId="openai" credentials={{ api_key: 'new' }} mode="stage" onSelectionChange={onSelectionChange} />);
  await act(async () => { resolveOld(result([model('stale')])); });
  expect(screen.queryByRole('checkbox', { name: 'stale' })).not.toBeInTheDocument();
  expect(onSelectionChange).toHaveBeenLastCalledWith([]);
  mocks.discoverModels.mockResolvedValueOnce(result([model('fresh')]));
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
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
  await user.dblClick(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  expect(mocks.discoverModels).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole('button', { name: 'discovery.close' }));
  await act(async () => { resolveRequest(result([model('late')])); });
  expect(screen.queryByRole('checkbox', { name: 'late' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'discovery.checkAndFetch' })).toBeEnabled();
});

it('keeps authentication errors distinct from empty success and allows retry', async () => {
  const user = userEvent.setup();
  mocks.discoverModels.mockResolvedValueOnce(result([], { source: 'none', status: 'error', code: 'authentication_failed', message: 'Invalid key' }));
  render(<ModelDiscovery providerId="openai" mode="import" />);
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  expect(await screen.findByText('discovery.codes.authentication_failed')).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('discovery.fetchFailedHeader');
  expect(screen.getByText('discovery.failed')).toBeInTheDocument();
  expect(screen.queryByText('discovery.catalogHint')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  expect(await screen.findByText('discovery.empty')).toBeInTheDocument();
  expect(screen.getByText('discovery.listReachable')).toBeInTheDocument();
  expect(screen.queryByText('status.connected')).not.toBeInTheDocument();
});

it.each(['import', 'stage'] as const)('checks every row in the all-added state in %s mode without staging duplicates', async mode => {
  const user = userEvent.setup();
  const onSelectionChange = vi.fn();
  mocks.discoverModels.mockResolvedValue(result([
    model('existing', { already_added: true }), model('existing-two', { already_added: true }),
  ]));
  render(<ModelDiscovery providerId="openai" mode={mode} onSelectionChange={onSelectionChange} />);
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
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
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
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
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
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
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
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
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
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
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
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
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
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
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  rerender(<ModelDiscovery providerId="openai" mode="import" existingModelIds={['arrived']} />);
  await act(async () => { resolveRequest(result([model('arrived')])); });
  expect(screen.getByRole('checkbox', { name: 'arrived' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'arrived' })).toBeDisabled();
  expect(screen.getByText('discovery.selected 1')).toBeInTheDocument();
  expect(screen.getByText('discovery.pending 0')).toBeInTheDocument();
});

it('allows checking with missing draft credentials and shows the backend reason', async () => {
  const user = userEvent.setup();
  mocks.discoverModels.mockResolvedValue(result([], { source: 'none', status: 'error', code: 'missing_credentials' }));
  render(<ModelDiscovery providerId="openai" mode="stage" credentials={{ api_key: '' }} />);
  const checkButton = screen.getByRole('button', { name: 'discovery.checkAndFetch' });
  expect(checkButton).toBeEnabled();
  await user.click(checkButton);
  expect(mocks.discoverModels).toHaveBeenCalledWith('openai', { api_key: '' });
  expect(await screen.findByRole('alert')).toHaveTextContent('discovery.codes.missing_credentials');
  expect(checkButton).toBeEnabled();
});

it('filters numeric minimums and distinguishes unsupported capabilities from unconfirmed ones', async () => {
  const user = userEvent.setup();
  mocks.discoverModels.mockResolvedValue(result([
    model('large', { context_window: 128000, max_output_tokens: 8192, supports_tools: true, supports_vision: false }),
    model('small', { context_window: 32000, max_output_tokens: 2048, supports_tools: false }),
    model('unknown'),
  ]));
  render(<ModelDiscovery providerId="openai" mode="stage" />);
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  const contextFilter = await screen.findByRole('combobox', { name: 'discovery.filter discovery.fields.context_window' });
  await user.selectOptions(contextFilter, 'minimum');
  await user.type(screen.getByRole('spinbutton', { name: 'discovery.minimumValue discovery.fields.context_window' }), '64000');
  expect(screen.getByRole('checkbox', { name: 'large' })).toBeInTheDocument();
  expect(screen.queryByRole('checkbox', { name: 'small' })).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox', { name: 'unknown' })).not.toBeInTheDocument();
  const outputFilter = screen.getByRole('combobox', { name: 'discovery.filter discovery.fields.max_output_tokens' });
  await user.selectOptions(outputFilter, 'minimum');
  await user.type(screen.getByRole('spinbutton', { name: 'discovery.minimumValue discovery.fields.max_output_tokens' }), '16384');
  expect(screen.getByText('discovery.noMatches')).toBeInTheDocument();
  await user.selectOptions(contextFilter, 'unknown');
  await user.selectOptions(outputFilter, 'unknown');
  expect(screen.getByRole('checkbox', { name: 'unknown' })).toBeInTheDocument();
  expect(screen.queryByRole('checkbox', { name: 'large' })).not.toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: 'discovery.resetFilters' }));
  const toolsFilter = screen.getByRole('combobox', { name: 'discovery.filter discovery.fields.supports_tools' });
  await user.selectOptions(toolsFilter, 'unsupported');
  expect(screen.getByRole('checkbox', { name: 'small' })).toBeInTheDocument();
  expect(screen.queryByRole('checkbox', { name: 'unknown' })).not.toBeInTheDocument();
  await user.selectOptions(toolsFilter, 'unknown');
  expect(screen.getByRole('checkbox', { name: 'unknown' })).toBeInTheDocument();
  expect(screen.queryByRole('checkbox', { name: 'small' })).not.toBeInTheDocument();
  await user.selectOptions(toolsFilter, 'supported');
  await user.selectOptions(screen.getByRole('combobox', { name: 'discovery.filter discovery.fields.supports_vision' }), 'unsupported');
  await user.selectOptions(screen.getByRole('combobox', { name: 'discovery.filter discovery.fields.supports_reasoning' }), 'unknown');
  expect(screen.getByRole('checkbox', { name: 'large' })).toBeInTheDocument();
  expect(screen.getByText('discovery.visibleCount 1')).toBeInTheDocument();
});

it.each(['stage', 'import'] as const)('applies explicit model configuration before %s while retaining unknown values and false capabilities', async mode => {
  const user = userEvent.setup();
  const onSelectionChange = vi.fn();
  const onReadyChange = vi.fn();
  mocks.discoverModels.mockResolvedValue(result([model('draft', {
    context_window: 131072, max_output_tokens: 8192, supports_tools: true, supports_reasoning: true,
  })]));
  mocks.importModels.mockResolvedValue({ data: { added: ['draft'], skipped: [], failed: [] } });
  render(<ModelDiscovery providerId="openai" mode={mode} onSelectionChange={onSelectionChange} onReadyChange={onReadyChange} />);
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  await user.click(await screen.findByRole('button', { name: 'discovery.configureModel draft' }));
  expect(screen.getByRole('checkbox', { name: 'draft' })).not.toBeChecked();
  expect(onReadyChange).toHaveBeenLastCalledWith(false);
  const editor = within(screen.getByRole('region', { name: 'discovery.configurationTitle' }));
  const context = editor.getByRole('spinbutton', { name: 'discovery.fields.context_window' });
  expect(context).toHaveFocus();
  await user.clear(context);
  await user.type(context, '64000');
  await user.clear(editor.getByRole('spinbutton', { name: 'discovery.fields.max_output_tokens' }));
  await user.selectOptions(editor.getByRole('combobox', { name: 'discovery.fields.supports_tools' }), 'false');
  await user.selectOptions(editor.getByRole('combobox', { name: 'discovery.fields.supports_vision' }), 'true');
  await user.selectOptions(editor.getByRole('combobox', { name: 'discovery.fields.supports_reasoning' }), 'unknown');
  await user.click(editor.getByRole('button', { name: 'discovery.applyConfiguration' }));
  expect(onReadyChange).toHaveBeenLastCalledWith(true);
  expect(screen.getByRole('checkbox', { name: 'draft' })).not.toBeChecked();
  await user.click(screen.getByRole('checkbox', { name: 'draft' }));
  const expected = expect.objectContaining({
    id: 'draft', context_window: 64000, max_output_tokens: undefined,
    supports_tools: false, supports_vision: true, supports_reasoning: undefined,
  });
  expect(onSelectionChange).toHaveBeenLastCalledWith([expected]);
  if (mode === 'import') {
    await user.click(screen.getByRole('button', { name: 'discovery.import 1' }));
    expect(mocks.importModels).toHaveBeenCalledWith('openai', [expected]);
    const payload = JSON.parse(JSON.stringify(mocks.importModels.mock.calls[0][1]))[0];
    expect(payload.supports_tools).toBe(false);
    expect(payload).not.toHaveProperty('max_output_tokens');
    expect(payload).not.toHaveProperty('supports_reasoning');
    expect(payload).not.toHaveProperty('already_added');
  } else {
    await user.click(screen.getByRole('button', { name: 'discovery.close' }));
    await user.click(screen.getByRole('button', { name: 'discovery.reviewSelection' }));
    await user.click(screen.getByRole('button', { name: 'discovery.configureModel draft' }));
    expect(screen.getByRole('spinbutton', { name: 'discovery.fields.context_window' })).toHaveValue(64000);
    expect(screen.getByRole('combobox', { name: 'discovery.fields.supports_tools' })).toHaveValue('false');
    await user.click(screen.getByRole('button', { name: 'discovery.cancelConfiguration' }));
    expect(onReadyChange).toHaveBeenLastCalledWith(true);
  }
});

it.each(['0', '-1', '1.5', '100000001'])('requires applying or cancelling an invalid limit %s before import', async invalid => {
  const user = userEvent.setup();
  const onReadyChange = vi.fn();
  const onSelectionChange = vi.fn();
  mocks.discoverModels.mockResolvedValue(result([model('draft')]));
  render(<ModelDiscovery providerId="openai" mode="import" onReadyChange={onReadyChange} onSelectionChange={onSelectionChange} />);
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  await user.click(await screen.findByRole('checkbox', { name: 'draft' }));
  await user.click(screen.getByRole('button', { name: 'discovery.configureModel draft' }));
  await user.type(screen.getByRole('spinbutton', { name: 'discovery.fields.context_window' }), invalid);
  expect(screen.getByRole('alert')).toHaveTextContent('discovery.invalidLimit');
  expect(screen.getByRole('button', { name: 'discovery.applyConfiguration' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'discovery.import 1' })).toBeDisabled();
  expect(onReadyChange).toHaveBeenLastCalledWith(false);
  expect(onSelectionChange).toHaveBeenLastCalledWith([model('draft')]);
  await user.click(screen.getByRole('button', { name: 'discovery.cancelConfiguration' }));
  expect(onReadyChange).toHaveBeenLastCalledWith(true);
  expect(screen.getByRole('button', { name: 'discovery.import 1' })).toBeEnabled();
  expect(mocks.importModels).not.toHaveBeenCalled();
});

it('batch-configures only pending models, preserves untouched fields, and can clear unknown limits explicitly', async () => {
  const user = userEvent.setup();
  mocks.discoverModels.mockResolvedValue(result([
    model('owned', { already_added: true, context_window: 999000 }),
    model('one', { context_window: 32000, max_output_tokens: 1024, supports_tools: true }),
    model('two', { context_window: 64000, max_output_tokens: 8192, supports_tools: false }),
  ]));
  render(<ModelDiscovery providerId="openai" mode="import" />);
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  await user.click(await screen.findByRole('button', { name: 'discovery.selectVisible' }));
  expect(screen.queryByRole('button', { name: 'discovery.configureModel owned' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'discovery.configureSelected 2' }));
  const editor = within(screen.getByRole('region', { name: 'discovery.configurationTitle' }));
  await user.selectOptions(editor.getByRole('combobox', { name: 'discovery.batchField discovery.fields.context_window' }), 'value');
  await user.type(editor.getByRole('spinbutton', { name: 'discovery.fields.context_window' }), '128000');
  await user.selectOptions(editor.getByRole('combobox', { name: 'discovery.batchField discovery.fields.max_output_tokens' }), 'unknown');
  await user.selectOptions(editor.getByRole('combobox', { name: 'discovery.fields.supports_vision' }), 'false');
  await user.click(editor.getByRole('button', { name: 'discovery.applyConfiguration' }));
  expect(screen.getByRole('checkbox', { name: 'owned' })).toBeChecked();
  await user.click(screen.getByRole('button', { name: 'discovery.import 2' }));
  expect(mocks.importModels).toHaveBeenCalledWith('openai', [
    expect.objectContaining({ id: 'one', context_window: 128000, max_output_tokens: undefined, supports_tools: true, supports_vision: false }),
    expect.objectContaining({ id: 'two', context_window: 128000, max_output_tokens: undefined, supports_tools: false, supports_vision: false }),
  ]);
});

it('clears every pending selection even when filters hide some selected models', async () => {
  const user = userEvent.setup();
  const onSelectionChange = vi.fn();
  mocks.discoverModels.mockResolvedValue(result([model('owned', { already_added: true }), model('alpha'), model('beta')]));
  render(<ModelDiscovery providerId="openai" mode="stage" onSelectionChange={onSelectionChange} />);
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  await user.click(await screen.findByRole('button', { name: 'discovery.selectVisible' }));
  await user.type(screen.getByRole('searchbox'), 'alpha');
  await user.click(screen.getByRole('button', { name: 'discovery.deselectVisible' }));
  await user.clear(screen.getByRole('searchbox'));
  expect(screen.getByRole('checkbox', { name: 'owned' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'alpha' })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'beta' })).not.toBeChecked();
  expect(onSelectionChange).toHaveBeenLastCalledWith([]);
});

it('requires cancelling unapplied drafts before refetch and resets readiness after credentials change', async () => {
  const user = userEvent.setup();
  const onReadyChange = vi.fn();
  mocks.discoverModels.mockResolvedValue(result([model('draft')]));
  const { rerender } = render(<ModelDiscovery providerId="openai" mode="stage" credentials={{ api_key: 'old' }} onReadyChange={onReadyChange} />);
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  await user.click(await screen.findByRole('button', { name: 'discovery.configureModel draft' }));
  await user.type(screen.getByRole('spinbutton', { name: 'discovery.fields.context_window' }), '0');
  expect(screen.getByRole('button', { name: 'discovery.checkAndFetch' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  expect(mocks.discoverModels).toHaveBeenCalledTimes(1);
  expect(onReadyChange).toHaveBeenLastCalledWith(false);
  await user.click(screen.getByRole('button', { name: 'discovery.cancelConfiguration' }));
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  expect(mocks.discoverModels).toHaveBeenCalledTimes(2);
  expect(onReadyChange).toHaveBeenLastCalledWith(true);
  expect(screen.queryByRole('region', { name: 'discovery.configurationTitle' })).not.toBeInTheDocument();
  await user.click(await screen.findByRole('button', { name: 'discovery.configureModel draft' }));
  expect(screen.getByRole('spinbutton', { name: 'discovery.fields.context_window' })).toHaveValue(null);
  rerender(<ModelDiscovery providerId="openai" mode="stage" credentials={{ api_key: 'new' }} onReadyChange={onReadyChange} />);
  expect(screen.queryByRole('region', { name: 'discovery.configurationTitle' })).not.toBeInTheDocument();
  expect(onReadyChange).toHaveBeenLastCalledWith(true);
});

it('reports not ready throughout discovery and import requests so parents cannot save an incomplete inventory', async () => {
  const user = userEvent.setup();
  const onReadyChange = vi.fn();
  let resolveDiscovery: (value: ReturnType<typeof result>) => void = () => undefined;
  let resolveImport: (value: { data: { added: string[]; skipped: string[]; failed: [] } }) => void = () => undefined;
  mocks.discoverModels.mockImplementationOnce(() => new Promise(resolve => { resolveDiscovery = resolve; }));
  mocks.importModels.mockImplementationOnce(() => new Promise(resolve => { resolveImport = resolve; }));
  render(<ModelDiscovery providerId="openai" mode="import" onReadyChange={onReadyChange} />);
  expect(onReadyChange).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByRole('button', { name: 'discovery.checkAndFetch' }));
  expect(onReadyChange).toHaveBeenLastCalledWith(false);
  await act(async () => { resolveDiscovery(result([model('draft')])); });
  expect(onReadyChange).toHaveBeenLastCalledWith(true);
  await user.click(screen.getByRole('checkbox', { name: 'draft' }));
  await user.click(screen.getByRole('button', { name: 'discovery.import 1' }));
  expect(onReadyChange).toHaveBeenLastCalledWith(false);
  await act(async () => { resolveImport({ data: { added: ['draft'], skipped: [], failed: [] } }); });
  expect(onReadyChange).toHaveBeenLastCalledWith(true);
  expect(screen.getByRole('checkbox', { name: 'draft' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'draft' })).toBeDisabled();
});
