/**
 * React hook for managing hook system
 */

import { useState, useEffect } from 'react';
import { hooksApi, type HookStatus } from '@/api/hooks';

export function useHooks() {
  const [status, setStatus] = useState<HookStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await hooksApi.getStatus();
      setStatus(data);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch hook status');
      setStatus(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStatus();
  }, []);

  return {
    status,
    loading,
    error,
    refetch: fetchStatus,
  };
}
