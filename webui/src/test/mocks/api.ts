/**
 * Centralized API mock factory.
 *
 * Use these helpers inside `vi.mock('@/api/...')` calls to avoid
 * repeating boilerplate across test files.
 *
 * @example
 * vi.mock('@/api/session', () => ({
 *   sessionApi: createSessionApiMock(),
 * }));
 */

import { vi } from 'vitest';

// ---------------------------------------------------------------------------
// Generic resolved / rejected value helpers
// ---------------------------------------------------------------------------

export const ok = <T>(data: T) => Promise.resolve({ data, status: 200 });
export const fail = (status = 500, message = 'Server Error') =>
  Promise.reject(Object.assign(new Error(message), { response: { status, data: { error: message } } }));

// ---------------------------------------------------------------------------
// Session API mock
// ---------------------------------------------------------------------------

export function createSessionApiMock() {
  return {
    list: vi.fn().mockResolvedValue([]),
    get: vi.fn().mockResolvedValue({ id: 'ses_test', title: 'Test Session' }),
    create: vi.fn().mockResolvedValue({ data: { id: 'ses_new', title: 'New Session' } }),
    update: vi.fn().mockResolvedValue({ data: { id: 'ses_test', title: 'Updated' } }),
    delete: vi.fn().mockResolvedValue({ data: true }),
    listMessages: vi.fn().mockResolvedValue([]),
    sendMessage: vi.fn().mockResolvedValue({ data: { id: 'msg_test' } }),
    clear: vi.fn().mockResolvedValue({ data: true }),
    abort: vi.fn().mockResolvedValue({ data: null }),
  };
}

// ---------------------------------------------------------------------------
// Agent API mock
// ---------------------------------------------------------------------------

export const MOCK_AGENT = {
  name: 'rex',
  description: 'Security AI Agent',
  mode: 'primary',
  permission: [],
  options: {},
};

export function createAgentApiMock() {
  return {
    list: vi.fn().mockResolvedValue({ data: [MOCK_AGENT] }),
    get: vi.fn().mockResolvedValue({ data: MOCK_AGENT }),
    create: vi.fn().mockResolvedValue({ data: MOCK_AGENT }),
    update: vi.fn().mockResolvedValue({ data: MOCK_AGENT }),
    delete: vi.fn().mockResolvedValue({ data: true }),
    test: vi.fn().mockResolvedValue({ data: { sessionId: 'ses_test' } }),
  };
}

// ---------------------------------------------------------------------------
// Workflow API mock
// ---------------------------------------------------------------------------

export const MOCK_WORKFLOW = {
  id: 'wf_test',
  name: 'Test Workflow',
  category: 'default',
  workflowJson: { nodes: [], edges: [] },
  status: 'draft',
  createdAt: Date.now(),
  updatedAt: Date.now(),
  stats: {},
};

export function createWorkflowApiMock() {
  return {
    list: vi.fn().mockResolvedValue({ data: [MOCK_WORKFLOW] }),
    get: vi.fn().mockResolvedValue({ data: MOCK_WORKFLOW }),
    create: vi.fn().mockResolvedValue({ data: MOCK_WORKFLOW }),
    update: vi.fn().mockResolvedValue({ data: MOCK_WORKFLOW }),
    delete: vi.fn().mockResolvedValue({ data: true }),
    run: vi.fn().mockResolvedValue({ data: { executionId: 'exec_test', status: 'success' } }),
  };
}

// ---------------------------------------------------------------------------
// Skill API mock
// ---------------------------------------------------------------------------

export const MOCK_SKILL = {
  name: 'test-skill',
  description: 'A test skill',
  content: '# test skill',
  location: '/path/to/skill',
  source: 'project',
};

export function createSkillApiMock() {
  return {
    list: vi.fn().mockResolvedValue({ data: [MOCK_SKILL] }),
    get: vi.fn().mockResolvedValue({ data: MOCK_SKILL }),
    create: vi.fn().mockResolvedValue({}),
    update: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue({}),
  };
}

// ---------------------------------------------------------------------------
// apiClient (axios instance) mock
// ---------------------------------------------------------------------------

export function createApiClientMock() {
  return {
    get: vi.fn().mockResolvedValue({ data: {}, status: 200 }),
    post: vi.fn().mockResolvedValue({ data: {}, status: 200 }),
    put: vi.fn().mockResolvedValue({ data: {}, status: 200 }),
    patch: vi.fn().mockResolvedValue({ data: {}, status: 200 }),
    delete: vi.fn().mockResolvedValue({ data: true, status: 200 }),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  };
}
