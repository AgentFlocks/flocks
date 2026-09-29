import apiClient, { getApiBase } from './client';

const ROOT = '/api/knowledgebase';

export interface KnowledgeFile {
  id: string;
  name: string;
  size: number | null;
  parent_id: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}
export interface Dataset {
  id: string;
  name: string;
  description: string;
  document_count: number | null;
  chunk_count: number | null;
}
export interface KnowledgeDocument {
  id: string;
  dataset_id: string;
  name: string;
  status: string;
  progress: number | null;
  chunk_count: number | null;
  source_file_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  size?: number | null;
}
export interface Page<T> { items: T[]; total: number; page: number }
export interface KnowledgeEntry extends KnowledgeFile {
  kind: 'file' | 'folder';
  can_manage: boolean;
}
export interface KnowledgeFolder {
  id: string;
  name: string;
  parent_id: string | null;
  can_write: boolean;
}
export interface KnowledgeDirectory extends Page<KnowledgeEntry> {
  file_total: number;
  current_folder: KnowledgeFolder;
  breadcrumbs: KnowledgeFolder[];
}
export interface DirectoryOptions extends ListOptions { parent_id?: string }
export interface SessionDatasets { session_id: string; dataset_ids: string[]; datasets?: Dataset[] }
export interface IntegrationStatus { configured: boolean; ready: boolean }
export interface ConnectionSettings { provider: 'ragflow' | null; base_url: string; has_api_key: boolean }
export interface ConnectionSaveResult { provider: 'ragflow'; base_url: string; has_api_key: boolean; applied: true; restart_required: false }
export interface ListOptions { q?: string; page?: number; page_size?: number }

interface Envelope<T> { data: T }
interface FailureData { error?: { code?: string; message?: string } }

export class KnowledgebaseError extends Error {
  constructor(message: string, public readonly code: string, public readonly status: number) {
    super(message);
    this.name = 'KnowledgebaseError';
  }
}

function normalizeError(error: unknown): KnowledgebaseError {
  if (error instanceof KnowledgebaseError) return error;
  const response = (error as { response?: { status?: number; data?: FailureData } })?.response;
  const body = response?.data?.error;
  return new KnowledgebaseError(
    body?.message || 'Knowledgebase request failed.',
    body?.code || (response?.status === 404 ? 'not_found' : 'knowledgebase_unavailable'),
    response?.status || 0,
  );
}

async function unwrap<T>(request: Promise<{ data: Envelope<T> }>): Promise<T> {
  try { return (await request).data.data; } catch (error) { throw normalizeError(error); }
}

const pathId = (id: string) => encodeURIComponent(id);

function isDirectoryNode(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const node = value as Record<string, unknown>;
  return typeof node.id === 'string' && node.id.length > 0 && typeof node.name === 'string'
    && (node.parent_id === null || typeof node.parent_id === 'string');
}

function isDirectoryFolder(value: unknown): value is KnowledgeFolder {
  return isDirectoryNode(value) && typeof value.can_write === 'boolean';
}

function isDirectoryEntry(value: unknown): value is KnowledgeEntry {
  return isDirectoryNode(value) && (value.kind === 'file' || value.kind === 'folder')
    && typeof value.can_manage === 'boolean'
    && (value.size === null || (typeof value.size === 'number' && Number.isFinite(value.size) && value.size >= 0));
}

function directoryData(value: unknown): KnowledgeDirectory {
  const data = value && typeof value === 'object' ? value as Record<string, unknown> : undefined;
  if (data && Array.isArray(data.items) && data.items.every(isDirectoryEntry)
    && typeof data.total === 'number' && Number.isSafeInteger(data.total) && data.total >= 0
    && typeof data.page === 'number' && Number.isSafeInteger(data.page) && data.page >= 1
    && typeof data.file_total === 'number' && Number.isSafeInteger(data.file_total) && data.file_total >= 0
    && isDirectoryFolder(data.current_folder)
    && Array.isArray(data.breadcrumbs) && data.breadcrumbs.length > 0 && data.breadcrumbs.every(isDirectoryFolder)) {
    return data as unknown as KnowledgeDirectory;
  }
  // Older servers may ignore include_folders and return a flat Page. Never invent a writable root.
  throw new KnowledgebaseError('The knowledge service returned an unsupported directory response.', 'knowledgebase_unavailable', 502);
}

export const knowledgebaseAPI = {
  async status(signal?: AbortSignal): Promise<IntegrationStatus> {
    try { return await unwrap<IntegrationStatus>(apiClient.get(`${ROOT}/status`, { signal })); }
    catch (error) {
      const failure = normalizeError(error);
      if (failure.status === 404) return { configured: false, ready: false };
      if (failure.code === 'knowledgebase_not_configured') return { configured: false, ready: false };
      throw failure;
    }
  },
  connection(signal?: AbortSignal) {
    return unwrap<ConnectionSettings>(apiClient.get(`${ROOT}/connection`, { signal }));
  },
  saveConnection(data: { provider: 'ragflow'; base_url: string; api_key: string }) {
    // Another configuration save may finish its probe before this one acquires the write lock.
    return unwrap<ConnectionSaveResult>(apiClient.put(`${ROOT}/connection`, data, { timeout: 60_000 }));
  },
  files(params: ListOptions = {}, signal?: AbortSignal) {
    return unwrap<Page<KnowledgeFile>>(apiClient.get(`${ROOT}/files`, { params, signal }));
  },
  directory(params: DirectoryOptions = {}, signal?: AbortSignal) {
    return unwrap<unknown>(apiClient.get(`${ROOT}/files`, { params: { ...params, include_folders: true }, signal })).then(directoryData);
  },
  createFolder(data: { name: string; parent_id?: string }, signal?: AbortSignal) {
    return unwrap<KnowledgeEntry>(apiClient.post(`${ROOT}/folders`, data, { signal }));
  },
  async updateFile(id: string, data: { name?: string; parent_id?: string }, signal?: AbortSignal): Promise<void> {
    try { await apiClient.put(`${ROOT}/files/${pathId(id)}`, data, { signal }); }
    catch (error) { throw normalizeError(error); }
  },
  upload(file: File, parentId?: string, signal?: AbortSignal) {
    const form = new FormData();
    form.append('file', file);
    if (parentId !== undefined) form.append('parent_id', parentId);
    return unwrap<KnowledgeEntry>(apiClient.post(`${ROOT}/files`, form, { headers: { 'Content-Type': 'multipart/form-data' }, signal }));
  },
  async removeFile(id: string, signal?: AbortSignal): Promise<void> {
    try { await apiClient.delete(`${ROOT}/files/${pathId(id)}`, { signal }); }
    catch (error) { throw normalizeError(error); }
  },
  fileText(id: string, signal?: AbortSignal) {
    return apiClient.get<string>(`${ROOT}/files/${pathId(id)}/content`, { responseType: 'text', signal }).then(response => (
      typeof response.data === 'string' ? response.data : String(response.data)
    )).catch(error => { throw normalizeError(error); });
  },
  downloadUrl(id: string) { return `${getApiBase()}${ROOT}/files/${pathId(id)}/content`; },
  previewUrl(id: string) { return `${getApiBase()}${ROOT}/files/${pathId(id)}/content?inline=1`; },
  datasets(params: ListOptions = {}, signal?: AbortSignal) {
    return unwrap<Page<Dataset>>(apiClient.get(`${ROOT}/datasets`, { params, signal }));
  },
  dataset(id: string, signal?: AbortSignal) {
    return unwrap<Dataset>(apiClient.get(`${ROOT}/datasets/${pathId(id)}`, { signal }));
  },
  createDataset(data: { name: string; description?: string }, signal?: AbortSignal) {
    return unwrap<Dataset>(apiClient.post(`${ROOT}/datasets`, data, { signal }));
  },
  async updateDataset(id: string, data: { name: string; description?: string }, signal?: AbortSignal): Promise<void> {
    // A successful update returns 204 with no response envelope.
    try { await apiClient.put(`${ROOT}/datasets/${pathId(id)}`, data, { signal }); }
    catch (error) { throw normalizeError(error); }
  },
  removeDataset(id: string, signal?: AbortSignal) {
    return unwrap<null>(apiClient.delete(`${ROOT}/datasets/${pathId(id)}`, { signal }));
  },
  documents(datasetId: string, params: ListOptions = {}, signal?: AbortSignal) {
    return unwrap<Page<KnowledgeDocument>>(apiClient.get(`${ROOT}/datasets/${pathId(datasetId)}/documents`, { params, signal }));
  },
  documentContentUrl(datasetId: string, documentId: string, inline = true) {
    return `${getApiBase()}${ROOT}/datasets/${pathId(datasetId)}/documents/${pathId(documentId)}/content?inline=${inline}`;
  },
  documentText(datasetId: string, documentId: string, signal?: AbortSignal) {
    return apiClient.get<string>(`${ROOT}/datasets/${pathId(datasetId)}/documents/${pathId(documentId)}/content`, { responseType: 'text', signal }).then(response => (
      typeof response.data === 'string' ? response.data : String(response.data)
    )).catch(error => { throw normalizeError(error); });
  },
  linkFiles(datasetId: string, fileIds: string[], signal?: AbortSignal) {
    return unwrap<{ dataset_id: string }>(apiClient.post(`${ROOT}/datasets/${pathId(datasetId)}/files`, { file_ids: fileIds }, { signal }));
  },
  removeDocuments(datasetId: string, documentIds: string[], signal?: AbortSignal) {
    return unwrap<{ dataset_id: string }>(apiClient.delete(`${ROOT}/datasets/${pathId(datasetId)}/documents`, { data: { document_ids: documentIds }, signal }));
  },
  parse(datasetId: string, documentIds: string[], signal?: AbortSignal) {
    return unwrap<{ dataset_id: string }>(apiClient.post(`${ROOT}/datasets/${pathId(datasetId)}/parse`, { document_ids: documentIds }, { signal }));
  },
  sessionDatasets(sessionId: string, signal?: AbortSignal) {
    return unwrap<SessionDatasets>(apiClient.get(`${ROOT}/sessions/${pathId(sessionId)}/datasets`, { signal }));
  },
  setSessionDatasets(sessionId: string, datasetIds: string[], signal?: AbortSignal) {
    return unwrap<SessionDatasets>(apiClient.put(`${ROOT}/sessions/${pathId(sessionId)}/datasets`, { dataset_ids: datasetIds }, { signal }));
  },
};
