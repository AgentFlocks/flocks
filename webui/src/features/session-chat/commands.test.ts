import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  __resetSessionChatCommandsResourceForTesting,
  fetchSessionChatCommands,
} from './commands';

const { listCommandsMock } = vi.hoisted(() => ({
  listCommandsMock: vi.fn(),
}));

vi.mock('@/api/skill', () => ({
  commandAPI: {
    list: listCommandsMock,
  },
}));

function makeCommand(name: string) {
  return {
    name,
    canonical_name: name,
    description: `${name} command`,
    template: '',
    hidden: false,
    aliases: [],
    visible_surfaces: [],
    execution_kind: 'direct',
    allow_attachments: false,
    requires_existing_session: true,
    channel_safe: false,
  };
}

describe('session chat commands resource', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
    __resetSessionChatCommandsResourceForTesting();
  });

  it('shares concurrent command list requests and prepends the local /new command', async () => {
    let resolveCommands: (value: { data: any[] }) => void = () => {};
    listCommandsMock.mockReturnValue(new Promise((resolve) => {
      resolveCommands = resolve;
    }));

    const first = fetchSessionChatCommands();
    const second = fetchSessionChatCommands();

    expect(listCommandsMock).toHaveBeenCalledTimes(1);

    resolveCommands({ data: [makeCommand('summarize')] });

    const [firstCommands, secondCommands] = await Promise.all([first, second]);
    expect(firstCommands.map((command) => command.name)).toEqual(['new', 'summarize']);
    expect(secondCommands.map((command) => command.name)).toEqual(['new', 'summarize']);
  });

  it('reuses fresh commands without another request', async () => {
    listCommandsMock.mockResolvedValue({ data: [makeCommand('summarize')] });

    await fetchSessionChatCommands();
    await fetchSessionChatCommands();

    expect(listCommandsMock).toHaveBeenCalledTimes(1);
  });

  it('forces a retry after a failed command request', async () => {
    listCommandsMock
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ data: [makeCommand('summarize')] });

    await expect(fetchSessionChatCommands()).rejects.toThrow('offline');
    const commands = await fetchSessionChatCommands();

    expect(commands.map((command) => command.name)).toEqual(['new', 'summarize']);
    expect(listCommandsMock).toHaveBeenCalledTimes(2);
  });

  it('refreshes commands after the short autocomplete stale window', async () => {
    let now = 1_000;
    vi.spyOn(Date, 'now').mockImplementation(() => now);
    listCommandsMock
      .mockResolvedValueOnce({ data: [makeCommand('first')] })
      .mockResolvedValueOnce({ data: [makeCommand('second')] });

    await fetchSessionChatCommands();
    now += 5_001;
    const commands = await fetchSessionChatCommands();

    expect(commands.map((command) => command.name)).toEqual(['new', 'second']);
    expect(listCommandsMock).toHaveBeenCalledTimes(2);
  });
});
