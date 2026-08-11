export interface SSEChatEvent {
  type: string;
  properties?: Record<string, any>;
}

export function shouldForwardSSEEventToParent(event: SSEChatEvent, sessionId?: string | null): boolean {
  const { type, properties } = event;
  if (!properties) return false;

  if (
    type === 'session.updated' ||
    type === 'task.updated' ||
    type.startsWith('workflow.')
  ) {
    return true;
  }

  if (!sessionId) return false;

  return (
    properties.sessionID === sessionId ||
    properties.info?.sessionID === sessionId ||
    properties.part?.sessionID === sessionId ||
    (type.startsWith('session.') && properties.id === sessionId)
  );
}
