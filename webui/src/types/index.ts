// Common types for the Flocks WebUI

/**
 * Session 会话信息
 */
export interface Session {
  id: string;
  slug: string;
  projectID: string;
  directory: string;
  parentID?: string;
  summary?: SessionSummary;
  share?: SessionShare;
  title: string;
  version: string;
  time: SessionTime;
  permission?: PermissionRule[];
  revert?: SessionRevert;
  /** Session category: 'user' | 'workflow' | 'task' | 'entity-config' | ... */
  category?: string;
}

export interface SessionTime {
  created: number;
  updated: number;
  compacting?: number;
  archived?: number;
}

export interface SessionSummary {
  title?: string;
  diffs?: FileDiff[];
}

export interface SessionShare {
  id: string;
  createdAt: number;
}

export interface SessionRevert {
  messageID: string;
  snapshotID?: string;
}

export interface FileDiff {
  file: string;
  before: string;
  after: string;
  additions: number;
  deletions: number;
}

export interface PermissionRule {
  permission: string;
  action: 'allow' | 'deny';
  pattern: string;
}

/**
 * Message 消息信息
 */
export interface Message {
  id: string;
  sessionID: string;
  role: 'user' | 'assistant' | 'system';
  parts: MessagePart[];
  agent?: string;
  model?: string;
  timestamp: number;
  // 用户消息特有字段
  system?: string;
  tools?: Record<string, boolean>;
  // 助手消息特有字段
  parentID?: string;
  modelID?: string;
  providerID?: string;
  cost?: number;
  tokens?: TokenUsage;
  finish?: string;
  error?: MessageError;
  /** Archived by compaction (soft-deleted, still visible in collapsed UI) */
  compacted?: boolean;
}

export interface TokenUsage {
  input: number;
  output: number;
  reasoning?: number;
  cache?: {
    read: number;
    write: number;
  };
}

export interface MessageError {
  code: string;
  message: string;
}

/**
 * MessagePart 消息部分
 */
export interface MessagePart {
  id: string;
  type: 'text' | 'tool' | 'file' | 'reasoning' | 'toolCall' | 'toolResult' | 'thinking' | 'image' | 'step-start' | 'step-finish';
  // Text part
  text?: string;
  synthetic?: boolean;
  ignored?: boolean;
  // Tool part
  tool?: string;
  callID?: string;
  state?: ToolState;
  // File part
  mime?: string;
  filename?: string;
  url?: string;
  // Legacy support
  toolCall?: ToolCall;
  toolResult?: ToolResult;
  thinking?: string;
  image?: {
    url: string;
    alt?: string;
  };
}

export interface ToolState {
  status: 'pending' | 'running' | 'completed' | 'error';
  input?: Record<string, any>;
  output?: any;
  error?: string;
  title?: string;
  metadata?: Record<string, any>;
  time?: {
    start: number;
    end?: number;
  };
}

export interface ToolCall {
  id: string;
  name: string;
  params: Record<string, any>;
}

export interface ToolResult {
  id: string;
  success: boolean;
  output?: any;
  error?: string;
}

export interface Provider {
  providerId: string;
  name: string;
  description?: string;
  configured: boolean;
  available: boolean;
  models: string[];
}

export interface Model {
  modelID: string;
  providerID: string;
  name: string;
  description?: string;
  capabilities: string[];
  contextWindow?: number;
  maxOutput?: number;
  pricing?: any;
  hidden: boolean;
}

export interface ToolParameter {
  name: string;
  type: string;
  description: string;
  required: boolean;
  default?: any;
  enum?: string[];
}

export type ToolSource = 'builtin' | 'mcp' | 'api' | 'custom' | 'plugin_py' | 'plugin_yaml';

export interface Tool {
  name: string;
  description: string;
  description_cn?: string;
  category: string;
  source: ToolSource;
  source_name?: string;
  parameters: ToolParameter[];
  enabled: boolean;
  /** Factory default from the YAML/registration source (no overlay applied). */
  enabled_default?: boolean;
  /** True when a user setting is recorded in flocks.json `tool_settings`. */
  enabled_customized?: boolean;
  requires_confirmation: boolean;
}

export interface MCPServer {
  name: string;
  url?: string;
  status: 'connected' | 'disconnected' | 'error' | 'connecting' | 'failed' | 'needs_auth' | 'disabled';
  tools: string[];
  resources: string[];
  error?: string;
  connected_at?: number;
  tools_count?: number;
  resources_count?: number;
  metadata?: Record<string, any>;
}

export interface APIServiceSummary {
  id: string;
  name: string;
  enabled: boolean;
  status: string;
  message?: string;
  latency_ms?: number;
  checked_at?: number;
  tool_count: number;
  description?: string;
  description_cn?: string;
  builtin?: boolean;
  verify_ssl: boolean;
}

export interface APIServiceCredentialField {
  key: string;
  label: string;
  description?: string;
  storage: 'config' | 'secret';
  sensitive: boolean;
  required: boolean;
  input_type: 'text' | 'password' | 'url' | string;
  config_key: string;
  secret_id?: string;
  default_value?: string;
}

export interface APIServiceMetadata {
  name: string;
  version?: string;
  description?: string;
  description_cn?: string;
  author?: string;
  category?: string;
  authentication?: any;
  dependencies?: string[];
  apis?: Array<{ name: string; endpoint?: string; method?: string; description: string }>;
  rate_limits?: any;
  tags?: string[];
  base_url?: string;
  docs_url?: string;
  credential_schema?: APIServiceCredentialField[];
  verify_ssl?: boolean;
}

export interface MCPServerConfig {
  type: 'stdio' | 'sse';
  url?: string;
  command?: string | string[];
  args?: string[];
}

export interface MCPServerDetail {
  name: string;
  status: {
    status: string;
    error?: string;
    connected_at?: number;
    tools_count: number;
    resources_count: number;
    metadata?: Record<string, any>;
  };
  tools: MCPToolDef[];
  resources: MCPResourceDef[];
  server_version?: string;
  protocol_version?: string;
  /** 配置（与添加 MCP 表单一致，用于详情展示） */
  config?: MCPServerConfig | null;
}

export interface MCPToolDef {
  name: string;
  description?: string;
  input_schema?: Record<string, any>;
}

export interface MCPResourceDef {
  name: string;
  uri: string;
  description?: string;
  mime_type?: string;
  server: string;
}

export interface MCPCredentials {
  secret_id?: string;
  api_key_masked?: string;
  has_credential: boolean;
}

export interface MCPCredentialInput {
  secret_id?: string;
  api_key: string;
}

// ==================== MCP Catalog Types ====================

export interface MCPCatalogEnvVar {
  required: boolean;
  description: string;
  default?: string;
  secret: boolean;
}

export interface MCPCatalogInstall {
  pip?: string;
  npx?: string;
  command?: string[];
  local_command?: string[];
  note?: string;
}

export interface MCPCatalogEntry {
  id: string;
  name: string;
  description: string;
  description_cn?: string;
  category: string;
  tool_type: 'mcp' | 'api';
  github: string;
  language: string;
  license: string;
  stars: number;
  transport: string;
  install: MCPCatalogInstall;
  env_vars: Record<string, MCPCatalogEnvVar>;
  system_deps: string[];
  tags: string[];
  official: boolean;
  requires_auth: boolean;
}

export interface MCPCatalogCategory {
  label: string;
  description: string;
}

export interface MCPCatalogStats {
  version: string;
  total_servers: number;
  total_categories: number;
  official_servers: number;
  requires_auth: number;
  by_category: Record<string, number>;
  by_language: Record<string, number>;
}

export interface ProviderCredentials {
  secret_id?: string;
  api_key?: string;
  api_key_masked?: string;
  secret?: string;
  secret_masked?: string;
  base_url?: string;
  username?: string;
  fields?: Record<string, string | undefined>;
  secret_ids?: Record<string, string>;
  has_credential: boolean;
}

export interface ProviderCredentialInput {
  secret_id?: string;
  api_key?: string;
  secret?: string;
  base_url?: string;
  username?: string;
  fields?: Record<string, string | undefined>;
  provider_name?: string;
}

// ==================== Model Management V2 Types ====================

/** Provider info from backend (Flocks-compatible format) */
export interface ProviderInfoV2 {
  id: string;
  name: string;
  source: string;
  env: string[];
  key: string | null;
  options: Record<string, any>;
  models: Record<string, ProviderModelInfo>;
  // Derived on frontend
  configured?: boolean;
  modelCount?: number;
  category?: ProviderCategory;
}

export interface ProviderModelInfo {
  id: string;
  name: string;
  providerID: string;
  attachment: boolean;
  reasoning: boolean;
  temperature: boolean;
  tool_call: boolean;
  limit: { context: number; output: number };
  options?: Record<string, any>;
}

export type ProviderCategory = 'connected' | 'chinese' | 'international' | 'local';

/** V2 Model Definition from /api/model/v2/definitions */
export interface ModelDefinitionV2 {
  id: string;
  name: string;
  provider_id: string;
  family?: string;
  model_type: string;
  status: string;
  /** 'customizable' = user-added (deletable); 'predefined' = catalog/SDK model */
  fetch_from?: 'predefined' | 'customizable';
  capabilities: ModelCapabilitiesV2;
  limits?: ModelLimitsV2;
  pricing?: PriceConfigV2;
  parameter_rules?: ParameterRuleV2[];
  release_date?: string;
}

export interface ModelCapabilitiesV2 {
  features: string[];
  modalities?: { input: string[]; output: string[] };
  supports_streaming: boolean;
  supports_tools: boolean;
  supports_vision?: boolean;
  supports_reasoning?: boolean;
  supports_temperature?: boolean;
  supports_json_mode?: boolean;
  supports_structured_output?: boolean;
}

export interface ModelLimitsV2 {
  context_window: number;
  max_output_tokens: number;
}

export interface PriceConfigV2 {
  input: number;
  output: number;
  cache_read?: number;
  cache_write?: number;
  unit: number;
  currency: string;
}

export interface ParameterRuleV2 {
  name: string;
  label: string;
  type: string;
  default?: any;
  min?: number;
  max?: number;
  options?: string[];
  help_text?: string;
}

/** Default model config from /api/default-model */
export interface DefaultModelConfig {
  model_type: string;
  provider_id: string;
  model_id: string;
}

/** Usage summary from /api/usage/summary */
export interface UsageStats {
  summary: {
    total_tokens: number;
    total_input_tokens: number;
    total_output_tokens: number;
    total_cost: number;
    total_requests: number;
    currency: string;
    cost_by_currency: { currency: string; total_cost: number }[];
  };
  by_provider: {
    provider_id: string;
    total_tokens: number;
    total_cost: number;
    request_count: number;
    currency: string;
    cost_by_currency: { currency: string; total_cost: number }[];
  }[];
  by_model: {
    provider_id: string;
    model_id: string;
    total_tokens: number;
    total_cost: number;
    request_count: number;
    currency: string;
    cost_by_currency: { currency: string; total_cost: number }[];
  }[];
  daily: {
    date: string;
    total_tokens: number;
    total_cost: number;
    request_count: number;
    currency: string;
    cost_by_currency: { currency: string; total_cost: number }[];
  }[];
}

/** Custom provider creation */
export interface CustomProviderCreate {
  name: string;
  base_url: string;
  api_key?: string;
  description?: string;
}

export interface CustomProviderInfo {
  id: string;
  name: string;
  base_url: string;
  description?: string;
  created_at: string;
}

/** Custom model creation */
export interface CustomModelCreate {
  model_id: string;
  name: string;
  context_window?: number;
  max_output_tokens?: number;
  supports_vision?: boolean;
  supports_tools?: boolean;
  supports_streaming?: boolean;
  supports_reasoning?: boolean;
  input_price?: number;
  output_price?: number;
  currency?: string;
}

export interface CustomModelInfo {
  id: string;
  provider_id: string;
  model_id: string;
  name: string;
  context_window: number;
  max_output_tokens: number;
  supports_vision: boolean;
  supports_tools: boolean;
  supports_streaming: boolean;
  supports_reasoning: boolean;
  input_price: number;
  output_price: number;
  currency: string;
  created_at: string;
}

/** Model setting (enable/disable + params) */
export interface ModelSettingV2 {
  provider_id: string;
  model_id: string;
  enabled: boolean;
  credential_id?: string;
  default_parameters: Record<string, any>;
}

// ==================== Provider Catalog Types ====================

/** Provider catalog entry from /api/provider/catalog */
export interface CatalogProvider {
  id: string;
  name: string;
  description: string | null;
  credential_schemas: CatalogCredentialSchema[];
  env_vars: string[];
  default_base_url: string | null;
  model_count: number;
  models: CatalogModel[];
  allow_multiple?: boolean;
}

export interface CatalogCredentialSchema {
  auth_method: string;
  fields: CatalogCredentialField[];
}

export interface CatalogCredentialField {
  name: string;
  label: string;
  type: 'text' | 'secret' | 'select';
  required: boolean;
  placeholder?: string;
  help_text?: string;
  options?: { label: string; value: string }[];
}

export interface CatalogModel {
  id: string;
  name: string;
  family?: string;
  model_type: string;
  status: string;
  capabilities: {
    supports_tools: boolean;
    supports_vision: boolean;
    supports_reasoning: boolean;
    supports_streaming: boolean;
  };
  limits?: {
    context_window: number;
    max_output_tokens: number;
  };
  pricing?: {
    input: number;
    output: number;
    currency: string;
  };
}

export interface Config {
  [key: string]: any;
}

export interface Permission {
  id: string;
  permission: string;
  action: 'allow' | 'deny';
  pattern: string;
  createdAt: number;
}

// View types
export type ViewType = 'card' | 'table';
export type Status = 'success' | 'error' | 'loading' | 'idle';
