import { describe, expect, it } from 'vitest';
import { resolveApiBaseURL } from './client';

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
