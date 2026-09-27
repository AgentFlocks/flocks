import { beforeEach, describe, expect, it, vi } from 'vitest';
import { knowledgebaseAPI, KnowledgebaseError } from './knowledgebase';

const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() }));
vi.mock('./client', () => ({ default: client, getApiBase: () => 'https://fixture.invalid' }));

beforeEach(() => {
  vi.resetAllMocks();
});

describe('knowledgebaseAPI', () => {
  it('reads status and treats a missing route as not configured', async () => {
    client.get.mockResolvedValueOnce({ data: { data: { configured: true, ready: true } } });
    await expect(knowledgebaseAPI.status()).resolves.toEqual({ configured: true, ready: true });
    client.get.mockRejectedValueOnce({ response: { status: 404 } });
    await expect(knowledgebaseAPI.status()).resolves.toEqual({ configured: false, ready: false });
  });

  it('loads only non-secret connection metadata and saves through one PUT', async () => {
    const settings = { provider: 'ragflow' as const, base_url: 'https://ragflow.example', has_api_key: true };
    client.get.mockResolvedValueOnce({ data: { data: settings } });
    await expect(knowledgebaseAPI.connection()).resolves.toEqual(settings);
    expect(client.get).toHaveBeenCalledWith('/api/knowledgebase/connection', { signal: undefined });

    const saved = { ...settings, applied: true, restart_required: false };
    client.put.mockResolvedValueOnce({ data: { data: saved } });
    const body = { provider: 'ragflow' as const, base_url: settings.base_url, api_key: '' };
    await expect(knowledgebaseAPI.saveConnection(body)).resolves.toEqual(saved);
    expect(client.put).toHaveBeenCalledTimes(1);
    expect(client.put).toHaveBeenCalledWith('/api/knowledgebase/connection', body, { timeout: 60_000 });
  });

  it('surfaces connection-save errors through the standard envelope', async () => {
    client.put.mockRejectedValueOnce({ response: { status: 502, data: { error: { code: 'connection_test_failed', message: 'private backend details' } } } });
    await expect(knowledgebaseAPI.saveConnection({ provider: 'ragflow', base_url: 'https://ragflow.example', api_key: '' }))
      .rejects.toMatchObject({ code: 'connection_test_failed', status: 502 });
  });

  it('creates a dataset without a model selection and encodes resource ids', async () => {
    client.post.mockResolvedValue({ data: { data: { id: 'ds-1', name: 'Notes', description: '', document_count: 0, chunk_count: 0 } } });
    await knowledgebaseAPI.createDataset({ name: 'Notes' });
    expect(client.post).toHaveBeenCalledWith('/api/knowledgebase/datasets', { name: 'Notes' }, { signal: undefined });
    client.get.mockResolvedValue({ data: 'hello' });
    await expect(knowledgebaseAPI.fileText('file/a')).resolves.toBe('hello');
    expect(knowledgebaseAPI.downloadUrl('file/a ?')).toBe('https://fixture.invalid/api/knowledgebase/files/file%2Fa%20%3F/content');
    expect(knowledgebaseAPI.previewUrl('file/a ?')).toBe('https://fixture.invalid/api/knowledgebase/files/file%2Fa%20%3F/content?inline=1');
  });

  it('surfaces service error codes', async () => {
    client.get.mockRejectedValue({ response: { status: 503, data: { error: { code: 'knowledgebase_unavailable', message: 'down' } } } });
    await expect(knowledgebaseAPI.datasets()).rejects.toBeInstanceOf(KnowledgebaseError);
  });
});
