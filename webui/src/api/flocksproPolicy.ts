import client from './client';

export type PermissionMode = 'readonly' | 'require-confirm' | 'auto-allow-all';
export type RuntimeMode = 'dev-mode' | 'exe-mode';

export type PermissionModeResponse = {
  permissionMode: PermissionMode | null;
  revision: number;
  updatedAt?: string;
  updatedBy?: string;
};

export type SessionExecutionSettingsResponse = {
  permissionMode: PermissionMode;
  runtimeMode: RuntimeMode;
  revision: number;
  updatedBy?: string;
};

export const isSessionExecutionSettingsUnsupported = (error: unknown): boolean => {
  const status = (error as { response?: { status?: number } } | undefined)?.response?.status;
  return status === 404 || status === 405 || status === 501;
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
  getSessionExecutionSettings: async (sessionId: string): Promise<SessionExecutionSettingsResponse> =>
    (await client.get(`/api/flockspro/policy/sessions/${encodeURIComponent(sessionId)}/execution-settings`)).data,
  setSessionExecutionSettings: async (
    sessionId: string,
    payload: Partial<Pick<SessionExecutionSettingsResponse, 'permissionMode' | 'runtimeMode'>> & { revision?: number },
  ): Promise<SessionExecutionSettingsResponse> =>
    (await client.patch(`/api/flockspro/policy/sessions/${encodeURIComponent(sessionId)}/execution-settings`, payload)).data,
};
