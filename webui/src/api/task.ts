import client from './client';

// ======================================================================
// Types
// ======================================================================

export type TaskType = 'queued' | 'scheduled';
export type TaskStatus = 'pending' | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'paused';
export type TaskPriority = 'urgent' | 'high' | 'normal' | 'low';
export type DeliveryStatus = 'unread' | 'notified' | 'viewed';
export type ExecutionMode = 'agent' | 'workflow';

export interface TaskSource {
  sourceType: string;
  sessionID?: string;
  userPrompt?: string;
}

export interface TaskSchedule {
  cron?: string;
  timezone: string;
  nextRun?: string;
  enabled: boolean;
  cronDescription?: string;
  runOnce?: boolean;
  runAt?: string;
}

export interface TaskExecution {
  sessionID?: string;
  agent: string;
  startedAt?: string;
  completedAt?: string;
  durationMs?: number;
  resultSummary?: string;
  error?: string;
}

export interface RetryConfig {
  maxRetries: number;
  retryCount: number;
  retryDelaySeconds: number;
}

export interface Task {
  id: string;
  title: string;
  description: string;
  type: TaskType;
  status: TaskStatus;
  priority: TaskPriority;
  source: TaskSource;
  schedule?: TaskSchedule;
  execution?: TaskExecution;
  deliveryStatus: DeliveryStatus;
  executionMode: ExecutionMode;
  agentName: string;
  workflowID?: string;
  skills: string[];
  category?: string;
  context: Record<string, any>;
  workspaceDirectory?: string;
  retry?: RetryConfig;
  tags: string[];
  createdAt: string;
  updatedAt: string;
  createdBy: string;
  taskID?: string;
  sourceTaskType?: TaskType;
  recordID?: string;
}

export interface TaskExecutionRecord {
  id: string;
  taskID: string;
  status: TaskStatus;
  startedAt?: string;
  completedAt?: string;
  durationMs?: number;
  resultSummary?: string;
  error?: string;
  sessionID?: string;
  deliveryStatus: DeliveryStatus;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}

export interface TaskListParams {
  status?: TaskStatus;
  type?: TaskType;
  priority?: TaskPriority;
  deliveryStatus?: DeliveryStatus;
  sortBy?: string;
  sortOrder?: 'asc' | 'desc';
  offset?: number;
  limit?: number;
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

// ======================================================================
// API
// ======================================================================

export const taskAPI = {
  list: (params?: TaskListParams) =>
    client.get<PaginatedResponse<Task>>('/api/tasks', { params }),

  get: (taskId: string) =>
    client.get<Task>(`/api/tasks/${taskId}`),

  create: (data: TaskCreateParams) =>
    client.post<Task>('/api/tasks', data),

  update: (taskId: string, data: TaskUpdateParams) =>
    client.put<Task>(`/api/tasks/${taskId}`, data),

  delete: (taskId: string) =>
    client.delete(`/api/tasks/${taskId}`),

  cancel: (taskId: string) =>
    client.post<Task>(`/api/tasks/${taskId}/cancel`),

  pause: (taskId: string) =>
    client.post<Task>(`/api/tasks/${taskId}/pause`),

  resume: (taskId: string) =>
    client.post<Task>(`/api/tasks/${taskId}/resume`),

  retry: (taskId: string) =>
    client.post<Task>(`/api/tasks/${taskId}/retry`),

  rerun: (taskId: string) =>
    client.post<Task>(`/api/tasks/${taskId}/rerun`),

  dashboard: () =>
    client.get<DashboardCounts>('/api/tasks/dashboard'),

  queueStatus: () =>
    client.get<QueueStatus>('/api/tasks/queue/status'),

  listQueueItems: (params?: Omit<TaskListParams, 'type'>) =>
    client.get<PaginatedResponse<Task>>('/api/tasks/queue/items', { params }),

  cancelQueueItem: (itemId: string) =>
    client.post(`/api/tasks/queue/items/${itemId}/cancel`),

  rerunQueueItem: (itemId: string) =>
    client.post(`/api/tasks/queue/items/${itemId}/rerun`),

  deleteQueueItem: (itemId: string) =>
    client.delete(`/api/tasks/queue/items/${itemId}`),

  pauseQueue: () =>
    client.post('/api/tasks/queue/pause'),

  resumeQueue: () =>
    client.post('/api/tasks/queue/resume'),

  batchCancel: (taskIds: string[]) =>
    client.post<{ cancelled: number }>('/api/tasks/batch/cancel', { taskIds }),

  batchDelete: (taskIds: string[]) =>
    client.post<{ deleted: number }>('/api/tasks/batch/delete', { taskIds }),

  listScheduled: () =>
    client.get<Task[]>('/api/tasks/scheduled'),

  enableScheduled: (taskId: string) =>
    client.post<Task>(`/api/tasks/scheduled/${taskId}/enable`),

  disableScheduled: (taskId: string) =>
    client.post<Task>(`/api/tasks/scheduled/${taskId}/disable`),

  listRecords: (taskId: string, params?: { offset?: number; limit?: number }) =>
    client.get<PaginatedResponse<TaskExecutionRecord>>(`/api/tasks/${taskId}/records`, { params }),
};
