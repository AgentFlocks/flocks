import { describe, expect, it } from 'vitest';

import {
  buildInstructionDisplayText,
  parseInstructionDisplayText,
  stripTaskMetadata,
} from './display';

describe('session chat display metadata', () => {
  it('round-trips instruction display labels', () => {
    const text = buildInstructionDisplayText('创建 SOC 自定义页面');

    expect(text).toBe('@@flocks-instruction:创建 SOC 自定义页面');
    expect(parseInstructionDisplayText(text)).toBe('创建 SOC 自定义页面');
  });

  it('ignores regular display text', () => {
    expect(parseInstructionDisplayText('普通消息')).toBeNull();
  });

  it('removes task metadata while preserving the visible result', () => {
    const text = [
      '查询完成。',
      '',
      '<task_metadata>',
      'session_id: ses-child',
      '</task_metadata>',
      '',
      '消息发送失败。',
    ].join('\n');

    const visibleText = stripTaskMetadata(text);

    expect(visibleText).toContain('查询完成。');
    expect(visibleText).toContain('消息发送失败。');
    expect(visibleText).not.toContain('task_metadata');
    expect(visibleText).not.toContain('ses-child');
  });

  it('hides a trailing task metadata block while it is still streaming', () => {
    expect(stripTaskMetadata('查询完成。\n\n<task_metadata>\nsession_id: ses-child'))
      .toBe('查询完成。\n\n');
  });
});
