/**
 * Hook management API
 * 
 * Provides API for accessing hook system status and statistics
 */

import client from './client';

export interface HookStats {
  total_event_keys: number;
  total_handlers: number;
  event_keys: Record<string, {
    handler_count: number;
    handlers: string[];
  }>;
}

export interface HookStatus {
  enabled: boolean;
  session_memory: {
    enabled: boolean;
    message_count: number;
    use_llm_slug: boolean;
    slug_timeout: number;
  };
  stats: HookStats;
  error?: string;
}

export const hooksApi = {
  /**
   * Get hook system statistics
   */
  getStats: async (): Promise<HookStats> => {
    const response = await client.get('/api/hooks/stats');
    return response.data;
  },

  /**
   * Get hook system status and configuration
   */
  getStatus: async (): Promise<HookStatus> => {
    const response = await client.get('/api/hooks/status');
    return response.data;
  },
};
