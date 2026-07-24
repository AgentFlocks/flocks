export type SessionExecutionMode = 'build' | 'plan' | 'goal';
export type PersistentSessionExecutionMode = Exclude<SessionExecutionMode, 'goal'>;

export const DEFAULT_SESSION_EXECUTION_MODE: PersistentSessionExecutionMode = 'build';
export const EXECUTION_MODE_STORAGE_PREFIX = 'flocks:session-execution-mode:';
export const EXECUTION_MODE_DRAFT_STORAGE_KEY = `${EXECUTION_MODE_STORAGE_PREFIX}draft`;

function isPersistentMode(value: unknown): value is PersistentSessionExecutionMode {
  return value === 'build' || value === 'plan';
}

function storageKey(sessionId?: string | null): string {
  return sessionId
    ? `${EXECUTION_MODE_STORAGE_PREFIX}${sessionId}`
    : EXECUTION_MODE_DRAFT_STORAGE_KEY;
}

export function readSessionExecutionMode(
  sessionId?: string | null,
): PersistentSessionExecutionMode {
  if (typeof window === 'undefined') return DEFAULT_SESSION_EXECUTION_MODE;
  try {
    const stored = window.localStorage.getItem(storageKey(sessionId));
    return isPersistentMode(stored) ? stored : DEFAULT_SESSION_EXECUTION_MODE;
  } catch {
    return DEFAULT_SESSION_EXECUTION_MODE;
  }
}

export function writeSessionExecutionMode(
  sessionId: string | null | undefined,
  mode: PersistentSessionExecutionMode,
): void {
  if (typeof window === 'undefined') return;
  try {
    const key = storageKey(sessionId);
    if (mode === DEFAULT_SESSION_EXECUTION_MODE) {
      window.localStorage.removeItem(key);
    } else {
      window.localStorage.setItem(key, mode);
    }
  } catch {
    // Composer preferences must never block the chat flow.
  }
}

export function promoteDraftExecutionMode(
  sessionId: string,
  mode: PersistentSessionExecutionMode,
): void {
  writeSessionExecutionMode(sessionId, mode);
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(EXECUTION_MODE_DRAFT_STORAGE_KEY);
  } catch {
    // Composer preferences must never block the chat flow.
  }
}

export function resetDraftExecutionMode(): void {
  writeSessionExecutionMode(null, DEFAULT_SESSION_EXECUTION_MODE);
}
