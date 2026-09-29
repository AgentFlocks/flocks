import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { knowledgebaseAPI, type Dataset, type KnowledgeDocument, type Page } from '@/api/knowledgebase';
import { DOCUMENT_REFRESH_INTERVAL_MS, DOCUMENT_REFRESH_LIMIT_MS, useDatasetDetail } from './useDatasetDetail';

vi.mock('@/api/knowledgebase', () => ({ knowledgebaseAPI: {
  dataset: vi.fn(), documents: vi.fn(), parse: vi.fn(), linkFiles: vi.fn(),
} }));
const api = vi.mocked(knowledgebaseAPI);
const dataset: Dataset = { id: 'set-1', name: 'Notes', description: '', document_count: 0, chunk_count: 0 };
const document: KnowledgeDocument = { id: 'doc-1', dataset_id: dataset.id, name: 'example.txt', status: 'unparsed', progress: 0, chunk_count: 0 };
const pageOf = (items: KnowledgeDocument[]): Page<KnowledgeDocument> => ({ items, total: items.length, page: 1 });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
const settle = () => act(async () => { await Promise.resolve(); });
const tick = (duration = DOCUMENT_REFRESH_INTERVAL_MS) => act(async () => { await vi.advanceTimersByTimeAsync(duration); });

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  api.dataset.mockResolvedValue(dataset);
  api.documents.mockResolvedValue(pageOf([]));
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('bounded read-only dataset document refresh', () => {
  it('observes documents after an initially empty detail without repeating metadata or writes on every tick', async () => {
    const { result } = renderHook(() => useDatasetDetail(dataset.id, '', 1, 0));
    await settle();
    expect(result.current.data?.documents.items).toEqual([]);
    api.documents.mockResolvedValue(pageOf([document]));
    await tick();
    expect(result.current.data?.documents.items).toEqual([document]);
    expect(result.current.loading).toBe(false);
    expect(api.dataset).toHaveBeenCalledTimes(1);
    expect(api.documents).toHaveBeenCalledTimes(2);
    expect(api.parse).not.toHaveBeenCalled();
    expect(api.linkFiles).not.toHaveBeenCalled();
  });

  it('bounds empty-list polling, refreshes final metadata, and lets a manual refresh restart observation', async () => {
    const { result } = renderHook(() => useDatasetDetail(dataset.id, '', 1, 0));
    await settle();
    await tick(DOCUMENT_REFRESH_LIMIT_MS);
    expect(result.current.refreshStatus).toBe('expired');
    expect(result.current.data?.documents.items).toEqual([]);
    expect(api.documents).toHaveBeenCalledTimes(Math.ceil(DOCUMENT_REFRESH_LIMIT_MS / DOCUMENT_REFRESH_INTERVAL_MS));
    expect(api.dataset).toHaveBeenCalledTimes(2);
    const calls = api.documents.mock.calls.length;
    await tick(DOCUMENT_REFRESH_LIMIT_MS);
    expect(api.documents).toHaveBeenCalledTimes(calls);
    api.dataset.mockResolvedValue({ ...dataset, name: 'Refreshed', document_count: 1 });
    api.documents.mockResolvedValue(pageOf([document]));
    act(() => result.current.reload());
    await settle();
    expect(result.current.data?.dataset.name).toBe('Refreshed');
    expect(result.current.data?.documents.items).toEqual([document]);
    expect(result.current.refreshStatus).toBe('refreshing');
    expect(api.parse).not.toHaveBeenCalled();
    expect(api.linkFiles).not.toHaveBeenCalled();
  });

  it('retains data during an external revision and reloads both metadata and documents', async () => {
    const { result, rerender } = renderHook(({ revision }) => useDatasetDetail(dataset.id, '', 1, revision), { initialProps: { revision: 0 } });
    await settle();
    const previous = result.current.data;
    const updated = deferred<Dataset>();
    api.dataset.mockImplementation(() => updated.promise);
    api.documents.mockResolvedValue(pageOf([document]));
    rerender({ revision: 1 });
    expect(result.current.data).toBe(previous);
    expect(result.current.loading).toBe(false);
    await act(async () => { updated.resolve({ ...dataset, name: 'Edited title', description: 'Edited description', document_count: 1 }); });
    expect(result.current.data?.dataset).toMatchObject({ name: 'Edited title', description: 'Edited description', document_count: 1 });
    expect(result.current.data?.documents.items).toEqual([document]);
    expect(api.dataset).toHaveBeenCalledTimes(2);
    expect(api.documents).toHaveBeenCalledTimes(2);
  });

  it('stops on refresh errors and keeps previously loaded documents', async () => {
    api.documents.mockResolvedValue(pageOf([document]));
    const { result } = renderHook(() => useDatasetDetail(dataset.id, '', 1, 0));
    await settle();
    const previous = result.current.data;
    api.documents.mockRejectedValue(new Error('Read failed'));
    await tick();
    expect(result.current.error).toBe('Read failed');
    expect(result.current.refreshStatus).toBe('error');
    expect(result.current.data).toBe(previous);
    expect(api.documents.mock.calls[1][2]?.aborted).toBe(true);
    await tick(DOCUMENT_REFRESH_LIMIT_MS);
    expect(api.documents).toHaveBeenCalledTimes(2);
  });

  it('cancels an in-flight read and ignores its late result', async () => {
    const { result } = renderHook(() => useDatasetDetail(dataset.id, '', 1, 0));
    await settle();
    const delayed = deferred<Page<KnowledgeDocument>>();
    api.documents.mockImplementation(() => delayed.promise);
    await tick();
    const signal = api.documents.mock.calls[1][2]!;
    expect(signal.aborted).toBe(false);
    act(() => result.current.cancel());
    expect(signal.aborted).toBe(true);
    expect(result.current.refreshStatus).toBe('stopped');
    await act(async () => { delayed.resolve(pageOf([document])); });
    expect(result.current.data?.documents.items).toEqual([]);
    await tick(DOCUMENT_REFRESH_LIMIT_MS);
    expect(api.documents).toHaveBeenCalledTimes(2);
  });

  it('aborts a stalled initial request at the deadline without accepting a late response', async () => {
    const delayed = deferred<Page<KnowledgeDocument>>();
    api.documents.mockImplementation(() => delayed.promise);
    const { result } = renderHook(() => useDatasetDetail(dataset.id, '', 1, 0));
    await tick(DOCUMENT_REFRESH_LIMIT_MS);
    expect(api.documents.mock.calls[0][2]?.aborted).toBe(true);
    expect(result.current.refreshStatus).toBe('expired');
    expect(result.current.loading).toBe(false);
    await act(async () => { delayed.resolve(pageOf([document])); });
    expect(result.current.data).toBeUndefined();
    expect(api.documents).toHaveBeenCalledTimes(1);
  });

  it('aborts on dataset changes and unmount, without leaking a previous target response', async () => {
    const delayed = deferred<Page<KnowledgeDocument>>();
    api.documents.mockImplementationOnce(() => delayed.promise).mockResolvedValue(pageOf([]));
    const { result, rerender, unmount } = renderHook(({ id }) => useDatasetDetail(id, '', 1, 0), { initialProps: { id: dataset.id } });
    const firstSignal = api.documents.mock.calls[0][2]!;
    const other = { ...dataset, id: 'set-2', name: 'Other notes' };
    api.dataset.mockResolvedValue(other);
    rerender({ id: other.id });
    expect(firstSignal.aborted).toBe(true);
    await settle();
    await act(async () => { delayed.resolve(pageOf([document])); });
    expect(result.current.data?.dataset.id).toBe(other.id);
    expect(result.current.data?.documents.items).toEqual([]);
    const currentSignal = api.documents.mock.calls[1][2]!;
    unmount();
    expect(currentSignal.aborted).toBe(true);
    await tick(DOCUMENT_REFRESH_LIMIT_MS);
    expect(api.documents).toHaveBeenCalledTimes(2);
  });
});
