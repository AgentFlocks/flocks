import client from './client';

// ─── Models ────────────────────────────────────────────────────────────────

export interface WorkspaceNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size?: number;
  modified_at?: number;
  is_text_file?: boolean;
  children?: WorkspaceNode[];
}

export interface WorkspaceStats {
  file_count: number;
  dir_count: number;
  total_size_bytes: number;
  memory_file_count: number;
  memory_total_size_bytes: number;
}

export interface UploadResult {
  name: string;
  path?: string;
  size?: number;
  is_text_file?: boolean;
  preview_warning?: string;
  error?: string;
}

export type UploadPurpose = 'chat';

// ─── API ───────────────────────────────────────────────────────────────────

export const workspaceAPI = {
  // Directory operations
  tree: (path = '', depth = 2) =>
    client.get<WorkspaceNode>('/api/workspace/tree', { params: { path, depth } }),

  list: (path = '') =>
    client.get<WorkspaceNode[]>('/api/workspace/list', { params: { path } }),

  createDir: (path: string) =>
    client.post<{ path: string; created: boolean }>('/api/workspace/dir', { path }),

  deleteDir: (path: string) =>
    client.delete<{ path: string; deleted: boolean }>('/api/workspace/dir', { params: { path } }),

  // File operations
  upload: (files: File[], dest = '', purpose?: UploadPurpose) => {
    const form = new FormData();
    files.forEach((f) => form.append('files', f));
    // Set Content-Type to undefined to remove the axios instance default
    // "application/json", allowing XHR to automatically set
    // "multipart/form-data; boundary=----XYZ".
    // Uploads can legitimately exceed the global 30s API timeout for large PDFs/DOCs,
    // so disable per-request timeout here and let the browser keep the connection open.
    return client.post<{ uploaded: UploadResult[] }>('/api/workspace/upload', form, {
      params: { dest, purpose },
      headers: { 'Content-Type': undefined },
      timeout: 0,
      validateStatus: (status) => (status >= 200 && status < 300) || status === 409,
    });
  },

  readFile: (path: string) =>
    client.get<{ path: string; content: string }>('/api/workspace/file', { params: { path } }),

  writeFile: (path: string, content: string) =>
    client.put<{ path: string; written: boolean }>('/api/workspace/file', { path, content }),

  deleteFile: (path: string) =>
    client.delete<{ path: string; deleted: boolean }>('/api/workspace/file', { params: { path } }),

  downloadUrl: (path: string) =>
    `${client.defaults.baseURL ?? ''}/api/workspace/download?path=${encodeURIComponent(path)}`,

  downloadZip: (paths: string[], archiveName = 'workspace_files.zip') =>
    client.post(
      '/api/workspace/download/zip',
      { paths, archive_name: archiveName },
      { responseType: 'blob' },
    ),

  move: (src: string, dst: string) =>
    client.post<{ src: string; dst: string; moved: boolean }>('/api/workspace/move', { src, dst }),

  // Memory (read-only)
  listMemory: () =>
    client.get<WorkspaceNode[]>('/api/workspace/memory/list'),

  readMemoryFile: (path: string) =>
    client.get<{ path: string; content: string }>('/api/workspace/memory/file', { params: { path } }),

  // Stats
  stats: () =>
    client.get<WorkspaceStats>('/api/workspace/stats'),
};

// ─── Helpers ───────────────────────────────────────────────────────────────

export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export function formatDate(ts?: number): string {
  if (!ts) return '-';
  return new Date(ts * 1000).toLocaleString();
}

export function fileIcon(node: WorkspaceNode): string {
  if (node.type === 'directory') return '📁';
  const ext = node.name.split('.').pop()?.toLowerCase() ?? '';
  const map: Record<string, string> = {
    md: '📝', txt: '📄', log: '📋', json: '🔧', yaml: '🔧', yml: '🔧',
    py: '🐍', js: '🟨', ts: '🔷', tsx: '🔷', jsx: '🟨',
    sh: '⚙️', bash: '⚙️', csv: '📊', pdf: '📕', png: '🖼️',
    jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', zip: '🗜️', tar: '🗜️', gz: '🗜️',
  };
  return map[ext] ?? '📄';
}
