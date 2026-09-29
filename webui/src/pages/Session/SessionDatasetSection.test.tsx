import type { ReactNode } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createInstance } from 'i18next';
import { I18nextProvider } from 'react-i18next';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { knowledgebaseAPI } from '@/api/knowledgebase';
import { ToastProvider } from '@/components/common/Toast';
import session from '@/locales/en-US/session.json';
import sessionZh from '@/locales/zh-CN/session.json';
import common from '@/locales/en-US/common.json';
import SessionDatasetSection from './SessionDatasetSection';

vi.mock('@/api/knowledgebase', async importOriginal => {
  const actual = await importOriginal<typeof import('@/api/knowledgebase')>();
  return {
    ...actual,
    knowledgebaseAPI: {
      status: vi.fn(),
      sessionDatasets: vi.fn(),
      datasets: vi.fn(),
      setSessionDatasets: vi.fn(),
    },
  };
});

const api = vi.mocked(knowledgebaseAPI);
const i18n = createInstance();
beforeAll(async () => {
  await i18n.init({ lng: 'en-US', fallbackLng: 'en-US', resources: { 'en-US': { session, common }, 'zh-CN': { session: sessionZh, common } }, interpolation: { escapeValue: false } });
});

function Providers({ children }: { children: ReactNode }) {
  return <I18nextProvider i18n={i18n}><ToastProvider>{children}</ToastProvider></I18nextProvider>;
}

function dataset(id: string, name = id) {
  return { id, name, description: '', document_count: 0, chunk_count: 0 };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

describe('SessionDatasetSection', () => {
  beforeEach(async () => { vi.resetAllMocks(); await i18n.changeLanguage('en-US'); });

  it('keeps only the small scope description below the matching section heading', async () => {
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: [] });
    api.datasets.mockResolvedValue({ items: [], total: 0, page: 1 });
    const { container } = render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    await screen.findByRole('button', { name: session.dataset.select });
    const heading = screen.getByRole('heading', { name: session.dataset.title });
    const hint = screen.getByText(session.dataset.scope);
    expect(heading).toHaveClass('text-xs', 'font-semibold', 'text-zinc-700');
    expect(hint).toHaveClass('session-knowledge-hint');
    expect(heading.parentElement!.nextElementSibling).toBe(hint);
    expect(screen.queryByText(session.dataset.scopeTitle)).not.toBeInTheDocument();
    expect(container.querySelector('.session-knowledge-scope')).not.toBeInTheDocument();
  });

  it.each([
    ['en-US', session],
    ['zh-CN', sessionZh],
  ] as const)('marks saved bindings, but not unapplied drafts, as added in %s', async (language, messages) => {
    await i18n.changeLanguage(language);
    const user = userEvent.setup();
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: [], datasets: [] });
    api.datasets.mockResolvedValue({ items: [dataset('alpha', 'Alpha')], total: 1, page: 1 });
    api.setSessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: ['alpha'] });
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: messages.dataset.select }));
    await user.click(screen.getByRole('checkbox', { name: 'Alpha' }));
    expect(screen.queryByText(messages.dataset.addedToSession)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: messages.dataset.cancel }));
    expect(screen.queryByText(messages.dataset.addedToSession)).not.toBeInTheDocument();
    expect(api.setSessionDatasets).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: messages.dataset.select }));
    await user.click(screen.getByRole('checkbox', { name: 'Alpha' }));
    await user.click(screen.getByRole('button', { name: messages.dataset.apply }));
    expect(await screen.findByText(messages.dataset.addedToSession)).toBeInTheDocument();
  });

  it('marks only bound IDs as added even when inline metadata includes other sets', async () => {
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockResolvedValue({
      session_id: 'sess-1', dataset_ids: ['alpha', 'missing'], datasets: [dataset('alpha', 'Alpha'), dataset('extra', 'Not bound')],
    });
    api.datasets.mockResolvedValue({ items: [], total: 0, page: 1 });
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    expect(await screen.findByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('missing')).toBeInTheDocument();
    expect(screen.getAllByText(session.dataset.addedToSession)).toHaveLength(2);
    expect(screen.queryByText('Not bound')).not.toBeInTheDocument();
  });

  it('ignores a saved response from the previously active session', async () => {
    const user = userEvent.setup();
    const saved = deferred<{ session_id: string; dataset_ids: string[] }>();
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockImplementation(async id => ({ session_id: id, dataset_ids: id === 'sess-b' ? ['beta'] : [] }));
    api.datasets.mockResolvedValue({ items: [dataset('alpha', 'Alpha'), dataset('beta', 'Beta')], total: 2, page: 1 });
    api.setSessionDatasets.mockReturnValue(saved.promise);
    const view = render(<SessionDatasetSection sessionId="sess-a" />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: session.dataset.select }));
    await user.click(screen.getByRole('checkbox', { name: 'Alpha' }));
    await user.click(screen.getByRole('button', { name: session.dataset.apply }));
    view.rerender(<SessionDatasetSection sessionId="sess-b" />);
    expect(await screen.findByText('Beta')).toBeInTheDocument();
    await act(async () => saved.resolve({ session_id: 'sess-a', dataset_ids: ['alpha'] }));
    expect(screen.queryByText('Alpha')).not.toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
    expect(screen.getAllByText(session.dataset.addedToSession)).toHaveLength(1);
  });

  it('does not let a pending refresh replace a newer successfully saved selection', async () => {
    const user = userEvent.setup();
    const alpha = dataset('alpha', 'Alpha');
    const beta = dataset('beta', 'Beta');
    const oldCatalog = deferred<{ items: ReturnType<typeof dataset>[]; total: number; page: number }>();
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: ['alpha'] });
    api.datasets.mockResolvedValue({ items: [alpha, beta], total: 2, page: 1 });
    api.setSessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: ['beta'] });
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    await screen.findByText('Alpha');
    api.datasets.mockReturnValueOnce(oldCatalog.promise);
    await user.click(screen.getByRole('button', { name: session.dataset.refresh }));
    await waitFor(() => expect(api.datasets).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole('button', { name: session.dataset.select }));
    await user.click(screen.getByRole('checkbox', { name: 'Alpha' }));
    await user.click(screen.getByRole('checkbox', { name: 'Beta' }));
    await user.click(screen.getByRole('button', { name: session.dataset.apply }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.queryByText(session.dataset.loading)).not.toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
    await act(async () => oldCatalog.resolve({ items: [alpha, beta], total: 2, page: 1 }));
    expect(screen.queryByText('Alpha')).not.toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: session.dataset.select }));
    expect(screen.getByRole('checkbox', { name: 'Beta' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Alpha' })).not.toBeChecked();
    expect(api.setSessionDatasets).toHaveBeenCalledExactlyOnceWith('sess-1', ['beta']);
  });

  it('renders the reference card metadata and stages removal until Apply', async () => {
    const user = userEvent.setup();
    api.status.mockResolvedValue({ configured: true, ready: true });
    const alpha = { ...dataset('alpha', 'Alpha'), document_count: 3 };
    api.sessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: ['alpha'], datasets: [alpha] });
    api.datasets.mockResolvedValue({ items: [alpha], total: 1, page: 1 });
    api.setSessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: [] });
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    expect(await screen.findByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('3 files')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: session.dataset.manage })).toHaveAttribute('href', '/workspace?tab=knowledge');
    await user.click(screen.getByRole('button', { name: 'Remove Knowledge Set Alpha' }));
    expect(screen.getByRole('dialog', { name: session.dataset.picker })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Alpha' })).not.toBeChecked();
    expect(api.setSessionDatasets).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: session.dataset.cancel }));
    expect(screen.getByText(session.dataset.addedToSession)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Remove Knowledge Set Alpha' }));
    await user.click(screen.getByRole('button', { name: session.dataset.apply }));
    await waitFor(() => expect(api.setSessionDatasets).toHaveBeenCalledWith('sess-1', []));
    expect(await screen.findByText(session.dataset.empty)).toBeInTheDocument();
  });

  it('does not replace unknown file metadata with a zero count', async () => {
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: ['missing'], datasets: [] });
    api.datasets.mockResolvedValue({ items: [], total: 0, page: 1 });
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    expect(await screen.findByText(session.dataset.fileCountUnknown)).toBeInTheDocument();
    expect(screen.queryByText('0 files')).not.toBeInTheDocument();
  });

  it('keeps the picker open and prevents repeated Apply while saving', async () => {
    const user = userEvent.setup();
    const saved = deferred<{ session_id: string; dataset_ids: string[] }>();
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: [] });
    api.datasets.mockResolvedValue({ items: [dataset('alpha', 'Alpha')], total: 1, page: 1 });
    api.setSessionDatasets.mockReturnValue(saved.promise);
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: session.dataset.select }));
    await user.click(screen.getByRole('checkbox', { name: 'Alpha' }));
    await user.click(screen.getByRole('button', { name: session.dataset.apply }));
    expect(screen.getByRole('button', { name: session.dataset.saving })).toBeDisabled();
    expect(screen.getByRole('button', { name: session.dataset.cancel })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Alpha' })).toBeDisabled();
    await act(async () => saved.resolve({ session_id: 'sess-1', dataset_ids: ['alpha'] }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.setSessionDatasets).toHaveBeenCalledTimes(1);
  });

  it('keeps a load failure inside the section', async () => {
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockRejectedValue(new Error('Dataset GET failed'));
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    expect(await screen.findByRole('alert')).toHaveTextContent('Dataset GET failed');
    expect(screen.getByRole('region', { name: session.dataset.title })).toBeInTheDocument();
  });

  it('saves the current session selection', async () => {
    const user = userEvent.setup();
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: [] });
    api.datasets.mockResolvedValue({ items: [{ id: 'alpha', name: 'Alpha', description: '', document_count: 0, chunk_count: 0 }], total: 1, page: 1 });
    api.setSessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: ['alpha'] });
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: session.dataset.select }));
    await user.click(screen.getByRole('checkbox', { name: 'Alpha' }));
    await user.click(screen.getByRole('button', { name: session.dataset.apply }));
    await waitFor(() => expect(api.setSessionDatasets).toHaveBeenCalledWith('sess-1', ['alpha']));
  });

  it('keeps the current session when an older load returns later', async () => {
    const pending: Array<(value: { session_id: string; dataset_ids: string[]; datasets: { id: string; name: string; description: string; document_count: number; chunk_count: number }[] }) => void> = [];
    const beta = { id: 'beta', name: 'Beta', description: '', document_count: 0, chunk_count: 0 };
    api.datasets.mockResolvedValue({ items: [beta], total: 1, page: 1 });
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockImplementation(id => {
      if (id === 'sess-a') return new Promise(resolve => { pending.push(resolve); });
      return Promise.resolve({ session_id: 'sess-b', dataset_ids: ['beta'], datasets: [beta] });
    });
    const view = render(<SessionDatasetSection sessionId="sess-a" />, { wrapper: Providers });
    view.rerender(<SessionDatasetSection sessionId="sess-b" />);
    expect(await screen.findByText('Beta')).toBeInTheDocument();
    pending.forEach(resolve => resolve({ session_id: 'sess-a', dataset_ids: ['alpha'], datasets: [{ id: 'alpha', name: 'Alpha', description: '', document_count: 0, chunk_count: 0 }] }));
    await waitFor(() => expect(screen.queryByText('Alpha')).not.toBeInTheDocument());
    expect(screen.getByText('Beta')).toBeInTheDocument();
  });

  it.each([101, 201])('loads all %i datasets and can select the last item', async total => {
    const user = userEvent.setup();
    const items = Array.from({ length: total }, (_, i) => dataset(`d${i + 1}`, `Dataset ${i + 1}`));
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: [], datasets: [] });
    api.datasets.mockImplementation(async ({ page = 1, page_size = 100 } = {}) => ({
      items: items.slice((page - 1) * page_size, page * page_size), total, page,
    }));
    api.setSessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: [`d${total}`] });
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: session.dataset.select }));
    expect(screen.getAllByRole('checkbox')).toHaveLength(total);
    await user.click(screen.getByRole('checkbox', { name: `Dataset ${total}` }));
    await user.click(screen.getByRole('button', { name: session.dataset.apply }));
    await waitFor(() => expect(api.setSessionDatasets).toHaveBeenCalledWith('sess-1', [`d${total}`]));
    expect(api.datasets.mock.calls.map(([options]) => options)).toEqual(
      Array.from({ length: Math.ceil(total / 100) }, (_, i) => ({ page: i + 1, page_size: 100 })),
    );
  });

  it.each([false, true])('allows removing a catalog-missing binding (metadata=%s)', async withMetadata => {
    const user = userEvent.setup();
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockResolvedValue({
      session_id: 'sess-1', dataset_ids: ['missing'],
      ...(withMetadata ? { datasets: [dataset('missing', 'Missing dataset')] } : {}),
    });
    api.datasets.mockResolvedValue({ items: [dataset('other', 'Other')], total: 1, page: 1 });
    api.setSessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: [] });
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: session.dataset.select }));
    const name = withMetadata ? 'Missing dataset' : 'missing';
    expect(screen.getByRole('checkbox', { name })).toBeChecked();
    await user.click(screen.getByRole('checkbox', { name }));
    expect(screen.getByRole('checkbox', { name })).not.toBeChecked();
    await user.click(screen.getByRole('checkbox', { name }));
    expect(screen.getByRole('checkbox', { name })).toBeChecked();
    await user.click(screen.getByRole('checkbox', { name }));
    await user.click(screen.getByRole('button', { name: session.dataset.apply }));
    await waitFor(() => expect(api.setSessionDatasets).toHaveBeenCalledWith('sess-1', []));
    expect(api.datasets).toHaveBeenCalledWith({ page: 1, page_size: 100 });
  });

  it('keeps bindings selectable even when inline metadata is incomplete', async () => {
    const user = userEvent.setup();
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockResolvedValue({
      session_id: 'sess-1', dataset_ids: ['named', 'unnamed'], datasets: [dataset('named', 'Named dataset')],
    });
    api.datasets.mockResolvedValue({ items: [], total: 0, page: 1 });
    api.setSessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: ['named'] });
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: session.dataset.select }));
    expect(screen.getByRole('checkbox', { name: 'Named dataset' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'unnamed' })).toBeChecked();
    await user.click(screen.getByRole('checkbox', { name: 'unnamed' }));
    await user.click(screen.getByRole('button', { name: session.dataset.apply }));
    await waitFor(() => expect(api.setSessionDatasets).toHaveBeenCalledWith('sess-1', ['named']));
  });

  it('deduplicates overlapping pages and stops on an empty page', async () => {
    const user = userEvent.setup();
    const first = Array.from({ length: 100 }, (_, i) => dataset(`d${i + 1}`));
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: [] });
    api.datasets
      .mockResolvedValueOnce({ items: first, total: 400, page: 1 })
      .mockResolvedValueOnce({ items: [dataset('d1'), dataset('d101')], total: 400, page: 2 })
      .mockResolvedValueOnce({ items: [], total: 400, page: 3 });
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: session.dataset.select }));
    expect(screen.getAllByRole('checkbox')).toHaveLength(101);
    expect(screen.getAllByRole('checkbox', { name: 'd1' })).toHaveLength(1);
    expect(api.datasets).toHaveBeenCalledTimes(3);
  });

  it('reports a later-page failure and retries the complete catalog', async () => {
    const user = userEvent.setup();
    const first = Array.from({ length: 100 }, (_, i) => dataset(`d${i + 1}`));
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: [] });
    api.datasets
      .mockResolvedValueOnce({ items: first, total: 101, page: 1 })
      .mockRejectedValueOnce(new Error('Second page failed'))
      .mockResolvedValueOnce({ items: first, total: 101, page: 1 })
      .mockResolvedValueOnce({ items: [dataset('d101')], total: 101, page: 2 });
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    expect(await screen.findByRole('alert')).toHaveTextContent('Second page failed');
    expect(screen.queryByRole('button', { name: session.dataset.select })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: session.dataset.retry }));
    await user.click(await screen.findByRole('button', { name: session.dataset.select }));
    expect(screen.getByRole('checkbox', { name: 'd101' })).toBeInTheDocument();
    expect(api.datasets.mock.calls.map(([options]) => options?.page)).toEqual([1, 2, 1, 2]);
  });

  it('stops an obsolete paginated load after the session changes', async () => {
    const oldPage = deferred<{ items: ReturnType<typeof dataset>[]; total: number; page: number }>();
    const first = Array.from({ length: 100 }, (_, i) => dataset(`old-${i}`));
    const beta = dataset('beta', 'Beta');
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockImplementation(async id => ({
      session_id: id, dataset_ids: id === 'sess-b' ? ['beta'] : [],
    }));
    api.datasets
      .mockResolvedValueOnce({ items: first, total: 201, page: 1 })
      .mockImplementationOnce(() => oldPage.promise)
      .mockResolvedValue({ items: [beta], total: 1, page: 1 });
    const view = render(<SessionDatasetSection sessionId="sess-a" />, { wrapper: Providers });
    await waitFor(() => expect(api.datasets).toHaveBeenCalledTimes(2));
    view.rerender(<SessionDatasetSection sessionId="sess-b" />);
    expect(await screen.findByText('Beta')).toBeInTheDocument();
    await act(async () => oldPage.resolve({ items: [dataset('stale', 'Stale')], total: 201, page: 2 }));
    expect(api.datasets).toHaveBeenCalledTimes(3);
    expect(screen.queryByText('Stale')).not.toBeInTheDocument();
    expect(screen.getByText('Beta')).toBeInTheDocument();
  });
});
