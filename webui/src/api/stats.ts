import { apiClient } from './client';

export interface SystemStats {
  tasks: {
    week: number;        // 7日任务数（completed + failed）
    scheduledActive: number;  // 启动的计划任务数
  };
  agents: {
    total: number;
  };
  workflows: {
    total: number;
  };
  skills: {
    total: number;
  };
  tools: {
    total: number;
  };
  models: {
    total: number;
  };
  system: {
    status: 'healthy' | 'warning' | 'error';
    message: string;
  };
}

export const statsApi = {
  getSystemStats: async (): Promise<SystemStats> => {
    try {
      const [taskDash, agents, workflows, skills, tools, providers, health] = await Promise.all([
        apiClient.get('/api/task-system/dashboard').catch(() => ({ data: {} })),
        apiClient.get('/api/agent').catch(() => ({ data: [] })),
        apiClient.get('/api/workflow').catch(() => ({ data: [] })),
        apiClient.get('/api/skills').catch(() => ({ data: [] })),
        apiClient.get('/api/tools').catch(() => ({ data: [] })),
        apiClient.get('/api/provider').catch(() => ({ data: { all: [] } })),
        apiClient.get('/api/health').catch(() => ({ data: { status: 'error' } })),
      ]);

      const dash = taskDash.data || {};
      const agentList = Array.isArray(agents.data) ? agents.data : [];
      const workflowList = Array.isArray(workflows.data) ? workflows.data : [];
      const skillList = Array.isArray(skills.data) ? skills.data : [];
      const toolList = Array.isArray(tools.data) ? tools.data : [];
      const providerData = providers.data ?? {};
      const providerAll: any[] = providerData.all ?? (Array.isArray(providers.data) ? providers.data : []);
      const connectedSet = new Set<string>(providerData.connected ?? []);
      const totalModels = providerAll
        .filter((p: any) => connectedSet.has(p.id))
        .reduce((sum: number, p: any) => sum + Object.keys(p.models ?? {}).length, 0);

      return {
        tasks: {
          week: (dash.completed_week ?? 0) + (dash.failed_week ?? 0),
          scheduledActive: dash.scheduled_active ?? 0,
        },
        agents: { total: agentList.length },
        workflows: { total: workflowList.length },
        skills: { total: skillList.length },
        tools: { total: toolList.length },
        models: { total: totalModels },
        system: {
          status: health.data.status === 'healthy' ? 'healthy' : 'error',
          message: health.data.status === 'healthy' ? '所有服务运行正常' : '部分服务异常',
        },
      };
    } catch (error) {
      console.error('Failed to fetch system stats:', error);
      return {
        tasks: { week: 0, scheduledActive: 0 },
        agents: { total: 0 },
        workflows: { total: 0 },
        skills: { total: 0 },
        tools: { total: 0 },
        models: { total: 0 },
        system: { status: 'error', message: '无法连接到后端服务' },
      };
    }
  },
};
