import client from './client';

export interface CloudAccountInfo {
  provider: string;
  account_id: string;
  account_name?: string | null;
  token_masked?: string | null;
  mcp_quota?: string | null;
  api_quota?: string | null;
  balance?: string | null;
  metadata: Record<string, unknown>;
  bound_by: string;
  bound_at: string;
  updated_at: string;
}

export interface CloudBindPayload {
  provider: string;
  account_id: string;
  account_name?: string;
  token?: string;
  mcp_quota?: string;
  api_quota?: string;
  balance?: string;
  metadata?: Record<string, unknown>;
}

export const cloudAccountApi = {
  get: async (): Promise<CloudAccountInfo | null> => {
    const response = await client.get('/api/cloud-account');
    return response.data;
  },
  bind: async (payload: CloudBindPayload): Promise<CloudAccountInfo> => {
    const response = await client.post('/api/cloud-account/bind', payload);
    return response.data;
  },
};
