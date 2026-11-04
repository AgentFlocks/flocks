import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { noteChange, requestClock, shareRequest } from './sharedRequest';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe('shareRequest', () => {
  let now = 1000;
  beforeEach(() => {
    now = 1000;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
  });
  afterEach(() => { vi.restoreAllMocks(); });

  it('hands a running request only to callers it started after', async () => {
    const answer = deferred<string>();
    const start = vi.fn(() => answer.promise);
    const first = shareRequest('t', 'k', -Infinity, start);
    now = 1200;
    expect(shareRequest('t', 'k', 900, start)).toBe(first);
    expect(shareRequest('t', 'k', 1000, start)).toBe(first);
    expect(start).toHaveBeenCalledTimes(1);
    // A change heard after the request started needs a request of its own.
    shareRequest('t', 'k', 1100, () => Promise.resolve('later'));
    expect(shareRequest('t', 'k', Infinity, () => Promise.resolve('own'))).not.toBe(first);
    answer.resolve('first');
    await expect(first).resolves.toBe('first');
  });

  it('hands out an answered request the same way, but never a failed one', async () => {
    await shareRequest('t', 'answered', -Infinity, () => Promise.resolve('answer'));
    now = 1500;
    const start = vi.fn(() => Promise.resolve('new'));
    await expect(shareRequest('t', 'answered', 1000, start)).resolves.toBe('answer');
    expect(start).not.toHaveBeenCalled();
    await expect(shareRequest('t', 'answered', 1001, start)).resolves.toBe('new');

    await expect(shareRequest('t', 'failed', -Infinity, () => Promise.reject(new Error('down')))).rejects.toThrow('down');
    await expect(shareRequest('t', 'failed', -Infinity, () => Promise.resolve('retried'))).resolves.toBe('retried');
  });

  it('hands out nothing started before a change noted for its topic', async () => {
    const running = deferred<string>();
    shareRequest('lists', 'k', -Infinity, () => running.promise);
    await shareRequest('lists', 'done', -Infinity, () => Promise.resolve('answered'));
    await shareRequest('other', 'k', -Infinity, () => Promise.resolve('other topic'));
    now = 1100;
    noteChange('lists', 1050);
    await expect(shareRequest('lists', 'k', -Infinity, () => Promise.resolve('after change'))).resolves.toBe('after change');
    await expect(shareRequest('lists', 'done', -Infinity, () => Promise.resolve('read again'))).resolves.toBe('read again');
    await expect(shareRequest('other', 'k', -Infinity, () => Promise.resolve('unused'))).resolves.toBe('other topic');
    // A later request still serves callers that heard of the change.
    await expect(shareRequest('lists', 'k', 1050, () => Promise.resolve('unused'))).resolves.toBe('after change');
    // An earlier mark never lowers the floor.
    noteChange('lists', 10);
    now = 1200;
    await expect(shareRequest('lists', 'done', -Infinity, () => Promise.resolve('unused'))).resolves.toBe('read again');
    running.resolve('before change');
  });

  it('keeps the newest answer when an older request finishes last', async () => {
    const older = deferred<string>();
    shareRequest('t', 'order', Infinity, () => older.promise);
    now = 1100;
    await shareRequest('t', 'order', Infinity, () => Promise.resolve('newer'));
    older.resolve('older');
    await older.promise;
    await Promise.resolve();
    await expect(shareRequest('t', 'order', 1050, () => Promise.resolve('fresh'))).resolves.toBe('newer');
  });

  it('forgets answers after a minute and keys requests apart', async () => {
    await shareRequest('t', 'old', -Infinity, () => Promise.resolve('stale'));
    await expect(shareRequest('t', 'other', -Infinity, () => Promise.resolve('other'))).resolves.toBe('other');
    now = 1000 + 60_001;
    await expect(shareRequest('t', 'old', -Infinity, () => Promise.resolve('read again'))).resolves.toBe('read again');
    expect(requestClock()).toBe(now);
  });
});
