import client from './client';

export interface PendingPermission {
  id: string;
  sessionID: string;
  messageID: string;
  toolID: string;
  permission: string;
  patterns: string[];
  always: string[];
  metadata: Record<string, unknown>;
  time: { created: number };
}

export interface PermissionReply {
  allow: boolean;
  always?: boolean;
  response?: 'allow' | 'deny' | 'always' | 'never' | 'allow_session' | 'deny_session' | 'trust_tool_network' | 'trust_network_target';
}

export const permissionApi = {
  list: async (): Promise<PendingPermission[]> => {
    const response = await client.get<PendingPermission[]>('/api/permission');
    return response.data;
  },

  reply: async (
    permissionId: string,
    reply: PermissionReply,
  ): Promise<void> => {
    await client.post(`/api/permission/${encodeURIComponent(permissionId)}/reply`, reply);
  },
};
