import client, { getApiBase } from './client';

export type HubPluginType = 'skill' | 'agent' | 'tool' | 'device' | 'workflow' | 'webui' | 'component';
export type HubPluginState =
  | 'available'
  | 'installed'
  | 'updateAvailable'
  | 'localOnly'
  | 'broken'
  | 'incompatible';

export interface HubCatalogEntry {
  id: string;
  type: HubPluginType;
  name: string;
  nameCn?: string;
  description: string;
  descriptionCn?: string;
  version: string;
  category: string;
  tags: string[];
  useCases: string[];
  domains: string[];
  capabilities: string[];
  trust: string;
  riskLevel: string;
  state: HubPluginState;
  installedVersion?: string;
  source: string;
  manifestPath: string;
  installPath?: string;
  native: boolean;
  brokenReason?: string;
}

export interface HubManifest extends HubCatalogEntry {
  schemaVersion: string;
  author?: string;
  license?: string;
  homepage?: string;
  components?: Array<{
    type: HubPluginType;
    id: string;
    optional?: boolean;
  }>;
  dependencies: Record<string, string[]>;
  permissions: {
    tools: string[];
    network: boolean;
    shell: boolean;
    filesystem: string;
  };
  risk: {
    level: string;
    reasons: string[];
  };
  entrypoints: string[];
  checksums: Record<string, string>;
}

export type HubInstallProgressStatus = 'pending' | 'installing' | 'installed' | 'skipped' | 'failed' | 'completed';

export interface HubInstallProgressItem {
  type: HubPluginType;
  id: string;
  name?: string;
  nameCn?: string;
  optional?: boolean;
  status: HubInstallProgressStatus;
  message?: string;
}

export interface HubInstallProgressEvent {
  event: 'start' | 'item' | 'complete' | 'error';
  id: string;
  type: HubPluginType;
  name: string;
  nameCn?: string;
  total: number;
  item?: HubInstallProgressItem;
  items?: HubInstallProgressItem[];
  record?: unknown;
  message?: string;
}

export interface HubFileNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size: number;
  checksum?: string;
  previewable: boolean;
  children: HubFileNode[];
}

export interface HubFileContent {
  path: string;
  content: string;
  size: number;
  checksum?: string;
  language?: string;
}

export interface HubCatalogParams {
  type?: HubPluginType;
  category?: string;
  tags?: string;
  useCases?: string;
  state?: string;
  trust?: string;
  risk?: string;
  q?: string;
}

export const hubAPI = {
  catalog: (params?: HubCatalogParams) =>
    client.get<HubCatalogEntry[]>('/api/hub/catalog', { params }),

  categories: () =>
    client.get('/api/hub/categories'),

  get: (type: HubPluginType, id: string) =>
    client.get<HubManifest>(`/api/hub/plugins/${type}/${id}`),

  files: (type: HubPluginType, id: string) =>
    client.get<HubFileNode>(`/api/hub/plugins/${type}/${id}/files`),

  fileContent: (type: HubPluginType, id: string, path: string) =>
    client.get<HubFileContent>(`/api/hub/plugins/${type}/${id}/files/content`, { params: { path } }),

  install: (type: HubPluginType, id: string, scope = 'global') =>
    client.post(`/api/hub/plugins/${type}/${id}/install`, { scope }),

  installStream: (
    type: HubPluginType,
    id: string,
    onProgress: (progress: HubInstallProgressEvent) => void,
    scope = 'global',
  ): Promise<void> => {
    return new Promise((resolve, reject) => {
      fetch(`${getApiBase()}/api/hub/plugins/${type}/${id}/install/stream`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope }),
      })
        .then((res) => {
          if (!res.ok || !res.body) {
            reject(new Error(`HTTP ${res.status}`));
            return;
          }

          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';

          const pump = (): Promise<void> =>
            reader.read().then(({ done, value }) => {
              if (done) {
                resolve();
                return;
              }

              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split('\n');
              buffer = lines.pop() ?? '';

              for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                  const progress = JSON.parse(line.slice(6)) as HubInstallProgressEvent;
                  onProgress(progress);
                  if (progress.event === 'error') {
                    reject(new Error(progress.message || 'Install failed'));
                    return;
                  }
                } catch {
                  // Ignore malformed SSE frames.
                }
              }

              return pump();
            });

          pump().catch(reject);
        })
        .catch(reject);
    });
  },

  update: (type: HubPluginType, id: string, scope = 'global') =>
    client.post(`/api/hub/plugins/${type}/${id}/update`, { scope }),

  uninstall: (type: HubPluginType, id: string) =>
    client.delete(`/api/hub/plugins/${type}/${id}`),

  refresh: () =>
    client.post('/api/hub/refresh'),
};
