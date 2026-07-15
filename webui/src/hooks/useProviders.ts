import { useState, useEffect, useCallback } from 'react';
import { providerAPI, getProviderCategory } from '@/api/provider';
import type { ProviderInfoV2, ProviderCategory } from '@/types';

export interface EnrichedProvider extends ProviderInfoV2 {
  configured: boolean;
  modelCount: number;
  category: ProviderCategory;
}

export function useProviders() {
  const [providers, setProviders] = useState<EnrichedProvider[]>([]);
  const [connectedIds, setConnectedIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchProviders = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await providerAPI.list();
      const data = response.data as any;

      let rawProviders: ProviderInfoV2[] = [];
      let connected: string[] = [];

      if (data && Array.isArray(data.all)) {
        rawProviders = data.all;
        connected = data.connected || [];
      } else if (Array.isArray(data)) {
        rawProviders = data;
      }

      const connectedSet = new Set(connected);

      const enriched: EnrichedProvider[] = rawProviders.map((p) => {
        const modelCount = p.models ? Object.keys(p.models).length : 0;
        const configured = connectedSet.has(p.id);
        const category = configured ? 'connected' : getProviderCategory(p.id);
        return {
          ...p,
          configured,
          modelCount,
          category,
        };
      });

      setProviders(enriched);
      setConnectedIds(connected);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch providers');
      setProviders([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  return {
    providers,
    connectedIds,
    loading,
    error,
    refetch: fetchProviders,
  };
}
