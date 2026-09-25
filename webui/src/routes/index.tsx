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
  bareExtensionRoutes,
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
        <div className="w-full max-w-lg bg-surface border border-line rounded-xl p-6 shadow-sm space-y-4 dark:border-line dark:bg-surface dark:shadow-xl ">
          <div>
            <h1 className="text-xl font-semibold text-fg dark:text-fg">{t('error.systemUnknownTitle')}</h1>
            <p className="text-sm text-fg-muted mt-1 dark:text-fg-secondary">{error}</p>
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            className="bg-selected text-action-fg rounded-lg px-4 py-2 font-medium hover:bg-hover dark:bg-subtle dark:hover:bg-hover"
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
      {/* Extension pages that opted out of the layout (full-page flows). */}
      {bareExtensionRoutes.map((route) => (
        <Route key={route.path} path={route.path} element={route.element} />
      ))}
      {/* Every in-app page renders inside the layout, which keeps one pane per
          open tab alive; see routes/contentRoutes.tsx for the page list. */}
      <Route path="/*" element={<Layout />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </RouterRoutes>
  );
}
