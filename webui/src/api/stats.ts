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

type StatsStatus = SystemStats['system']['status'];

const LEGACY_RESOURCE_ENDPOINT_COUNT = 6;

function shouldCountForAgentPage(agent: any): boolean {
  if (!agent || typeof agent !== 'object') return false;
  if (agent.mode === 'primary') return true;
  return !Array.isArray(agent.tags) || !agent.tags.includes('system');
}

function isSystemStats(value: any): value is SystemStats {
  return Boolean(
    value &&
    typeof value === 'object' &&
    value.tasks &&
    value.agents &&
    value.workflows &&
    value.skills &&
    value.tools &&
    value.models &&
    value.system
  );
}

type StatsEndpointFailure = {
  endpoint: string;
  error: unknown;
};

type StatsFailureCode =
  | 'authExpired'
  | 'forbidden'
  | 'notFound'
  | 'partial'
  | 'network'
  | 'unavailable';

function getErrorStatus(error: unknown): number | undefined {
  return (error as { response?: { status?: number } } | undefined)?.response?.status;
}

function classifyStatsFailure(failures: StatsEndpointFailure[]): StatsFailureCode {
  if (failures.some((failure) => getErrorStatus(failure.error) === 401)) {
    return 'authExpired';
  }

  if (failures.some((failure) => getErrorStatus(failure.error) === 403)) {
    return 'forbidden';
  }

  if (failures.some((failure) => getErrorStatus(failure.error) === 404)) {
    return 'notFound';
  }

  if (failures.length > 0) {
    return 'network';
  }

  return 'unavailable';
}

function emptyStats(status: StatsStatus, message: StatsFailureCode): SystemStats {
  return {
    tasks: { week: 0, scheduledActive: 0 },
    agents: { total: 0 },
    workflows: { total: 0 },
    skills: { total: 0 },
    tools: { total: 0 },
    models: { total: 0 },
    system: { status, message },
  };
}

async function getSystemStatsLegacy(): Promise<SystemStats> {
  const resourceFailures: StatsEndpointFailure[] = [];
  const healthFailures: StatsEndpointFailure[] = [];
  const getWithFallback = async (endpoint: string, fallbackData: unknown, failures: StatsEndpointFailure[] = resourceFailures) => {
    try {
      return await apiClient.get(endpoint);
    } catch (error) {
      failures.push({ endpoint, error });
      return { data: fallbackData };
    }
  };

  const [taskDash, agents, workflows, skills, tools, providers, health] = await Promise.all([
    getWithFallback('/api/task-system/dashboard', {}),
    getWithFallback('/api/agent', []),
    getWithFallback('/api/workflow', []),
    getWithFallback('/api/skills', []),
    getWithFallback('/api/tools', []),
    getWithFallback('/api/provider', { all: [] }),
    getWithFallback('/api/health', { status: 'error' }, healthFailures),
  ]);

  const dash = taskDash.data || {};
  const agentList = (Array.isArray(agents.data) ? agents.data : []).filter(shouldCountForAgentPage);
  const workflowList = Array.isArray(workflows.data) ? workflows.data : [];
  // Exclude `system` category skills so the count matches the Skills page,
  // which hides system skills (e.g. onboarding) from the user.
  const skillList = (Array.isArray(skills.data) ? skills.data : []).filter(
    (s: any) => s?.category !== 'system'
  );
  const toolList = Array.isArray(tools.data) ? tools.data : [];
  const providerData = providers.data ?? {};
  const providerAll: any[] = providerData.all ?? (Array.isArray(providers.data) ? providers.data : []);
  const connectedSet = new Set<string>(providerData.connected ?? []);
  const totalModels = providerAll
    .filter((p: any) => connectedSet.has(p.id))
    .reduce((sum: number, p: any) => sum + Object.keys(p.models ?? {}).length, 0);
  const allResourceEndpointsFailed = resourceFailures.length === LEGACY_RESOURCE_ENDPOINT_COUNT;
  const hasPartialResourceFailure = resourceFailures.length > 0 && !allResourceEndpointsFailed;
  const healthIsHealthy = health.data.status === 'healthy';
  const systemStatus: StatsStatus = !healthIsHealthy || allResourceEndpointsFailed
    ? 'error'
    : hasPartialResourceFailure
      ? 'warning'
      : 'healthy';
  const systemMessage: string = systemStatus === 'healthy'
    ? 'healthy'
    : hasPartialResourceFailure
      ? 'partial'
      : classifyStatsFailure(allResourceEndpointsFailed ? resourceFailures : healthFailures);

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
      status: systemStatus,
      message: systemMessage,
    },
  };
}

export const statsApi = {
  getSystemStats: async (): Promise<SystemStats> => {
    try {
      const response = await apiClient.get('/api/stats/summary');
      if (isSystemStats(response.data)) {
        return response.data;
      }
      return await getSystemStatsLegacy();
    } catch (error) {
      const summaryStatus = getErrorStatus(error);
      if (summaryStatus === 401 || summaryStatus === 403) {
        return emptyStats('error', classifyStatsFailure([{ endpoint: '/api/stats/summary', error }]));
      }

      try {
        return await getSystemStatsLegacy();
      } catch (fallbackError) {
        console.error('Failed to fetch system stats:', fallbackError || error);
        return emptyStats('error', classifyStatsFailure([{ endpoint: '/api/stats/summary', error: fallbackError || error }]));
      }
    }
  },
};
