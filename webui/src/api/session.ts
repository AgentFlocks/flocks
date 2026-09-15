import client from './client';
import type { Session } from '@/types';
import type { SessionExecutionMode } from '@/utils/sessionExecutionMode';

export interface SessionMessagePartPayload {
  id: string;
  messageID: string;
  sessionID: string;
  type: string;
  text?: string;
  time?: {
    start: number;
    end?: number;
    compacted?: number;
  };
  synthetic?: boolean;
  tool?: string;
  state?: Record<string, unknown>;
  callID?: string;
  metadata?: Record<string, unknown>;
  url?: string | null;
  mime?: string;
  filename?: string;
  resourceID?: string;
}

export interface QueuedPrompt {
  id: string;
  sessionID: string;
  parts: Array<Record<string, unknown>>;
  agent?: string | null;
  model?: Record<string, unknown> | null;
  variant?: string | null;
  display_text?: string | null;
  displayText?: string | null;
  messageID?: string | null;
  status: 'pending' | 'executing' | string;
  createdAt: number;
  updatedAt: number;
  executionMode?: SessionExecutionMode;
}

export interface PromptQueueResponse {
  sessionID: string;
  items: QueuedPrompt[];
}

export interface ContextUsageSegment {
  key: string;
  tokens: number;
  included: boolean;
  source?: 'observed' | 'estimated' | string;
}

export interface ContextUsageSnapshot {
  sessionID: string;
  usedTokens: number;
  contextWindow: number;
  percent: number;
  source: 'observed' | 'estimated' | string;
  lastMessageID?: string | null;
  observedTokens?: number | null;
  estimatedTokens: number;
  compactedTokens: number;
  providerID?: string | null;
  modelID?: string | null;
  segments: ContextUsageSegment[];
  excludedSegments: ContextUsageSegment[];
}

export interface SessionGoalState {
  status: 'active' | 'completed' | 'blocked' | 'paused';
  objective: string;
  reason?: string | null;
}

export interface SessionResponse {
  id: string;
  goal?: SessionGoalState | null;
  [key: string]: unknown;
}

export interface SessionListParams {
  view?: 'list';
  manager?: boolean;
  limit?: number;
  offset?: number;
  directory?: string;
  projectID?: string;
  roots?: boolean;
  start?: number;
  search?: string;
  category?: string;
  status?: 'active' | 'archived' | 'all';
}

export interface SessionMessagePage {
  sessionID: string;
  items: Array<{ info: Record<string, unknown>; parts: SessionMessagePartPayload[] }>;
  hasMore: boolean;
  nextBefore?: string | null;
}

export interface SessionMessageListParams {
  limit?: number;
  before?: string | null;
  page?: boolean;
  include_archived?: boolean;
}

export interface SessionContextFile {
  resourceID: string;
  displayName: string;
  mimeType: string;
  size?: number | null;
  modifiedAt?: number | null;
  createdAt?: number | null;
  status: 'ready' | 'changed' | 'missing' | string;
  previewStatus: 'text' | 'inline' | 'unsupported' | string;
  canPreview: boolean;
  isTextFile: boolean;
  origin: 'user_upload' | 'agent_output' | string;
  section: 'outputs' | 'context';
  sourceMessageID: string;
  logicalPath: string;
}

export interface SessionContextTodo {
  id: string;
  content: string;
  activeForm?: string;
  status: 'pending' | 'in_progress' | 'completed' | 'cancelled';
  priority?: 'high' | 'medium' | 'low';
}

export interface SessionContextRoot {
  id: string;
  kind: 'project' | 'folder';
  displayName: string;
  status: 'available' | 'missing' | string;
}

export interface SessionContextSkill {
  name: string;
  description?: string | null;
  status: 'loaded' | 'loading' | string;
}

export interface SessionContextCounts {
  total: number;
  outputs: number;
  contextFiles: number;
  roots: number;
  progress: number;
}

export interface SessionContextSnapshot {
  sessionID: string;
  canManageFolders: boolean;
  historyTruncated: boolean;
  outputs: SessionContextFile[];
  contextFiles: SessionContextFile[];
  progress: SessionContextTodo[];
  roots: SessionContextRoot[];
  skills: SessionContextSkill[];
  counts: SessionContextCounts;
}

export interface SessionContextContent {
  content: string;
  truncated?: boolean;
  size?: number;
  previewLimitBytes?: number;
}

export interface SessionContextRootNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size?: number | null;
  modifiedAt?: number | null;
  isTextFile?: boolean;
}

export const sessionApi = {
  /**
   * 获取会话列表
   */
  list: async (params?: SessionListParams): Promise<Session[]> => {
    const response = await client.get<Session[]>('/api/session', { params });
    return response.data;
  },

  /**
   * 获取会话数量
   */
  count: async () => {
    const response = await client.get('/api/session');
    return Array.isArray(response.data) ? response.data.length : 0;
  },

  /**
   * 获取单个会话
   */
  get: async (sessionId: string): Promise<SessionResponse> => {
    const response = await client.get(`/api/session/${sessionId}`);
    return response.data;
  },

  /**
   * 创建会话
   */
  create: async (data?: { title?: string; parentID?: string; projectID?: string; model_auto?: boolean }) => {
    const response = await client.post('/api/session', data || {});
    return response.data;
  },

  /**
   * 永久删除会话（普通工作台应使用 archive）
   */
  delete: async (sessionId: string): Promise<boolean> => {
    const response = await client.delete<boolean>(`/api/session/${sessionId}`);
    return response.data;
  },

  /**
   * 归档会话并保留全部持久化数据
   */
  archive: async (sessionId: string): Promise<SessionResponse> => {
    const response = await client.post(`/api/session/${sessionId}/archive`);
    return response.data;
  },

  /**
   * 恢复已归档会话
   */
  restore: async (sessionId: string): Promise<SessionResponse> => {
    const response = await client.post(`/api/session/${sessionId}/restore`);
    return response.data;
  },

  /**
   * 更新会话
   */
  update: async (sessionId: string, data: {
    title?: string;
    provider?: string;
    model?: string;
    model_pinned?: boolean;
    model_auto?: boolean;
  }) => {
    const response = await client.patch(`/api/session/${sessionId}`, data);
    return response.data;
  },

  /**
   * 将任务及其子任务移动到指定项目
   */
  moveToProject: async (sessionId: string, projectID: string): Promise<SessionResponse> => {
    const response = await client.patch(`/api/session/${sessionId}/project`, { projectID });
    return response.data;
  },

  /**
   * 本地共享会话（所有本地账号可见，只读）
   */
  shareLocal: async (sessionId: string) => {
    const response = await client.post(`/api/session/${sessionId}/share-local`);
    return response.data;
  },

  /**
   * 取消本地共享会话
   */
  unshareLocal: async (sessionId: string) => {
    const response = await client.post(`/api/session/${sessionId}/unshare-local`);
    return response.data;
  },

  /**
   * 清空会话消息
   */
  clear: async (sessionId: string) => {
    const response = await client.post(`/api/session/${sessionId}/clear`);
    return response.data;
  },

  /**
   * 获取会话消息
   */
  getMessages: async (sessionId: string) => {
    const response = await client.get(`/api/session/${sessionId}/message`);
    return response.data;
  },

  getMessagesPage: async (sessionId: string, params?: SessionMessageListParams): Promise<SessionMessagePage> => {
    const response = await client.get(`/api/session/${sessionId}/message`, {
      params: { page: true, limit: 50, include_archived: true, ...params },
    });
    return response.data;
  },

  getContextUsage: async (sessionId: string): Promise<ContextUsageSnapshot> => {
    const response = await client.get(`/api/session/${sessionId}/context-usage`);
    return response.data;
  },

  getContext: async (sessionId: string): Promise<SessionContextSnapshot> => {
    const response = await client.get<SessionContextSnapshot>(`/api/session/${sessionId}/context`);
    return response.data;
  },

  readContextFile: async (sessionId: string, resourceId: string): Promise<SessionContextContent> => {
    const response = await client.get<SessionContextContent>(
      `/api/session/${sessionId}/context/files/${resourceId}/content`,
    );
    return response.data;
  },

  contextFilePreviewUrl: (sessionId: string, resourceId: string) =>
    `${client.defaults.baseURL ?? ''}/api/session/${sessionId}/context/files/${resourceId}/preview`,

  contextFileDownloadUrl: (sessionId: string, resourceId: string) =>
    `${client.defaults.baseURL ?? ''}/api/session/${sessionId}/context/files/${resourceId}/download`,

  addContextFolder: async (sessionId: string, path: string, displayName?: string) => {
    const response = await client.post(`/api/session/${sessionId}/context/folders`, {
      path,
      ...(displayName ? { displayName } : {}),
    });
    return response.data;
  },

  removeContextFolder: async (sessionId: string, rootId: string) => {
    const response = await client.delete(`/api/session/${sessionId}/context/folders/${rootId}`);
    return response.data;
  },

  listContextRoot: async (sessionId: string, rootId: string, path = '') => {
    const response = await client.get<{ rootID: string; path: string; items: SessionContextRootNode[] }>(
      `/api/session/${sessionId}/context/roots/${rootId}/list`,
      { params: { path } },
    );
    return response.data;
  },

  readContextRootFile: async (
    sessionId: string,
    rootId: string,
    path: string,
  ): Promise<SessionContextContent> => {
    const response = await client.get<SessionContextContent>(
      `/api/session/${sessionId}/context/roots/${rootId}/content`,
      { params: { path } },
    );
    return response.data;
  },

  contextRootPreviewUrl: (sessionId: string, rootId: string, path: string) =>
    `${client.defaults.baseURL ?? ''}/api/session/${sessionId}/context/roots/${rootId}/preview?path=${encodeURIComponent(path)}`,

  contextRootDownloadUrl: (sessionId: string, rootId: string, path: string) =>
    `${client.defaults.baseURL ?? ''}/api/session/${sessionId}/context/roots/${rootId}/download?path=${encodeURIComponent(path)}`,

  /**
   * 发送消息
   */
  sendMessage: async (sessionId: string, data: {
    role?: string;
    parts: Array<Record<string, unknown>>;
    noReply?: boolean;
    mockReply?: string;
  }) => {
    const response = await client.post(`/api/session/${sessionId}/message`, data, { timeout: 0 });
    return response.data;
  },

  listPromptQueue: async (sessionId: string): Promise<PromptQueueResponse> => {
    const response = await client.get(`/api/session/${sessionId}/prompt_queue`);
    return response.data;
  },

  enqueuePrompt: async (sessionId: string, data: {
    parts: Array<Record<string, unknown>>;
    agent?: string;
    model?: Record<string, unknown>;
    variant?: string;
    displayText?: string;
    executionMode?: SessionExecutionMode;
  }) => {
    const response = await client.post(`/api/session/${sessionId}/prompt_queue`, data);
    return response.data;
  },

  updateQueuedPrompt: async (sessionId: string, queueId: string, text: string) => {
    const response = await client.patch(`/api/session/${sessionId}/prompt_queue/${queueId}`, { text });
    return response.data;
  },

  removeQueuedPrompt: async (sessionId: string, queueId: string) => {
    const response = await client.delete(`/api/session/${sessionId}/prompt_queue/${queueId}`);
    return response.data;
  },

  runQueuedPromptNow: async (sessionId: string, queueId: string) => {
    const response = await client.post(`/api/session/${sessionId}/prompt_queue/${queueId}/run_now`);
    return response.data;
  },

  /**
   * 更新消息 part
   */
  updateMessagePart: async (
    sessionId: string,
    messageId: string,
    partId: string,
    data: SessionMessagePartPayload,
  ) => {
    const response = await client.patch(
      `/api/session/${sessionId}/message/${messageId}/part/${partId}`,
      data,
    );
    return response.data;
  },

  /**
   * 编辑用户消息后重新发送
   */
  resendMessage: async (sessionId: string, messageId: string, partId: string, text: string) => {
    const response = await client.post(
      `/api/session/${sessionId}/message/${messageId}/resend`,
      { text, partID: partId },
      { timeout: 0 },
    );
    return response.data;
  },

  /**
   * 重新生成助手消息
   */
  regenerateMessage: async (sessionId: string, messageId: string) => {
    const response = await client.post(
      `/api/session/${sessionId}/message/${messageId}/regenerate`,
      {},
      { timeout: 0 },
    );
    return response.data;
  },

  /**
   * 获取会话统计
   */
  getStatistics: async (sessionId: string) => {
    const response = await client.get(`/api/session/${sessionId}/statistics`);
    return response.data;
  },

  /**
   * 获取子会话列表
   */
  getChildren: async (sessionId: string) => {
    const response = await client.get(`/api/session/${sessionId}/children`);
    return response.data;
  },

};
