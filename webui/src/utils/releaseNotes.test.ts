import { describe, expect, it } from 'vitest';
import { getLocalizedReleaseNotes } from './releaseNotes';

describe('getLocalizedReleaseNotes', () => {
  it('extracts the Chinese section for Chinese locales', () => {
    const notes = [
      '## 中文',
      '中文更新 1',
      '中文更新 2',
      '',
      '## English',
      'English update 1',
    ].join('\n');

    expect(getLocalizedReleaseNotes(notes, 'zh-CN')).toBe('中文更新 1\n中文更新 2');
  });

  it('extracts the English section for English locales', () => {
    const notes = [
      '## 简体中文',
      '中文更新',
      '',
      '## English',
      '### Authentication',
      'English update 1',
      'English update 2',
    ].join('\n');

    expect(getLocalizedReleaseNotes(notes, 'en-US')).toBe('### Authentication\nEnglish update 1\nEnglish update 2');
  });

  it('falls back to full release notes when the target section is missing', () => {
    const notes = [
      '## 中文',
      '中文更新',
    ].join('\n');

    expect(getLocalizedReleaseNotes(notes, 'en-US')).toBe(notes);
  });
});
