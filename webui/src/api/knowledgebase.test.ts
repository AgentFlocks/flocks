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

  it('updates a dataset through an encoded PUT without reading a 204 body', async () => {
    const signal = new AbortController().signal;
    const fields = { name: 'Renamed notes', description: '' };
    client.put.mockResolvedValue({ status: 204, get data() { throw new Error('204 responses have no body'); } });
    await expect(knowledgebaseAPI.updateDataset('set/a ?', fields, signal)).resolves.toBeUndefined();
    expect(client.put).toHaveBeenCalledTimes(1);
    expect(client.put).toHaveBeenCalledWith('/api/knowledgebase/datasets/set%2Fa%20%3F', fields, { signal });
    expect(client.get).not.toHaveBeenCalled();
  });

  it('normalizes dataset update failures and does not retry the write', async () => {
    client.put.mockRejectedValue({ response: { status: 400, data: { error: { code: 'invalid_request', message: 'Name is required' } } } });
    await expect(knowledgebaseAPI.updateDataset('set-1', { name: '' })).rejects.toMatchObject({ code: 'invalid_request', status: 400, message: 'Name is required' });
    expect(client.put).toHaveBeenCalledTimes(1);
  });

  it('preserves nullable document source and creation metadata without inferring ids', async () => {
    const documents = {
      items: [
        { id: 'doc-1', dataset_id: 'set-1', name: 'same.pdf', status: 'ready', progress: 1, chunk_count: 2, source_file_id: 'source-9', created_at: '2026-09-28T10:00:00Z', size: 123 },
        { id: 'doc-2', dataset_id: 'set-1', name: 'same.pdf', status: 'ready', progress: 1, chunk_count: 2, source_file_id: null, created_at: null, size: null },
      ],
      total: 2,
      page: 1,
    };
    client.get.mockResolvedValue({ data: { data: documents } });
    await expect(knowledgebaseAPI.documents('set-1')).resolves.toEqual(documents);
  });

  it('reads document originals through dataset/document routes with separate preview and download URLs', async () => {
    const signal = new AbortController().signal;
    client.get.mockResolvedValue({ data: 'Original document text' });
    await expect(knowledgebaseAPI.documentText('set/a', 'doc/b ?', signal)).resolves.toBe('Original document text');
    expect(client.get).toHaveBeenCalledWith('/api/knowledgebase/datasets/set%2Fa/documents/doc%2Fb%20%3F/content', { responseType: 'text', signal });
    expect(knowledgebaseAPI.documentContentUrl('set/a', 'doc/b ?')).toBe('https://fixture.invalid/api/knowledgebase/datasets/set%2Fa/documents/doc%2Fb%20%3F/content?inline=true');
    expect(knowledgebaseAPI.documentContentUrl('set/a', 'doc/b ?', false)).toBe('https://fixture.invalid/api/knowledgebase/datasets/set%2Fa/documents/doc%2Fb%20%3F/content?inline=false');
    expect(client.get.mock.calls.every(([url]) => !String(url).includes('/files/'))).toBe(true);
  });

  it('normalizes missing document-original errors without falling back to a file route', async () => {
    client.get.mockRejectedValue({ response: { status: 404, data: { error: { code: 'not_found', message: 'Original unavailable' } } } });
    await expect(knowledgebaseAPI.documentText('set-1', 'doc-1')).rejects.toMatchObject({ code: 'not_found', status: 404 });
    expect(client.get).toHaveBeenCalledTimes(1);
  });

  it('opts into real directory entries without changing the legacy flat file request', async () => {
    const root = { id: 'root-id', name: 'Root', parent_id: null, can_write: true };
    const directory = {
      items: [{ id: 'folder-1', name: 'Notes', size: null, parent_id: root.id, kind: 'folder', can_manage: true }],
      total: 1, page: 1, file_total: 0, current_folder: root, breadcrumbs: [root],
    };
    const signal = new AbortController().signal;
    client.get.mockResolvedValue({ data: { data: directory } });
    await expect(knowledgebaseAPI.directory({ parent_id: 'folder/a', q: 'note', page: 2 }, signal)).resolves.toEqual(directory);
    expect(client.get).toHaveBeenLastCalledWith('/api/knowledgebase/files', { params: { include_folders: true, parent_id: 'folder/a', q: 'note', page: 2 }, signal });
    await knowledgebaseAPI.directory();
    expect(client.get).toHaveBeenLastCalledWith('/api/knowledgebase/files', { params: { include_folders: true }, signal: undefined });
    await knowledgebaseAPI.files({ page: 1 });
    expect(client.get).toHaveBeenLastCalledWith('/api/knowledgebase/files', { params: { page: 1 }, signal: undefined });
  });

  it('rejects legacy flat pages and incomplete directory metadata instead of inventing a writable root', async () => {
    const root = { id: 'root-id', name: 'Root', parent_id: null, can_write: true };
    const entry = { id: 'file-1', name: 'note.txt', size: 10, parent_id: root.id, kind: 'file', can_manage: true };
    const valid = { items: [entry], total: 1, page: 1, file_total: 1, current_folder: root, breadcrumbs: [root] };
    const invalid: unknown[] = [
      { items: [entry], total: 1, page: 1 },
      { ...valid, current_folder: undefined },
      { ...valid, current_folder: { ...root, can_write: undefined } },
      { ...valid, breadcrumbs: undefined },
      { ...valid, breadcrumbs: [] },
      { ...valid, breadcrumbs: [{ ...root, can_write: 'true' }] },
      { ...valid, file_total: undefined },
      { ...valid, file_total: -1 },
      { ...valid, items: [{ ...entry, kind: undefined }] },
      { ...valid, items: [{ ...entry, can_manage: undefined }] },
      { ...valid, items: [{ ...entry, parent_id: undefined }] },
      null,
    ];
    for (const data of invalid) {
      client.get.mockResolvedValueOnce({ data: { data } });
      const request = knowledgebaseAPI.directory();
      await expect(request).rejects.toBeInstanceOf(KnowledgebaseError);
      await expect(request).rejects.toMatchObject({ code: 'knowledgebase_unavailable', status: 502 });
    }
    expect(client.get).toHaveBeenCalledTimes(invalid.length);
    expect(client.get.mock.calls.every(([, options]) => options.params.include_folders === true)).toBe(true);
    expect(client.post).not.toHaveBeenCalled();
  });

  it('preserves explicitly read-only directory metadata without granting write permissions', async () => {
    const root = { id: 'root-id', name: 'Root', parent_id: null, can_write: false };
    const directory = {
      items: [{ id: 'file-1', name: 'note.txt', size: null, parent_id: root.id, kind: 'file', can_manage: false }],
      total: 1, page: 1, file_total: 1, current_folder: root, breadcrumbs: [root],
    };
    client.get.mockResolvedValue({ data: { data: directory } });
    await expect(knowledgebaseAPI.directory()).resolves.toEqual(directory);
  });

  it('creates folders and uploads file bytes with an explicit parent identity', async () => {
    const entry = { id: 'entry-1', name: 'Notes', kind: 'folder', can_manage: true, size: null, parent_id: 'root-id' };
    client.post.mockResolvedValue({ data: { data: entry } });
    await expect(knowledgebaseAPI.createFolder({ name: 'Notes', parent_id: 'root-id' })).resolves.toEqual(entry);
    expect(client.post).toHaveBeenLastCalledWith('/api/knowledgebase/folders', { name: 'Notes', parent_id: 'root-id' }, { signal: undefined });
    const file = new File(['fixture bytes'], 'note.txt', { type: 'text/plain' });
    await knowledgebaseAPI.upload(file, 'folder-id');
    const [, form, options] = client.post.mock.calls[client.post.mock.calls.length - 1];
    expect(form).toBeInstanceOf(FormData);
    expect(form.get('file')).toBe(file);
    expect(form.get('parent_id')).toBe('folder-id');
    expect(options).toEqual({ headers: { 'Content-Type': 'multipart/form-data' }, signal: undefined });
    await knowledgebaseAPI.upload(file);
    expect(client.post.mock.calls[client.post.mock.calls.length - 1][1].has('parent_id')).toBe(false);
  });

  it('renames, moves and deletes entries without reading a 204 response body', async () => {
    const signal = new AbortController().signal;
    const empty = { status: 204, get data() { throw new Error('204 responses have no body'); } };
    client.put.mockResolvedValue(empty);
    client.delete.mockResolvedValue(empty);
    await expect(knowledgebaseAPI.updateFile('entry/a ?', { name: 'New name' }, signal)).resolves.toBeUndefined();
    expect(client.put).toHaveBeenLastCalledWith('/api/knowledgebase/files/entry%2Fa%20%3F', { name: 'New name' }, { signal });
    await expect(knowledgebaseAPI.updateFile('file-id', { parent_id: 'target-id' })).resolves.toBeUndefined();
    expect(client.put).toHaveBeenLastCalledWith('/api/knowledgebase/files/file-id', { parent_id: 'target-id' }, { signal: undefined });
    await expect(knowledgebaseAPI.removeFile('folder/a', signal)).resolves.toBeUndefined();
    expect(client.delete).toHaveBeenLastCalledWith('/api/knowledgebase/files/folder%2Fa', { signal });
    expect(client.get).not.toHaveBeenCalled();
  });

  it('surfaces rejected directory writes once without silently retrying or falling back', async () => {
    const failure = { response: { status: 409, data: { error: { code: 'conflict', message: 'Folder is not empty' } } } };
    client.delete.mockRejectedValue(failure);
    client.put.mockRejectedValue(failure);
    client.post.mockRejectedValue(failure);
    await expect(knowledgebaseAPI.removeFile('folder-1')).rejects.toMatchObject({ code: 'conflict', status: 409 });
    await expect(knowledgebaseAPI.updateFile('folder-1', { name: 'Taken' })).rejects.toMatchObject({ code: 'conflict', status: 409 });
    await expect(knowledgebaseAPI.createFolder({ name: 'Taken' })).rejects.toMatchObject({ code: 'conflict', status: 409 });
    expect(client.delete).toHaveBeenCalledTimes(1);
    expect(client.put).toHaveBeenCalledTimes(1);
    expect(client.post).toHaveBeenCalledTimes(1);
    expect(client.get).not.toHaveBeenCalled();
  });

  it('surfaces service error codes', async () => {
    client.get.mockRejectedValue({ response: { status: 503, data: { error: { code: 'knowledgebase_unavailable', message: 'down' } } } });
    await expect(knowledgebaseAPI.datasets()).rejects.toBeInstanceOf(KnowledgebaseError);
  });
});
