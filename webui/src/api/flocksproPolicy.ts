import client from './client';

export type PermissionMode = 'readonly' | 'require-confirm' | 'auto-allow-all';

export type PermissionModeResponse = {
  permissionMode: PermissionMode | null;
  revision: number;
  updatedAt?: string;
  updatedBy?: string;
};

export const flocksproPolicyApi = {
  getChannel: async (channelId: string): Promise<PermissionModeResponse> =>
    (await client.get(`/api/flockspro/policy/channels/${encodeURIComponent(channelId)}/permission-mode`)).data,
  setChannel: async (channelId: string, permissionMode: PermissionMode): Promise<PermissionModeResponse> =>
    (await client.put(`/api/flockspro/policy/channels/${encodeURIComponent(channelId)}/permission-mode`, { permissionMode })).data,
  getSession: async (sessionId: string): Promise<PermissionModeResponse> =>
    (await client.get(`/api/flockspro/policy/sessions/${encodeURIComponent(sessionId)}/permission-mode`)).data,
  setSession: async (sessionId: string, permissionMode: PermissionMode): Promise<PermissionModeResponse> =>
    (await client.patch(`/api/flockspro/policy/sessions/${encodeURIComponent(sessionId)}/permission-mode`, { permissionMode })).data,
};
