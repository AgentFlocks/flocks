import { Suspense } from 'react';
import { Routes as RouterRoutes, Route, Navigate, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { safeReturnTo } from '@/utils/sessionUrl';
import Layout from '@/components/layout/Layout';
import LazyLoadErrorBoundary from '@/components/common/LazyLoadErrorBoundary';
import RoutePageSkeleton from '@/components/common/RoutePageSkeleton';
import AuthLayout from '@/components/layout/AuthLayout';
import { useAuth } from '@/contexts/AuthContext';
import { installVitePreloadErrorRecovery } from '@/utils/chunkLoadRecovery';
import {
  ForceChangePasswordPage,
  LoginPage,
  SetupAdminPage,
} from './contentRoutes';

export { LegacyWebUIContractPageRedirect } from './contentRoutes';

installVitePreloadErrorRecovery();

// After sign-in / setup, land on the page the user was heading to (validated
// by safeReturnTo so a crafted returnTo cannot leave the app).
export function LoginRedirect({ setup = false }: { setup?: boolean }) {
  const location = useLocation();
  const returnTo = safeReturnTo(`${location.pathname}${location.search}${location.hash}`);
  return <Navigate to={`${setup ? '/setup-admin' : '/login'}?${new URLSearchParams({ returnTo })}`} replace />;
}

export function AuthReturnRedirect() {
  const location = useLocation();
  return <Navigate to={safeReturnTo(new URLSearchParams(location.search).get('returnTo'))} replace />;
}

export function Routes() {
  const { t } = useTranslation('auth');
  const { loading, bootstrapped, error, user, refresh } = useAuth();

  if (loading) {
    return <RoutePageSkeleton />;
  }

  if (error) {
    return (
      <AuthLayout>
        <div className="w-full max-w-lg bg-white border border-gray-200 rounded-xl p-6 shadow-sm space-y-4 dark:border-[#4a5563] dark:bg-[#303842] dark:shadow-xl dark:shadow-black/20">
          <div>
            <h1 className="text-xl font-semibold text-gray-900 dark:text-[#d7dee8]">{t('error.systemUnknownTitle')}</h1>
            <p className="text-sm text-gray-500 mt-1 dark:text-[#b8c2cc]">{error}</p>
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            className="bg-slate-900 text-white rounded-lg px-4 py-2 font-medium hover:bg-slate-800 dark:bg-[#46515e] dark:hover:bg-[#5a6573]"
          >
            {t('error.retry')}
          </button>
        </div>
      </AuthLayout>
    );
  }

  if (!bootstrapped) {
    return (
      <LazyLoadErrorBoundary>
        <Suspense fallback={<RoutePageSkeleton />}>
          <RouterRoutes>
            <Route path="/setup-admin" element={<SetupAdminPage />} />
            <Route path="*" element={<LoginRedirect setup />} />
          </RouterRoutes>
        </Suspense>
      </LazyLoadErrorBoundary>
    );
  }

  if (!user) {
    return (
      <LazyLoadErrorBoundary>
        <Suspense fallback={<RoutePageSkeleton />}>
          <RouterRoutes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="*" element={<LoginRedirect />} />
          </RouterRoutes>
        </Suspense>
      </LazyLoadErrorBoundary>
    );
  }

  if (user.must_reset_password) {
    return (
      <LazyLoadErrorBoundary>
        <Suspense fallback={<RoutePageSkeleton />}>
          <ForceChangePasswordPage />
        </Suspense>
      </LazyLoadErrorBoundary>
    );
  }

  return (
    <RouterRoutes>
      <Route path="/login" element={<AuthReturnRedirect />} />
      <Route path="/setup-admin" element={<AuthReturnRedirect />} />
      {/* Every in-app page renders inside the layout, which keeps one pane per
          open tab alive; see routes/contentRoutes.tsx for the page list. */}
      <Route path="/*" element={<Layout />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </RouterRoutes>
  );
}
