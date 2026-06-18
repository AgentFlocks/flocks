import client from './client';

export interface UserDefinedPageListItem {
  id: string;
  title: string;
  route: string;
  icon: string;
  order: number;
  enabled: boolean;
  placement: string;
  buildHash: string;
  buildStatus: 'idle' | 'building' | 'ready' | 'failed';
}

export interface UserDefinedPageManifest {
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

export interface UserDefinedPageBuildMeta {
  hash: string;
  builtAt: number;
  status: 'idle' | 'building' | 'ready' | 'failed';
  error?: string | null;
}

export interface UserDefinedPageDetail {
  manifest: UserDefinedPageManifest;
  build: UserDefinedPageBuildMeta;
  sourceFiles: string[];
}

export interface UserDefinedPageCreateRequest {
  id: string;
  title: string;
  icon?: string;
  order?: number;
}

export interface UserDefinedPageSaveRequest {
  manifest?: Partial<UserDefinedPageManifest>;
  sourcePath?: string;
  sourceContent?: string;
}

export const userDefinedPagesAPI = {
  list: (enabledOnly = false) =>
    client.get<UserDefinedPageListItem[]>('/api/user-defined-pages', {
      params: enabledOnly ? { enabledOnly: true } : undefined,
    }),

  create: (payload: UserDefinedPageCreateRequest) =>
    client.post<UserDefinedPageDetail>('/api/user-defined-pages', payload),

  get: (pageId: string) =>
    client.get<UserDefinedPageDetail>(`/api/user-defined-pages/${pageId}`),

  save: (pageId: string, payload: UserDefinedPageSaveRequest) =>
    client.put<{ manifest: UserDefinedPageManifest; build: UserDefinedPageBuildMeta }>(
      `/api/user-defined-pages/${pageId}`,
      payload,
    ),

  build: (pageId: string) =>
    client.post<UserDefinedPageBuildMeta>(`/api/user-defined-pages/${pageId}/build`),
};
