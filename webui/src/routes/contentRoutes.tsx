import { Suspense, lazy } from 'react';
import type { ComponentType, ReactNode } from 'react';
import { Navigate, useLocation, useParams, type RouteObject } from 'react-router-dom';
import LazyLoadErrorBoundary from '@/components/common/LazyLoadErrorBoundary';
import RoutePageSkeleton from '@/components/common/RoutePageSkeleton';
import Home from '@/pages/Home';
import { useAuth } from '@/contexts/AuthContext';
import { preloadI18nNamespaces } from '@/i18nResources';
import { recoverLazyLoad } from '@/utils/chunkLoadRecovery';

// All non-Home pages are code-split. Home stays eager because it's the very
// first frame after auth and we don't want a Suspense flash on initial paint.
// In particular, Session/Agent and the auth screens are kept lazy so heavy
// transitive deps (SessionChat ~2.7k LOC + react-markdown + rehype/remark +
// highlight.js) are not pulled into the main entry chunk.
type LazyPageModule = { default: ComponentType<any> };

export function lazyPage<T extends LazyPageModule>(
  loader: () => Promise<T>,
  namespaces: readonly string[] = [],
) {
  return lazy(() => recoverLazyLoad(
    Promise.all([
      loader(),
      preloadI18nNamespaces(namespaces),
    ]).then(([module]) => module),
  ));
}

const SessionPage = lazyPage(() => import('@/pages/Session'), ['session']);
const AgentPage = lazyPage(() => import('@/pages/Agent'), ['agent']);
export const LoginPage = lazyPage(() => import('@/pages/Login'));
export const SetupAdminPage = lazyPage(() => import('@/pages/SetupAdmin'));
export const ForceChangePasswordPage = lazyPage(() => import('@/pages/ForceChangePassword')); // secret-guard: allow 变量名带 Password，值是懒加载组件
const WorkflowListPage = lazyPage(() => import('@/pages/Workflow'), ['workflow']);
const WorkflowCreate = lazyPage(() => import('@/pages/WorkflowCreate'), ['workflow']);
const WorkflowEditor = lazyPage(() => import('@/pages/WorkflowEditor'), ['workflow']);
const WorkflowDetail = lazyPage(() => import('@/pages/WorkflowDetail'), ['workflow']);
const TaskPage = lazyPage(() => import('@/pages/Task'), ['task']);
const ToolPage = lazyPage(() => import('@/pages/Tool'), ['tool']);
const HubPage = lazyPage(() => import('@/pages/Hub'));
const SkillPage = lazyPage(() => import('@/pages/Skill'), ['skill']);
const ModelPage = lazyPage(() => import('@/pages/Model'), ['model']);
const ChannelPage = lazyPage(() => import('@/pages/Channel'), ['channel']);
const PermissionPage = lazyPage(() => import('@/pages/Permission'), ['permission']);
const MonitoringPage = lazyPage(() => import('@/pages/Monitoring'), ['monitoring']);
const WorkspacePage = lazyPage(() => import('@/pages/Workspace'), ['workspace']);
const DeviceIntegrationPage = lazyPage(() => import('@/pages/DeviceIntegration'), ['device']);
const FlocksproUpgradeCallbackPage = lazyPage(() => import('@/pages/FlocksproUpgrade/Callback'), ['flockspro']);
export const SettingsPage = lazyPage(() => import('@/pages/Settings'));
export const SceneSuitesPage = lazyPage(() => import('@/pages/SceneSuites'));
const WebUIContractPageHost = lazyPage(() => import('@/pages/WebUIContractPageHost'));
const WebUIContractWorkspaceHost = lazyPage(() => import('@/pages/WebUIContractWorkspaceHost'));
const ROUTE_FALLBACK_DELAY_MS = 180;

export function LazyRoute({ children }: { children: ReactNode }) {
  return (
    <LazyLoadErrorBoundary>
      <Suspense fallback={<RoutePageSkeleton delayMs={ROUTE_FALLBACK_DELAY_MS} />}>
        {children}
      </Suspense>
    </LazyLoadErrorBoundary>
  );
}

function AdminOnlyRoute({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (user?.role !== 'admin') {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}

export function LegacyWebUIContractPageRedirect() {
  const params = useParams();
  const location = useLocation();
  const pageId = params.pageId;
  const rest = params['*'];
  if (!pageId) return <Navigate to="/" replace />;
  return (
    <Navigate
      to={`/contracts/webui/${pageId}${rest ? `/${rest}` : ''}${location.search}${location.hash}`}
      replace
    />
  );
}

/**
 * Pages shown inside the main layout. The layout keeps one pane alive per open
 * tab and matches these routes against each tab's own location, so the list
 * lives here instead of being nested under the layout route.
 */
export const contentRoutes: RouteObject[] = [
  { index: true, element: <Home /> },
  { path: 'contracts/webui/workspaces/:workspaceId/:pageId?', element: <LazyRoute><WebUIContractWorkspaceHost /></LazyRoute> },
  { path: 'contracts/webui/:pageId/*', element: <LazyRoute><WebUIContractPageHost /></LazyRoute> },
  { path: 'user-defined-pages/:pageId/*', element: <LegacyWebUIContractPageRedirect /> },

  // AI 工作台
  { path: 'sessions', element: <LazyRoute><SessionPage /></LazyRoute> },
  { path: 'agents', element: <LazyRoute><AgentPage /></LazyRoute> },
  { path: 'workflows', element: <LazyRoute><WorkflowListPage /></LazyRoute> },
  { path: 'workflows/new', element: <LazyRoute><WorkflowCreate /></LazyRoute> },
  { path: 'workflows/:id', element: <LazyRoute><WorkflowDetail /></LazyRoute> },
  { path: 'workflows/:id/edit', element: <LazyRoute><WorkflowEditor /></LazyRoute> },
  { path: 'tasks', element: <LazyRoute><TaskPage /></LazyRoute> },
  { path: 'workspace', element: <LazyRoute><WorkspacePage /></LazyRoute> },

  // 设备接入
  { path: 'devices', element: <LazyRoute><DeviceIntegrationPage /></LazyRoute> },

  // Agent Smith
  { path: 'tools', element: <LazyRoute><ToolPage /></LazyRoute> },
  { path: 'hub', element: <LazyRoute><HubPage /></LazyRoute> },
  { path: 'models', element: <LazyRoute><ModelPage /></LazyRoute> },
  { path: 'skills', element: <LazyRoute><SkillPage /></LazyRoute> },
  // MCP 已整合到工具清单页面
  { path: 'mcp', element: <Navigate to="/tools" replace /> },

  // 场景工作区分区
  { path: 'scenes/suites', element: <LazyRoute><SceneSuitesPage /></LazyRoute> },
  { path: 'scenes', element: <Navigate to="/scenes/suites" replace /> },

  // 系统设置分区
  { path: 'settings', element: <LazyRoute><SettingsPage /></LazyRoute> },
  { path: 'settings/:sectionId', element: <LazyRoute><SettingsPage /></LazyRoute> },

  { path: 'config', element: <Navigate to="/settings/account" replace /> },
  { path: 'config/*', element: <Navigate to="/settings/account" replace /> },
  { path: 'system-logs', element: <Navigate to="/settings/system-logs" replace /> },
  { path: 'channels', element: <LazyRoute><ChannelPage /></LazyRoute> },
  { path: 'permissions', element: <LazyRoute><PermissionPage /></LazyRoute> },
  { path: 'monitoring', element: <LazyRoute><MonitoringPage /></LazyRoute> },
  { path: 'audit-logs', element: <Navigate to="/settings/audit-logs" replace /> },
  { path: 'admin/users', element: <Navigate to="/settings/account" replace /> },
  { path: 'flockspro-upgrade', element: <AdminOnlyRoute><Navigate to="/settings/flockspro" replace /></AdminOnlyRoute> },
  {
    path: 'flockspro-upgrade/callback',
    element: <AdminOnlyRoute><LazyRoute><FlocksproUpgradeCallbackPage /></LazyRoute></AdminOnlyRoute>,
  },
  { path: '*', element: <Navigate to="/" replace /> },
];

/** Pages that manage their own scrolling and fill the content area edge to edge. */
export const FULL_SCREEN_PATH_PATTERNS = [
  '/workflows/create',
  '/workflows/:id/edit',
  '/workflows/:id',
  '/sessions',
  '/devices',
  '/contracts/webui/*',
];
