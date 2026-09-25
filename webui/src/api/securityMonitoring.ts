import client from './client';

export const MONITOR_PATH = '/suites/host-security-monitor';
export const MONITOR_API = '/api/monitoring/host-security-monitor';
export interface MonitorStep {
  id: string; tool: string; status: string;
  input?: unknown; output?: unknown; error?: string | null;
  started_at?: string; finished_at?: string | null;
}
export interface MonitorRunResult {
  events?: number; risk?: number; unknown?: number; analyzed?: number; deferred?: number;
  errors?: string[];
  mail?: {
    enabled?: boolean;
    notification?: { sent?: number; pending?: number; errors?: string[] };
    feedback?: { processed?: number; verified?: number; pending?: number; errors?: string[] };
  };
}
export interface MonitorRun {
  id: string; execution_id: string; session_id: string; message_id: string;
  started_at: string; scheduled_for: string | null; finished_at: string | null;
  status: string; error: string | null; summary?: string; next_step?: string;
  result?: MonitorRunResult; steps: MonitorStep[];
}
export interface MonitorEvent {
  key: string; name: string; device: string; host: string; risk: string; reason: string;
  related?: string[]; sessionID: string; messageID: string;
  automaticReason?: string; closure?: 'open' | 'closed' | 'contained' | 'ignored'; disposition?: string; dispositionRecord?: MonitorDisposition;
}
export interface MonitorDisposition {
  mode?: 'manual' | 'automatic' | 'mail'; target_status?: number;
  id: string; status: 'writing' | 'pending' | 'mismatch' | 'failed' | 'verified';
  observed_status: number | null; error: string | null; comment: string; updated_at: string; session_id: string | null;
}
export interface MonitorSnapshot {
  developmentSample?: boolean;
  investigationEngine?: 'rules' | 'agent-v1';
  roundTimeoutSeconds?: number;
  mail?: { enabled: boolean; sentToday: number; receivedToday: number; pending: number; needsReview: number; sendUnknown: number };
  automatic?: { enabled: boolean; rule: string; queued: number };
  businessDate: string; timezone: string; sessionID: string | null; nextRun: string | null; scheduledNextRun: string | null;
  installation: { installed: boolean; ready: boolean; reason: string | null; status: string; projectID: string | null };
  metrics: { definitions: number; started: number; attempts: number; events: number; risk: number; unknown: number; ignored: number; openRisk?: number; closed?: number; contained?: number };
  runs: MonitorRun[]; events: MonitorEvent[];
  queued: { id: string; status: string; scheduled_for: string | null; error: string | null; slot_status: string | null }[];
  report: { status: string; version: number; error: string | null };
}
export interface MailHistory {
  unparsed_count?: number;
  sender_verification_required?: boolean;
  settings: { enabled: boolean; recipient_email: string; responsible_name: string };
  counts: Record<string, number>; reply_counts: Record<string, number>; has_more: boolean;
  notices: { id: string; recipient: string; state: string; subject: string; body: string; error: string | null; created_at: string; sent_at?: string; event: { id: string; name: string; host: string }; items: { id: string; reply_id: string; reply_excerpt?: string; target: number; state: string; reason: string; error: string | null }[] }[];
  replies: { id: string; sender: string; state: string; error: string | null; received_at: string; targets?: { event_id: string; name: string; state: string; target: number }[]; payload: { subject: string; text: string; authenticated_sender?: boolean; sender_verification_bypassed?: boolean }; result: { items?: { notice_id: string; outcome: string; evidence: string; reason: string }[] } | null }[];
}
export const monitoringApi = {
  setInvestigationEngine: (engine: 'agent-v1') => client.put<MonitorSnapshot>(`${MONITOR_API}/investigation-engine`, { engine }),
  mail: (offset = 0, tab?: 'sent' | 'received') => client.get<MailHistory>(`${MONITOR_API}/mail`, { params: { offset, tab } }),
  saveMail: (body: { enabled: boolean; recipient_email: string; responsible_name: string }) => client.put<MonitorSnapshot>(`${MONITOR_API}/mail/settings`, body),
  setAutomaticStatus: (enabled: boolean) => client.put<MonitorSnapshot>(`${MONITOR_API}/automatic-status`, { enabled }),
  confirmDisposition: (body: { request_id: string; event_key: string; comment: string; confirmed: true }) => client.post<MonitorDisposition>(`${MONITOR_API}/dispositions`, body),
  recheckDisposition: (id: string) => client.post<MonitorDisposition>(`${MONITOR_API}/dispositions/${encodeURIComponent(id)}/recheck`),
  diagnostics: () => client.get<Blob>(`${MONITOR_API}/diagnostics`, { responseType: 'blob' }),
  start: () => client.post<MonitorSnapshot>(`${MONITOR_API}/start`),
  pause: () => client.post<MonitorSnapshot>(`${MONITOR_API}/pause`),
  overview: (day?: string) => client.get<MonitorSnapshot>(`${MONITOR_API}/overview`, { params: day ? { day } : {} }),
  state: () => client.get<{ owner: string; latest: { sequence: number; session_id: string; message_id: string; business_date: string; started_at: string } | null }>(`${MONITOR_API}/state`),
  report: (day: string, kind: 'timeline' | 'summary' = 'timeline') => client.get<string>(`${MONITOR_API}/reports/${day}`, { responseType: 'text', params: { kind } }),
};
