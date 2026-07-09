import { commandAPI, type Command } from '@/api/skill';
import {
  createSharedResource,
  type SharedResourceFetchOptions,
} from '@/hooks/useSharedResource';

const SESSION_CHAT_COMMANDS_STALE_TIME_MS = 10 * 60 * 1000;

const newSessionCommand = {
  name: 'new',
  canonical_name: 'new',
  description: 'Create a new session',
  template: '',
  hidden: false,
  aliases: [],
  visible_surfaces: [],
  execution_kind: 'session_control',
  allow_attachments: false,
  requires_existing_session: false,
  channel_safe: false,
} satisfies Command;

const sessionChatCommandsResource = createSharedResource<Command[]>({
  initialData: [],
  staleTimeMs: SESSION_CHAT_COMMANDS_STALE_TIME_MS,
  minFetchIntervalMs: 1_000,
  fetcher: async () => {
    const res = await commandAPI.list();
    return [newSessionCommand, ...(res.data ?? [])];
  },
  fallbackDataOnError: (previous) => previous,
  getErrorMessage: (err) => (err instanceof Error && err.message ? err.message : 'Failed to fetch commands'),
});

export async function fetchSessionChatCommands(
  options?: SharedResourceFetchOptions,
): Promise<Command[]> {
  const current = sessionChatCommandsResource.getSnapshot();
  const fetchOptions = current.error && options?.force !== true
    ? { ...options, force: true }
    : options;
  const commands = await sessionChatCommandsResource.fetch(fetchOptions);
  const error = sessionChatCommandsResource.getSnapshot().error;
  if (error) throw new Error(error);
  return commands;
}

export function __resetSessionChatCommandsResourceForTesting(): void {
  sessionChatCommandsResource.resetForTesting();
}
