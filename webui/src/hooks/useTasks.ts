import { useState, useEffect, useRef, useCallback } from 'react';
import {
  taskAPI,
  TaskExecution,
  TaskListParams,
  TaskScheduler,
  SchedulerListParams,
  DashboardCounts,
  QueueStatus,
  TaskSystemNotice,
} from '@/api/task';

const ACTIVE_EXECUTION_STATUSES = new Set(['pending', 'queued', 'running']);
const ACTIVE_SCHEDULER_STATUSES = new Set(['active']);

export function useTaskSchedulers(
  filters?: SchedulerListParams,
  options?: { pollInterval?: number },
) {
  const [tasks, setTasks] = useState<TaskScheduler[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const tasksRef = useRef<TaskScheduler[]>([]);

  const fetchTasks = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await taskAPI.listSchedulers(filters);
      const data = response.data;
      const items = data.items ?? [];
      setTasks(items);
      setTotal(data.total ?? 0);
      tasksRef.current = items;
    } catch (err: any) {
      setError(err.message || 'Failed to fetch tasks');
      setTasks([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [
    filters?.status,
    filters?.priority,
    filters?.scheduledOnly,
    filters?.sortBy,
    filters?.sortOrder,
    filters?.offset,
    filters?.limit,
  ]);

  useEffect(() => {
    fetchTasks();
  }, [fetchTasks]);

  // Auto-polling: use shorter interval when there are active tasks
  useEffect(() => {
    const baseInterval = options?.pollInterval;
    if (!baseInterval) return;

    const schedule = () => {
      const hasActive = tasksRef.current.some(t => ACTIVE_SCHEDULER_STATUSES.has(t.status));
      return hasActive ? Math.min(baseInterval, 4000) : baseInterval;
    };

    let timerId: ReturnType<typeof setTimeout>;

    const tick = async () => {
      await fetchTasks();
      timerId = setTimeout(tick, schedule());
    };

    timerId = setTimeout(tick, schedule());
    return () => clearTimeout(timerId);
  }, [fetchTasks, options?.pollInterval]);

  return { tasks, total, loading, error, refetch: fetchTasks };
}

export function useTaskExecutions(
  filters?: TaskListParams & { schedulerID?: string },
  options?: { pollInterval?: number },
) {
  const [tasks, setTasks] = useState<TaskExecution[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const tasksRef = useRef<TaskExecution[]>([]);

  const fetchTasks = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await taskAPI.listExecutions(filters);
      const data = response.data;
      const items = data.items ?? [];
      setTasks(items);
      setTotal(data.total ?? 0);
      tasksRef.current = items;
    } catch (err: any) {
      setError(err.message || 'Failed to fetch task executions');
      setTasks([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [
    filters?.status,
    filters?.priority,
    filters?.deliveryStatus,
    filters?.schedulerID,
    filters?.sortBy,
    filters?.sortOrder,
    filters?.offset,
    filters?.limit,
  ]);

  useEffect(() => {
    fetchTasks();
  }, [fetchTasks]);

  useEffect(() => {
    const baseInterval = options?.pollInterval;
    if (!baseInterval) return;

    const schedule = () => {
      const hasActive = tasksRef.current.some(t => ACTIVE_EXECUTION_STATUSES.has(t.status));
      return hasActive ? Math.min(baseInterval, 4000) : baseInterval;
    };

    let timerId: ReturnType<typeof setTimeout>;

    const tick = async () => {
      await fetchTasks();
      timerId = setTimeout(tick, schedule());
    };

    timerId = setTimeout(tick, schedule());
    return () => clearTimeout(timerId);
  }, [fetchTasks, options?.pollInterval]);

  return { tasks, total, loading, error, refetch: fetchTasks };
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
  const [counts, setCounts] = useState<DashboardCounts | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchDashboard = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await taskAPI.dashboard();
      setCounts(response.data);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch dashboard');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDashboard();
  }, [fetchDashboard]);

  useEffect(() => {
    if (!options?.pollInterval) return;
    const id = setInterval(fetchDashboard, options.pollInterval);
    return () => clearInterval(id);
  }, [fetchDashboard, options?.pollInterval]);

  return { counts, loading, error, refetch: fetchDashboard };
}

export function useTaskExecutionsByScheduler(schedulerId?: string, params?: { offset?: number; limit?: number }) {
  const [records, setRecords] = useState<TaskExecution[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchRecords = useCallback(async () => {
    if (!schedulerId) return;
    try {
      setLoading(true);
      setError(null);
      const response = await taskAPI.listSchedulerExecutions(schedulerId, params);
      setRecords(response.data.items ?? []);
      setTotal(response.data.total ?? 0);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch executions');
    } finally {
      setLoading(false);
    }
  }, [schedulerId, params?.offset, params?.limit]);

  useEffect(() => {
    fetchRecords();
  }, [fetchRecords]);

  return { records, total, loading, error, refetch: fetchRecords };
}

export function useQueueStatus(options?: { pollInterval?: number }) {
  const [queueStatus, setQueueStatus] = useState<QueueStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchQueueStatus = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await taskAPI.queueStatus();
      setQueueStatus(response.data);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch queue status');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchQueueStatus();
  }, [fetchQueueStatus]);

  useEffect(() => {
    if (!options?.pollInterval) return;
    const id = setInterval(fetchQueueStatus, options.pollInterval);
    return () => clearInterval(id);
  }, [fetchQueueStatus, options?.pollInterval]);

  return { queueStatus, loading, error, refetch: fetchQueueStatus };
}

export function useTaskSystemNotice() {
  const [notice, setNotice] = useState<TaskSystemNotice | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchNotice = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await taskAPI.getSystemNotice();
      setNotice(response.data ?? null);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch system notice');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchNotice();
  }, [fetchNotice]);

  return { notice, loading, error, refetch: fetchNotice };
}

