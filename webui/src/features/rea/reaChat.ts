import { createContext, useContext } from 'react';
import type { Message, MessagePart } from '@/types';

export const REA_TOOL_NAME = 'rea_analysis';

export type ReaToolAction = 'status' | 'install' | 'report';

export interface ReaChatContextValue {
  sessionId: string | null;
  /** Viewers without write access only see the cards. */
  readOnly: boolean;
  /** Older status and install cards collapse so one live card owns the actions. */
  latestCardPartId: string | null;
  /** Send a message into this conversation (queued while Rex is busy), shown as typed. */
  submitPrompt: (text: string) => Promise<void>;
  /** Put a guide into this conversation's message box; the user edits and sends it. */
  fillComposer: (text: string) => void;
}

export const ReaChatContext = createContext<ReaChatContextValue | null>(null);

export function useReaChat(): ReaChatContextValue | null {
  return useContext(ReaChatContext);
}

export function reaToolAction(part: MessagePart): ReaToolAction | null {
  if (part.type !== 'tool' || part.tool !== REA_TOOL_NAME) return null;
  const action = part.state?.input?.action;
  return action === 'status' || action === 'install' || action === 'report' ? action : null;
}

/** REA state Rex saw when it asked for the status (``metadata.rea.state``). */
function statusStateAtCall(part: MessagePart): string | null {
  const state = part.state?.metadata?.rea?.state;
  return typeof state === 'string' ? state : null;
}

/**
 * Parts drawn as cards between the process groups:
 * - a status call that found REA not usable (offers the install or the repair),
 * - every install call (the tier plan, progress, then the evidence or the failure),
 * - a finished report.
 * A status call that found REA ready stays an ordinary tool step.
 */
export function isReaCardPart(part: MessagePart): boolean {
  const action = reaToolAction(part);
  if (action === 'status') {
    const state = statusStateAtCall(part);
    return part.state?.status === 'completed' && state !== null && state !== 'ready';
  }
  if (action === 'install') return true;
  if (action === 'report') return part.state?.status === 'completed';
  return false;
}

/** The newest status or install card owns the actions; older ones collapse. */
export function findLatestReaCardPartId(messages: Message[]): string | null {
  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const parts = messages[messageIndex]?.parts || [];
    for (let partIndex = parts.length - 1; partIndex >= 0; partIndex -= 1) {
      const part = parts[partIndex];
      const action = reaToolAction(part);
      if ((action === 'status' || action === 'install') && isReaCardPart(part)) return part.id;
    }
  }
  return null;
}
