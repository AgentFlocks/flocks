export type TextDiffLineType = 'context' | 'add' | 'remove';

export interface TextDiffLine {
  type: TextDiffLineType;
  oldLine?: number;
  newLine?: number;
  text: string;
}

export interface TextDiffHunk {
  id: string;
  lines: TextDiffLine[];
  changeStartLineIndex: number;
  changeEndLineIndex: number;
  oldStartIndex: number;
  oldEndIndex: number;
  newStartIndex: number;
  newEndIndex: number;
  oldLines: string[];
  newLines: string[];
  added: number;
  removed: number;
}

function splitLines(text: string): string[] {
  const normalized = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  if (!normalized) return [];
  const lines = normalized.split('\n');
  if (lines[lines.length - 1] === '') {
    lines.pop();
  }
  return lines;
}

function joinLines(lines: string[], keepTrailingNewline: boolean): string {
  const joined = lines.join('\n');
  if (!joined) return keepTrailingNewline && lines.length > 0 ? '\n' : '';
  return keepTrailingNewline ? `${joined}\n` : joined;
}

export function buildLineDiff(before: string, after: string): TextDiffLine[] {
  const oldLines = splitLines(before);
  const newLines = splitLines(after);
  const oldCount = oldLines.length;
  const newCount = newLines.length;
  const lcs = Array.from({ length: oldCount + 1 }, () => Array<number>(newCount + 1).fill(0));

  for (let oldIndex = oldCount - 1; oldIndex >= 0; oldIndex -= 1) {
    for (let newIndex = newCount - 1; newIndex >= 0; newIndex -= 1) {
      if (oldLines[oldIndex] === newLines[newIndex]) {
        lcs[oldIndex][newIndex] = lcs[oldIndex + 1][newIndex + 1] + 1;
      } else {
        lcs[oldIndex][newIndex] = Math.max(lcs[oldIndex + 1][newIndex], lcs[oldIndex][newIndex + 1]);
      }
    }
  }

  const result: TextDiffLine[] = [];
  let oldIndex = 0;
  let newIndex = 0;

  while (oldIndex < oldCount && newIndex < newCount) {
    if (oldLines[oldIndex] === newLines[newIndex]) {
      result.push({
        type: 'context',
        oldLine: oldIndex + 1,
        newLine: newIndex + 1,
        text: oldLines[oldIndex],
      });
      oldIndex += 1;
      newIndex += 1;
    } else if (lcs[oldIndex + 1][newIndex] >= lcs[oldIndex][newIndex + 1]) {
      result.push({
        type: 'remove',
        oldLine: oldIndex + 1,
        text: oldLines[oldIndex],
      });
      oldIndex += 1;
    } else {
      result.push({
        type: 'add',
        newLine: newIndex + 1,
        text: newLines[newIndex],
      });
      newIndex += 1;
    }
  }

  while (oldIndex < oldCount) {
    result.push({
      type: 'remove',
      oldLine: oldIndex + 1,
      text: oldLines[oldIndex],
    });
    oldIndex += 1;
  }

  while (newIndex < newCount) {
    result.push({
      type: 'add',
      newLine: newIndex + 1,
      text: newLines[newIndex],
    });
    newIndex += 1;
  }

  return result;
}

export function buildTextDiffHunks(
  before: string,
  after: string,
  contextLines = 2,
): TextDiffHunk[] {
  const diffLines = buildLineDiff(before, after);
  const oldLines = splitLines(before);
  const newLines = splitLines(after);
  const hunks: TextDiffHunk[] = [];

  let oldCursor = 0;
  let newCursor = 0;
  let changeStartLineIndex: number | null = null;
  let oldStartIndex = 0;
  let newStartIndex = 0;

  const pushHunk = (changeEndLineIndex: number) => {
    if (changeStartLineIndex === null) return;

    const oldEndIndex = oldCursor;
    const newEndIndex = newCursor;
    const displayStart = Math.max(0, changeStartLineIndex - contextLines);
    const displayEnd = Math.min(diffLines.length, changeEndLineIndex + contextLines);
    const lines = diffLines.slice(displayStart, displayEnd);
    const removed = lines.filter((line) => line.type === 'remove').length;
    const added = lines.filter((line) => line.type === 'add').length;

    hunks.push({
      id: `hunk-${hunks.length + 1}-${oldStartIndex}-${newStartIndex}`,
      lines,
      changeStartLineIndex,
      changeEndLineIndex,
      oldStartIndex,
      oldEndIndex,
      newStartIndex,
      newEndIndex,
      oldLines: oldLines.slice(oldStartIndex, oldEndIndex),
      newLines: newLines.slice(newStartIndex, newEndIndex),
      added,
      removed,
    });

    changeStartLineIndex = null;
  };

  diffLines.forEach((line, index) => {
    if (line.type === 'context') {
      pushHunk(index);
      oldCursor += 1;
      newCursor += 1;
      return;
    }

    if (changeStartLineIndex === null) {
      changeStartLineIndex = index;
      oldStartIndex = oldCursor;
      newStartIndex = newCursor;
    }

    if (line.type === 'remove') {
      oldCursor += 1;
    } else {
      newCursor += 1;
    }
  });

  pushHunk(diffLines.length);

  return hunks;
}

export function acceptTextDiffHunk(before: string, hunk: TextDiffHunk): string {
  const beforeLines = splitLines(before);
  beforeLines.splice(
    hunk.oldStartIndex,
    hunk.oldEndIndex - hunk.oldStartIndex,
    ...hunk.newLines,
  );
  return joinLines(beforeLines, before.endsWith('\n'));
}

export function rejectTextDiffHunk(after: string, hunk: TextDiffHunk): string {
  const afterLines = splitLines(after);
  afterLines.splice(
    hunk.newStartIndex,
    hunk.newEndIndex - hunk.newStartIndex,
    ...hunk.oldLines,
  );
  return joinLines(afterLines, after.endsWith('\n'));
}
