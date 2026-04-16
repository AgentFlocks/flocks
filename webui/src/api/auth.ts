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
};
