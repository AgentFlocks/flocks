import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { authApi, type LocalUser } from '@/api/auth';

interface AuthContextValue {
  loading: boolean;
  bootstrapped: boolean;
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
  const [bootstrapped, setBootstrapped] = useState(false);
  const [user, setUser] = useState<LocalUser | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const status = await authApi.bootstrapStatus();
      setBootstrapped(status.bootstrapped);
      if (!status.bootstrapped) {
        setUser(null);
        return;
      }
      const me = await authApi.me();
      setUser(me);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const onAuthExpired = () => {
      setUser(null);
    };
    window.addEventListener('flocks:auth-expired', onAuthExpired);
    return () => window.removeEventListener('flocks:auth-expired', onAuthExpired);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const me = await authApi.login({ username, password });
    setBootstrapped(true);
    setUser(me);
  }, []);

  const bootstrapAdmin = useCallback(async (username: string, password: string) => {
    const me = await authApi.bootstrapAdmin({ username, password });
    setBootstrapped(true);
    setUser(me);
  }, []);

  const logout = useCallback(async () => {
    await authApi.logout();
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
    user,
    refresh,
    login,
    bootstrapAdmin,
    logout,
    changePassword,
  }), [loading, bootstrapped, user, refresh, login, bootstrapAdmin, logout, changePassword]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return ctx;
}
