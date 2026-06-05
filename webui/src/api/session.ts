import client from './client';

export interface SessionMessagePartPayload {
  id: string;
  messageID: string;
  sessionID: string;
  type: string;
  text?: string;
  synthetic?: boolean;
  tool?: string;
  state?: Record<string, unknown>;
  callID?: string;
  metadata?: Record<string, unknown>;
}

export interface QueuedPrompt {
  id: string;
  sessionID: string;
  parts: Array<Record<string, unknown>>;
  agent?: string | null;
  model?: Record<string, unknown> | null;
  variant?: string | null;
  messageID?: string | null;
  status: 'pending' | 'executing' | string;
  createdAt: number;
  updatedAt: number;
}

export interface PromptQueueResponse {
  sessionID: string;
  items: QueuedPrompt[];
}

export interface SessionListParams {
  limit?: number;
  offset?: number;
  directory?: string;
  roots?: boolean;
  start?: number;
  search?: string;
  category?: string;
}

export const sessionApi = {
  /**
   * 获取会话列表
   */
  list: async (params?: SessionListParams) => {
    const response = await client.get('/api/session', { params });
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
  get: async (sessionId: string) => {
    const response = await client.get(`/api/session/${sessionId}`);
    return response.data;
  },

  /**
   * 创建会话
   */
  create: async (data?: { title?: string; parentID?: string }) => {
    const response = await client.post('/api/session', data || {});
    return response.data;
  },

  /**
   * 删除会话
   */
  delete: async (sessionId: string) => {
    const response = await client.delete(`/api/session/${sessionId}`);
    return response.data;
  },

  /**
   * 更新会话
   */
  update: async (sessionId: string, data: { title?: string }) => {
    const response = await client.patch(`/api/session/${sessionId}`, data);
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

  /**
   * 发送消息
   */
  sendMessage: async (sessionId: string, data: {
    role?: string;
    parts: Array<{ type: string; text: string }>;
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
