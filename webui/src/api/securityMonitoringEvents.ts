import client from "./client";
import { MONITOR_API } from "./securityMonitoring";

export type EventHandling = "open" | "closed" | "ignored" | "contained";
export interface MonitorEventQuery {
  start_date: string;
  end_date: string;
  group: boolean;
  include_backlog: boolean;
  window_hours: number;
  offset: number;
  limit: number;
  search?: string;
  device?: string;
  risk?: string;
  handling?: string;
}
export interface EventMember {
  key: string;
  id: string;
  name: string;
  device: string;
  device_name: string;
  host: string;
  risk: string;
  handling: EventHandling;
  deal_status?: number | string | null;
  observed_at?: string | null;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
  event_at?: string | number | null;
  event_end_at?: string | number | null;
  investigation?: {
    state?: string;
    verdict?: string;
    reason?: string;
    updated_at?: string;
    is_current?: boolean;
  } | null;
  mail?: {
    state?: string;
    sent?: boolean;
    replied?: boolean;
    reply_count?: number;
    recipient?: string;
    sent_at?: string | null;
  } | null;
  disposition?: {
    status?: string;
    target_status?: number | null;
    observed_status?: number | null;
    updated_at?: string;
  } | null;
  sessionID?: string;
  messageID?: string;
  is_backlog?: boolean;
  handling_source?: "xdr" | "monitor_verified";
}
export interface EventListRow {
  id: string;
  event_keys: string[];
  grouped: boolean;
  member_count: number;
  name: string;
  device: string;
  device_name: string;
  host: string;
  first_event_at?: string | number | null;
  last_event_at?: string | number | null;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
  risk: string;
  handling: EventHandling;
  mail: { sent: number; replied: number; failed: number };
  ioc_matches: { type: string; value: string }[];
  merge_reason: string;
  members: EventMember[];
  is_backlog?: boolean;
  closure_counts?: Record<EventHandling, number>;
}
export interface EventListResponse {
  start_date: string;
  end_date: string;
  timezone: string;
  window_hours: number;
  items: EventListRow[];
  total: number;
  has_more: boolean;
  offset: number;
  limit: number;
  metrics: {
    events: number;
    events_in_range?: number;
    backlog_events?: number;
    rows: number;
    groups?: number;
    ungrouped?: number;
    open: number;
    closed: number;
    ignored: number;
    contained: number;
    mail_sent: number;
    mail_replied: number;
  };
  devices: { id: string; name: string }[];
  truncated: boolean;
  warnings: string[];
}
export interface EventDetailResponse {
  event: EventMember & Record<string, unknown>;
  investigation?: {
    state?: string;
    is_current?: boolean;
    result?: Record<string, unknown>;
    historical_result?: Record<string, unknown>;
    evidence?: Record<string, unknown>[];
  } | null;
  dispositions: Record<string, unknown>[];
  notice?:
    | (Record<string, unknown> & { replies?: Record<string, unknown>[] })
    | null;
  observations: {
    business_date: string;
    observed_at: string;
    sessionID: string;
    messageID: string;
  }[];
  truncated: boolean;
  warnings: string[];
}
export const monitoringEventsApi = {
  list: (params: MonitorEventQuery, signal?: AbortSignal) =>
    client.get<EventListResponse>(`${MONITOR_API}/events`, { params, signal }),
  detail: (eventKey: string, signal?: AbortSignal) =>
    client.get<EventDetailResponse>(`${MONITOR_API}/events/detail`, {
      params: { event_key: eventKey },
      signal,
    }),
};
