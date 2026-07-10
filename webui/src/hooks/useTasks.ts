import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  taskAPI,
  TaskExecution,
  TaskListParams,
  TaskScheduler,
  SchedulerListParams,
  DashboardCounts,
  QueueStatus,
  TaskSystemNotice,
  PaginatedResponse,
} from '@/api/task';
import { createSharedResource, useSharedResource, type SharedResource } from './useSharedResource';

const ACTIVE_EXECUTION_STATUSES = new Set(['pending', 'queued', 'running']);
const ACTIVE_SCHEDULER_STATUSES = new Set(['active']);
const TASK_LIST_STALE_TIME_MS = 1000;
const TASK_LIST_MIN_FETCH_INTERVAL_MS = 1000;
const MAX_TASK_LIST_RESOURCES = 80;

type TaskPageData<T> = Pick<PaginatedResponse<T>, 'items' | 'total'>;

const EMPTY_SCHEDULERS: TaskPageData<TaskScheduler> = { items: [], total: 0 };
const EMPTY_EXECUTIONS: TaskPageData<TaskExecution> = { items: [], total: 0 };

const schedulerListResources = new Map<string, SharedResource<TaskPageData<TaskScheduler>>>();
const executionListResources = new Map<string, SharedResource<TaskPageData<TaskExecution>>>();
const schedulerExecutionResources = new Map<string, SharedResource<TaskPageData<TaskExecution>>>();

const taskDashboardResource = createSharedResource<DashboardCounts | null>({
  initialData: null,
  staleTimeMs: TASK_LIST_STALE_TIME_MS,
  minFetchIntervalMs: TASK_LIST_MIN_FETCH_INTERVAL_MS,
  fetcher: async () => {
    const response = await taskAPI.dashboard();
    return response.data;
  },
  getErrorMessage: (err) => (err instanceof Error && err.message ? err.message : 'Failed to fetch dashboard'),
});

const queueStatusResource = createSharedResource<QueueStatus | null>({
  initialData: null,
  staleTimeMs: TASK_LIST_STALE_TIME_MS,
  minFetchIntervalMs: TASK_LIST_MIN_FETCH_INTERVAL_MS,
  fetcher: async () => {
    const response = await taskAPI.queueStatus();
    return response.data;
  },
  getErrorMessage: (err) => (err instanceof Error && err.message ? err.message : 'Failed to fetch queue status'),
});

const taskSystemNoticeResource = createSharedResource<TaskSystemNotice | null>({
  initialData: null,
  staleTimeMs: 10000,
  minFetchIntervalMs: TASK_LIST_MIN_FETCH_INTERVAL_MS,
  fetcher: async () => {
    const response = await taskAPI.getSystemNotice();
    return response.data ?? null;
  },
  getErrorMessage: (err) => (err instanceof Error && err.message ? err.message : 'Failed to fetch system notice'),
});

function normalizeSchedulerFilters(filters?: SchedulerListParams): SchedulerListParams {
  return {
    status: filters?.status,
    priority: filters?.priority,
    scheduledOnly: filters?.scheduledOnly,
    sortBy: filters?.sortBy,
    sortOrder: filters?.sortOrder,
    offset: filters?.offset,
    limit: filters?.limit,
  };
}

function normalizeExecutionFilters(filters?: TaskListParams & { schedulerID?: string }): TaskListParams & { schedulerID?: string } {
  return {
    status: filters?.status,
    priority: filters?.priority,
    deliveryStatus: filters?.deliveryStatus,
    schedulerID: filters?.schedulerID,
    sortBy: filters?.sortBy,
    sortOrder: filters?.sortOrder,
    offset: filters?.offset,
    limit: filters?.limit,
  };
}

function makeResourceKey(params: object): string {
  return JSON.stringify(params);
}

function getCachedResource<T>(
  resources: Map<string, SharedResource<T>>,
  key: string,
): SharedResource<T> | undefined {
  const existing = resources.get(key);
  if (existing) {
    resources.delete(key);
    resources.set(key, existing);
  }
  return existing;
}

function cacheResource<T>(
  resources: Map<string, SharedResource<T>>,
  key: string,
  resource: SharedResource<T>,
): void {
  resources.set(key, resource);
  if (resources.size > MAX_TASK_LIST_RESOURCES) {
    const oldestKey = resources.keys().next().value;
    if (oldestKey) resources.delete(oldestKey);
  }
}

function getSchedulerListResource(filters?: SchedulerListParams): SharedResource<TaskPageData<TaskScheduler>> {
  const params = normalizeSchedulerFilters(filters);
  const key = makeResourceKey(params);
  const existing = getCachedResource(schedulerListResources, key);
  if (existing) return existing;

  const resource = createSharedResource<TaskPageData<TaskScheduler>>({
    initialData: EMPTY_SCHEDULERS,
    staleTimeMs: TASK_LIST_STALE_TIME_MS,
    minFetchIntervalMs: TASK_LIST_MIN_FETCH_INTERVAL_MS,
    fetcher: async () => {
      const response = await taskAPI.listSchedulers(params);
      return {
        items: response.data.items ?? [],
        total: response.data.total ?? 0,
      };
    },
    fallbackDataOnError: EMPTY_SCHEDULERS,
    getErrorMessage: (err) => (err instanceof Error && err.message ? err.message : 'Failed to fetch tasks'),
  });

  cacheResource(schedulerListResources, key, resource);
  return resource;
}

function getExecutionListResource(filters?: TaskListParams & { schedulerID?: string }): SharedResource<TaskPageData<TaskExecution>> {
  const params = normalizeExecutionFilters(filters);
  const key = makeResourceKey(params);
  const existing = getCachedResource(executionListResources, key);
  if (existing) return existing;

  const resource = createSharedResource<TaskPageData<TaskExecution>>({
    initialData: EMPTY_EXECUTIONS,
    staleTimeMs: TASK_LIST_STALE_TIME_MS,
    minFetchIntervalMs: TASK_LIST_MIN_FETCH_INTERVAL_MS,
    fetcher: async () => {
      const response = await taskAPI.listExecutions(params);
      return {
        items: response.data.items ?? [],
        total: response.data.total ?? 0,
      };
    },
    fallbackDataOnError: EMPTY_EXECUTIONS,
    getErrorMessage: (err) => (err instanceof Error && err.message ? err.message : 'Failed to fetch task executions'),
  });

  cacheResource(executionListResources, key, resource);
  return resource;
}

function getSchedulerExecutionResource(
  schedulerId: string,
  params?: { offset?: number; limit?: number },
): SharedResource<TaskPageData<TaskExecution>> {
  const normalizedParams = {
    schedulerId,
    offset: params?.offset,
    limit: params?.limit,
  };
  const key = makeResourceKey(normalizedParams);
  const existing = getCachedResource(schedulerExecutionResources, key);
  if (existing) return existing;

  const resource = createSharedResource<TaskPageData<TaskExecution>>({
    initialData: EMPTY_EXECUTIONS,
    staleTimeMs: TASK_LIST_STALE_TIME_MS,
    minFetchIntervalMs: TASK_LIST_MIN_FETCH_INTERVAL_MS,
    fetcher: async () => {
      const response = await taskAPI.listSchedulerExecutions(schedulerId, {
        offset: params?.offset,
        limit: params?.limit,
      });
      return {
        items: response.data.items ?? [],
        total: response.data.total ?? 0,
      };
    },
    fallbackDataOnError: EMPTY_EXECUTIONS,
    getErrorMessage: (err) => (err instanceof Error && err.message ? err.message : 'Failed to fetch executions'),
  });

  cacheResource(schedulerExecutionResources, key, resource);
  return resource;
}

function usePollSharedResource<T>(
  resource: SharedResource<T>,
  pollInterval?: number,
) {
  useEffect(() => {
    if (!pollInterval) return;
    const id = window.setInterval(() => {
      void resource.fetch({ silent: true });
    }, pollInterval);
    return () => window.clearInterval(id);
  }, [pollInterval, resource]);
}

function useAdaptiveTaskPolling<T extends { status: string }>(
  resource: SharedResource<TaskPageData<T>>,
  items: T[],
  activeStatuses: Set<string>,
  pollInterval?: number,
) {
  const itemsRef = useRef(items);

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  useEffect(() => {
    if (!pollInterval) return;

    const scheduleDelay = () => {
      const hasActive = itemsRef.current.some((task) => activeStatuses.has(task.status));
      return hasActive ? Math.min(pollInterval, 4000) : pollInterval;
    };

    let timerId: ReturnType<typeof setTimeout>;
    const tick = async () => {
      await resource.fetch({ silent: true });
      timerId = window.setTimeout(tick, scheduleDelay());
    };

    timerId = window.setTimeout(tick, scheduleDelay());
    return () => window.clearTimeout(timerId);
  }, [activeStatuses, pollInterval, resource]);
}

export function __resetTaskResourcesForTesting(): void {
  schedulerListResources.forEach((resource) => resource.resetForTesting());
  executionListResources.forEach((resource) => resource.resetForTesting());
  schedulerExecutionResources.forEach((resource) => resource.resetForTesting());
  schedulerListResources.clear();
  executionListResources.clear();
  schedulerExecutionResources.clear();
  taskDashboardResource.resetForTesting();
  queueStatusResource.resetForTesting();
  taskSystemNoticeResource.resetForTesting();
}

export function __getTaskResourceCacheSizesForTesting() {
  return {
    schedulers: schedulerListResources.size,
    executions: executionListResources.size,
    schedulerExecutions: schedulerExecutionResources.size,
  };
}

export function useTaskSchedulers(
  filters?: SchedulerListParams,
  options?: { pollInterval?: number },
) {
  const resource = useMemo(
    () => getSchedulerListResource(filters),
    [
      filters?.status,
      filters?.priority,
      filters?.scheduledOnly,
      filters?.sortBy,
      filters?.sortOrder,
      filters?.offset,
      filters?.limit,
    ],
  );
  const { data, loading, error, refetch } = useSharedResource(resource);
  useAdaptiveTaskPolling(resource, data.items, ACTIVE_SCHEDULER_STATUSES, options?.pollInterval);

  return { tasks: data.items, total: data.total, loading, error, refetch };
}

export function useTaskExecutions(
  filters?: TaskListParams & { schedulerID?: string },
  options?: { pollInterval?: number },
) {
  const resource = useMemo(
    () => getExecutionListResource(filters),
    [
      filters?.status,
      filters?.priority,
      filters?.deliveryStatus,
      filters?.schedulerID,
      filters?.sortBy,
      filters?.sortOrder,
      filters?.offset,
      filters?.limit,
    ],
  );
  const { data, loading, error, refetch } = useSharedResource(resource);
  useAdaptiveTaskPolling(resource, data.items, ACTIVE_EXECUTION_STATUSES, options?.pollInterval);

  return { tasks: data.items, total: data.total, loading, error, refetch };
}

export function useTaskScheduler(schedulerId?: string) {
  const [task, setTask] = useState<TaskScheduler | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTask = useCallback(async () => {
    if (!schedulerId) return;
    try {
      setLoading(true);
      setError(null);
      const response = await taskAPI.getScheduler(schedulerId);
      setTask(response.data);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch task scheduler');
    } finally {
      setLoading(false);
    }
  }, [schedulerId]);

  useEffect(() => {
    fetchTask();
  }, [fetchTask]);

  return { task, loading, error, refetch: fetchTask };
}

export function useTaskDashboard(options?: { pollInterval?: number }) {
  const {
    data: counts,
    loading,
    error,
    refetch,
  } = useSharedResource(taskDashboardResource);
  usePollSharedResource(taskDashboardResource, options?.pollInterval);

  return { counts, loading, error, refetch };
}

export function useTaskExecutionsByScheduler(schedulerId?: string, params?: { offset?: number; limit?: number }) {
  const resource = useMemo(
    () => (schedulerId ? getSchedulerExecutionResource(schedulerId, params) : null),
    [schedulerId, params?.offset, params?.limit],
  );
  const {
    data,
    loading,
    error,
    refetch,
  } = useSharedResource(resource ?? getSchedulerExecutionResource('__disabled__'), {
    enabled: !!resource,
    loadOnMount: !!resource,
  });

  return {
    records: resource ? data.items : [],
    total: resource ? data.total : 0,
    loading: resource ? loading : false,
    error: resource ? error : null,
    refetch: resource ? refetch : async () => EMPTY_EXECUTIONS,
  };
}

export function useQueueStatus(options?: { pollInterval?: number }) {
  const {
    data: queueStatus,
    loading,
    error,
    refetch,
  } = useSharedResource(queueStatusResource);
  usePollSharedResource(queueStatusResource, options?.pollInterval);

  return { queueStatus, loading, error, refetch };
}

export function useTaskSystemNotice() {
  const {
    data: notice,
    loading,
    error,
    refetch,
  } = useSharedResource(taskSystemNoticeResource);

  return { notice, loading, error, refetch };
}
