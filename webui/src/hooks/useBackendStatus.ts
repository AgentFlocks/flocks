import { useState, useEffect, useCallback } from 'react';
import { apiClient } from '@/api/client';

export type BackendStatus = 'connected' | 'connecting' | 'disconnected' | 'error';

interface BackendStatusState {
  status: BackendStatus;
  message?: string;
  lastChecked?: Date;
}

/**
 * 监控后端连接状态的 Hook
 * 定期检查后端健康状态，并在后端重启时提供友好的用户反馈
 */
export function useBackendStatus() {
  const [state, setState] = useState<BackendStatusState>({
    status: 'connecting',
  });

  const checkHealth = useCallback(async () => {
    try {
      // 使用较短的超时时间快速检测连接问题
      const response = await apiClient.get('/api/health', {
        timeout: 5000,
      });
      
      if (response.status === 200) {
        setState({
          status: 'connected',
          message: '后端服务正常',
          lastChecked: new Date(),
        });
        return true;
      }
      
      setState({
        status: 'error',
        message: '后端返回异常状态',
        lastChecked: new Date(),
      });
      return false;
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
      
      setState({
        status,
        message,
        lastChecked: new Date(),
      });
      
      return false;
    }
  }, []);

  useEffect(() => {
    // 立即检查一次
    checkHealth();

    // 每 1 小时检查一次（3600000 毫秒）
    const interval = setInterval(() => {
      checkHealth();
    }, 3600000);

    return () => clearInterval(interval);
  }, [checkHealth]);

  return {
    ...state,
    checkHealth,
  };
}
