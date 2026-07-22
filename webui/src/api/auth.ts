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

export interface ConsoleLoginStartResult {
  console_login_id: string;
  passport_login_url: string;
}

export interface ConsoleLoginFinishResult {
  console_login_id: string;
  logged_in: boolean;
  account_name?: string | null;
  updated_at?: string | null;
}

export interface ConsoleLoginSessionStatus {
  logged_in: boolean;
  console_login_id?: string | null;
  account_name?: string | null;
  updated_at?: string | null;
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

  startConsoleLogin: async (returnTo: string): Promise<ConsoleLoginStartResult> => {
    const response = await client.get('/api/auth/console-login/start', {
      params: { return_to: returnTo },
    });
    return response.data;
  },

  finishConsoleLogin: async (
    consoleLoginId: string,
    state?: string,
    passportUid?: string,
  ): Promise<ConsoleLoginFinishResult> => {
    const response = await client.post('/api/auth/console-login/finish', {
      console_login_id: consoleLoginId,
      ...(state ? { state } : {}),
      ...(passportUid ? { passport_uid: passportUid } : {}),
    });
    return response.data;
  },

  consoleLoginSession: async (): Promise<ConsoleLoginSessionStatus> => {
    const response = await client.get('/api/auth/console-login/session');
    return response.data;
  },

  logoutConsoleLogin: async (): Promise<void> => {
    await client.post('/api/auth/console-login/logout');
  },
};
