import { beforeEach, describe, expect, it } from 'vitest';

import { buildSessionsPath, resolveWorkflowSessionId } from './index';
import { getStoredSessions, pushStoredSession, setStoredSessions } from './sessionStorage';

describe('WorkflowDetail', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('跳转到当前工作流会话详情', async () => {
    expect(buildSessionsPath('session-123')).toBe('/sessions?session=session-123');
  });

  it('没有当前会话时回到会话列表', () => {
    expect(buildSessionsPath(null)).toBe('/sessions');
  });

  it('未打开 Chat Tab 时仍能从本地历史恢复最近会话', () => {
    pushStoredSession('wf-1', {
      id: 'session-123',
      title: '最近会话',
      createdAt: Date.now(),
    });

    const sessionId = resolveWorkflowSessionId(null, 'wf-1');
    expect(buildSessionsPath(sessionId)).toBe('/sessions?session=session-123');
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
