import { describe, expect, it } from 'vitest';

import { buildInstructionDisplayText, parseInstructionDisplayText } from './display';

describe('session chat display metadata', () => {
  it('round-trips instruction display labels', () => {
    const text = buildInstructionDisplayText('创建 SOC 自定义页面');

    expect(text).toBe('@@flocks-instruction:创建 SOC 自定义页面');
    expect(parseInstructionDisplayText(text)).toBe('创建 SOC 自定义页面');
  });

  it('ignores regular display text', () => {
    expect(parseInstructionDisplayText('普通消息')).toBeNull();
  });
});
