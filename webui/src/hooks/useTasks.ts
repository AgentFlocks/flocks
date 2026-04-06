import { useState, useEffect, useRef, useCallback } from 'react';
import {
  taskAPI,
  Task,
  TaskListParams,
  TaskExecutionRecord,
  DashboardCounts,
  QueueStatus,
} from '@/api/task';

const ACTIVE_STATUSES = new Set(['pending', 'queued', 'running']);

export function useTasks(filters?: TaskListParams, options?: { pollInterval?: number }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const tasksRef = useRef<Task[]>([]);

  const fetchTasks = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await taskAPI.list(filters);
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
    filters?.type,
    filters?.priority,
    filters?.deliveryStatus,
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
      const hasActive = tasksRef.current.some(t => ACTIVE_STATUSES.has(t.status));
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

export function useQueueItems(filters?: Omit<TaskListParams, 'type'>, options?: { pollInterval?: number }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const tasksRef = useRef<Task[]>([]);

  const fetchTasks = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await taskAPI.listQueueItems(filters);
      const data = response.data;
      const items = data.items ?? [];
      setTasks(items);
      setTotal(data.total ?? 0);
      tasksRef.current = items;
    } catch (err: any) {
      setError(err.message || 'Failed to fetch queue items');
      setTasks([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [
    filters?.status,
    filters?.priority,
    filters?.deliveryStatus,
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
      const hasActive = tasksRef.current.some(t => ACTIVE_STATUSES.has(t.status));
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

export function useTask(taskId?: string) {
  const [task, setTask] = useState<Task | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTask = useCallback(async () => {
    if (!taskId) return;
    try {
      setLoading(true);
      setError(null);
      const response = await taskAPI.get(taskId);
      setTask(response.data);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch task');
    } finally {
      setLoading(false);
    }
  }, [taskId]);

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

export function useTaskRecords(taskId?: string, params?: { offset?: number; limit?: number }) {
  const [records, setRecords] = useState<TaskExecutionRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchRecords = useCallback(async () => {
    if (!taskId) return;
    try {
      setLoading(true);
      setError(null);
      const response = await taskAPI.listRecords(taskId, params);
      setRecords(response.data.items ?? []);
      setTotal(response.data.total ?? 0);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch records');
    } finally {
      setLoading(false);
    }
  }, [taskId, params?.offset, params?.limit]);

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
