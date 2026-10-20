import client from "./client";
import { MONITOR_API } from "./securityMonitoring";

export type EventHandling =
  | "open"
  | "closed"
  | "ignored"
  | "contained"
  | "ended";
export type EventAssessmentStatus =
  | "risk_open"
  | "risk_closed"
  | "benign"
  | "insufficient"
  | "ended"
  | "source_closed_risk"
  | "source_closed_benign"
  | "source_closed_unknown";
export interface EventAssessment {
  lifecycle?: string;
  observation_rounds?: number;
  target_status?: number | null;
  external?: boolean;
  ended?: boolean;
  suspected_risk?: boolean;
  lifecycle_reason?: string;
  status: EventAssessmentStatus;
  label: string;
  verdict: "risk" | "benign" | "unknown";
  notify_eligible: boolean;
  reason: string;
  gaps?: string[];
  is_current?: boolean;
  evidence_supported?: boolean;
  source_closed?: boolean;
  source_closed_verdict?: "risk" | "benign" | "unknown";
  source_closure_basis?: string;
  historical_verdict?: "risk" | "benign" | "unknown";
  confirmed_risk_retained?: boolean;
  previously_confirmed_risk?: boolean;
  risk_scope?: "current" | "previous_activity";
  recheck_state?: string;
  recheck_reason?: string;
  confirmed_at?: string | null;
  risk_fact_summary?: string;
}
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
  assessment?: EventAssessmentStatus;
  shape?: "grouped" | "ungrouped";
  sort_by?: "first_seen_at" | "last_processed_at";
  sort_order?: "asc" | "desc";
}
export interface EventMember {
  key: string;
  id: string;
  name: string;
  device: string;
  device_name: string;
  host: string;
  risk: string;
  assessment?: EventAssessment;
  handling: EventHandling;
  deal_status?: number | string | null;
  observed_at?: string | null;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
  last_processed_at?: string | null;
  source_closed?: boolean;
  source_closed_verdict?: "risk" | "benign" | "unknown";
  event_at?: string | number | null;
  event_end_at?: string | number | null;
  occurrence_at?: string | number | null;
  occurrence_time_unknown?: boolean;
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
  handling_source?: "xdr" | "monitor_verified" | "monitoring";
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
  primary_event_key?: string;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
  last_processed_at?: string | null;
  source_closed?: boolean;
  source_closed_verdict?: "risk" | "benign" | "unknown";
  risk: string;
  handling: EventHandling;
  assessment_counts?: Partial<Record<EventAssessmentStatus, number>>;
  assessment_status?: EventAssessmentStatus | "mixed";
  assessment_label?: string;
  assessment?: EventAssessment;
  hosts?: string[];
  host_count?: number;
  mail: { sent: number; replied: number; failed: number };
  ioc_matches: { type: string; value: string }[];
  merge_reason: string;
  members: EventMember[];
  is_backlog?: boolean;
  closure_counts?: Partial<Record<EventHandling, number>>;
}
export interface EventListMetrics {
  assessment_counts?: Partial<Record<EventAssessmentStatus, number>>;
  source_closed_counts?: Partial<Record<"risk" | "benign" | "unknown", number>>;
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
  ended?: number;
  mail_sent: number;
  mail_replied: number;
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
  metrics: EventListMetrics;
  summary_metrics?: EventListMetrics;
  devices: { id: string; name: string }[];
  truncated: boolean;
  warnings: string[];
}
export interface EventEvidenceCard {
  id: string;
  kind?: "network" | "domain" | "file" | "process" | "request" | "observation";
  summary: string;
  source: string;
  time?: string | number | null;
  partial?: boolean;
  boundary?: string;
  fields?: { key: string; label: string; value: unknown }[];
  packets?: {
    field: string;
    label: string;
    text: string;
    truncated?: boolean;
  }[];
  query?: unknown;
  raw?: unknown;
  evidence_id?: string;
  alert_id?: string;
  records?: {
    evidence_id?: string;
    source?: string;
    query?: unknown;
    raw?: unknown;
  }[];
}
export interface EventDetailResponse {
  story?: Record<string, unknown>;
  confirmed_story?: Record<string, unknown>;
  confirmed_evidence_scope?: "current_activity" | "previous_activity";
  confirmed_at?: string | null;
  overview?: {
    basics?: Record<string, unknown>;
    conclusion?: string;
    summary?: string;
    findings?: string[];
    missing_evidence?: string[];
    operator_summary?: string;
    operator_explanation?: string[];
    verdict?: string;
    evidence?: { text: string; time?: string | number }[];
    evidence_cards?: EventEvidenceCard[];
    timeline?: { time: string | number; text: string }[];
    gaps?: string[];
    recheck_state?: string;
    recheck_reason?: string;
    confirmed_risk_retained?: boolean;
    risk_scope?: "current" | "previous_activity";
  };
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
