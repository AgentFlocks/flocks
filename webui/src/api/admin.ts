import client from './client';

export interface AdminUser {
  id: string;
  username: string;
  role: 'admin' | 'member';
  status: 'active' | 'disabled';
  must_reset_password: boolean;
  created_at: string;
  updated_at: string;
  last_login_at?: string | null;
}

export interface CreateUserResult extends AdminUser {
  temporary_password?: string | null;
}

export interface AuditLog {
  id: string;
  operator_user_id?: string | null;
  target_user_id?: string | null;
  operator_username?: string | null;
  target_username?: string | null;
  action: string;
  result: string;
  ip?: string | null;
  user_agent?: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export const adminApi = {
  listUsers: async (): Promise<AdminUser[]> => {
    const response = await client.get('/api/admin/users');
    return response.data;
  },
  createUser: async (payload: {
    username: string;
    role: 'admin' | 'member';
    password?: string;
    force_reset?: boolean;
  }): Promise<CreateUserResult> => {
    const response = await client.post('/api/admin/users', payload);
    return response.data;
  },
  resetPassword: async (userId: string, payload: { new_password?: string; force_reset: boolean }) => {
    const response = await client.post(`/api/admin/users/${userId}/reset-password`, payload);
    return response.data as { success: boolean; temporary_password?: string | null; must_reset_password: boolean };
  },
  updateStatus: async (userId: string, status: 'active' | 'disabled'): Promise<AdminUser> => {
    const response = await client.patch(`/api/admin/users/${userId}/status`, { status });
    return response.data;
  },
  updateRole: async (userId: string, role: 'admin' | 'member'): Promise<AdminUser> => {
    const response = await client.patch(`/api/admin/users/${userId}/role`, { role });
    return response.data;
  },
  deleteUser: async (userId: string): Promise<{ success: boolean; retained_sessions: number }> => {
    const response = await client.delete(`/api/admin/users/${userId}`);
    return response.data;
  },
  listAuditLogs: async (): Promise<AuditLog[]> => {
    const response = await client.get('/api/admin/audit-logs');
    return response.data;
  },
};
