import { describe, expect, it } from 'vitest';

import { createMessageId } from './messageId';

describe('createMessageId', () => {
  it('creates a canonical message ID with its timestamp bits encoded', () => {
    const timestamp = 1_700_000_000_000;
    const id = createMessageId(timestamp);

    expect(id).toMatch(/^msg_[0-9a-f]{12}[0-9A-Za-z]{14}$/);
    const encodedTime = BigInt(`0x${id.slice(4, 16)}`);
    expect(encodedTime >> 12n).toBe(BigInt(timestamp) & ((1n << 36n) - 1n));
  });

  it('keeps IDs unique and ordered within the same millisecond', () => {
    const timestamp = 1_700_000_000_001;
    const first = createMessageId(timestamp);
    const second = createMessageId(timestamp);

    expect(second).not.toBe(first);
    expect(second > first).toBe(true);
  });
});
