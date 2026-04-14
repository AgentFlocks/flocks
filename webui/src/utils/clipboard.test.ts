import { afterEach, describe, expect, it, vi } from 'vitest';

import { copyText, fallbackCopyText } from './clipboard';

function setExecCommand(fn?: (commandId: string) => boolean) {
  Object.defineProperty(document, 'execCommand', {
    configurable: true,
    value: fn,
  });
}

describe('clipboard helpers', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    setExecCommand(undefined);
  });

  it('uses async clipboard in secure contexts', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    await copyText('hello');

    expect(writeText).toHaveBeenCalledWith('hello');
  });

  it('falls back to execCommand when async clipboard is unavailable', async () => {
    const execCommand = vi.fn().mockReturnValue(true);
    setExecCommand(execCommand);
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: false,
    });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: undefined,
    });

    await copyText('fallback text');

    expect(execCommand).toHaveBeenCalledWith('copy');
  });

  it('falls back to execCommand when async clipboard write fails', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('not allowed'));
    const execCommand = vi.fn().mockReturnValue(true);
    setExecCommand(execCommand);
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    await copyText('fallback after failure');

    expect(writeText).toHaveBeenCalledWith('fallback after failure');
    expect(execCommand).toHaveBeenCalledWith('copy');
  });

  it('throws when both clipboard strategies fail', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('blocked'));
    setExecCommand(vi.fn().mockReturnValue(false));
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    });
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    await expect(copyText('nope')).rejects.toThrow('blocked');
  });

  it('returns false when execCommand is unavailable', () => {
    setExecCommand(undefined);
    expect(fallbackCopyText('text')).toBe(false);
  });
});
