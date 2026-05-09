import client from './client';

export type WorkflowNodeType =
  | 'python'
  | 'logic'
  | 'branch'
  | 'loop'
  | 'tool'
  | 'llm'
  | 'http_request'
  | 'subworkflow';

export interface WorkflowNode {
  id: string;
  type: WorkflowNodeType;
  code?: string;
  description?: string;
  select_key?: string;
  join?: boolean;
  join_mode?: 'flat' | 'namespace';
  // tool node
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  // llm node
  prompt?: string;
  model?: string;
  // llm / subworkflow shared
  output_key?: string;
  // http_request node
  method?: string;
  url?: string;
  headers?: Record<string, string>;
  body?: unknown;
  response_key?: string;
  // subworkflow node
  workflow_id?: string;
  inputs_mapping?: Record<string, string>;
  inputs_const?: Record<string, unknown>;
}

export interface WorkflowEdge {
  from: string;
  to: string;
  order: number;
  label?: string;
  mapping?: Record<string, string>;
  const?: Record<string, any>;
}

export interface WorkflowOutputSchema {
  type?: string | string[];
  title?: string;
  description?: string;
  properties?: Record<string, WorkflowOutputSchema>;
  required?: string[];
  items?: WorkflowOutputSchema | WorkflowOutputSchema[];
  enum?: Array<string | number | boolean | null>;
  additionalProperties?: boolean | WorkflowOutputSchema;
  [key: string]: any;
}

export interface WorkflowMetadata {
  sampleInputs?: Record<string, any>;
  outputSchema?: WorkflowOutputSchema;
  [key: string]: any;
}

export interface WorkflowJSON {
  version?: string;
  name?: string;
  start: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  metadata?: WorkflowMetadata;
}

export interface Workflow {
  id: string;
  name: string;
  description?: string;
  markdownContent?: string;
  category: string;
  workflowJson: WorkflowJSON;
  status: 'draft' | 'active' | 'archived';
  source?: 'project' | 'global';
  createdBy?: string;
  createdAt: number;
  updatedAt: number;
  stats: {
    callCount: number;
    successCount: number;
    errorCount: number;
    totalRuntime: number;
    avgRuntime: number;
    thumbsUp: number;
    thumbsDown: number;
  };
}

export interface WorkflowExecutionStep {
  node_id: string;
  node_type?: string;
  type?: string;
  inputs: Record<string, any>;
  outputs: Record<string, any>;
  stdout?: string;
  error?: string;
  traceback?: string;
  duration_ms?: number;
}

export interface WorkflowExecution {
  id: string;
  workflowId: string;
  inputParams: Record<string, any>;
  outputResults?: Record<string, any>;
  status: 'running' | 'success' | 'error' | 'timeout' | 'cancelled';
  startedAt: number;
  finishedAt?: number;
  duration?: number;
  executionLog: WorkflowExecutionStep[];
  errorMessage?: string;
  currentNodeId?: string;
  currentNodeType?: string;
  currentPhase?: string;
  currentStepIndex?: number;
}

export interface WorkflowNodeExecution {
  node_id: string;
  outputs: Record<string, any>;
  stdout: string;
  error?: string;
  traceback?: string;
  duration_ms?: number;
  success: boolean;
}

export interface WorkflowService {
  workflowId: string;
  workflowName: string;
  serviceUrl: string;
  invokeUrl: string;
  apiKey: string;
  status: 'publishing' | 'running' | 'stopped' | 'error';
  publishedAt: number;
  containerName?: string;
}

/** Saved syslog listener config (per workflow). */
export interface SyslogConfig {
  workflowId?: string;
  enabled?: boolean;
  protocol?: string;
  host?: string;
  port?: number;
  format?: string;
  inputKey?: string;
  updatedAt?: number;
}

export const workflowAPI = {
  list: (params?: { category?: string; status?: string; excludeId?: string }) =>
    client.get<Workflow[]>('/api/workflow', { params }),
  
  get: (id: string) =>
    client.get<Workflow>(`/api/workflow/${id}`),
  
  create: (data: {
    name: string;
    description?: string;
    category?: string;
    workflowJson: WorkflowJSON;
    createdBy?: string;
    source?: 'project' | 'global';
  }) =>
    client.post<Workflow>('/api/workflow', data),
  
  update: (id: string, data: {
    name?: string;
    description?: string;
    category?: string;
    workflowJson?: WorkflowJSON;
    status?: 'draft' | 'active' | 'archived';
  }) =>
    client.put<Workflow>(`/api/workflow/${id}`, data),
  
  delete: (id: string) =>
    client.delete(`/api/workflow/${id}`),
  
  run: (id: string, data: {
    inputs?: Record<string, any>;
    timeoutS?: number;
    trace?: boolean;
  }) =>
    client.post<WorkflowExecution>(`/api/workflow/${id}/run`, data, { timeout: 0 }),
  
  validate: (id: string) =>
    client.post<{ valid: boolean; issues: any[] }>(`/api/workflow/${id}/validate`),
  
  getHistory: (id: string, params?: { limit?: number }) =>
    client.get<WorkflowExecution[]>(`/api/workflow/${id}/history`, { params }),
  
  getExecution: (workflowId: string, execId: string) =>
    client.get<WorkflowExecution>(`/api/workflow/${workflowId}/history/${execId}`),

  cancelExecution: (workflowId: string, execId: string) =>
    client.post<{ status: string; message: string; executionId: string }>(
      `/api/workflow/${workflowId}/history/${execId}/cancel`
    ),
  
  getStats: (id: string) =>
    client.get(`/api/workflow/${id}/stats`),
  
  getAggregateStats: () =>
    client.get('/api/workflow/stats'),
  
  import: (workflowJson: WorkflowJSON) =>
    client.post<Workflow>('/api/workflow/import', workflowJson),
  
  export: (id: string) =>
    client.get<WorkflowJSON>(`/api/workflow/${id}/export`),

  publish: (id: string) =>
    client.post<WorkflowService>(`/api/workflow/${id}/publish`, undefined, { timeout: 300000 }),

  unpublish: (id: string) =>
    client.post<{ ok: boolean }>(`/api/workflow/${id}/unpublish`),

  getService: (id: string) =>
    client.get<WorkflowService | null>(`/api/workflow/${id}/service`),

  listServices: () =>
    client.get<WorkflowService[]>('/api/workflow-services'),

  saveKafkaConfig: (id: string, config: {
    inputBroker?: string;
    inputTopic?: string;
    inputGroupId?: string;
    outputBroker?: string;
    outputTopic?: string;
  }) =>
    client.post<{ ok: boolean }>(`/api/workflow/${id}/kafka-config`, config),

  getKafkaConfig: (id: string) =>
    client.get<{
      inputBroker?: string;
      inputTopic?: string;
      inputGroupId?: string;
      outputBroker?: string;
      outputTopic?: string;
    } | null>(`/api/workflow/${id}/kafka-config`),

  saveSyslogConfig: (id: string, config: {
    enabled?: boolean;
    protocol?: string;
    host?: string;
    port?: number;
    format?: string;
    inputKey?: string;
  }) =>
    client.post<{ ok: boolean }>(`/api/workflow/${id}/syslog-config`, config),

  getSyslogConfig: (id: string) =>
    client.get<SyslogConfig | null>(`/api/workflow/${id}/syslog-config`),

  runNode: (id: string, data: { nodeId: string; inputs?: Record<string, any> }) =>
    client.post<WorkflowNodeExecution>(`/api/workflow/${id}/run-node`, { node_id: data.nodeId, inputs: data.inputs ?? {} }),

  getSampleInputs: (id: string) =>
    client.get<{ sampleInputs: Record<string, any> }>(`/api/workflow/${id}/sample-inputs`),

  saveSampleInputs: (id: string, sampleInputs: Record<string, any>) =>
    client.post<{ ok: boolean }>(`/api/workflow/${id}/sample-inputs`, { sampleInputs }),
};
