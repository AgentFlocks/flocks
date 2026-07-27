import { beforeEach, describe, expect, it, vi } from 'vitest';

import { recoverLazyLoad, reloadOnceForChunkLoadError } from './chunkLoadRecovery';

describe('chunkLoadRecovery', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('reloads only once for the same failed chunk and allows a new chunk failure', () => {
    const reload = vi.fn();
    const staleChunk = new TypeError('Failed to fetch /assets/Session-old.js');

    expect(reloadOnceForChunkLoadError(staleChunk, reload)).toBe(true);
    expect(reloadOnceForChunkLoadError(staleChunk, reload)).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);

    expect(reloadOnceForChunkLoadError(
      new TypeError('Failed to fetch /assets/Session-new.js'),
      reload,
    )).toBe(true);
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it('does not reload when session storage cannot guard against a loop', () => {
    const reload = vi.fn();
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('storage unavailable');
    });

    expect(reloadOnceForChunkLoadError(new Error('chunk failed'), reload)).toBe(false);
    expect(reload).not.toHaveBeenCalled();
    getItem.mockRestore();
  });

  it('reloads for an explicit dynamic-import failure', async () => {
    const reload = vi.fn();
    const error = new TypeError('Failed to fetch dynamically imported module: /assets/Session-old.js');

    await expect(recoverLazyLoad(Promise.reject(error), reload)).rejects.toBe(error);

    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('does not reload for a non-chunk lazy dependency failure', async () => {
    const reload = vi.fn();
    const error = new Error('i18n instance is unavailable');

    await expect(recoverLazyLoad(Promise.reject(error), reload)).rejects.toBe(error);

    expect(reload).not.toHaveBeenCalled();
  });
});
