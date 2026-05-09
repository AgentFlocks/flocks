import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { DRAFT_STORAGE_PREFIX, readChatDraft, writeChatDraft } from './chatDraft';

describe('chatDraft helpers', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => localStorage.clear());

  describe('readChatDraft', () => {
    it('returns empty string when sessionId is null', () => {
      expect(readChatDraft(null)).toBe('');
    });

    it('returns empty string when sessionId is undefined', () => {
      expect(readChatDraft(undefined)).toBe('');
    });

    it('returns empty string when no draft is stored', () => {
      expect(readChatDraft('sess-1')).toBe('');
    });

    it('returns the stored draft for a given session', () => {
      localStorage.setItem(`${DRAFT_STORAGE_PREFIX}sess-1`, 'hello world');
      expect(readChatDraft('sess-1')).toBe('hello world');
    });

    it('does not mix drafts between sessions', () => {
      localStorage.setItem(`${DRAFT_STORAGE_PREFIX}sess-a`, 'draft a');
      localStorage.setItem(`${DRAFT_STORAGE_PREFIX}sess-b`, 'draft b');
      expect(readChatDraft('sess-a')).toBe('draft a');
      expect(readChatDraft('sess-b')).toBe('draft b');
    });
  });

  describe('writeChatDraft', () => {
    it('persists a non-empty draft', () => {
      writeChatDraft('sess-1', 'my draft');
      expect(localStorage.getItem(`${DRAFT_STORAGE_PREFIX}sess-1`)).toBe('my draft');
    });

    it('removes the key when value is empty (send-success cleanup)', () => {
      localStorage.setItem(`${DRAFT_STORAGE_PREFIX}sess-1`, 'stale');
      writeChatDraft('sess-1', '');
      expect(localStorage.getItem(`${DRAFT_STORAGE_PREFIX}sess-1`)).toBeNull();
    });

    it('is a no-op when sessionId is null', () => {
      writeChatDraft(null, 'ignored');
      expect(localStorage.length).toBe(0);
    });

    it('is a no-op when sessionId is undefined', () => {
      writeChatDraft(undefined, 'ignored');
      expect(localStorage.length).toBe(0);
    });

    it('restores draft after round-trip read', () => {
      writeChatDraft('sess-x', 'round trip');
      expect(readChatDraft('sess-x')).toBe('round trip');
    });
  });
});
