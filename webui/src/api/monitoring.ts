import client from './client';

export interface SystemStatus {
  status: 'healthy' | 'degraded' | 'down';
  uptime: number;
  activeSessions: number;
  activeAgents: number;
  mcpServers: Record<string, string>;
  timestamp: number;
}

export interface MetricsSnapshot {
  timestamp: number;
  messageRate: number;
  toolCallRate: number;
  errorRate: number;
  avgResponseTime: number;
  activeRequests: number;
}

export interface PerformanceData {
  category: 'llm' | 'tool' | 'api';
  name: string;
  avgDuration: number;
  p50?: number;
  p95?: number;
  p99?: number;
  count: number;
  errors: number;
}

export interface EventLog {
  id: string;
  timestamp: number;
  level: 'info' | 'warn' | 'error';
  service: string;
  event: string;
  message?: string;
  data?: Record<string, any>;
}

export const monitoringAPI = {
  getStatus: () =>
    client.get<SystemStatus>('/api/monitoring/status'),
  
  getMetrics: () =>
    client.get<MetricsSnapshot>('/api/monitoring/metrics'),
  
  getMetricsHistory: (params: { duration?: number; interval?: number }) =>
    client.get('/api/monitoring/metrics/history', { params }),
  
  getPerformance: (params?: { category?: 'llm' | 'tool' | 'api' }) =>
    client.get<PerformanceData[]>('/api/monitoring/performance', { params }),
  
  getLLMPerformance: () =>
    client.get<PerformanceData[]>('/api/monitoring/performance/llm'),
  
  getToolPerformance: () =>
    client.get<PerformanceData[]>('/api/monitoring/performance/tool'),
  
  getAPIStats: () =>
    client.get('/api/monitoring/api-stats'),
  
  getAPIStatsHistory: (params: { duration?: number }) =>
    client.get('/api/monitoring/api-stats/history', { params }),
  
  getEventLogs: (params?: {
    level?: 'info' | 'warn' | 'error';
    service?: string;
    limit?: number;
    offset?: number;
  }) =>
    client.get<EventLog[]>('/api/monitoring/events', { params }),
};
