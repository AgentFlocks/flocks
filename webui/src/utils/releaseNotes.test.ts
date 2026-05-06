import { describe, expect, it } from 'vitest';
import { getLocalizedReleaseNotes } from './releaseNotes';

describe('getLocalizedReleaseNotes', () => {
  it('falls back to full release notes when no details section is present', () => {
    const notes = [
      '## 中文',
      '中文更新',
      '',
      '## English',
      'English update',
    ].join('\n');

    expect(getLocalizedReleaseNotes(notes, 'en-US')).toBe(notes);
    expect(getLocalizedReleaseNotes(notes, 'zh-CN')).toBe(notes);
  });

  it('extracts Chinese release notes from a GitHub details block', () => {
    const notes = [
      '### What\'s Changed',
      '',
      '- English update 1',
      '- English update 2',
      '',
      '<details>',
      '<summary>中文</summary>',
      '',
      '### 更新内容',
      '',
      '- 中文更新 1',
      '- 中文更新 2',
      '',
      '</details>',
    ].join('\n');

    expect(getLocalizedReleaseNotes(notes, 'zh-CN')).toBe('### 更新内容\n\n- 中文更新 1\n- 中文更新 2');
  });

  it('recognizes Chinese Release Notes as a Chinese details summary', () => {
    const notes = [
      '## What\'s Changed',
      '',
      '### Authentication & Session',
      '',
      '- English update 1',
      '',
      '<details>',
      '',
      '<summary>Chinese Release Notes</summary>',
      '## 更新内容',
      '',
      '### 认证与会话',
      '',
      '- 中文更新 1',
      '',
      '</details>',
    ].join('\n');

    expect(getLocalizedReleaseNotes(notes, 'zh-CN')).toBe('## 更新内容\n\n### 认证与会话\n\n- 中文更新 1');
    expect(getLocalizedReleaseNotes(notes, 'en-US')).toBe(
      [
        '## What\'s Changed',
        '',
        '### Authentication & Session',
        '',
        '- English update 1',
      ].join('\n'),
    );
  });

  it('removes Chinese details blocks for English release notes', () => {
    const notes = [
      '### What\'s Changed',
      '',
      '- English update 1',
      '',
      '<details>',
      '<summary>简体中文</summary>',
      '',
      '- 中文更新',
      '',
      '</details>',
      '',
      '### Fixes',
      '',
      '- English fix 1',
    ].join('\n');

    expect(getLocalizedReleaseNotes(notes, 'en-US')).toBe(
      [
        '### What\'s Changed',
        '',
        '- English update 1',
        '',
        '### Fixes',
        '',
        '- English fix 1',
      ].join('\n'),
    );
  });
});
