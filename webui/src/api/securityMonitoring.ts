import client from './client';

export const MONITOR_PATH = '/suites/host-security-monitor';
export const MONITOR_API = '/api/monitoring/host-security-monitor';
export interface MonitorStep { id: string; tool: string; status: string; }
export interface MonitorRun {
  id: string; execution_id: string; session_id: string; message_id: string;
  started_at: string; scheduled_for: string | null; finished_at: string | null;
  status: string; error: string | null; steps: MonitorStep[];
}
export interface MonitorEvent {
  key: string; name: string; device: string; host: string; risk: string; reason: string;
  related?: string[]; sessionID: string; messageID: string;
}
export interface MonitorSnapshot {
  businessDate: string; timezone: string; sessionID: string | null; nextRun: string | null; scheduledNextRun: string | null;
  installation: { installed: boolean; ready: boolean; reason: string | null; status: string; projectID: string | null };
  metrics: { definitions: number; started: number; attempts: number; events: number; risk: number; unknown: number; ignored: number };
  runs: MonitorRun[]; events: MonitorEvent[];
  queued: { id: string; status: string; scheduled_for: string | null; error: string | null; slot_status: string | null }[];
  report: { status: string; version: number; error: string | null };
}
export const monitoringApi = {
  start: () => client.post<MonitorSnapshot>(`${MONITOR_API}/start`),
  pause: () => client.post<MonitorSnapshot>(`${MONITOR_API}/pause`),
  overview: (day?: string) => client.get<MonitorSnapshot>(`${MONITOR_API}/overview`, { params: day ? { day } : {} }),
  state: () => client.get<{ owner: string; latest: { sequence: number; session_id: string; message_id: string; business_date: string; started_at: string } | null }>(`${MONITOR_API}/state`),
  report: (day: string) => client.get<string>(`${MONITOR_API}/reports/${day}`, { responseType: 'text' }),
};
