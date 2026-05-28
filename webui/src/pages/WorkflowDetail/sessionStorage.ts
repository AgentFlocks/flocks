const MAX_STORED_SESSIONS = 15;

export interface StoredSession {
  id: string;
  title: string;
  createdAt: number;
}

function lsKey(workflowId: string) {
  return `wf-sessions-${workflowId}`;
}

export function getStoredSessions(workflowId: string): StoredSession[] {
  try {
    const raw = localStorage.getItem(lsKey(workflowId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function getLatestStoredSessionId(workflowId: string): string | null {
  return getStoredSessions(workflowId)[0]?.id ?? null;
}

export function pushStoredSession(workflowId: string, session: StoredSession) {
  const existing = getStoredSessions(workflowId).filter((stored) => stored.id !== session.id);
  localStorage.setItem(
    lsKey(workflowId),
    JSON.stringify([session, ...existing].slice(0, MAX_STORED_SESSIONS)),
  );
}
