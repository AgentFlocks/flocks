import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const clientPostMock = vi.fn();
const clientGetMock = vi.fn();

vi.mock('@/api/client', () => ({
  __esModule: true,
  default: {
    get: (...args: unknown[]) => clientGetMock(...args),
    post: (...args: unknown[]) => clientPostMock(...args),
  },
}));

import { usePendingQuestions } from './usePendingQuestions';

describe('usePendingQuestions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('clears stale pending questions when reply returns 404', async () => {
    clientPostMock.mockRejectedValueOnce({ response: { status: 404 } });
    const { result } = renderHook(() => usePendingQuestions());

    act(() => {
      result.current.handleQuestionAsked('call-1', 'req-1', [{ question: 'Continue?' }]);
    });

    expect(result.current.pendingQuestions['call-1']).toBeDefined();

    await act(async () => {
      await result.current.submitAnswer('call-1', 'req-1', [['yes']]);
    });

    expect(result.current.pendingQuestions['call-1']).toBeUndefined();
  });

  it('clears stale pending questions when reject returns 404', async () => {
    clientPostMock.mockRejectedValueOnce({ response: { status: 404 } });
    const { result } = renderHook(() => usePendingQuestions());

    act(() => {
      result.current.handleQuestionAsked('call-1', 'req-1', [{ question: 'Continue?' }]);
    });

    await act(async () => {
      await result.current.submitReject('call-1', 'req-1');
    });

    expect(result.current.pendingQuestions['call-1']).toBeUndefined();
  });

  it('still surfaces non-404 reply errors', async () => {
    const error = { response: { status: 500 } };
    clientPostMock.mockRejectedValueOnce(error);
    const { result } = renderHook(() => usePendingQuestions());

    act(() => {
      result.current.handleQuestionAsked('call-1', 'req-1', [{ question: 'Continue?' }]);
    });

    await expect(result.current.submitAnswer('call-1', 'req-1', [['yes']])).rejects.toBe(error);
    expect(result.current.pendingQuestions['call-1']).toBeDefined();
  });
});
