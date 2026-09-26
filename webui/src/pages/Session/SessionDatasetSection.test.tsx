import type { ReactNode } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createInstance } from 'i18next';
import { I18nextProvider } from 'react-i18next';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { knowledgebaseAPI } from '@/api/knowledgebase';
import { ToastProvider } from '@/components/common/Toast';
import session from '@/locales/en-US/session.json';
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
  await i18n.init({ lng: 'en-US', fallbackLng: 'en-US', resources: { 'en-US': { session, common } }, interpolation: { escapeValue: false } });
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
  beforeEach(() => { vi.resetAllMocks(); });

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
