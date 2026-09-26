import apiClient, { getApiBase } from './client';

const ROOT = '/api/knowledgebase';

export interface KnowledgeFile {
  id: string;
  name: string;
  size: number | null;
  parent_id: string | null;
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
}
export interface Page<T> { items: T[]; total: number; page: number }
export interface SessionDatasets { session_id: string; dataset_ids: string[]; datasets?: Dataset[] }
export interface IntegrationStatus { configured: boolean; ready: boolean }
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
  files(params: ListOptions = {}, signal?: AbortSignal) {
    return unwrap<Page<KnowledgeFile>>(apiClient.get(`${ROOT}/files`, { params, signal }));
  },
  upload(file: File, signal?: AbortSignal) {
    const form = new FormData();
    form.append('file', file);
    return unwrap<KnowledgeFile>(apiClient.post(`${ROOT}/files`, form, { headers: { 'Content-Type': 'multipart/form-data' }, signal }));
  },
  removeFile(id: string, signal?: AbortSignal) {
    return unwrap<null>(apiClient.delete(`${ROOT}/files/${pathId(id)}`, { signal }));
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
  removeDataset(id: string, signal?: AbortSignal) {
    return unwrap<null>(apiClient.delete(`${ROOT}/datasets/${pathId(id)}`, { signal }));
  },
  documents(datasetId: string, params: ListOptions = {}, signal?: AbortSignal) {
    return unwrap<Page<KnowledgeDocument>>(apiClient.get(`${ROOT}/datasets/${pathId(datasetId)}/documents`, { params, signal }));
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
