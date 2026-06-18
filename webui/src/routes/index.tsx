import { Suspense, lazy } from 'react';
import { Routes as RouterRoutes, Route, Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import Layout from '@/components/layout/Layout';
import RoutePageSkeleton from '@/components/common/RoutePageSkeleton';
import AuthLayout from '@/components/layout/AuthLayout';
import Home from '@/pages/Home';
import { useAuth } from '@/contexts/AuthContext';

// All non-Home pages are code-split. Home stays eager because it's the very
// first frame after auth and we don't want a Suspense flash on initial paint.
// In particular, Session/Agent and the auth screens are kept lazy so heavy
// transitive deps (SessionChat ~2.7k LOC + react-markdown + rehype/remark +
// highlight.js) are not pulled into the main entry chunk.
const SessionPage = lazy(() => import('@/pages/Session'));
const AgentPage = lazy(() => import('@/pages/Agent'));
const LoginPage = lazy(() => import('@/pages/Login'));
const SetupAdminPage = lazy(() => import('@/pages/SetupAdmin'));
const ForceChangePasswordPage = lazy(() => import('@/pages/ForceChangePassword'));
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
const AuditLogsPage = lazy(() => import('@/pages/AuditLogs'));
const WorkspacePage = lazy(() => import('@/pages/Workspace'));
const DeviceIntegrationPage = lazy(() => import('@/pages/DeviceIntegration'));
const SystemLogPage = lazy(() => import('@/pages/SystemLog'));
const FlocksproUpgradePage = lazy(() => import('@/pages/FlocksproUpgrade'));
const FlocksproUpgradeCallbackPage = lazy(() => import('@/pages/FlocksproUpgrade/Callback'));
const UserDefinedPageHost = lazy(() => import('@/pages/UserDefinedPageHost'));
const SocOverviewPage = lazy(() => import('@/pages/Soc'));
const SocAlertsPage = lazy(() => import('@/pages/Soc/Alerts'));
const SocAssetsPage = lazy(() => import('@/pages/Soc/Assets'));
const SocIntelPage = lazy(() => import('@/pages/Soc/Intel'));
const SocVulnerabilitiesPage = lazy(() => import('@/pages/Soc/Vulnerabilities'));
const SocDrillsPage = lazy(() => import('@/pages/Soc/Drills'));
const SocAttackSurfacePage = lazy(() => import('@/pages/Soc/AttackSurface'));

function LazyRoute({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<RoutePageSkeleton />}>
      {children}
    </Suspense>
  );
}

function AdminOnlyRoute({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  if (user?.role !== 'admin') {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
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
      <Suspense fallback={<RoutePageSkeleton />}>
        <RouterRoutes>
          <Route path="/setup-admin" element={<SetupAdminPage />} />
          <Route path="*" element={<Navigate to="/setup-admin" replace />} />
        </RouterRoutes>
      </Suspense>
    );
  }

  if (!user) {
    return (
      <Suspense fallback={<RoutePageSkeleton />}>
        <RouterRoutes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="*" element={<Navigate to="/login" replace />} />
        </RouterRoutes>
      </Suspense>
    );
  }

  if (user.must_reset_password) {
    return (
      <Suspense fallback={<RoutePageSkeleton />}>
        <ForceChangePasswordPage />
      </Suspense>
    );
  }

  return (
    <RouterRoutes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route path="/setup-admin" element={<Navigate to="/" replace />} />
      <Route path="/" element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="user-defined-pages/:pageId/*" element={<LazyRoute><UserDefinedPageHost /></LazyRoute>} />
        <Route path="soc/pages/:pageId/*" element={<LazyRoute><UserDefinedPageHost /></LazyRoute>} />

        {/* AI 工作台 */}
        <Route path="sessions" element={<LazyRoute><SessionPage /></LazyRoute>} />
        <Route path="agents" element={<LazyRoute><AgentPage /></LazyRoute>} />
        <Route path="workflows" element={<LazyRoute><WorkflowListPage /></LazyRoute>} />
        <Route path="workflows/new" element={<LazyRoute><WorkflowCreate /></LazyRoute>} />
        <Route path="workflows/:id" element={<LazyRoute><WorkflowDetail /></LazyRoute>} />
        <Route path="workflows/:id/edit" element={<LazyRoute><WorkflowEditor /></LazyRoute>} />
        <Route path="tasks" element={<LazyRoute><TaskPage /></LazyRoute>} />
        <Route path="workspace" element={<LazyRoute><WorkspacePage /></LazyRoute>} />

        {/* 设备接入 */}
        <Route path="devices" element={<LazyRoute><DeviceIntegrationPage /></LazyRoute>} />

        {/* SOC 工作区 */}
        <Route path="soc" element={<LazyRoute><SocOverviewPage /></LazyRoute>} />
        <Route path="soc/alerts" element={<LazyRoute><SocAlertsPage /></LazyRoute>} />
        <Route path="soc/alerts/:incidentId" element={<LazyRoute><SocAlertsPage /></LazyRoute>} />
        <Route path="soc/inspections" element={<Navigate to="/soc/assets" replace />} />
        <Route path="soc/assets" element={<LazyRoute><SocAssetsPage /></LazyRoute>} />
        <Route path="soc/intel" element={<LazyRoute><SocIntelPage /></LazyRoute>} />
        <Route path="soc/vulnerabilities" element={<LazyRoute><SocVulnerabilitiesPage /></LazyRoute>} />
        <Route path="soc/drills" element={<LazyRoute><SocDrillsPage /></LazyRoute>} />
        <Route path="soc/attack-surface" element={<LazyRoute><SocAttackSurfacePage /></LazyRoute>} />
        <Route path="soc/cases" element={<Navigate to="/soc/alerts" replace />} />
        <Route path="soc/cases/:caseId" element={<Navigate to="/soc/alerts" replace />} />
        <Route path="soc/reports" element={<Navigate to="/soc/alerts" replace />} />

        {/* Agent Smith */}
        <Route path="tools" element={<LazyRoute><ToolPage /></LazyRoute>} />
        <Route path="hub" element={<LazyRoute><HubPage /></LazyRoute>} />
        <Route path="models" element={<LazyRoute><ModelPage /></LazyRoute>} />
        <Route path="skills" element={<LazyRoute><SkillPage /></LazyRoute>} />
        {/* MCP 已整合到工具清单页面 */}
        <Route path="mcp" element={<Navigate to="/tools" replace />} />

        {/* 系统中心 */}
        <Route path="config" element={<LazyRoute><ConfigPage /></LazyRoute>} />
        <Route path="config/*" element={<Navigate to="/config" replace />} />
        <Route path="system-logs" element={<LazyRoute><SystemLogPage /></LazyRoute>} />
        <Route path="channels" element={<LazyRoute><ChannelPage /></LazyRoute>} />
        <Route path="permissions" element={<Navigate to="/config" replace />} />
        <Route path="monitoring" element={<Navigate to="/config" replace />} />
        <Route path="audit-logs" element={<LazyRoute><AuditLogsPage /></LazyRoute>} />
        <Route path="admin/users" element={<Navigate to="/config" replace />} />
        <Route
          path="flockspro-upgrade"
          element={<AdminOnlyRoute><LazyRoute><FlocksproUpgradePage /></LazyRoute></AdminOnlyRoute>}
        />
        <Route
          path="flockspro-upgrade/callback"
          element={<AdminOnlyRoute><LazyRoute><FlocksproUpgradeCallbackPage /></LazyRoute></AdminOnlyRoute>}
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </RouterRoutes>
  );
}
