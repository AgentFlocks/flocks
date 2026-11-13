import { describe, expect, it } from 'vitest';
import { getLocalizedReleaseNotes, humanizeReleaseNotes } from './releaseNotes';

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

describe('release notes written as commit subjects (v0.21 #49)', () => {
  const notes = [
    "## What's Changed",
    '',
    '* feat(models): streamline model discovery and imports',
    '* fix(enterprise): improve Flocks Pro installation and licensing flows',
    '',
    '<details>',
    '<summary>Chinese Release Notes</summary>',
    '',
    '* feat(models)：简化模型发现与导入，支持向导草稿恢复和 Azure 部署配置',
    '* fix(runtime)：就地升级保留额外 Python 依赖，保护配置凭据和受管环境，修复 Windows 多实例控制问题',
    '* fix(something-new)：修复一个问题',
    '* 已经写成读者语言的一行。',
    '',
    '</details>',
  ].join('\n');

  it('names the area instead of the commit type and module', () => {
    expect(getLocalizedReleaseNotes(notes, 'zh-CN')).toBe([
      '* 模型管理：简化模型发现与导入，支持向导草稿恢复和 Azure 部署配置。',
      '* 运行环境：就地升级保留额外 Python 依赖，保护配置凭据和受管环境，修复 Windows 多实例控制问题。',
      '* 修复一个问题。',
      '* 已经写成读者语言的一行。',
    ].join('\n'));
    expect(getLocalizedReleaseNotes(notes, 'en-US')).toContain('* Models: streamline model discovery and imports');
    expect(getLocalizedReleaseNotes(notes, 'en-US')).toContain('* Enterprise edition: improve Flocks Pro installation');
  });

  it('leaves examples in fenced code blocks as written', () => {
    expect(humanizeReleaseNotes(['```', '* fix: example command', '```', '* fix(models)：真的一条'].join('\n'), 'zh'))
      .toBe(['```', '* fix: example command', '```', '* 模型管理：真的一条。'].join('\n'));
  });

  it('keeps a longer fence open across a shorter fence inside it', () => {
    const notes = ['````', '```', '* fix: example command', '```', '````', '* fix(models)：真的一条'].join('\n');
    expect(humanizeReleaseNotes(notes, 'zh'))
      .toBe(['````', '```', '* fix: example command', '```', '````', '* 模型管理：真的一条。'].join('\n'));
  });

  it('leaves headings and ordinary text alone', () => {
    expect(humanizeReleaseNotes('## fix: not a bullet\nfeat(models): not a bullet either', 'zh'))
      .toBe('## fix: not a bullet\nfeat(models): not a bullet either');
  });
});
