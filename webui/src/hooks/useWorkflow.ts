import { useCallback, useEffect, useMemo, useState } from 'react';
import { workflowAPI, Workflow } from '@/api/workflow';
import {
  createSharedResource,
  useRefreshOnResume,
  useSharedResource,
  type SharedResource,
} from './useSharedResource';

const WORKFLOW_LIST_STALE_TIME_MS = 5000;
const WORKFLOW_LIST_MIN_FETCH_INTERVAL_MS = 1000;
const MAX_WORKFLOW_LIST_RESOURCES = 80;

const workflowListResources = new Map<string, SharedResource<Workflow[]>>();

function makeWorkflowListKey(category?: string, status?: string): string {
  return JSON.stringify({
    category: category ?? null,
    status: status ?? null,
  });
}

function getWorkflowListResource(category?: string, status?: string): SharedResource<Workflow[]> {
  const key = makeWorkflowListKey(category, status);
  const existing = workflowListResources.get(key);
  if (existing) {
    workflowListResources.delete(key);
    workflowListResources.set(key, existing);
    return existing;
  }

  const resource = createSharedResource<Workflow[]>({
    initialData: [],
    staleTimeMs: WORKFLOW_LIST_STALE_TIME_MS,
    minFetchIntervalMs: WORKFLOW_LIST_MIN_FETCH_INTERVAL_MS,
    fetcher: async () => {
      const response = await workflowAPI.list({ category, status });
      return Array.isArray(response.data) ? response.data : [];
    },
    fallbackDataOnError: [],
    getErrorMessage: (err) => (err instanceof Error && err.message ? err.message : 'Failed to fetch workflows'),
  });

  workflowListResources.set(key, resource);
  if (workflowListResources.size > MAX_WORKFLOW_LIST_RESOURCES) {
    const oldestKey = workflowListResources.keys().next().value;
    if (oldestKey) workflowListResources.delete(oldestKey);
  }
  return resource;
}

export function __resetWorkflowResourcesForTesting(): void {
  workflowListResources.forEach((resource) => resource.resetForTesting());
  workflowListResources.clear();
}

export function __getWorkflowResourceCacheSizeForTesting(): number {
  return workflowListResources.size;
}

export function useWorkflows(category?: string, status?: string) {
  const resource = useMemo(
    () => getWorkflowListResource(category, status),
    [category, status],
  );
  const {
    data: workflows,
    loading,
    error,
    refetch: fetchWorkflows,
  } = useSharedResource(resource);

  const refreshOnResume = useCallback(
    () => resource.fetch({ silent: true }),
    [resource],
  );
  useRefreshOnResume(refreshOnResume);

  return {
    workflows,
    loading,
    error,
    refetch: () => fetchWorkflows(),
  };
}

export function useWorkflow(id?: string) {
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchWorkflow = async () => {
    if (!id) return;
    
    try {
      setLoading(true);
      setError(null);
      const response = await workflowAPI.get(id);
      setWorkflow(response.data);
    } catch (err: any) {
      setError(err.message || 'Failed to fetch workflow');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchWorkflow();
  }, [id]);

  return {
    workflow,
    loading,
    error,
    refetch: fetchWorkflow,
  };
}
