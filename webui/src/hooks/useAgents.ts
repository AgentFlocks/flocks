import { useState, useEffect } from 'react';
import { agentAPI, Agent } from '@/api/agent';

export function useAgents() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchAgents = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await agentAPI.list();
      setAgents(Array.isArray(response.data) ? response.data : []);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch agents');
      setAgents([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAgents();
  }, []);

  return {
    agents,
    loading,
    error,
    refetch: fetchAgents,
  };
}
