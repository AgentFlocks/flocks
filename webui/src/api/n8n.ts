import client from './client';

export interface N8nConnection {
  baseUrl: string;
  apiKeySecretRef: string;
  apiKeyConfigured: boolean;
  apiKeyMasked?: string | null;
  updatedAt?: string | null;
  lastHealthStatus?: string | null;
  lastHealthError?: string | null;
  lastCheckedAt?: string | null;
}

export interface N8nBuildRun {
  runId: string;
  recordId?: string | null;
  status: string;
  currentStep: string;
  userRequest: string;
  baseUrl: string;
  apiKeySecretRef: string;
  ir: Record<string, any>;
  workflow?: Record<string, any> | null;
  workflowJsonPath?: string | null;
  reportPath?: string | null;
  n8nWorkflowId?: string | null;
  workflowUrl?: string | null;
  webhookUrl?: string | null;
  lintIssues: Array<Record<string, any>>;
  testResults: Array<Record<string, any>>;
  cleanup: Array<Record<string, any>>;
  error?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface N8nWorkflowRecord {
  id: string;
  name: string;
  engine: 'n8n';
  source: 'generated' | 'manual' | 'imported' | string;
  n8nWorkflowId: string;
  n8nBaseUrl: string;
  apiKeySecretRef: string;
  workflowUrl: string;
  webhookUrl?: string | null;
  webhookPath?: string | null;
  webhookMethod?: string | null;
  remoteStatus: 'active' | 'inactive' | 'missing' | 'auth_error' | 'sync_error' | 'cleaned' | 'unknown' | string;
  testStatus: 'not_tested' | 'test_passed' | 'test_failed' | 'test_error' | string;
  buildStatus: 'not_built' | 'running' | 'success' | 'failed' | 'lint_failed' | string;
  userRequest?: string | null;
  ir?: Record<string, any> | null;
  workflowJson?: Record<string, any> | null;
  lintIssues: Array<Record<string, any>>;
  testCases: Array<Record<string, any>>;
  testResults: Array<Record<string, any>>;
  latestBuildRunId?: string | null;
  latestExecutionId?: string | null;
  irPath?: string | null;
  workflowJsonPath?: string | null;
  reportPath?: string | null;
  createdAt: string;
  updatedAt: string;
  lastSyncedAt?: string | null;
  lastTestedAt?: string | null;
  error?: string | null;
}

export interface N8nBuildRunCreateInput {
  userRequest?: string;
  ir: Record<string, any>;
  baseUrl?: string;
  apiKeySecretRef?: string;
  publish?: boolean;
  activate?: boolean;
  cleanupOnSuccess?: boolean;
  waitForExecution?: boolean;
}

export const n8nAPI = {
  getConnection: () =>
    client.get<N8nConnection>('/api/integrations/n8n/connection'),

  updateConnection: (data: {
    baseUrl: string;
    apiKeySecretRef: string;
    apiKey?: string;
    clearApiKey?: boolean;
  }) =>
    client.put<N8nConnection>('/api/integrations/n8n/connection', data),

  healthCheck: (data?: { baseUrl?: string; apiKeySecretRef?: string }) =>
    client.post<{
      success: boolean;
      connection: N8nConnection;
      result?: Record<string, any>;
      error?: string;
    }>('/api/integrations/n8n/health-check', data ?? {}),

  createBuildRun: (data: N8nBuildRunCreateInput) =>
    client.post<N8nBuildRun>('/api/integrations/n8n/build-runs', data),

  listBuildRuns: (limit = 10) =>
    client.get<N8nBuildRun[]>('/api/integrations/n8n/build-runs', { params: { limit } }),

  getBuildRun: (runId: string) =>
    client.get<N8nBuildRun>(`/api/integrations/n8n/build-runs/${encodeURIComponent(runId)}`),

  retryTests: (runId: string) =>
    client.post<N8nBuildRun>(`/api/integrations/n8n/build-runs/${encodeURIComponent(runId)}/retry-test`),

  cleanup: (runId: string) =>
    client.post<N8nBuildRun>(`/api/integrations/n8n/build-runs/${encodeURIComponent(runId)}/cleanup`),

  listWorkflowRecords: (limit = 100) =>
    client.get<N8nWorkflowRecord[]>('/api/integrations/n8n/workflows', { params: { limit } }),

  getWorkflowRecord: (recordId: string) =>
    client.get<N8nWorkflowRecord>(`/api/integrations/n8n/workflows/${encodeURIComponent(recordId)}`),

  createWorkflowRecord: (data: {
    name: string;
    source?: string;
    n8nWorkflowId: string;
    n8nBaseUrl?: string;
    apiKeySecretRef?: string;
    workflowUrl?: string;
    webhookUrl?: string;
    webhookPath?: string;
    webhookMethod?: string;
    userRequest?: string;
    ir?: Record<string, any>;
    workflowJson?: Record<string, any>;
    testCases?: Array<Record<string, any>>;
  }) =>
    client.post<N8nWorkflowRecord>('/api/integrations/n8n/workflows', data),

  discoverWorkflowRecords: (data?: {
    baseUrl?: string;
    apiKeySecretRef?: string;
    prefix?: string;
    includeAll?: boolean;
  }) =>
    client.post<N8nWorkflowRecord[]>('/api/integrations/n8n/workflows/discover', data ?? {}),

  syncWorkflowRecord: (recordId: string) =>
    client.post<N8nWorkflowRecord>(`/api/integrations/n8n/workflows/${encodeURIComponent(recordId)}/sync`),

  retryWorkflowRecordTests: (recordId: string) =>
    client.post<N8nWorkflowRecord>(`/api/integrations/n8n/workflows/${encodeURIComponent(recordId)}/retry-test`),

  openWorkflowRecord: (recordId: string) =>
    client.post<{ url: string }>(`/api/integrations/n8n/workflows/${encodeURIComponent(recordId)}/open-event`),

  cleanupWorkflowRecord: (recordId: string) =>
    client.post<N8nWorkflowRecord>(`/api/integrations/n8n/workflows/${encodeURIComponent(recordId)}/cleanup`),

  deleteWorkflowRecord: (recordId: string) =>
    client.delete<{ success: boolean }>(`/api/integrations/n8n/workflows/${encodeURIComponent(recordId)}`),
};
