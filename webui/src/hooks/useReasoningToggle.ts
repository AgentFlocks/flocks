import { useState, useMemo } from 'react';

/**
 * Shared hook for reasoning/thinking toggle logic.
 *
 * Used by both Session/MessageBubble and ChatDialog/DialogMessageBubble
 * to avoid duplicating the expand/collapse state management.
 *
 * Rules:
 * - While reasoning is in progress (no text/tool part yet) → always expanded
 * - Once reasoning is done (text or tool part exists, or message finished) → collapsed by default
 * - User can manually toggle (collapse/expand) each reasoning block independently
 */
interface ReasoningToggleOptions {
  /** Expand active reasoning before the assistant has produced text/tool output. */
  expandWhileActive?: boolean;
  /** Default expanded state after reasoning is complete. */
  defaultExpanded?: boolean;
}

export function useReasoningToggle(
  parts: any[],
  messageFinish?: any,
  options: ReasoningToggleOptions = {},
) {
  const expandWhileActive = options.expandWhileActive ?? true;
  const defaultExpanded = options.defaultExpanded ?? false;
  // Check if a text part already exists → reasoning is done
  const hasTextPart = useMemo(
    () => parts.some((p: any) => p.type === 'text' && p.text),
    [parts],
  );

  // Check if a tool part exists → reasoning is also done (e.g. reasoning + tool call, no text)
  const hasToolPart = useMemo(
    () => parts.some((p: any) => p.type === 'tool' || p.type === 'toolCall'),
    [parts],
  );

  const hasReasoningPart = useMemo(
    () => parts.some((p: any) => (p.type === 'reasoning' || p.type === 'thinking') && (p.text || p.thinking)),
    [parts],
  );

  const isReasoningDone = !!messageFinish || hasTextPart || hasToolPart;

  // Per-part expanded state: keyed by part ID or index string
  const [expandedByKey, setExpandedByKey] = useState<Record<string, boolean>>({});

  /**
   * Get the display state for a specific reasoning part.
   * - reasoning in progress → expanded (true)
   * - reasoning done → collapsed by default, user can expand manually
   */
  const getPartExpanded = (partKey: string): boolean => {
    const fallback = isReasoningDone ? defaultExpanded : expandWhileActive;
    // 思考结束后默认折叠，用户可手动展开
    return expandedByKey[partKey] ?? fallback;
  };

  /**
   * Toggle a specific reasoning part's expanded state.
   */
  const togglePart = (partKey: string) => {
    setExpandedByKey((prev) => ({
      ...prev,
      [partKey]: !(prev[partKey] ?? (isReasoningDone ? defaultExpanded : expandWhileActive)),
    }));
  };

  return {
    getPartExpanded,
    togglePart,
    isReasoningDone,
    hasTextPart,
  };
}
