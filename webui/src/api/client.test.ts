import { describe, expect, it } from 'vitest';
import { resolveApiBaseURL, shouldDisableApiTimeout } from './client';

describe('resolveApiBaseURL', () => {
  it('returns the configured URL when no current origin is provided', () => {
    expect(resolveApiBaseURL('http://127.0.0.1:8000', undefined)).toBe('http://127.0.0.1:8000');
  });

  it('keeps the configured URL when current origin already matches', () => {
    expect(resolveApiBaseURL('http://127.0.0.1:8000', 'http://127.0.0.1:5173')).toBe('http://127.0.0.1:8000');
  });

  it('rewrites loopback aliases to the current page host', () => {
    expect(resolveApiBaseURL('http://127.0.0.1:8000', 'http://localhost:5173')).toBe('http://localhost:8000');
    expect(resolveApiBaseURL('http://localhost:9000', 'http://127.0.0.1:5173')).toBe('http://127.0.0.1:9000');
  });

  it('does not rewrite non-loopback hosts', () => {
    expect(resolveApiBaseURL('http://10.0.0.8:8000', 'http://localhost:5173')).toBe('http://10.0.0.8:8000');
  });
});

describe('shouldDisableApiTimeout', () => {
  it('disables timeout for session interaction mutations', () => {
    expect(shouldDisableApiTimeout('/api/session', 'post')).toBe(true);
    expect(shouldDisableApiTimeout('/api/session/sess-1/message', 'post')).toBe(true);
    expect(shouldDisableApiTimeout('/api/session/sess-1/prompt_async', 'post')).toBe(true);
    expect(shouldDisableApiTimeout('/api/session/sess-1/prompt_queue', 'post')).toBe(true);
    expect(shouldDisableApiTimeout('/api/session/sess-1/prompt_queue/item-1', 'patch')).toBe(true);
    expect(shouldDisableApiTimeout('/api/session/sess-1/prompt_queue/item-1', 'delete')).toBe(true);
    expect(shouldDisableApiTimeout('/api/session/sess-1/prompt_queue/item-1/run_now', 'post')).toBe(true);
    expect(shouldDisableApiTimeout('/api/session/sess-1/command', 'post')).toBe(true);
    expect(shouldDisableApiTimeout('/api/session/sess-1/abort', 'post')).toBe(true);
  });

  it('disables timeout for question replies and rejects', () => {
    expect(shouldDisableApiTimeout('/api/question/question-1/reply', 'post')).toBe(true);
    expect(shouldDisableApiTimeout('/api/question/question-1/reject', 'post')).toBe(true);
  });

  it('keeps timeout for normal read endpoints', () => {
    expect(shouldDisableApiTimeout('/api/session', 'get')).toBe(false);
    expect(shouldDisableApiTimeout('/api/session/sess-1', 'get')).toBe(false);
    expect(shouldDisableApiTimeout('/api/session/sess-1/message', 'get')).toBe(false);
    expect(shouldDisableApiTimeout('/api/question/session/sess-1/pending', 'get')).toBe(false);
    expect(shouldDisableApiTimeout('/api/workflow', 'post')).toBe(false);
  });
});
