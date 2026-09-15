import { useCallback, useMemo, useState } from 'react';
import type { Message, MessagePart, ToolState } from '@/types';
import { ChatMessageTimeline, buildChatTimelineItems, mergeConsecutiveAssistantMessages } from './SessionChat';

export interface AuditTranscriptMessage {
  id: string;
  role: 'user' | 'assistant';
  time?: { created?: number; completed?: number };
  finish?: string;
  parts: Array<{
    id?: string;
    type: 'text' | 'reasoning' | 'thinking' | 'tool';
    text?: string;
    time?: { start: number; end?: number };
    tool?: string;
    callID?: string;
    status?: ToolState['status'];
    input?: ToolState['input'];
    output?: ToolState['output'];
    error?: string;
    title?: string;
  }>;
}

/** Read-only rendering of authorized audit transcripts using the workbench timeline. */
export default function AuditSessionTranscript({ messages, running = false }: {
  messages: AuditTranscriptMessage[];
  running?: boolean;
}) {
  const [processGroupOpenState, setProcessGroupOpenState] = useState<Record<string, boolean>>({});
  const onProcessGroupOpenChange = useCallback((key: string, open: boolean) => {
    setProcessGroupOpenState(current => ({ ...current, [key]: open }));
  }, []);
  const items = useMemo(() => {
    const normalized: Message[] = messages.map(message => ({
      id: message.id,
      sessionID: '',
      role: message.role,
      timestamp: message.time?.created || 0,
      finish: message.finish,
      parts: message.parts.map((part, index): MessagePart => ({
        id: part.id || `${message.id}-${index}`,
        type: part.type,
        text: part.text,
        time: part.type === 'tool' ? undefined : part.time,
        ...(part.type === 'tool' ? {
          tool: part.tool,
          callID: part.callID,
          state: {
            status: part.status || 'pending',
            input: part.input,
            output: part.output,
            error: part.error,
            title: part.title,
            time: part.time,
          },
        } : {}),
      })),
    }));
    return buildChatTimelineItems({
      messages: mergeConsecutiveAssistantMessages(normalized),
      skipIndices: new Set(),
      isStreaming: running,
    });
  }, [messages, running]);
  return (
    <div className="min-w-0 space-y-6 py-4 pl-10 pr-2">
      <ChatMessageTimeline items={items} compact={false} collapseIntermediateSteps
        processGroupOpenState={processGroupOpenState} onProcessGroupOpenChange={onProcessGroupOpenChange}
        processGroupsOpenWhileActive showTimestamp={false} showActions={false} />
    </div>
  );
}
