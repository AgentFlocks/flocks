import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import i18n from '@/i18n';
import { authApi, type LocalUser } from '@/api/auth';

interface AuthContextValue {
  loading: boolean;
  bootstrapped: boolean | null;
  error: string | null;
  user: LocalUser | null;
  refresh: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  bootstrapAdmin: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [bootstrapped, setBootstrapped] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [user, setUser] = useState<LocalUser | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const status = await authApi.bootstrapStatus();
      setBootstrapped(status.bootstrapped);
      setError(null);
      if (!status.bootstrapped) {
        setUser(null);
        return;
      }
      try {
        const me = await authApi.me();
        setUser(me);
      } catch (err: any) {
        if (err?.response?.status === 401) {
          setUser(null);
          return;
        }
        setUser(null);
        setError(
          err?.response?.data?.message ||
            err?.response?.data?.detail ||
            err?.message ||
            i18n.t('auth:error.defaultMeError'),
        );
      }
    } catch (err: any) {
      setBootstrapped(null);
      setUser(null);
      setError(
        err?.response?.data?.message ||
          err?.response?.data?.detail ||
          err?.message ||
          i18n.t('auth:error.defaultBackendError'),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const onAuthExpired = () => {
      setError(null);
      setUser(null);
    };
    window.addEventListener('flocks:auth-expired', onAuthExpired);
    return () => window.removeEventListener('flocks:auth-expired', onAuthExpired);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const me = await authApi.login({ username, password });
    setBootstrapped(true);
    setError(null);
    setUser(me);
  }, []);

  const bootstrapAdmin = useCallback(async (username: string, password: string) => {
    const me = await authApi.bootstrapAdmin({ username, password });
    setBootstrapped(true);
    setError(null);
    setUser(me);
  }, []);

  const logout = useCallback(async () => {
    await authApi.logout();
    setError(null);
    setUser(null);
  }, []);

  const changePassword = useCallback(async (currentPassword: string, newPassword: string) => {
    await authApi.changePassword({
      current_password: currentPassword,
      new_password: newPassword,
    });
    await refresh();
  }, [refresh]);

  const value = useMemo<AuthContextValue>(() => ({
    loading,
    bootstrapped,
    error,
    user,
    refresh,
    login,
    bootstrapAdmin,
    logout,
    changePassword,
  }), [loading, bootstrapped, error, user, refresh, login, bootstrapAdmin, logout, changePassword]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return ctx;
}
