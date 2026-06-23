/**
 * Hook to manage pending question state for the Question tool.
 *
 * Centralises SSE event handling, answer/reject API calls, and session-switch
 * cleanup so that SessionChat and Session/index don't duplicate this logic.
 */

import { useState, useCallback } from 'react';
import type { QuestionItem } from '@/components/common/QuestionTool';
import client from '@/api/client';

export interface PendingQuestion {
  requestId: string;
  questions: QuestionItem[];
}

interface PendingQuestionApiResponse {
  id?: string;
  questions?: QuestionItem[];
  tool?: {
    callID?: string;
  };
}

function isNotFoundError(error: unknown): boolean {
  return !!(
    error &&
    typeof error === 'object' &&
    'response' in error &&
    (error as { response?: { status?: number } }).response?.status === 404
  );
}

export function usePendingQuestions() {
  const [pendingQuestions, setPendingQuestions] = useState<Record<string, PendingQuestion>>({});

  const removeByCallId = useCallback((callID: string) => {
    setPendingQuestions(prev => {
      const next = { ...prev };
      delete next[callID];
      return next;
    });
  }, []);

  /** Call from the SSE event handler when `question.asked` fires. */
  const handleQuestionAsked = useCallback(
    (callID: string, requestId: string, questions: QuestionItem[]) => {
      setPendingQuestions(prev => ({
        ...prev,
        [callID]: { requestId, questions },
      }));
    },
    [],
  );

  /** Submit answers for a pending question. */
  const submitAnswer = useCallback(
    async (callID: string, requestId: string, answers: string[][]) => {
      try {
        await client.post(`/api/question/${requestId}/reply`, { answers });
      } catch (error) {
        if (!isNotFoundError(error)) throw error;
      }
      removeByCallId(callID);
    },
    [removeByCallId],
  );

  /** Reject / skip a pending question. */
  const submitReject = useCallback(
    async (callID: string, requestId: string) => {
      try {
        await client.post(`/api/question/${requestId}/reject`, {});
      } catch (error) {
        if (!isNotFoundError(error)) throw error;
      }
      removeByCallId(callID);
    },
    [removeByCallId],
  );

  /** Remove a pending question by request ID (e.g. after SSE reply/reject). */
  const removeByRequestId = useCallback((requestId: string) => {
    setPendingQuestions(prev => {
      const next = { ...prev };
      for (const [callID, pending] of Object.entries(prev)) {
        if (pending.requestId === requestId) {
          delete next[callID];
        }
      }
      return next;
    });
  }, []);

  /** Recover pending questions for the active session after session switch / refresh. */
  const fetchPendingQuestions = useCallback(async (sessionId: string) => {
    const response = await client.get<PendingQuestionApiResponse[]>(`/api/question/session/${sessionId}/pending`);
    const next: Record<string, PendingQuestion> = {};
    for (const item of response.data || []) {
      const callID = item.tool?.callID;
      const requestId = item.id;
      if (!callID || !requestId) continue;
      next[callID] = {
        requestId,
        questions: item.questions || [],
      };
    }
    setPendingQuestions(next);
  }, []);

  /** Clear all pending questions (e.g. on session switch). */
  const clearAll = useCallback(() => {
    setPendingQuestions({});
  }, []);

  return {
    pendingQuestions,
    handleQuestionAsked,
    submitAnswer,
    submitReject,
    removeByRequestId,
    fetchPendingQuestions,
    clearAll,
  } as const;
}
