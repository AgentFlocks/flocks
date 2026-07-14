import { useCallback, useEffect } from 'react';
import { apiClient } from '@/api/client';
import { createSharedResource, useSharedResource } from './useSharedResource';

export type BackendStatus = 'connected' | 'connecting' | 'disconnected' | 'error';

interface BackendStatusState {
  status: BackendStatus;
  message?: string;
  lastChecked?: Date;
}

const BACKEND_HEALTH_POLL_INTERVAL_MS = 3_600_000;
const BACKEND_HEALTH_STALE_TIME_MS = 5_000;
const BACKEND_HEALTH_MIN_FETCH_INTERVAL_MS = 1_000;

async function fetchBackendHealth(): Promise<BackendStatusState> {
  try {
    // 使用较短的超时时间快速检测连接问题
    const response = await apiClient.get('/api/health', {
      timeout: 5000,
    });

    if (response.status === 200) {
      return {
        status: 'connected',
        message: '后端服务正常',
        lastChecked: new Date(),
      };
    }

    return {
      status: 'error',
      message: '后端返回异常状态',
      lastChecked: new Date(),
    };
  } catch (error: any) {
    // 根据错误类型提供不同的反馈
    let message = '无法连接到后端服务';
    let status: BackendStatus = 'disconnected';

    if (error.code === 'ECONNABORTED') {
      message = '连接超时';
    } else if (error.code === 'ERR_NETWORK') {
      message = '后端服务可能正在重启';
      status = 'connecting';
    } else if (error.response?.status === 503) {
      message = '后端服务暂时不可用';
      status = 'connecting';
    }

    return {
      status,
      message,
      lastChecked: new Date(),
    };
  }
}

const backendStatusResource = createSharedResource<BackendStatusState>({
  initialData: {
    status: 'connecting',
  },
  staleTimeMs: BACKEND_HEALTH_STALE_TIME_MS,
  minFetchIntervalMs: BACKEND_HEALTH_MIN_FETCH_INTERVAL_MS,
  fetcher: fetchBackendHealth,
  fallbackDataOnError: (previous) => previous,
});

let backendHealthPollSubscriberCount = 0;
let backendHealthPollIntervalId: number | null = null;

function subscribeBackendHealthPolling(): () => void {
  backendHealthPollSubscriberCount += 1;

  if (backendHealthPollSubscriberCount === 1) {
    backendHealthPollIntervalId = window.setInterval(() => {
      void backendStatusResource.fetch({ silent: true });
    }, BACKEND_HEALTH_POLL_INTERVAL_MS);
  }

  return () => {
    backendHealthPollSubscriberCount = Math.max(0, backendHealthPollSubscriberCount - 1);
    if (backendHealthPollSubscriberCount === 0 && backendHealthPollIntervalId !== null) {
      window.clearInterval(backendHealthPollIntervalId);
      backendHealthPollIntervalId = null;
    }
  };
}

export function __resetBackendStatusResourceForTesting(): void {
  backendStatusResource.resetForTesting();
  backendHealthPollSubscriberCount = 0;
  if (backendHealthPollIntervalId !== null) {
    window.clearInterval(backendHealthPollIntervalId);
    backendHealthPollIntervalId = null;
  }
}

/**
 * 监控后端连接状态的 Hook
 * 定期检查后端健康状态，并在后端重启时提供友好的用户反馈
 */
export function useBackendStatus() {
  const { data: state } = useSharedResource(backendStatusResource);

  const checkHealth = useCallback(async () => {
    const nextState = await backendStatusResource.fetch({ force: true });
    return nextState.status === 'connected';
  }, []);

  useEffect(() => {
    return subscribeBackendHealthPolling();
  }, []);

  return {
    ...state,
    checkHealth,
  };
}
