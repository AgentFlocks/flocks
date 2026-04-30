import { Suspense, lazy } from 'react';
import { Routes as RouterRoutes, Route, Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import Layout from '@/components/layout/Layout';
import RoutePageSkeleton from '@/components/common/RoutePageSkeleton';
import AuthLayout from '@/components/layout/AuthLayout';
import Home from '@/pages/Home';
import SessionPage from '@/pages/Session';
import AgentPage from '@/pages/Agent';
import LoginPage from '@/pages/Login';
import SetupAdminPage from '@/pages/SetupAdmin';
import ForceChangePasswordPage from '@/pages/ForceChangePassword';
import { useAuth } from '@/contexts/AuthContext';

const WorkflowListPage = lazy(() => import('@/pages/Workflow'));
const WorkflowCreate = lazy(() => import('@/pages/WorkflowCreate'));
const WorkflowEditor = lazy(() => import('@/pages/WorkflowEditor'));
const WorkflowDetail = lazy(() => import('@/pages/WorkflowDetail'));
const TaskPage = lazy(() => import('@/pages/Task'));
const ToolPage = lazy(() => import('@/pages/Tool'));
const HubPage = lazy(() => import('@/pages/Hub'));
const ModelPage = lazy(() => import('@/pages/Model'));
const SkillPage = lazy(() => import('@/pages/Skill'));
const ConfigPage = lazy(() => import('@/pages/Config'));
const ChannelPage = lazy(() => import('@/pages/Channel'));
const PermissionPage = lazy(() => import('@/pages/Permission'));
const MonitoringPage = lazy(() => import('@/pages/Monitoring'));
const WorkspacePage = lazy(() => import('@/pages/Workspace'));

function LazyRoute({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<RoutePageSkeleton />}>
      {children}
    </Suspense>
  );
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
        <div className="w-full max-w-lg bg-white border border-gray-200 rounded-xl p-6 shadow-sm space-y-4">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">{t('error.systemUnknownTitle')}</h1>
            <p className="text-sm text-gray-500 mt-1">{error}</p>
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            className="bg-slate-900 text-white rounded-lg px-4 py-2 font-medium hover:bg-slate-800"
          >
            {t('error.retry')}
          </button>
        </div>
      </AuthLayout>
    );
  }

  if (!bootstrapped) {
    return (
      <RouterRoutes>
        <Route path="/setup-admin" element={<SetupAdminPage />} />
        <Route path="*" element={<Navigate to="/setup-admin" replace />} />
      </RouterRoutes>
    );
  }

  if (!user) {
    return (
      <RouterRoutes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </RouterRoutes>
    );
  }

  if (user.must_reset_password) {
    return <ForceChangePasswordPage />;
  }

  return (
    <RouterRoutes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route path="/setup-admin" element={<Navigate to="/" replace />} />
      <Route path="/" element={<Layout />}>
        <Route index element={<Home />} />
        
        {/* AI 工作台 */}
        <Route path="sessions" element={<SessionPage />} />
        <Route path="agents" element={<AgentPage />} />
        <Route path="workflows" element={<LazyRoute><WorkflowListPage /></LazyRoute>} />
        <Route path="workflows/new" element={<LazyRoute><WorkflowCreate /></LazyRoute>} />
        <Route path="workflows/:id" element={<LazyRoute><WorkflowDetail /></LazyRoute>} />
        <Route path="workflows/:id/edit" element={<LazyRoute><WorkflowEditor /></LazyRoute>} />
        <Route path="tasks" element={<LazyRoute><TaskPage /></LazyRoute>} />
        <Route path="workspace" element={<LazyRoute><WorkspacePage /></LazyRoute>} />
        
        {/* Agent Smith */}
        <Route path="tools" element={<LazyRoute><ToolPage /></LazyRoute>} />
        <Route path="hub" element={<LazyRoute><HubPage /></LazyRoute>} />
        <Route path="models" element={<LazyRoute><ModelPage /></LazyRoute>} />
        <Route path="skills" element={<LazyRoute><SkillPage /></LazyRoute>} />
        {/* MCP 已整合到工具清单页面 */}
        <Route path="mcp" element={<Navigate to="/tools" replace />} />
        
        {/* 账号管理 */}
        <Route path="config" element={<LazyRoute><ConfigPage /></LazyRoute>} />
        <Route path="config/*" element={<Navigate to="/config" replace />} />
        <Route path="channels" element={<LazyRoute><ChannelPage /></LazyRoute>} />
        <Route path="permissions" element={<LazyRoute><PermissionPage /></LazyRoute>} />
        <Route path="monitoring" element={<LazyRoute><MonitoringPage /></LazyRoute>} />
        <Route path="admin/users" element={<Navigate to="/config" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </RouterRoutes>
  );
}
