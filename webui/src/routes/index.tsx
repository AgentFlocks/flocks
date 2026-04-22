import { Suspense, lazy } from 'react';
import { Routes as RouterRoutes, Route, Navigate } from 'react-router-dom';
import Layout from '@/components/layout/Layout';
import RoutePageSkeleton from '@/components/common/RoutePageSkeleton';
import Home from '@/pages/Home';
import SessionPage from '@/pages/Session';
import AgentPage from '@/pages/Agent';

const WorkflowListPage = lazy(() => import('@/pages/Workflow'));
const WorkflowCreate = lazy(() => import('@/pages/WorkflowCreate'));
const WorkflowEditor = lazy(() => import('@/pages/WorkflowEditor'));
const WorkflowDetail = lazy(() => import('@/pages/WorkflowDetail'));
const TaskPage = lazy(() => import('@/pages/Task'));
const ToolPage = lazy(() => import('@/pages/Tool'));
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
  return (
    <RouterRoutes>
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
        <Route path="models" element={<LazyRoute><ModelPage /></LazyRoute>} />
        <Route path="skills" element={<LazyRoute><SkillPage /></LazyRoute>} />
        {/* MCP 已整合到工具清单页面 */}
        <Route path="mcp" element={<Navigate to="/tools" replace />} />
        
        {/* 系统管理 */}
        <Route path="config" element={<LazyRoute><ConfigPage /></LazyRoute>} />
        <Route path="channels" element={<LazyRoute><ChannelPage /></LazyRoute>} />
        <Route path="permissions" element={<LazyRoute><PermissionPage /></LazyRoute>} />
        <Route path="monitoring" element={<LazyRoute><MonitoringPage /></LazyRoute>} />
      </Route>
    </RouterRoutes>
  );
}
