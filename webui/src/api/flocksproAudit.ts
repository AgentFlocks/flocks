import client from './client';

export interface AuditEventItem {
  id: number;
  event_type: string;
  category: string;
  action: string;
  status: string;
  result: string;
  user_id?: string | null;
  user_name?: string | null;
  actor_id?: string | null;
  actor_name?: string | null;
  resource_id?: string | null;
  resource_type?: string | null;
  session_id?: string | null;
  ip?: string | null;
  trace_id?: string | null;
  provider?: string | null;
  model?: string | null;
  tokens?: number | null;
  estimated_cost?: number | null;
  payload?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  created_at: string;
}

export interface AuditEventListResponse {
  items: AuditEventItem[];
  count: number;
  total: number;
  limit: number;
  offset: number;
}

export interface AuditEventQuery {
  limit?: number;
  offset?: number;
  event_type?: string;
  actor_id?: string;
  username?: string;
  user_id?: string;
  resource_type?: string;
  result?: string;
  start_at?: string;
  end_at?: string;
  sort_by?: string;
  order?: 'asc' | 'desc';
}

export const flocksproAuditApi = {
  listEvents: async (query: AuditEventQuery = {}): Promise<AuditEventListResponse> => {
    const response = await client.get('/api/flockspro/audit/events', { params: query });
    return response.data;
  },
  listEventTypes: async (): Promise<string[]> => {
    const response = await client.get('/api/flockspro/audit/event-types');
    return Array.isArray(response.data?.items) ? response.data.items : [];
  },
};
