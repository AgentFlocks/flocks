import client from './client';

export type HubPluginType = 'skill' | 'agent' | 'tool' | 'workflow';
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
  description: string;
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

  update: (type: HubPluginType, id: string, scope = 'global') =>
    client.post(`/api/hub/plugins/${type}/${id}/update`, { scope }),

  uninstall: (type: HubPluginType, id: string) =>
    client.delete(`/api/hub/plugins/${type}/${id}`),

  refresh: () =>
    client.post('/api/hub/refresh'),
};
