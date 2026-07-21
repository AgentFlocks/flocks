export interface PromptDisplayOptions {
  displayText?: string;
}

const INSTRUCTION_DISPLAY_PREFIX = '@@flocks-instruction:';

export function buildInstructionDisplayText(label: string): string {
  return `${INSTRUCTION_DISPLAY_PREFIX}${label}`;
}

export function parseInstructionDisplayText(text: string): string | null {
  return text.startsWith(INSTRUCTION_DISPLAY_PREFIX)
    ? text.slice(INSTRUCTION_DISPLAY_PREFIX.length).trim() || null
    : null;
}

/** Display-related options grouped to reduce prop surface. */
export interface SessionChatDisplay {
  /** Compact mode for panels/dialogs (default: true). Set false for full-page. */
  compact?: boolean;
  /** Let embedded chats use the full available message width. */
  fullWidth?: boolean;
  /** Show copy action on assistant messages */
  showActions?: boolean;
  /** Show timestamp below each message */
  showTimestamp?: boolean;
  /** Default-collapse intermediate reasoning and tool-process details in embedded panels. */
  collapseIntermediateSteps?: boolean;
  /** Initial open state for grouped reasoning/tool-process details. */
  processGroupsDefaultOpen?: boolean;
  /** Keep grouped reasoning/tool-process details open while the assistant message is actively streaming. */
  processGroupsOpenWhileActive?: boolean;
}
