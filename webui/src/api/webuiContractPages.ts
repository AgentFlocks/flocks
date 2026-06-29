import client from './client';

export interface WebUIContractPageListItem {
  id: string;
  title: string;
  route: string;
  icon: string;
  order: number;
  enabled: boolean;
  placement: string;
  buildHash: string;
  buildStatus: 'idle' | 'building' | 'ready' | 'failed';
  workspaceId?: string | null;
  workspaceTitle?: string | null;
  workspaceRoute?: string | null;
}

export interface WebUIContractWorkspaceSection {
  id: string;
  label: string;
  pageIds: string[];
  defaultPageId?: string | null;
  contentPadding?: 'comfortable' | 'none';
  themeOverride?: 'light' | 'dark' | null;
}

export interface WebUIContractWorkspaceListItem {
  id: string;
  title: string;
  route: string;
  icon: string;
  order: number;
  enabled: boolean;
  placement: 'sceneWorkspace' | 'aiWorkbench';
  defaultPageId?: string | null;
  sections?: WebUIContractWorkspaceSection[];
  pages: WebUIContractPageListItem[];
}

export interface WebUIContractPageManifest {
  id: string;
  title: string;
  route: string;
  icon: string;
  order: number;
  enabled: boolean;
  placement: string;
  entry: string;
  updatedAt: number;
}

export interface WebUIContractPageBuildMeta {
  hash: string;
  builtAt: number;
  status: 'idle' | 'building' | 'ready' | 'failed';
  error?: string | null;
}

export interface WebUIContractPageDetail {
  manifest: WebUIContractPageManifest;
  build: WebUIContractPageBuildMeta;
  sourceFiles: string[];
}

export interface WebUIContractPageCreateRequest {
  id: string;
  title: string;
  icon?: string;
  order?: number;
}

export interface WebUIContractPageSaveRequest {
  manifest?: Partial<WebUIContractPageManifest>;
  sourcePath?: string;
  sourceContent?: string;
}

export const webuiContractPagesAPI = {
  list: (enabledOnly = false) =>
    client.get<WebUIContractPageListItem[]>('/api/contracts/webui/pages', {
      params: enabledOnly ? { enabledOnly: true } : undefined,
    }),

  listWorkspaces: (enabledOnly = false) =>
    client.get<WebUIContractWorkspaceListItem[]>('/api/contracts/webui/workspaces', {
      params: enabledOnly ? { enabledOnly: true } : undefined,
    }),

  create: (payload: WebUIContractPageCreateRequest) =>
    client.post<WebUIContractPageDetail>('/api/contracts/webui/pages', payload),

  get: (pageId: string) =>
    client.get<WebUIContractPageDetail>(`/api/contracts/webui/pages/${pageId}`),

  save: (pageId: string, payload: WebUIContractPageSaveRequest) =>
    client.put<{ manifest: WebUIContractPageManifest; build: WebUIContractPageBuildMeta }>(
      `/api/contracts/webui/pages/${pageId}`,
      payload,
    ),

  build: (pageId: string) =>
    client.post<WebUIContractPageBuildMeta>(`/api/contracts/webui/pages/${pageId}/build`),
};
