import { useCallback } from 'react';
import { providerAPI, getProviderCategory } from '@/api/provider';
import type { ProviderInfoV2, ProviderCategory } from '@/types';
import { createSharedResource, useSharedResource } from './useSharedResource';

export interface EnrichedProvider extends ProviderInfoV2 {
  configured: boolean;
  modelCount: number;
  category: ProviderCategory;
}

interface ProvidersResourceData {
  providers: EnrichedProvider[];
  connectedIds: string[];
}

const PROVIDER_LIST_STALE_TIME_MS = 5000;
const PROVIDER_LIST_MIN_FETCH_INTERVAL_MS = 1000;

function normalizeProvidersPayload(data: unknown): ProvidersResourceData {
  let rawProviders: ProviderInfoV2[] = [];
  let connectedIds: string[] = [];

  if (data && typeof data === 'object' && Array.isArray((data as any).all)) {
    rawProviders = (data as any).all;
    connectedIds = Array.isArray((data as any).connected) ? (data as any).connected : [];
  } else if (Array.isArray(data)) {
    rawProviders = data;
  }

  const connectedSet = new Set(connectedIds);
  const providers: EnrichedProvider[] = rawProviders.map((provider) => {
    const modelCount = provider.models ? Object.keys(provider.models).length : 0;
    const configured = connectedSet.has(provider.id);
    const category: ProviderCategory = configured ? 'connected' : getProviderCategory(provider.id);
    return {
      ...provider,
      configured,
      modelCount,
      category,
    };
  });

  return { providers, connectedIds };
}

const providersResource = createSharedResource<ProvidersResourceData>({
  initialData: {
    providers: [],
    connectedIds: [],
  },
  staleTimeMs: PROVIDER_LIST_STALE_TIME_MS,
  minFetchIntervalMs: PROVIDER_LIST_MIN_FETCH_INTERVAL_MS,
  fetcher: async () => {
    const response = await providerAPI.list();
    return normalizeProvidersPayload(response.data);
  },
  fallbackDataOnError: {
    providers: [],
    connectedIds: [],
  },
  getErrorMessage: (err) => (err instanceof Error && err.message ? err.message : 'Failed to fetch providers'),
});

export function __resetProvidersResourceForTesting(): void {
  providersResource.resetForTesting();
}

export function useProviders() {
  const {
    data,
    loading,
    error,
    refetch: fetchProviders,
  } = useSharedResource(providersResource);

  const refetch = useCallback(
    () => fetchProviders({ force: true }),
    [fetchProviders],
  );

  return {
    providers: data.providers,
    connectedIds: data.connectedIds,
    loading,
    error,
    refetch,
  };
}
