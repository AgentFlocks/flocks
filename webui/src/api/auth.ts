import client from './client';

export interface BootstrapStatus {
  bootstrapped: boolean;
}

export interface LocalUser {
  id: string;
  username: string;
  role: 'admin' | 'member';
  status: 'active' | 'disabled';
  must_reset_password: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  last_login_at?: string | null;
}

export interface ResetPasswordResult {
  success: boolean;
  temporary_password?: string | null;
  must_reset_password: boolean;
}

export interface CloudBindingInitResult {
  binding_id: string;
  portal_login_url: string;
}

export interface CloudBindingExchangeResult {
  binding_id: string;
  cloud_session_token: string;
  fingerprint: string;
  install_id: string;
}

export interface CloudBindingSessionStatus {
  bound: boolean;
  binding_id?: string | null;
  account_name?: string | null;
  updated_at?: string | null;
}

export interface CloudSyncNowResult {
  success: boolean;
  synced_at?: string | null;
  detail?: string | null;
}

export const authApi = {
  bootstrapStatus: async (): Promise<BootstrapStatus> => {
    const response = await client.get('/api/auth/bootstrap-status');
    return response.data;
  },

  bootstrapAdmin: async (payload: { username: string; password: string }): Promise<LocalUser> => {
    const response = await client.post('/api/auth/bootstrap-admin', payload);
    return response.data;
  },

  login: async (payload: { username: string; password: string }): Promise<LocalUser> => {
    const response = await client.post('/api/auth/login', payload);
    return response.data;
  },

  me: async (): Promise<LocalUser> => {
    const response = await client.get('/api/auth/me');
    return response.data;
  },

  logout: async (): Promise<void> => {
    await client.post('/api/auth/logout');
  },

  changePassword: async (payload: { current_password: string; new_password: string }): Promise<void> => {
    await client.post('/api/auth/change-password', payload);
  },

  resetPassword: async (): Promise<ResetPasswordResult> => {
    const response = await client.post('/api/auth/reset-password');
    return response.data;
  },

  initCloudBinding: async (returnTo: string): Promise<CloudBindingInitResult> => {
    const response = await client.get('/api/auth/cloud/login', {
      params: { return_to: returnTo },
    });
    return response.data;
  },

  exchangeCloudBinding: async (
    bindingId: string,
    passportUid?: string,
  ): Promise<CloudBindingExchangeResult> => {
    const response = await client.get('/api/auth/cloud/return', {
      params: {
        binding_id: bindingId,
        ...(passportUid ? { passport_uid: passportUid } : {}),
      },
    });
    return response.data;
  },

  cloudBindingStatus: async (): Promise<CloudBindingSessionStatus> => {
    const response = await client.get('/api/auth/cloud/session');
    return response.data;
  },

  unbindCloudAccount: async (): Promise<void> => {
    await client.post('/api/auth/cloud/unbind');
  },

  syncCloudProfileNow: async (): Promise<CloudSyncNowResult> => {
    const response = await client.post('/api/auth/cloud/sync-now');
    return response.data;
  },
};
