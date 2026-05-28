import client from './client';

export interface Agent {
  name: string;
  description?: string;
  /** Chinese UI label; English \`description\` is used for delegation/tooling. */
  descriptionCn?: string;
  mode: string;
  native?: boolean;
  hidden?: boolean;
  topP?: number;
  temperature?: number;
  color?: string;
  permission: any[];
  model?: {
    modelID: string;
    providerID: string;
  };
  prompt?: string;
  options: Record<string, any>;
  delegatable?: boolean;
  steps?: number;
  skills: string[];
  tools: string[];
  tags?: string[];
}

export interface BackgroundTask {
  id: string;
  status: string;
  description: string;
  prompt: string;
  agent: string;
  parentSessionId?: string;
  parentMessageId?: string;
  sessionId?: string;
  error?: string;
  output?: string;
  createdAt: number;
  startedAt?: number;
  completedAt?: number;
}

export const agentAPI = {
  list: () =>
    client.get<Agent[]>('/api/agent'),

  refresh: () =>
    client.post<{ count: number }>('/api/agent/refresh'),

  get: (name: string) =>
    client.get<Agent>(`/api/agent/${name}`),

  create: (data: {
    name: string;
    description?: string;
    descriptionCn?: string;
    prompt: string;
    temperature?: number;
    color?: string;
    mode?: string;
    model?: { modelID: string; providerID: string };
    delegatable?: boolean;
    skills?: string[];
    tools?: string[];
  }) =>
    client.post<Agent>('/api/agent', data),

  update: (name: string, data: {
    description?: string;
    descriptionCn?: string;
    prompt?: string;
    temperature?: number;
    color?: string;
    model?: { modelID: string; providerID: string };
    delegatable?: boolean;
    skills?: string[];
    tools?: string[];
  }) =>
    client.put<Agent>(`/api/agent/${name}`, data),

  updateModel: (name: string, model: { modelID: string; providerID: string } | null, temperature?: number) =>
    client.put<Agent>(`/api/agent/${name}/model`, { model, temperature }),

  delete: (name: string) =>
    client.delete(`/api/agent/${name}`),

  test: (name: string, testPrompt?: string) =>
    client.post<{ sessionId: string; status: string }>(
      `/api/agent/${name}/test`,
      { test_prompt: testPrompt ?? 'Hello, this is a test message.' },
    ),
};

// Background tasks are managed under a dedicated route to avoid
// conflicting with /api/agent/{name} path parameters.
export const backgroundTaskAPI = {
  list: () =>
    client.get<BackgroundTask[]>('/api/background-task'),

  get: (taskId: string) =>
    client.get<BackgroundTask>(`/api/background-task/${taskId}`),

  cancel: (taskId: string) =>
    client.post(`/api/background-task/${taskId}/cancel`),
};
