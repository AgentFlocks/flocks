/**
 * Helpers for persisting composer draft text in ``localStorage``.
 *
 * Keyed per session (``flocks:chat-draft:<sessionId>``) so switching between
 * sessions never mixes up drafts.  When ``sessionId`` is null/undefined (e.g.
 * a creation composer with no session yet) the helpers are no-ops so callers
 * don't need to guard against undefined.
 */

export const DRAFT_STORAGE_PREFIX = 'flocks:chat-draft:';

export function readChatDraft(sessionId?: string | null): string {
  if (!sessionId || typeof window === 'undefined') return '';
  try {
    return window.localStorage.getItem(`${DRAFT_STORAGE_PREFIX}${sessionId}`) ?? '';
  } catch {
    return '';
  }
}

export function writeChatDraft(sessionId: string | null | undefined, value: string): void {
  if (!sessionId || typeof window === 'undefined') return;
  try {
    const key = `${DRAFT_STORAGE_PREFIX}${sessionId}`;
    if (value) {
      window.localStorage.setItem(key, value);
    } else {
      window.localStorage.removeItem(key);
    }
  } catch {
    // Quota / disabled storage — silently drop the draft rather than block typing.
  }
}
