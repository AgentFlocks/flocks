import client from './client';

export interface UpgradeRequestCreatePayload {
  product: string;
  license_type: 'trial_30d' | 'poc' | 'commercial';
  company: string;
  applicant_name: string;
  applicant_email?: string;
  applicant_phone?: string;
  notes?: string;
}

export interface UpgradeRequestDetails {
  product?: string;
  license_type?: 'trial_30d' | 'poc' | 'commercial' | string;
  company?: string;
  applicant_name?: string;
  applicant_email?: string | null;
  applicant_phone?: string | null;
  notes?: string | null;
}

export interface UpgradeRequestStatus {
  request_id: string;
  status: string;
  previous_request_id?: string | null;
  reason?: string | null;
  suggestion?: string | null;
  activate_key?: string | null;
  manifest_url?: string | null;
  details?: UpgradeRequestDetails;
  created_at: string;
  updated_at: string;
}

export const cloudUpgradeApi = {
  createRequest: async (payload: UpgradeRequestCreatePayload): Promise<UpgradeRequestStatus> => {
    const response = await client.post('/api/cloud/upgrade-requests', payload);
    return response.data;
  },

  listRequests: async (): Promise<UpgradeRequestStatus[]> => {
    const response = await client.get('/api/cloud/upgrade-requests');
    return response.data;
  },

  getRequest: async (requestId: string): Promise<UpgradeRequestStatus> => {
    const response = await client.get(`/api/cloud/upgrade-requests/${requestId}`);
    return response.data;
  },

  refreshRequest: async (requestId: string): Promise<UpgradeRequestStatus> => {
    const response = await client.post(`/api/cloud/upgrade-requests/${requestId}/refresh`);
    return response.data;
  },

  cancelRequest: async (requestId: string): Promise<UpgradeRequestStatus> => {
    const response = await client.post(`/api/cloud/upgrade-requests/${requestId}/cancel`);
    return response.data;
  },
};

