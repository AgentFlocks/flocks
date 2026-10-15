import { createContext, useContext } from 'react';
import type { LocalUser } from '@/api/auth';

// Kept apart from AuthContext.tsx (which pulls in i18n) so shared widgets can
// read the signed-in account without loading the provider's dependencies.

export interface AuthContextValue {
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

export const AuthContext = createContext<AuthContextValue | null>(null);

/** Auth state when rendered under AuthProvider, otherwise null (tests, isolated widgets). */
export function useOptionalAuth(): AuthContextValue | null {
  return useContext(AuthContext);
}
