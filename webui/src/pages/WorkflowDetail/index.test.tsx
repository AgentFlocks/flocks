import { beforeEach, describe, expect, it } from 'vitest';

import { getStoredSessions, pushStoredSession, setStoredSessions } from './sessionStorage';

describe('WorkflowDetail', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('未打开 Chat Tab 时仍能记录当前工作流的最近会话', () => {
    pushStoredSession('wf-1', {
      id: 'session-123',
      title: '最近会话',
      createdAt: Date.now(),
    });

    expect(getStoredSessions('wf-1')[0]?.id).toBe('session-123');
  });

  it('覆盖本地历史时会限制最大会话数量', () => {
    setStoredSessions(
      'wf-1',
      Array.from({ length: 20 }, (_, index) => ({
        id: `session-${index}`,
        title: `会话 ${index}`,
        createdAt: index,
      })),
    );

    const sessions = getStoredSessions('wf-1');
    expect(sessions).toHaveLength(15);
    expect(sessions[0]?.id).toBe('session-0');
    expect(sessions[14]?.id).toBe('session-14');
  });
});
