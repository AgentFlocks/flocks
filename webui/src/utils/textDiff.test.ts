import { describe, expect, it } from 'vitest';

import {
  acceptTextDiffHunk,
  buildLineDiff,
  buildTextDiffHunks,
  rejectTextDiffHunk,
} from './textDiff';

describe('buildLineDiff', () => {
  it('keeps context lines and marks additions and removals in order', () => {
    expect(buildLineDiff('a\nb\nc\n', 'a\nx\nc\nd\n')).toEqual([
      { type: 'context', oldLine: 1, newLine: 1, text: 'a' },
      { type: 'remove', oldLine: 2, text: 'b' },
      { type: 'add', newLine: 2, text: 'x' },
      { type: 'context', oldLine: 3, newLine: 3, text: 'c' },
      { type: 'add', newLine: 4, text: 'd' },
    ]);
  });

  it('builds hunks and can accept or reject one hunk at a time', () => {
    const before = 'old title\n\nkeep\n\nold tail\n';
    const after = 'new title\n\nkeep\n\nnew tail\n';
    const hunks = buildTextDiffHunks(before, after);

    expect(hunks).toHaveLength(2);
    expect(acceptTextDiffHunk(before, hunks[0])).toBe('new title\n\nkeep\n\nold tail\n');
    expect(rejectTextDiffHunk(after, hunks[0])).toBe('old title\n\nkeep\n\nnew tail\n');
  });
});
