import { useState, useEffect } from 'react';
import client from '@/api/client';

export interface LoopEngine {
  id: string;
  name: string;
  description?: string;
}

/**
 * Fetch available agent loop engines from GET /api/engine/list.
 *
 * The list always contains at least the built-in "native" engine.
 * Additional engines (e.g. "raptor") appear when the server-side adapter
 * is registered (i.e. hermes-agent is available).
 */
export function useLoopEngines() {
  const [engines, setEngines] = useState<LoopEngine[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    client
      .get<LoopEngine[]>('/api/engine/list')
      .then((res) => {
        if (!cancelled) {
          setEngines(Array.isArray(res.data) ? res.data : []);
        }
      })
      .catch(() => {
        // If the endpoint is unavailable (old server build), fall back to
        // showing only the native engine so the UI still works.
        if (!cancelled) {
          setEngines([{ id: 'native', name: 'Flocks Native', description: '' }]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { engines, loading };
}
