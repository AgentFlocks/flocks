import client from './client';
import type {
  ProviderCredentials,
  ProviderCredentialInput,
  ProviderInfoV2,
  ModelDefinitionV2,
  DefaultModelConfig,
  UsageStats,
  CustomProviderCreate,
  CustomProviderInfo,
  APIServiceSummary,
  CustomModelCreate,
  CustomModelInfo,
  ModelSettingV2,
  CatalogProvider,
  APIServiceMetadata,
} from '@/types';

const API_SERVICES_LIST_CACHE_TTL_MS = 5000;

function requestApiServicesList() {
  return client.get<APIServiceSummary[]>('/api/provider/api-services');
}

type APIServiceListResponse = Awaited<ReturnType<typeof requestApiServicesList>>;

let apiServicesListInFlight: Promise<APIServiceListResponse> | null = null;
let apiServicesListInFlightGeneration: number | null = null;
let apiServicesListCache: { response: APIServiceListResponse; updatedAt: number } | null = null;
let apiServicesListGeneration = 0;

function cloneApiServicesResponse(response: APIServiceListResponse): APIServiceListResponse {
  const services = Array.isArray(response.data) ? response.data : [];
  return {
    ...response,
    data: services.map((service) => ({ ...service })),
  };
}

function invalidateApiServicesListCache(): void {
  apiServicesListGeneration += 1;
  apiServicesListCache = null;
}

function listApiServicesCached(options: { force?: boolean } = {}) {
  const useCache = !options.force;
  if (useCache && apiServicesListCache && Date.now() - apiServicesListCache.updatedAt < API_SERVICES_LIST_CACHE_TTL_MS) {
    return Promise.resolve(cloneApiServicesResponse(apiServicesListCache.response));
  }
  if (
    useCache
    && apiServicesListInFlight
    && apiServicesListInFlightGeneration === apiServicesListGeneration
  ) {
    return apiServicesListInFlight.then(cloneApiServicesResponse);
  }

  if (options.force) {
    invalidateApiServicesListCache();
  }
  const requestGeneration = apiServicesListGeneration;
  const request = requestApiServicesList()
    .then((response) => {
      if (requestGeneration === apiServicesListGeneration) {
        apiServicesListCache = {
          response: cloneApiServicesResponse(response),
          updatedAt: Date.now(),
        };
      }
      return response;
    })
    .finally(() => {
      if (apiServicesListInFlight === request) {
        apiServicesListInFlight = null;
        apiServicesListInFlightGeneration = null;
      }
    });
  apiServicesListInFlight = request;
  apiServicesListInFlightGeneration = requestGeneration;

  return request.then(cloneApiServicesResponse);
}

// ==================== Provider API (Legacy + Enhanced) ====================

export const providerAPI = {
  /** List all providers (returns Flocks-compatible format) */
  list: () =>
    client.get<{ all: ProviderInfoV2[]; default: Record<string, string>; connected: string[] }>('/api/provider'),

  get: (id: string) =>
    client.get<ProviderInfoV2>(`/api/provider/${id}`),

  getModels: (id: string) =>
    client.get<any[]>(`/api/provider/${id}/models`),

  update: (id: string, data: {
    api_key?: string;
    base_url?: string;
    custom_settings?: Record<string, any>;
  }) =>
    client.put(`/api/provider/${id}`, data),

  test: (id: string) =>
    client.post(`/api/provider/${id}/test`),

  getStatistics: (id: string) =>
    client.get(`/api/provider/${id}/statistics`),

  // Credentials management (LLM providers — uses _llm_key convention)
  getCredentials: (id: string) =>
    client.get<ProviderCredentials>(`/api/provider/${id}/credentials`),

  revealCredentials: (id: string) =>
    client.post<ProviderCredentials>(`/api/provider/${id}/credentials/reveal`),

  setCredentials: (id: string, credentials: ProviderCredentialInput) =>
    client.post<{ success: boolean; message: string }>(`/api/provider/${id}/credentials`, credentials),

  // Credentials management (API services — uses _api_key convention)
  getServiceCredentials: (id: string) =>
    client.get<ProviderCredentials>(`/api/provider/${id}/service-credentials`),

  setServiceCredentials: (id: string, credentials: ProviderCredentialInput) =>
    client.post<{ success: boolean; message: string }>(`/api/provider/${id}/service-credentials`, credentials)
      .then((response) => {
        invalidateApiServicesListCache();
        return response;
      }),

  deleteCredentials: (id: string) =>
    client.delete<{ success: boolean }>(`/api/provider/${id}/credentials`),

  testCredentials: (id: string, modelId?: string) =>
    client.post<{
      success: boolean; message: string; latency_ms?: number;
      model_count?: number; error?: string;
      model_id?: string; question?: string; answer?: string;
    }>(
      `/api/provider/${id}/test-credentials`,
      modelId ? { model_id: modelId } : {}
    ),

  // API service status (connectivity)
  listApiServices: (options?: { force?: boolean }) =>
    listApiServicesCached(options),

  getServiceMetadata: (id: string) =>
    client.get<APIServiceMetadata>(`/api/provider/${id}/metadata`),

  updateApiService: (id: string, data: { enabled: boolean; verify_ssl?: boolean }) =>
    client.patch<APIServiceSummary>(`/api/provider/api-services/${id}`, data)
      .then((response) => {
        invalidateApiServicesListCache();
        return response;
      }),

  deleteApiService: (id: string) =>
    client.delete<{ success: boolean }>(`/api/provider/api-services/${id}`)
      .then((response) => {
        invalidateApiServicesListCache();
        return response;
      }),

  getApiServiceStatuses: () =>
    client.get<Record<string, { status: string; message?: string; latency_ms?: number; tool_tested?: string; error?: string; checked_at?: number }>>(
      '/api/provider/api-services/status'
    ),

  refreshApiServiceStatuses: () =>
    client.post<{ statuses: Record<string, any>; refreshed_at: number }>(
      '/api/provider/api-services/refresh'
    ).then((response) => {
      invalidateApiServicesListCache();
      return response;
    }),

  getApiServiceStatus: (id: string) =>
    client.get<{ status: string; message?: string; latency_ms?: number; tool_tested?: string; error?: string; checked_at?: number }>(
      `/api/provider/${id}/status`
    ),

  // API service metadata
  getMetadata: (id: string) =>
    client.get<APIServiceMetadata>(`/api/provider/${id}/metadata`),
};

// ==================== Provider Catalog API ====================

export const catalogAPI = {
  /** Get all available provider types with metadata and models */
  list: () =>
    client.get<{ providers: CatalogProvider[] }>('/api/provider/catalog'),
};

// ==================== Model V2 API ====================

export const modelV2API = {
  /** List model definitions with full metadata */
  listDefinitions: (options?: { provider?: string; enabled_only?: boolean }) =>
    client.get<{ models: ModelDefinitionV2[]; total: number }>(
      '/api/model/v2/definitions',
      {
        params: {
          ...(options?.provider ? { provider: options.provider } : {}),
          ...(options?.enabled_only ? { enabled_only: true } : {}),
        },
      }
    ),

  /** Get single model definition */
  getDefinition: (providerId: string, modelId: string) =>
    client.get<ModelDefinitionV2>(`/api/model/v2/definitions/${providerId}/${modelId}`),

  /** Create or update a model definition (upsert) */
  createDefinition: (providerId: string, data: CustomModelCreate) =>
    client.post<CustomModelInfo>(`/api/custom/models/${providerId}`, data),

  /** Delete model definition */
  deleteDefinition: (providerId: string, modelId: string) =>
    client.delete(`/api/model/v2/definitions/${providerId}/${modelId}`),
};

// ==================== Default Model API ====================

export const defaultModelAPI = {
  /** Get all default models */
  getAll: () =>
    client.get<{ defaults: DefaultModelConfig[] }>('/api/default-model'),

  /** Get default model for a type */
  get: (modelType: string) =>
    client.get<DefaultModelConfig>(`/api/default-model/${modelType}`),

  /**
   * Get the resolved default LLM model.
   * Checks both structured default_models.llm and legacy top-level "model" string.
   */
  getResolved: () =>
    client.get<{ provider_id: string; model_id: string }>('/api/default-model/resolved'),

  /** Set default model for a type */
  set: (modelType: string, providerId: string, modelId: string) =>
    client.put<DefaultModelConfig>(`/api/default-model/${modelType}`, {
      provider_id: providerId,
      model_id: modelId,
    }),

  /** Delete default model for a type */
  delete: (modelType: string) =>
    client.delete(`/api/default-model/${modelType}`),

};

// ==================== Usage API ====================

export const usageAPI = {
  /** Get usage statistics */
  getSummary: (params?: { start_date?: string; end_date?: string; provider_id?: string }) =>
    client.get<UsageStats>('/api/usage/summary', { params }),
};

// ==================== Custom Provider API ====================

export const customAPI = {
  /** List custom providers */
  listProviders: () =>
    client.get<CustomProviderInfo[]>('/api/custom/providers'),

  /** Create custom provider */
  createProvider: (data: CustomProviderCreate) =>
    client.post<CustomProviderInfo>('/api/custom/providers', data),

  /** Delete custom provider */
  deleteProvider: (id: string) =>
    client.delete(`/api/custom/providers/${id}`),

  /** List custom models for provider */
  listModels: (providerId: string) =>
    client.get<CustomModelInfo[]>(`/api/custom/models/${providerId}`),

  /** Add custom model to provider */
  createModel: (providerId: string, data: CustomModelCreate) =>
    client.post<CustomModelInfo>(`/api/custom/models/${providerId}`, data),

  /** Delete custom model */
  deleteModel: (providerId: string, modelId: string) =>
    client.delete(`/api/custom/models/${providerId}/${modelId}`),
};

// ==================== Model Settings API ====================

export const modelSettingsAPI = {
  /** Get model settings */
  get: (providerId: string, modelId: string) =>
    client.get<ModelSettingV2>(`/api/model/v2/settings/${providerId}/${modelId}`),

  /** Update model settings (enable/disable, parameters) */
  update: (providerId: string, modelId: string, data: { enabled?: boolean; default_parameters?: Record<string, any> }) =>
    client.put<ModelSettingV2>(`/api/model/v2/settings/${providerId}/${modelId}`, data),
};

// ==================== Provider Category Helpers ====================

const CHINESE_PROVIDERS = new Set([
  'deepseek', 'volcengine', 'alibaba', 'tencent', 'siliconflow',
  'moonshot', 'zhipu', 'baichuan', 'minimax', 'yi', 'stepfun',
  'threatbook',
]);
const LOCAL_PROVIDERS = new Set(['ollama', 'local', 'openai-compatible', 'gateway']);

export function getProviderCategory(id: string): 'chinese' | 'international' | 'local' {
  if (CHINESE_PROVIDERS.has(id)) return 'chinese';
  if (LOCAL_PROVIDERS.has(id)) return 'local';
  return 'international';
}

export function getCategoryLabel(category: string): string {
  switch (category) {
    case 'connected': return '已连接';
    case 'chinese': return '中国 Provider';
    case 'international': return '国际 Provider';
    case 'local': return '本地 / 自定义';
    default: return category;
  }
}
