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
}

export const permissionApi = {
  list: async (): Promise<PendingPermission[]> => {
    const response = await client.get<PendingPermission[]>('/permission');
    return response.data;
  },

  reply: async (
    permissionId: string,
    reply: PermissionReply,
  ): Promise<void> => {
    await client.post(`/permission/${encodeURIComponent(permissionId)}/reply`, reply);
  },
};
