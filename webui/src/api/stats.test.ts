import { describe, expect, it, vi, beforeEach } from 'vitest';

const mockGet = vi.fn();

vi.mock('./client', () => ({
  apiClient: { get: (...args: unknown[]) => mockGet(...args) },
}));

// Helper: build a default mock for every endpoint except /api/skills and /api/agent.
function defaultMock(skillsData: unknown[], agentsData: unknown[] = []) {
  mockGet.mockImplementation((url: string) => {
    if (url === '/api/stats/summary') return Promise.reject(new Error('legacy backend'));
    if (url === '/api/skills') return Promise.resolve({ data: skillsData });
    if (url === '/api/task-system/dashboard') return Promise.resolve({ data: {} });
    if (url === '/api/agent') return Promise.resolve({ data: agentsData });
    if (url === '/api/workflow') return Promise.resolve({ data: [] });
    if (url === '/api/tools') return Promise.resolve({ data: [] });
    if (url === '/api/provider') return Promise.resolve({ data: { all: [], connected: [] } });
    if (url === '/api/health') return Promise.resolve({ data: { status: 'healthy' } });
    return Promise.resolve({ data: [] });
  });
}

describe('statsApi.getSystemStats', () => {
  beforeEach(() => vi.clearAllMocks());

  it('uses the lightweight summary endpoint when available', async () => {
    const summary = {
      tasks: { week: 3, scheduledActive: 1 },
      agents: { total: 2 },
      workflows: { total: 4 },
      skills: { total: 5 },
      tools: { total: 6 },
      models: { total: 7 },
      system: { status: 'healthy', message: 'ok' },
    };
    mockGet.mockResolvedValue({ data: summary });

    const { statsApi } = await import('./stats');
    const result = await statsApi.getSystemStats();

    expect(result).toEqual(summary);
    expect(mockGet).toHaveBeenCalledTimes(1);
    expect(mockGet).toHaveBeenCalledWith('/api/stats/summary');
  });

  it('counts only non-system skills', async () => {
    defaultMock([
      { category: 'custom' },
      { category: 'system' },
      { category: 'system' },
      { category: 'search' },
    ]);

    const { statsApi } = await import('./stats');
    const result = await statsApi.getSystemStats();

    // 4 skills total, 2 are 'system' — only 2 should be counted.
    expect(result.skills.total).toBe(2);
  });

  it('handles an all-system skill list gracefully (returns 0)', async () => {
    defaultMock([{ category: 'system' }, { category: 'system' }]);

    const { statsApi } = await import('./stats');
    const result = await statsApi.getSystemStats();
    expect(result.skills.total).toBe(0);
  });

  it('counts agents with the same visibility rule as the Agent page', async () => {
    defaultMock([], [
      { name: 'rex', mode: 'primary', tags: [] },
      { name: 'custom-helper', mode: 'subagent', tags: [] },
      { name: 'explore', mode: 'subagent', tags: ['system'] },
      { name: 'oracle', mode: 'subagent', tags: ['system'] },
    ]);

    const { statsApi } = await import('./stats');
    const result = await statsApi.getSystemStats();

    expect(result.agents.total).toBe(2);
  });

  it('handles skills API failure gracefully (returns 0)', async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === '/api/stats/summary') return Promise.reject(new Error('legacy backend'));
      if (url === '/api/skills') return Promise.reject(new Error('network'));
      if (url === '/api/task-system/dashboard') return Promise.resolve({ data: {} });
      if (url === '/api/agent') return Promise.resolve({ data: [] });
      if (url === '/api/workflow') return Promise.resolve({ data: [] });
      if (url === '/api/tools') return Promise.resolve({ data: [] });
      if (url === '/api/provider') return Promise.resolve({ data: { all: [], connected: [] } });
      if (url === '/api/health') return Promise.resolve({ data: { status: 'healthy' } });
      return Promise.resolve({ data: [] });
    });

    const { statsApi } = await import('./stats');
    const result = await statsApi.getSystemStats();
    expect(result.skills.total).toBe(0);
  });
});
