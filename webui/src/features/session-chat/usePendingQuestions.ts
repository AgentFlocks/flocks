/**
 * Hook to manage pending question state for the Question tool.
 *
 * Centralises SSE event handling, answer/reject API calls, and session-switch
 * cleanup so SessionChat does not need to own question runtime state.
 */

import { useCallback, useRef, useState } from 'react';

import client from '@/api/client';
import type { QuestionItem } from '@/components/common/QuestionTool';

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
  const stateVersionRef = useRef(0);
  const fetchSequenceRef = useRef(0);

  const removeByCallId = useCallback((callID: string) => {
    stateVersionRef.current += 1;
    setPendingQuestions(prev => {
      const next = { ...prev };
      delete next[callID];
      return next;
    });
  }, []);

  const handleQuestionAsked = useCallback(
    (callID: string, requestId: string, questions: QuestionItem[]) => {
      stateVersionRef.current += 1;
      setPendingQuestions(prev => ({
        ...prev,
        [callID]: { requestId, questions },
      }));
    },
    [],
  );

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

  const removeByRequestId = useCallback((requestId: string) => {
    stateVersionRef.current += 1;
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

  const fetchPendingQuestions = useCallback(async (sessionId: string) => {
    const fetchSequence = ++fetchSequenceRef.current;
    const stateVersion = stateVersionRef.current;
    const response = await client.get<PendingQuestionApiResponse[]>(`/api/question/session/${sessionId}/pending`);
    if (
      fetchSequence !== fetchSequenceRef.current
      || stateVersion !== stateVersionRef.current
    ) {
      return;
    }
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

  const clearAll = useCallback(() => {
    stateVersionRef.current += 1;
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
