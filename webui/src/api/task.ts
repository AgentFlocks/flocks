import client from './client';

// ======================================================================
// Types
// ======================================================================

export type TaskType = 'queued' | 'scheduled';
export type TaskStatus = 'pending' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
export type TaskPriority = 'urgent' | 'high' | 'normal' | 'low';
export type DeliveryStatus = 'unread' | 'notified' | 'viewed';
export type ExecutionMode = 'agent' | 'workflow';
export type SchedulerMode = 'once' | 'cron';
export type TaskSchedulerStatus = 'active' | 'disabled' | 'archived';
export type ExecutionTriggerType = 'run_once' | 'scheduled' | 'rerun';

export interface TaskSource {
  sourceType: string;
  sessionID?: string;
  userPrompt?: string;
}

export interface TaskTrigger {
  runImmediately: boolean;
  runAt?: string;
  cron?: string;
  timezone: string;
  nextRun?: string;
  cronDescription?: string;
}

export interface RetryConfig {
  maxRetries: number;
  retryCount: number;
  retryDelaySeconds: number;
  retryAfter?: string;
}

export interface TaskScheduler {
  id: string;
  title: string;
  description: string;
  mode: SchedulerMode;
  status: TaskSchedulerStatus;
  priority: TaskPriority;
  source: TaskSource;
  trigger: TaskTrigger;
  executionMode: ExecutionMode;
  agentName: string;
  workflowID?: string;
  skills: string[];
  category?: string;
  context: Record<string, any>;
  workspaceDirectory?: string;
  retry: RetryConfig;
  tags: string[];
  createdAt: string;
  updatedAt: string;
  createdBy: string;
  dedupKey?: string;
}

export interface TaskExecution {
  id: string;
  schedulerID: string;
  title: string;
  description: string;
  priority: TaskPriority;
  source: TaskSource;
  triggerType: ExecutionTriggerType;
  status: TaskStatus;
  deliveryStatus: DeliveryStatus;
  queuedAt?: string;
  startedAt?: string;
  completedAt?: string;
  durationMs?: number;
  sessionID?: string;
  resultSummary?: string;
  error?: string;
  executionInputSnapshot: Record<string, any>;
  workspaceDirectory?: string;
  retry: RetryConfig;
  executionMode: ExecutionMode;
  agentName: string;
  workflowID?: string;
  createdAt: string;
  updatedAt: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}

export interface TaskListParams {
  status?: TaskStatus;
  priority?: TaskPriority;
  deliveryStatus?: DeliveryStatus;
  sortBy?: string;
  sortOrder?: 'asc' | 'desc';
  offset?: number;
  limit?: number;
}

export interface SchedulerListParams extends Omit<TaskListParams, 'deliveryStatus' | 'status'> {
  status?: TaskSchedulerStatus;
  scheduledOnly?: boolean;
}

export interface TaskCreateParams {
  title: string;
  description?: string;
  type?: TaskType;
  priority?: TaskPriority;
  runOnce?: boolean;
  runAt?: string;
  cron?: string;
  cronDescription?: string;
  timezone?: string;
  userPrompt?: string;
  workspaceDirectory?: string;
  tags?: string[];
  context?: Record<string, any>;
  executionMode?: ExecutionMode;
  agentName?: string;
  workflowID?: string;
  skills?: string[];
  category?: string;
}

export interface TaskUpdateParams {
  title?: string;
  description?: string;
  priority?: TaskPriority;
  tags?: string[];
  executionMode?: ExecutionMode;
  agentName?: string;
  workflowID?: string;
  skills?: string[];
  category?: string;
  runOnce?: boolean;
  runAt?: string;
  cron?: string;
  cronDescription?: string;
  timezone?: string;
  userPrompt?: string;
  workspaceDirectory?: string;
}

export interface DashboardCounts {
  running: number;
  queued: number;
  completed_week: number;
  completed_unviewed: number;
  failed_week: number;
  scheduled_active: number;
  queue_paused: boolean;
}

export interface QueueStatus {
  paused: boolean;
  max_concurrent: number;
  running: number;
  queued: number;
}

export interface TaskSystemNotice {
  message: string;
  displayCount: number;
}

// ======================================================================
// API
// ======================================================================

export const taskAPI = {
  listSchedulers: (params?: SchedulerListParams) =>
    client.get<PaginatedResponse<TaskScheduler>>('/api/task-schedulers', { params }),

  getScheduler: (schedulerId: string) =>
    client.get<TaskScheduler>(`/api/task-schedulers/${schedulerId}`),

  createScheduler: (data: TaskCreateParams) =>
    client.post<TaskScheduler>('/api/task-schedulers', data),

  updateScheduler: (schedulerId: string, data: TaskUpdateParams) =>
    client.put<TaskScheduler>(`/api/task-schedulers/${schedulerId}`, data),

  deleteScheduler: (schedulerId: string) =>
    client.delete(`/api/task-schedulers/${schedulerId}`),

  enableScheduler: (schedulerId: string) =>
    client.post<TaskScheduler>(`/api/task-schedulers/${schedulerId}/enable`),

  disableScheduler: (schedulerId: string) =>
    client.post<TaskScheduler>(`/api/task-schedulers/${schedulerId}/disable`),

  listSchedulerExecutions: (schedulerId: string, params?: { offset?: number; limit?: number }) =>
    client.get<PaginatedResponse<TaskExecution>>(`/api/task-schedulers/${schedulerId}/executions`, { params }),

  runScheduler: (schedulerId: string) =>
    client.post<TaskExecution>(`/api/task-schedulers/${schedulerId}/run`),

  listExecutions: (params?: TaskListParams & { schedulerID?: string }) =>
    client.get<PaginatedResponse<TaskExecution>>('/api/task-executions', { params }),

  getExecution: (executionId: string) =>
    client.get<TaskExecution>(`/api/task-executions/${executionId}`),

  markExecutionViewed: (executionId: string) =>
    client.post<TaskExecution>(`/api/task-executions/${executionId}/viewed`),

  cancelExecution: (executionId: string) =>
    client.post<TaskExecution>(`/api/task-executions/${executionId}/cancel`),

  retryExecution: (executionId: string) =>
    client.post<TaskExecution>(`/api/task-executions/${executionId}/retry`),

  rerunExecution: (executionId: string) =>
    client.post<TaskExecution>(`/api/task-executions/${executionId}/rerun`),

  deleteExecution: (executionId: string) =>
    client.delete(`/api/task-executions/${executionId}`),

  dashboard: () =>
    client.get<DashboardCounts>('/api/task-system/dashboard'),

  queueStatus: () =>
    client.get<QueueStatus>('/api/task-system/queue/status'),

  pauseQueue: () =>
    client.post('/api/task-system/queue/pause'),

  resumeQueue: () =>
    client.post('/api/task-system/queue/resume'),

  batchCancelExecutions: (executionIds: string[]) =>
    client.post<{ cancelled: number }>('/api/task-executions/batch/cancel', { executionIds }),

  batchDeleteExecutions: (executionIds: string[]) =>
    client.post<{ deleted: number }>('/api/task-executions/batch/delete', { executionIds }),

  getSystemNotice: () =>
    client.get<TaskSystemNotice | null>('/api/task-system/notice'),
};
