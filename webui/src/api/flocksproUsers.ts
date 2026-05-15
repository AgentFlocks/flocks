import client from './client';
import type { LocalUser } from './auth';

export interface FlocksproUserQuota {
  max_admins: number;
  max_members: number;
  admin_count: number;
  member_count: number;
}

export interface FlocksproCreateUserResult {
  user: LocalUser;
  temporary_password: string;
  temporary_password_expires_at?: string | null;
}

export interface FlocksproResetPasswordResult {
  success: boolean;
  temporary_password?: string | null;
  must_reset_password: boolean;
}

export interface FlocksproLicenseStatus {
  activated?: boolean;
  active?: boolean;
  license_id?: string | null;
  status?: string | null;
  license_status?: string | null;
}

export const flocksproUsersApi = {
  hasCapability: async (): Promise<boolean> => {
    try {
      await client.get('/api/flockspro/license/status');
      return true;
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 404 || status === 405 || status === 501) {
        return false;
      }
      return false;
    }
  },

  getLicenseStatus: async (): Promise<FlocksproLicenseStatus> => {
    const response = await client.get('/api/flockspro/license/status');
    return response.data;
  },

  listUsers: async (): Promise<LocalUser[]> => {
    const response = await client.get('/api/flockspro/users');
    return response.data;
  },

  getQuota: async (): Promise<FlocksproUserQuota> => {
    const response = await client.get('/api/flockspro/users/quota');
    return response.data;
  },

  createUser: async (payload: { username: string; role: 'admin' | 'member' }): Promise<FlocksproCreateUserResult> => {
    const response = await client.post('/api/flockspro/users', payload);
    return response.data;
  },

  updateUserRole: async (userId: string, role: 'admin' | 'member'): Promise<LocalUser> => {
    const response = await client.patch(`/api/flockspro/users/${userId}/role`, { role });
    return response.data;
  },

  deleteUser: async (userId: string): Promise<void> => {
    await client.delete(`/api/flockspro/users/${userId}`);
  },

  resetUserPassword: async (
    userId: string,
    payload: { new_password?: string; force_reset?: boolean } = {},
  ): Promise<FlocksproResetPasswordResult> => {
    const response = await client.post(`/api/flockspro/users/${userId}/reset-password`, payload);
    return response.data;
  },
};
