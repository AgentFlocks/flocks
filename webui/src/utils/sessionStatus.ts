import type { Session } from '@/types';

function readSessionStatusType(status: unknown): string | undefined {
  if (typeof status === 'string') return status;
  if (!status || typeof status !== 'object' || !('type' in status)) return undefined;
  return typeof status.type === 'string' ? status.type : undefined;
}

/** Whether a `session.status` value means the session is working right now. */
export function isRunningSessionStatus(status: unknown): boolean {
  const statusType = readSessionStatusType(status);
  return statusType === 'busy'
    || statusType === 'compacting'
    || statusType === 'dreaming'
    || statusType === 'retry';
}

/** Running session ids out of `GET /api/session/status` ({sessionId: status}). */
export function readRunningSessionIds(statuses: unknown): Set<string> {
  if (!statuses || typeof statuses !== 'object' || Array.isArray(statuses)) return new Set();
  return new Set(
    Object.entries(statuses)
      .filter(([, status]) => isRunningSessionStatus(status))
      .map(([sessionId]) => sessionId),
  );
}

export function sanitizeSessionExportName(value: string): string {
  const trimmed = value.trim();
  return trimmed
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'session';
}

/** Channel sessions carry a "[channel]" prefix that the list does not repeat. */
export function getSessionDisplayTitle(session: Pick<Session, 'title' | 'channelID'>): string {
  if (!session.channelID) return session.title;
  const prefix = `[${session.channelID}]`;
  if (session.title.slice(0, prefix.length).toLowerCase() !== prefix.toLowerCase()) {
    return session.title;
  }
  return session.title.slice(prefix.length).trim() || session.title;
}
