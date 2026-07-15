import { useState, useEffect } from 'react';
import { statsApi, SystemStats } from '@/api/stats';

export function useStats() {
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    let mounted = true;
    let intervalId: number | null = null;

    const fetchStats = async (isInitial = false) => {
      if (isInitial) setLoading(true);
      try {
        const data = await statsApi.getSystemStats();
        if (mounted) {
          setStats(data);
          // 用系统状态判断是否需要显示错误（后端不可达时 getSystemStats 返回 status:'error'）
          if (data.system.status === 'error') {
            setError(new Error(data.system.message));
          } else {
            setError(null);
          }
        }
      } finally {
        if (mounted) setLoading(false);
      }
    };

    fetchStats(true);

    intervalId = window.setInterval(() => {
      if (mounted) fetchStats(false);
    }, 30000);

    return () => {
      mounted = false;
      if (intervalId !== null) clearInterval(intervalId);
    };
  }, []);

  return { stats, loading, error };
}
