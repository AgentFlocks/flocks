import type { MessagePart, ToolState } from '@/types';

function areToolStatesRenderEqual(
  prevState?: ToolState,
  nextState?: ToolState,
): boolean {
  if (prevState === nextState) return true;
  if (
    prevState?.status !== nextState?.status
    || prevState?.title !== nextState?.title
    || prevState?.error !== nextState?.error
    || prevState?.time?.start !== nextState?.time?.start
    || prevState?.time?.end !== nextState?.time?.end
  ) {
    return false;
  }

  return (
    JSON.stringify(prevState?.input) === JSON.stringify(nextState?.input)
    && JSON.stringify(prevState?.output) === JSON.stringify(nextState?.output)
    && JSON.stringify(prevState?.metadata) === JSON.stringify(nextState?.metadata)
  );
}

function areLegacyToolPayloadsRenderEqual(
  prevPayload?: MessagePart['toolCall'] | MessagePart['toolResult'],
  nextPayload?: MessagePart['toolCall'] | MessagePart['toolResult'],
): boolean {
  if (prevPayload === nextPayload) return true;
  return JSON.stringify(prevPayload) === JSON.stringify(nextPayload);
}

export function areChatMessagePartsRenderEqual(
  prevParts?: MessagePart[],
  nextParts?: MessagePart[],
): boolean {
  if (prevParts === nextParts) return true;
  if ((prevParts?.length ?? 0) !== (nextParts?.length ?? 0)) return false;

  const total = prevParts?.length ?? 0;
  for (let i = 0; i < total; i++) {
    const prevPart = prevParts?.[i];
    const nextPart = nextParts?.[i];

    if (prevPart === nextPart) continue;
    if (!prevPart || !nextPart) return false;

    if (
      prevPart.id !== nextPart.id
      || prevPart.type !== nextPart.type
      || prevPart.text !== nextPart.text
      || prevPart.thinking !== nextPart.thinking
      || prevPart.synthetic !== nextPart.synthetic
      || prevPart.ignored !== nextPart.ignored
      || prevPart.tool !== nextPart.tool
      || prevPart.callID !== nextPart.callID
      || prevPart.mime !== nextPart.mime
      || prevPart.filename !== nextPart.filename
      || prevPart.url !== nextPart.url
      || prevPart.image?.url !== nextPart.image?.url
      || prevPart.image?.alt !== nextPart.image?.alt
    ) {
      return false;
    }

    if (!areToolStatesRenderEqual(prevPart.state, nextPart.state)) return false;
    if (!areLegacyToolPayloadsRenderEqual(prevPart.toolCall, nextPart.toolCall)) return false;
    if (!areLegacyToolPayloadsRenderEqual(prevPart.toolResult, nextPart.toolResult)) return false;
  }

  return true;
}
