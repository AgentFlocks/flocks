import type { ContextUsageSnapshot, QueuedPrompt } from '@/api/session';
import type { Message, MessagePart, SessionGoalState } from '@/types';

import type { SSEChatEvent } from './sseRouting';

export type SessionMessageInfo = Partial<Message> & {
  id?: string;
  sessionID?: string;
  role?: Message['role'];
  time?: { completed?: number | boolean | null };
};

export type CompactionStage =
  | 'load'
  | 'strategy'
  | 'chunk_done'
  | 'merge_started'
  | 'merge_done'
  | 'summarize_done'
  | 'complete';

export type SessionChatSSEAction =
  | { kind: 'ignore' }
  | { kind: 'session-cleared' }
  | { kind: 'session-status'; statusType?: string; message?: string }
  | { kind: 'message-updated'; info: SessionMessageInfo }
  | { kind: 'message-part-updated'; part: MessagePart; delta?: string }
  | { kind: 'question-asked'; callID: string; requestId: string; questions: unknown[] }
  | { kind: 'question-resolved'; requestId: string }
  | { kind: 'compaction-progress'; stage: CompactionStage; data: Record<string, unknown> }
  | { kind: 'prompt-queue-updated'; items: QueuedPrompt[] }
  | { kind: 'goal-updated'; goal: SessionGoalState }
  | { kind: 'context-compacted' }
  | { kind: 'context-usage-updated'; snapshot: ContextUsageSnapshot }
  | { kind: 'session-error'; message?: string };

function isRecord(value: unknown): value is Record<string, any> {
  return Boolean(value) && typeof value === 'object';
}

function isCurrentSession(properties: Record<string, any>, sessionId: string): boolean {
  return properties.sessionID === sessionId;
}

function readSessionStatusType(status: unknown): string | undefined {
  if (typeof status === 'string') return status;
  if (isRecord(status) && typeof status.type === 'string') return status.type;
  return undefined;
}

export function resolveSessionChatSSEAction(
  event: SSEChatEvent,
  sessionId?: string | null,
): SessionChatSSEAction {
  if (!sessionId || !isRecord(event.properties)) return { kind: 'ignore' };

  const { type } = event;
  const properties = event.properties;

  if (type === 'session.cleared') {
    return isCurrentSession(properties, sessionId) ? { kind: 'session-cleared' } : { kind: 'ignore' };
  }

  if (type === 'session.status') {
    if (!isCurrentSession(properties, sessionId)) return { kind: 'ignore' };
    return {
      kind: 'session-status',
      statusType: readSessionStatusType(properties.status),
      message: isRecord(properties.status) && typeof properties.status.message === 'string'
        ? properties.status.message
        : undefined,
    };
  }

  if (type === 'session.updated') {
    if (properties.id !== sessionId || properties.status !== 'idle') return { kind: 'ignore' };
    return { kind: 'session-status', statusType: 'idle' };
  }

  if (type === 'message.updated') {
    if (!isRecord(properties.info) || properties.info.sessionID !== sessionId) return { kind: 'ignore' };
    return { kind: 'message-updated', info: properties.info as SessionMessageInfo };
  }

  if (type === 'message.part.updated') {
    if (!isRecord(properties.part) || properties.part.sessionID !== sessionId) return { kind: 'ignore' };
    return {
      kind: 'message-part-updated',
      part: properties.part as MessagePart,
      delta: typeof properties.delta === 'string' ? properties.delta : undefined,
    };
  }

  if (type === 'question.asked') {
    if (!isCurrentSession(properties, sessionId)) return { kind: 'ignore' };
    const callID = isRecord(properties.tool) && typeof properties.tool.callID === 'string'
      ? properties.tool.callID
      : undefined;
    const requestId = typeof properties.id === 'string' ? properties.id : undefined;
    if (!callID || !requestId) return { kind: 'ignore' };
    return {
      kind: 'question-asked',
      callID,
      requestId,
      questions: Array.isArray(properties.questions) ? properties.questions : [],
    };
  }

  if (type === 'question.replied' || type === 'question.rejected') {
    if (!isCurrentSession(properties, sessionId)) return { kind: 'ignore' };
    const requestId = typeof properties.requestID === 'string' ? properties.requestID : undefined;
    return requestId ? { kind: 'question-resolved', requestId } : { kind: 'ignore' };
  }

  if (type === 'session.compaction_progress') {
    if (!isCurrentSession(properties, sessionId) || typeof properties.stage !== 'string') {
      return { kind: 'ignore' };
    }
    return {
      kind: 'compaction-progress',
      stage: properties.stage as CompactionStage,
      data: isRecord(properties.data) ? properties.data : {},
    };
  }

  if (type === 'session.prompt_queue.updated') {
    if (!isCurrentSession(properties, sessionId)) return { kind: 'ignore' };
    return {
      kind: 'prompt-queue-updated',
      items: Array.isArray(properties.items) ? properties.items as QueuedPrompt[] : [],
    };
  }

  if (type === 'session.goal.updated') {
    if (!isCurrentSession(properties, sessionId)) return { kind: 'ignore' };
    return { kind: 'goal-updated', goal: properties as SessionGoalState };
  }

  if (type === 'context.compacted') {
    return isCurrentSession(properties, sessionId) ? { kind: 'context-compacted' } : { kind: 'ignore' };
  }

  if (type === 'context.usage.updated') {
    return isCurrentSession(properties, sessionId)
      ? { kind: 'context-usage-updated', snapshot: properties as ContextUsageSnapshot }
      : { kind: 'ignore' };
  }

  if (type === 'session.error') {
    if (!isCurrentSession(properties, sessionId)) return { kind: 'ignore' };
    return {
      kind: 'session-error',
      message: isRecord(properties.error) && typeof properties.error.message === 'string'
        ? properties.error.message
        : undefined,
    };
  }

  return { kind: 'ignore' };
}
