import { describe, expect, it } from 'vitest';

import { buildSessionsPath } from './index';

describe('WorkflowDetail', () => {
  it('跳转到当前工作流会话详情', async () => {
    expect(buildSessionsPath('session-123')).toBe('/sessions?session=session-123');
  });

  it('没有当前会话时回到会话列表', () => {
    expect(buildSessionsPath(null)).toBe('/sessions');
  });
});
