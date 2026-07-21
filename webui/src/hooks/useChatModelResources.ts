import { defaultModelAPI, modelV2API } from '@/api/provider';
import type { FallbackModelRef, ModelDefinitionV2 } from '@/types';
import {
  createSharedResource,
  useSharedResource,
  type SharedResourceFetchOptions,
} from './useSharedResource';

export type ResolvedDefaultModel = {
  providerID: string;
  modelID: string;
} | null;

const CHAT_MODEL_RESOURCES_STALE_TIME_MS = 10000;

const enabledModelDefinitionsResource = createSharedResource<ModelDefinitionV2[]>({
  initialData: [],
  staleTimeMs: CHAT_MODEL_RESOURCES_STALE_TIME_MS,
  minFetchIntervalMs: 1000,
  fetcher: async () => {
    const response = await modelV2API.listDefinitions({ enabled_only: true });
    return response?.data?.models ?? [];
  },
  fallbackDataOnError: [],
});

const resolvedDefaultModelResource = createSharedResource<ResolvedDefaultModel>({
  initialData: null,
  staleTimeMs: CHAT_MODEL_RESOURCES_STALE_TIME_MS,
  minFetchIntervalMs: 1000,
  fetcher: async () => {
    const response = await defaultModelAPI.getResolved();
    const { provider_id: providerID, model_id: modelID } = response?.data ?? {};
    if (!providerID || !modelID) return null;
    return { providerID, modelID };
  },
  fallbackDataOnError: null,
});

const fallbackModelsResource = createSharedResource<FallbackModelRef[]>({
  initialData: [],
  staleTimeMs: CHAT_MODEL_RESOURCES_STALE_TIME_MS,
  minFetchIntervalMs: 1000,
  fetcher: async () => {
    const response = await defaultModelAPI.getFallbacks();
    return response?.data?.fallback_providers ?? [];
  },
  fallbackDataOnError: [],
});

export function useEnabledChatModelDefinitions() {
  return useSharedResource(enabledModelDefinitionsResource);
}

export function useResolvedDefaultModel(enabled: boolean) {
  return useSharedResource(resolvedDefaultModelResource, {
    enabled,
    loadOnMount: enabled,
    silentInitialLoad: true,
  });
}

export function useFallbackModels() {
  return useSharedResource(fallbackModelsResource, {
    silentInitialLoad: true,
  });
}

export function fetchEnabledChatModelDefinitions(
  options?: SharedResourceFetchOptions,
): Promise<ModelDefinitionV2[]> {
  return enabledModelDefinitionsResource.fetch(options);
}

export function fetchResolvedDefaultModel(
  options?: SharedResourceFetchOptions,
): Promise<ResolvedDefaultModel> {
  return resolvedDefaultModelResource.fetch(options);
}

export function invalidateFallbackModels(): void {
  fallbackModelsResource.invalidate();
}

export function __resetChatModelResourcesForTesting(): void {
  enabledModelDefinitionsResource.resetForTesting();
  resolvedDefaultModelResource.resetForTesting();
  fallbackModelsResource.resetForTesting();
}
