import client from './client';
import type { UpdateProgress } from './update';

export interface UpgradeRequestCreatePayload {
  product: string;
  license_type: 'trial_30d' | 'poc' | 'commercial';
  request_kind?: 'new' | 'trial_extension' | 'license_change';
  company: string;
  applicant_name: string;
  applicant_email?: string;
  applicant_phone?: string;
  notes?: string;
}

export interface UpgradeRequestDetails {
  product?: string;
  license_type?: 'trial_30d' | 'poc' | 'commercial' | string;
  license_status?: string | null;
  expires_at?: number | string | null;
  license_effective_expires_at?: number | string | null;
  license_duration_days?: number | null;
  license_id?: string | null;
  max_admins?: number | null;
  max_members?: number | null;
  request_kind?: 'new' | 'trial_extension' | 'license_change' | string;
  console_account_name?: string | null;
  passport_uid?: string | null;
  cloud_account?: string | null;
  account?: string | null;
  company?: string;
  enterprise_name?: string;
  applicant_name?: string;
  applicant_email?: string | null;
  applicant_phone?: string | null;
  notes?: string | null;
  auto_install_target?: string;
  auto_install_version?: string;
  auto_install_pro_version?: string;
  flockspro_component_version?: string;
  auto_install_result?: string;
  auto_install_completed_at?: string;
}

export interface UpgradeRequestStatus {
  request_id: string;
  status: string;
  previous_request_id?: string | null;
  reason?: string | null;
  suggestion?: string | null;
  activate_key?: string | null;
  manifest_url?: string | null;
  license_id?: string | null;
  license_status?: string | null;
  max_admins?: number | null;
  max_members?: number | null;
  expires_at?: number | string | null;
  details?: UpgradeRequestDetails;
  created_at: string;
  updated_at: string;
}

export interface ProPackageStatus {
  installed: boolean;
  display_version?: string | null;
  compare_version?: string | null;
  installed_version?: string | null;
  flockspro_component_version?: string | null;
  build_id?: string | null;
  installed_at?: string | null;
  pro_enabled?: boolean | null;
  license_status?: string | null;
  inactive_reason?: string | null;
}

export const consoleUpgradeApi = {
  createRequest: async (payload: UpgradeRequestCreatePayload): Promise<UpgradeRequestStatus> => {
    const response = await client.post('/api/console/upgrade-requests', payload);
    return response.data;
  },

  listRequests: async (): Promise<UpgradeRequestStatus[]> => {
    const response = await client.get('/api/console/upgrade-requests');
    return response.data;
  },

  getProPackageStatus: async (): Promise<ProPackageStatus> => {
    const response = await client.get('/api/console/pro-package-status');
    return response.data;
  },

  syncRevocations: async (): Promise<{
    revoked_license_ids: string[];
    imported: boolean;
    synced_license_ids?: string[];
    activated_license_id?: string | null;
    refreshed_license_id?: string | null;
  }> => {
    const response = await client.post('/api/console/licenses/sync-revocations');
    return response.data;
  },

  getRequest: async (requestId: string): Promise<UpgradeRequestStatus> => {
    const response = await client.get(`/api/console/upgrade-requests/${requestId}`);
    return response.data;
  },

  refreshRequest: async (requestId: string): Promise<UpgradeRequestStatus> => {
    const response = await client.post(`/api/console/upgrade-requests/${requestId}/refresh`);
    return response.data;
  },

  cancelRequest: async (requestId: string): Promise<UpgradeRequestStatus> => {
    const response = await client.post(`/api/console/upgrade-requests/${requestId}/cancel`);
    return response.data;
  },

  startRequest: (requestId: string, onProgress: (progress: UpdateProgress) => void): Promise<void> => {
    return new Promise((resolve, reject) => {
      fetch(`/api/console/upgrade-requests/${encodeURIComponent(requestId)}/start`, { method: 'POST' })
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
                if (line.startsWith('data: ')) {
                  try {
                    const progress: UpdateProgress = JSON.parse(line.slice(6));
                    onProgress(progress);
                    if (progress.stage === 'error') {
                      reject(new Error(progress.message));
                      return;
                    }
                  } catch {
                    // Ignore malformed SSE frames.
                  }
                }
              }

              return pump();
            });

          pump().catch(reject);
        })
        .catch(reject);
    });
  },
};

