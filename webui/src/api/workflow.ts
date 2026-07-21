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
  join_conflict?: 'overwrite' | 'error';
  join_namespace_key?: string;
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

export type WorkflowTriggerType =
  | 'manual'
  | 'schedule'
  | 'webhook'
  | 'syslog'
  | 'kafka'
  | 'internal_event'
  | 'custom_webhook'
  | 'custom_adapter'
  | 'plugin';

export interface WorkflowTriggerAuth {
  type?: string;
  secretRef?: string;
  headerName?: string;
  queryParam?: string;
  apiKey?: string;
  [key: string]: any;
}

export interface WorkflowTriggerFilter {
  expr?: string;
  mode?: string;
  path?: string;
  equals?: unknown;
  [key: string]: any;
}

export interface WorkflowTriggerConcurrency {
  policy?: 'allow' | 'no_overlap' | 'queue' | 'drop_oldest' | 'drop_newest';
  maxParallel?: number;
  queueSize?: number;
  [key: string]: any;
}

export interface WorkflowTriggerSample {
  name: string;
  payload?: unknown;
  headers?: Record<string, any>;
  query?: Record<string, any>;
  [key: string]: any;
}

export interface WorkflowTrigger {
  id: string;
  name?: string;
  type: WorkflowTriggerType;
  enabled?: boolean;
  description?: string;
  source?: Record<string, any>;
  auth?: WorkflowTriggerAuth;
  filter?: WorkflowTriggerFilter;
  mapping?: Record<string, string>;
  inputs?: Record<string, any>;
  concurrency?: WorkflowTriggerConcurrency;
  runtime?: Record<string, any>;
  testSamples?: WorkflowTriggerSample[];
  updatedAt?: number;
  [key: string]: any;
}

export interface WorkflowTriggerStatus {
  workflowId?: string;
  triggerId?: string;
  triggerType?: WorkflowTriggerType | string;
  state: string;
  error?: string | null;
  [key: string]: any;
}

export interface WorkflowTriggerRecord {
  trigger: WorkflowTrigger;
  status?: WorkflowTriggerStatus;
}

export interface WorkflowTriggerPreview {
  triggerId: string;
  triggerType: string;
  matched: boolean;
  inputs: Record<string, any>;
  filterError?: string | null;
}

export interface WorkflowTriggerPlugin {
  id: string;
  name: string;
  description?: string;
  root?: string;
  manifestPath?: string;
  handlerPath?: string;
  manifest?: Record<string, any>;
}

export interface WorkflowJSON {
  version?: string;
  name?: string;
  nameI18n?: Record<string, string>;
  start: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  triggers?: WorkflowTrigger[];
  metadata?: WorkflowMetadata;
}

export type WorkflowCapabilityState =
  | 'unconfigured'
  | 'starting'
  | 'running'
  | 'stopped'
  | 'error';

export interface WorkflowCapabilityStatus {
  configured: boolean;
  state: WorkflowCapabilityState;
  count?: number;
}

export interface WorkflowTriggerStatusSummary {
  id: string;
  type: WorkflowTriggerType;
  name?: string;
  state: WorkflowCapabilityState;
  rawState?: string;
}

export interface WorkflowTriggerCapabilityStatus extends WorkflowCapabilityStatus {
  items?: WorkflowTriggerStatusSummary[];
}

export interface WorkflowIntegrationStatus {
  api: WorkflowCapabilityStatus;
  trigger: WorkflowTriggerCapabilityStatus;
}

export interface WorkflowSummary {
  id: string;
  name: string;
  nameI18n?: Record<string, string>;
  description?: string;
  category: string;
  status: 'draft' | 'active' | 'archived';
  source?: 'project' | 'global';
  createdAt: number;
  updatedAt: number;
  nodeCount: number;
  integrationStatus?: WorkflowIntegrationStatus;
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

export interface Workflow {
  id: string;
  name: string;
  nameI18n?: Record<string, string>;
  description?: string;
  markdownContent?: string;
  editMarkdownContent?: string;
  category: string;
  workflowJson: WorkflowJSON;
  status: 'draft' | 'active' | 'archived';
  source?: 'project' | 'global';
  createdBy?: string;
  createdAt: number;
  updatedAt: number;
  integrationStatus?: WorkflowIntegrationStatus;
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
  triggerId?: string;
  triggerType?: string;
  deliveryId?: string;
  attempt?: number;
  triggerSource?: string;
  currentNodeId?: string;
  currentNodeType?: string;
  currentPhase?: string;
  currentStepIndex?: number;
  stepCount?: number;
  stepLogOffset?: number;
  stepLogLimit?: number;
  stepLogTotal?: number;
  loopProgress?: {
    loop_node_id?: string;
    iteration?: number;
    total_iterations?: number;
    current_item?: string;
    current_inner_node_id?: string;
    global_step_index?: number;
  };
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
  driver?: 'local' | 'docker';
}

export type WorkflowServiceDriver = 'local' | 'docker';

export interface WorkflowIntegrationConfig {
  version: number;
  kind: string;
  workflow: {
    id: string;
    name?: string;
    category?: string;
    source?: string;
  };
  updatedAt: number;
  publish: Record<string, any>;
  triggers: WorkflowTrigger[];
  [key: string]: any;
}

export interface WorkflowIntegrationConfigResponse {
  ok?: boolean;
  exists?: boolean;
  path: string;
  storageKey?: string;
  source?: 'storage' | 'file_migrated' | 'generated' | string;
  config: WorkflowIntegrationConfig;
  runtime?: {
    publish?: Record<string, any>;
    triggers?: WorkflowTriggerRecord[];
    [key: string]: any;
  };
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

/** Runtime state of the syslog listener (independent from saved config). */
export interface SyslogListenerStatus {
  state: 'binding' | 'listening' | 'failed' | 'stopped';
  error?: string | null;
  host?: string;
  port?: number;
  protocol?: string;
  queueSize?: number;
  queueCapacity?: number;
  workerCount?: number;
}

export interface KafkaConfig {
  workflowId?: string;
  enabled?: boolean;
  inputBroker?: string;
  inputTopic?: string;
  inputGroupId?: string;
  inputKey?: string;
  autoOffsetReset?: string;
  inputs?: Record<string, any>;
  updatedAt?: number;
}

/** Runtime state of the Kafka consumer (independent from saved config). */
export interface KafkaConsumerStatus {
  state: 'connecting' | 'running' | 'failed' | 'stopped';
  error?: string | null;
  broker?: string;
  topic?: string;
  groupId?: string;
  queueSize?: number;
  queueCapacity?: number;
  workerCount?: number;
}

export interface WorkflowPollerConfig {
  workflowId?: string;
  enabled?: boolean;
  intervalSeconds?: number;
  cronExpression?: string | null;
  timeoutSeconds?: number;
  noOverlap?: boolean;
  inputs?: Record<string, any>;
  updatedAt?: number;
}

export interface WorkflowPollerStatus {
  workflowId?: string;
  state: 'running' | 'stopped' | 'failed';
  error?: string | null;
  enabled?: boolean;
  intervalSeconds?: number;
  cronExpression?: string | null;
  timeoutSeconds?: number;
  noOverlap?: boolean;
  activeRuns?: number;
  lastRunAt?: number | null;
  lastRunId?: string | null;
  lastStatus?: string | null;
  lastError?: string | null;
  lastDurationMs?: number | null;
  selectedCount?: number | null;
  processedMarkCount?: number | null;
  channelNotifyStatus?: string | null;
  kafkaMessageCount?: number | null;
  nextRunAt?: number | null;
}

export const workflowAPI = {
  list: (params?: { category?: string; status?: string; excludeId?: string }) =>
    client.get<Workflow[]>('/api/workflow', { params }),

  listSummaries: (params?: { category?: string; status?: string; excludeId?: string }) =>
    client.get<WorkflowSummary[]>('/api/workflow-summaries', { params }),
  
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
    markdownContent?: string;
    editMarkdownContent?: string;
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
  
  getHistory: (id: string, params?: { limit?: number; triggerId?: string; triggerType?: string }) =>
    client.get<WorkflowExecution[]>(`/api/workflow/${id}/history`, { params }),
  
  getExecution: (
    workflowId: string,
    execId: string,
    params?: { stepOffset?: number; stepLimit?: number },
  ) =>
    client.get<WorkflowExecution>(`/api/workflow/${workflowId}/history/${execId}`, { params }),

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

  publish: (id: string, data?: { driver?: WorkflowServiceDriver }) =>
    client.post<WorkflowService>(`/api/workflow/${id}/publish`, data, { timeout: 600000 }),

  unpublish: (id: string) =>
    client.post<{ ok: boolean }>(`/api/workflow/${id}/unpublish`),

  getService: (id: string) =>
    client.get<WorkflowService | null>(`/api/workflow/${id}/service`),

  deleteService: (id: string) =>
    client.delete<{ ok: boolean; workflowId: string }>(`/api/workflow/${id}/service`),

  getConfig: (id: string) =>
    client.get<WorkflowIntegrationConfigResponse>(`/api/workflow/${id}/config`),

  updateConfig: (id: string, config: WorkflowIntegrationConfig) =>
    client.put<WorkflowIntegrationConfigResponse>(`/api/workflow/${id}/config`, config),

  syncConfig: (id: string) =>
    client.post<WorkflowIntegrationConfigResponse>(`/api/workflow/${id}/config/sync`),

  listServices: () =>
    client.get<WorkflowService[]>('/api/workflow-services'),

  getTriggers: (id: string) =>
    client.get<WorkflowTriggerRecord[]>(`/api/workflow/${id}/triggers`),

  createTrigger: (id: string, trigger: WorkflowTrigger) =>
    client.post<{ trigger: WorkflowTrigger; status?: WorkflowTriggerStatus }>(
      `/api/workflow/${id}/triggers`,
      trigger,
    ),

  updateTrigger: (id: string, triggerId: string, trigger: WorkflowTrigger) =>
    client.put<{ trigger: WorkflowTrigger; status?: WorkflowTriggerStatus }>(
      `/api/workflow/${id}/triggers/${triggerId}`,
      trigger,
    ),

  deleteTrigger: (id: string, triggerId: string) =>
    client.delete<{ ok: boolean; triggerId: string }>(`/api/workflow/${id}/triggers/${triggerId}`),

  getTriggerStatus: (id: string, triggerId: string) =>
    client.get<WorkflowTriggerStatus>(`/api/workflow/${id}/triggers/${triggerId}/status`),

  previewTriggerMapping: (
    id: string,
    triggerId: string,
    payload: { body?: unknown; headers?: Record<string, any>; query?: Record<string, any>; pathParams?: Record<string, any> },
  ) =>
    client.post<WorkflowTriggerPreview>(`/api/workflow/${id}/triggers/${triggerId}/preview-mapping`, payload),

  testTrigger: (
    id: string,
    triggerId: string,
    payload: { body?: unknown; headers?: Record<string, any>; query?: Record<string, any>; pathParams?: Record<string, any> },
  ) =>
    client.post<Record<string, any>>(`/api/workflow/${id}/triggers/${triggerId}/test`, payload),

  listTriggerPlugins: () =>
    client.get<WorkflowTriggerPlugin[]>('/api/workflow-trigger-plugins'),

  saveKafkaConfig: (id: string, config: {
    enabled?: boolean;
    inputBroker?: string;
    inputTopic?: string;
    inputGroupId?: string;
    inputKey?: string;
    autoOffsetReset?: string;
    inputs?: Record<string, any>;
  }) =>
    client.post<{ ok: boolean; consumer?: KafkaConsumerStatus }>(
      `/api/workflow/${id}/kafka-config`,
      config,
    ),

  getKafkaConfig: (id: string) =>
    client.get<KafkaConfig | null>(`/api/workflow/${id}/kafka-config`),

  getKafkaStatus: (id: string) =>
    client.get<KafkaConsumerStatus>(`/api/workflow/${id}/kafka-status`),

  savePollerConfig: (id: string, config: WorkflowPollerConfig) =>
    client.post<{ ok: boolean; status?: WorkflowPollerStatus }>(
      `/api/workflow/${id}/poller-config`,
      config,
    ),

  getPollerConfig: (id: string) =>
    client.get<WorkflowPollerConfig | null>(`/api/workflow/${id}/poller-config`),

  getPollerStatus: (id: string) =>
    client.get<WorkflowPollerStatus>(`/api/workflow/${id}/poller-status`),

  runPollerOnce: (id: string) =>
    client.post<{ ok: boolean; status?: WorkflowPollerStatus }>(
      `/api/workflow/${id}/poller-run-once`,
    ),

  saveSyslogConfig: (id: string, config: {
    enabled?: boolean;
    protocol?: string;
    host?: string;
    port?: number;
    format?: string;
    inputKey?: string;
  }) =>
    client.post<{ ok: boolean; listener?: SyslogListenerStatus }>(
      `/api/workflow/${id}/syslog-config`,
      config,
    ),

  getSyslogConfig: (id: string) =>
    client.get<SyslogConfig | null>(`/api/workflow/${id}/syslog-config`),

  getSyslogStatus: (id: string) =>
    client.get<SyslogListenerStatus>(`/api/workflow/${id}/syslog-status`),

  runNode: (id: string, data: { nodeId: string; inputs?: Record<string, any> }) =>
    client.post<WorkflowNodeExecution>(`/api/workflow/${id}/run-node`, { node_id: data.nodeId, inputs: data.inputs ?? {} }),

  getSampleInputs: (id: string) =>
    client.get<{ sampleInputs: Record<string, any> }>(`/api/workflow/${id}/sample-inputs`),

  saveSampleInputs: (id: string, sampleInputs: Record<string, any>) =>
    client.post<{ ok: boolean }>(`/api/workflow/${id}/sample-inputs`, { sampleInputs }),
};

export function workflowAPIEndpoints(id: string, triggerId = '{triggerId}') {
  const workflowBase = `/api/workflow/${id}`;
  const triggerBase = `${workflowBase}/triggers`;
  const triggerRecord = `${triggerBase}/${triggerId}`;

  return {
    config: {
      read: `GET ${workflowBase}/config`,
      write: `PUT ${workflowBase}/config`,
      syncFallback: `POST ${workflowBase}/config/sync`,
    },
    apiService: {
      read: `GET ${workflowBase}/service`,
      publish: `POST ${workflowBase}/publish`,
      unpublish: `POST ${workflowBase}/unpublish`,
      delete: `DELETE ${workflowBase}/service`,
    },
    triggers: {
      list: `GET ${triggerBase}`,
      create: `POST ${triggerBase}`,
      update: `PUT ${triggerRecord}`,
      delete: `DELETE ${triggerRecord}`,
      status: `GET ${triggerRecord}/status`,
      previewMapping: `POST ${triggerRecord}/preview-mapping`,
      test: `POST ${triggerRecord}/test`,
      invokeWebhook: `/webhook/workflows/${id}/${triggerId}`,
      plugins: 'GET /api/workflow-trigger-plugins',
    },
    legacyAdapters: {
      kafkaConfig: `GET/POST ${workflowBase}/kafka-config`,
      kafkaStatus: `GET ${workflowBase}/kafka-status`,
      pollerConfig: `GET/POST ${workflowBase}/poller-config`,
      pollerStatus: `GET ${workflowBase}/poller-status`,
      pollerRunOnce: `POST ${workflowBase}/poller-run-once`,
      syslogConfig: `GET/POST ${workflowBase}/syslog-config`,
      syslogStatus: `GET ${workflowBase}/syslog-status`,
    },
  };
}
