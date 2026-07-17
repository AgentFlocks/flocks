import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { __resetChatModelResourcesForTesting } from '@/hooks/useChatModelResources';
import SessionPage, { getVisibleSessionGroupItems, groupSessionsByDate } from './index';

const {
  client,
  sessionApi,
  updateSessionTitle,
  removeSession,
  removeSessions,
  addSession,
  refetchSessions,
  useSessions,
  useAgents,
  useProviders,
  defaultModelAPI,
  modelV2API,
  hubAPI,
  toast,
} = vi.hoisted(() => ({
  client: {
    delete: vi.fn(),
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
  },
  sessionApi: {
    delete: vi.fn(),
    get: vi.fn(),
    getMessages: vi.fn(),
    update: vi.fn(),
  },
  updateSessionTitle: vi.fn(),
  removeSession: vi.fn(),
  removeSessions: vi.fn(),
  addSession: vi.fn(),
  refetchSessions: vi.fn(),
  useSessions: vi.fn(),
  useAgents: vi.fn(),
  useProviders: vi.fn(),
  defaultModelAPI: {
    getResolved: vi.fn(),
  },
  modelV2API: {
    listDefinitions: vi.fn(),
  },
  hubAPI: {
    catalog: vi.fn(),
    install: vi.fn(),
    installStream: vi.fn(),
  },
  toast: {
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock('@/api/client', () => ({
  __esModule: true,
  default: client,
}));

vi.mock('@/api/session', () => ({
  sessionApi,
}));

vi.mock('@/api/hub', () => ({
  hubAPI,
}));

vi.mock('@/hooks/useSessions', () => ({
  useSessions,
}));

vi.mock('@/hooks/useAgents', () => ({
  useAgents,
}));

vi.mock('@/hooks/useProviders', () => ({
  useProviders,
}));

vi.mock('@/api/provider', () => ({
  defaultModelAPI,
  modelV2API,
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => toast,
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading-spinner</div>,
}));

vi.mock('@/components/common/SessionChat', () => ({
  __esModule: true,
  buildInstructionDisplayText: (label: string) => `@@flocks-instruction:${label}`,
  default: function MockSessionChat({
    sessionId,
    mentionAgents,
    toolbarSlot,
    centerToolbarSlot,
    welcomeContent,
    initialMessage,
    initialDisplayText,
    onCreateAndSend,
    onSSEEvent,
    agentName,
    model,
    display,
    hideInput,
  }: {
    sessionId?: string | null;
    agentName?: string;
    mentionAgents?: Array<{ name: string }>;
    toolbarSlot?: React.ReactNode;
    centerToolbarSlot?: React.ReactNode;
    welcomeContent?: React.ReactNode | ((setInput: (text: string) => void) => React.ReactNode);
    initialMessage?: string | null;
    initialDisplayText?: string | null;
    model?: { providerID: string; modelID: string } | null;
    hideInput?: boolean;
    display?: {
      compact?: boolean;
      showActions?: boolean;
      showTimestamp?: boolean;
      collapseIntermediateSteps?: boolean;
      processGroupsDefaultOpen?: boolean;
      processGroupsOpenWhileActive?: boolean;
    };
    onCreateAndSend?: (
      text: string,
      imageParts?: unknown[],
      agentOverride?: string,
      modelOverride?: unknown,
      options?: { displayText?: string },
    ) => Promise<unknown> | unknown;
    onSSEEvent?: (event: { type: string; properties?: Record<string, unknown> }) => void;
  }) {
    const [input, setInput] = React.useState('');
    return (
      <div
        data-testid="session-chat"
        data-agent-name={agentName ?? ''}
        data-mention-agents={(mentionAgents ?? []).map((a) => a.name).join(',')}
        data-model={model ? `${model.providerID}/${model.modelID}` : ''}
        data-collapse-intermediate={String(Boolean(display?.collapseIntermediateSteps))}
        data-process-groups-default-open={String(Boolean(display?.processGroupsDefaultOpen))}
        data-process-groups-open-while-active={String(Boolean(display?.processGroupsOpenWhileActive))}
        data-hide-input={String(Boolean(hideInput))}
        data-initial-message={initialMessage ?? ''}
        data-initial-display={initialDisplayText ?? ''}
      >
        {sessionId ?? 'no-session'}
        {toolbarSlot}
        {centerToolbarSlot}
        {!sessionId && welcomeContent ? (
          typeof welcomeContent === 'function' ? welcomeContent(setInput) : welcomeContent
        ) : null}
        <div data-testid="mock-chat-input">{input}</div>
        <button type="button" onClick={() => void onCreateAndSend?.('hello from empty session', [], agentName)}>
          mock-create-and-send
        </button>
        <button
          type="button"
          onClick={() => onSSEEvent?.({
            type: 'session.updated',
            properties: { id: 'session-1', title: 'Updated Session' },
          })}
        >
          mock-session-updated
        </button>
      </div>
    );
  },
}));

vi.mock('@/utils/agentDisplay', () => ({
  getAgentDisplayDescription: () => 'agent-description',
  getAgentDisplayName: (agent: { name: string }) => agent.name.charAt(0).toUpperCase() + agent.name.slice(1),
  isAgentUsableInChat: (agent: { mode?: string; hidden?: boolean; delegatable?: boolean; tags?: string[] }) => (
    Boolean(agent)
    && !agent.hidden
    && !(agent.tags ?? []).includes('system')
    && (agent.mode === 'primary' || agent.delegatable !== false)
  ),
}));

vi.mock('@/utils/time', () => ({
  formatSessionDate: () => 'formatted-date',
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN' },
  }),
}));

const session = {
  id: 'session-1',
  slug: 'session-1',
  projectID: 'project-1',
  directory: '/tmp/project',
  title: 'Original Session',
  version: '1.0.0',
  time: {
    created: 1710000000000,
    updated: 1710000001000,
  },
  category: 'user',
};

const secondSession = {
  ...session,
  id: 'session-2',
  slug: 'session-2',
  title: 'Second Session',
};

const modelProviders = [
  { id: 'openai', name: 'OpenAI', configured: true },
  { id: 'minimax', name: 'MiniMax', configured: true },
];

const modelDefinitions = [
  {
    provider_id: 'openai',
    id: 'gpt-4o',
    name: 'GPT-4o',
    model_type: 'llm',
    source: 'predefined',
    capabilities: {},
    pricing: null,
    limits: {},
  },
  {
    provider_id: 'minimax',
    id: 'minimax-m3',
    name: 'MiniMax M3',
    model_type: 'llm',
    source: 'predefined',
    capabilities: {},
    pricing: null,
    limits: {},
  },
];

function renderSessionPage(
  initialEntry: string | { pathname: string; state?: unknown } = '/sessions',
) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <SessionPage />
    </MemoryRouter>,
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe('session sidebar grouping helpers', () => {
  it('groups sessions by updated date and applies search filtering', () => {
    const now = new Date(2026, 6, 9, 12, 0, 0);
    const makeSession = (id: string, title: string, updated: number) => ({
      ...session,
      id,
      title,
      time: { ...session.time, updated },
    });

    const groups = groupSessionsByDate([
      makeSession('today', 'Today Investigation', new Date(2026, 6, 9, 9).getTime()),
      makeSession('yesterday', 'Yesterday Work', new Date(2026, 6, 8, 9).getTime()),
      makeSession('earlier', 'Old Investigation', new Date(2026, 5, 1, 9).getTime()),
    ], 'investigation', now);

    expect(groups.map((group) => [group.key, group.items.map((item) => item.id)])).toEqual([
      ['today', ['today']],
      ['earlier', ['earlier']],
    ]);
  });

  it('limits collapsed older groups and reports hidden count', () => {
    const group = {
      key: 'thisWeek' as const,
      labelKey: 'groupThisWeek',
      items: Array.from({ length: 7 }, (_, index) => ({
        ...session,
        id: `session-${index}`,
        title: `Session ${index}`,
      })),
    };

    expect(getVisibleSessionGroupItems({
      group,
      expanded: false,
      searching: false,
    })).toMatchObject({
      visibleItems: group.items.slice(0, 5),
      hiddenCount: 2,
      limit: 5,
    });

    expect(getVisibleSessionGroupItems({
      group,
      expanded: false,
      searching: true,
    })).toMatchObject({
      visibleItems: group.items,
      hiddenCount: 0,
      limit: Infinity,
    });
  });
});

describe('SessionPage session actions menu', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    __resetChatModelResourcesForTesting();
    localStorage.clear();
    sessionStorage.clear();

    useSessions.mockReturnValue({
      sessions: [session],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });

    useAgents.mockReturnValue({
      agents: [],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    useProviders.mockReturnValue({
      providers: [],
      connectedIds: [],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    defaultModelAPI.getResolved.mockResolvedValue({ data: { provider_id: '', model_id: '' } });
    modelV2API.listDefinitions.mockResolvedValue({ data: { models: [] } });
    client.get.mockImplementation((url: string) => {
      if (url === '/api/project/current') {
        return Promise.resolve({ data: { id: 'project-1', worktree: '/tmp/project', name: 'project' } });
      }
      return Promise.resolve({
        data: [
          { id: 'project-1', worktree: '/tmp/project', name: 'project' },
        ],
      });
    });
    hubAPI.catalog.mockResolvedValue({
      data: [{ id: 'soc-workspace', type: 'component', state: 'installed' }],
    });
    hubAPI.install.mockResolvedValue({ data: { id: 'soc-workspace' } });
    hubAPI.installStream.mockResolvedValue(undefined);

    sessionApi.update.mockResolvedValue({ ...session, title: 'Renamed Session' });
    client.patch.mockResolvedValue({ data: { id: 'project-1', worktree: '/tmp/project', name: 'Renamed Project' } });
    client.post.mockResolvedValue({ data: secondSession });
    sessionApi.get.mockResolvedValue(session);
    sessionApi.getMessages.mockResolvedValue([
      {
        info: {
          id: 'message-1',
          sessionID: session.id,
          role: 'user',
          time: { created: session.time.created },
        },
        parts: [{ id: 'part-1', type: 'text', text: 'hello export' }],
      },
    ]);
    sessionApi.delete.mockResolvedValue(true);

    vi.stubGlobal('confirm', vi.fn(() => true));
  });

  it('groups sidebar sessions under their project', async () => {
    const user = userEvent.setup();

    renderSessionPage();

    const projectLabel = await screen.findByText('defaultProjectName');
    expect(screen.getByText('Original Session')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'toggleProject' }));

    expect(screen.queryByText('Original Session')).not.toBeInTheDocument();
  });

  it('toggles project sessions when clicking the selected project row', async () => {
    const user = userEvent.setup();

    renderSessionPage();

    await screen.findByText('defaultProjectName');
    const toggle = screen.getByRole('button', { name: 'toggleProject' });
    const projectRow = screen.getByRole('button', { name: 'selectProject' });

    await user.click(toggle);
    expect(screen.queryByText('Original Session')).not.toBeInTheDocument();

    await user.click(projectRow);
    expect(screen.getByText('Original Session')).toBeInTheDocument();

    await user.click(projectRow);
    expect(screen.queryByText('Original Session')).not.toBeInTheDocument();
  });

  it('merges legacy default sessions into the current project group', async () => {
    useSessions.mockReturnValue({
      sessions: [
        session,
        {
          ...secondSession,
          projectID: 'default',
          directory: '/tmp/project',
          title: 'Legacy Default Session',
        },
      ],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });

    renderSessionPage();

    await screen.findByText('defaultProjectName');
    expect(screen.getByText('Original Session')).toBeInTheDocument();
    expect(screen.getByText('Legacy Default Session')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.queryByText('project')).not.toBeInTheDocument();
  });

  it('labels the current project as default even when the stored name is the worktree name', async () => {
    client.get.mockImplementation((url: string) => {
      const currentProject = { id: 'project-1', worktree: '/tmp/project', name: 'project' };
      if (url === '/api/project/current') {
        return Promise.resolve({ data: currentProject });
      }
      return Promise.resolve({ data: [currentProject] });
    });

    renderSessionPage();

    expect(await screen.findByText('defaultProjectName')).toBeInTheDocument();
    expect(screen.queryByText('project')).not.toBeInTheDocument();
  });

  it('merges historical project rows for the current worktree into default', async () => {
    client.get.mockImplementation((url: string) => {
      const currentProject = { id: 'project-1', worktree: '/tmp/project', name: 'project' };
      if (url === '/api/project/current') {
        return Promise.resolve({ data: currentProject });
      }
      return Promise.resolve({
        data: [
          currentProject,
          { id: 'old-project-id', worktree: '/tmp/project', name: 'project' },
        ],
      });
    });
    useSessions.mockReturnValue({
      sessions: [
        session,
        {
          ...secondSession,
          projectID: 'old-project-id',
          directory: '/tmp/project',
          title: 'Historical Project Session',
        },
      ],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });

    renderSessionPage();

    expect(await screen.findByText('defaultProjectName')).toBeInTheDocument();
    expect(screen.getByText('Historical Project Session')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.queryByText('project')).not.toBeInTheDocument();
  });

  it('falls back to the first automatic project as default when current project is missing', async () => {
    client.get.mockImplementation((url: string) => {
      if (url === '/api/project/current') {
        return Promise.reject({ response: { status: 404 } });
      }
      return Promise.resolve({
        data: [
          { id: 'project-1', worktree: '/tmp/project', name: 'project' },
          { id: 'old-project-id', worktree: '/tmp/project', name: 'project' },
        ],
      });
    });
    useSessions.mockReturnValue({
      sessions: [
        session,
        {
          ...secondSession,
          projectID: 'old-project-id',
          directory: '/tmp/project',
          title: 'Old Flocks Project Session',
        },
      ],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });

    renderSessionPage();

    expect(await screen.findByText('defaultProjectName')).toBeInTheDocument();
    expect(screen.getByText('Old Flocks Project Session')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.queryByText('flocks')).not.toBeInTheDocument();
  });

  it('uses the current project as default when the project list is empty', async () => {
    client.get.mockImplementation((url: string) => {
      if (url === '/api/project/current') {
        return Promise.resolve({ data: { id: 'project-1', worktree: '/tmp/flocks', name: null } });
      }
      return Promise.resolve({ data: [] });
    });
    useSessions.mockReturnValue({
      sessions: [
        { ...session, projectID: 'project-1', directory: '/tmp/flocks' },
        {
          ...secondSession,
          projectID: 'old-project-id',
          directory: '/tmp/flocks',
          title: 'Old Same Worktree Session',
        },
      ],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });

    renderSessionPage();

    expect(await screen.findByText('defaultProjectName')).toBeInTheDocument();
    expect(screen.getByText('Old Same Worktree Session')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.queryByText('flocks')).not.toBeInTheDocument();
  });

  it('infers the default project from sessions when project metadata is unavailable', async () => {
    client.get.mockRejectedValue({ response: { status: 404 } });
    useSessions.mockReturnValue({
      sessions: [
        { ...session, projectID: 'project-1', directory: '/tmp/flocks' },
        {
          ...secondSession,
          projectID: 'old-project-id',
          directory: '/tmp/flocks',
          title: 'Fallback Same Worktree Session',
        },
      ],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });

    renderSessionPage();

    expect(await screen.findByText('defaultProjectName')).toBeInTheDocument();
    expect(screen.getByText('Fallback Same Worktree Session')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.queryByText('flocks')).not.toBeInTheDocument();
  });

  it('creates a user-managed project from the sidebar', async () => {
    const user = userEvent.setup();
    let projectRows = [
      { id: 'project-1', worktree: '/tmp/project', name: 'Security Project' },
    ];
    client.get.mockImplementation((url: string) => {
      if (url === '/api/project/current') {
        return Promise.resolve({ data: projectRows[0] });
      }
      return Promise.resolve({ data: projectRows });
    });
    client.post.mockImplementation((url: string, payload: Record<string, unknown>) => {
      if (url === '/api/project') {
        const created = { id: 'prj_project2', worktree: '/tmp/project', name: payload.name as string };
        projectRows = [projectRows[0], created];
        return Promise.resolve({ data: created });
      }
      return Promise.resolve({ data: secondSession });
    });

    renderSessionPage();

    await user.click(await screen.findByRole('button', { name: 'projectDialog.createTitle' }));
    await user.type(screen.getByLabelText('projectDialog.nameLabel'), 'Labs');
    await user.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith('/api/project', { name: 'Labs' });
      expect(screen.getByText('Labs')).toBeInTheDocument();
    });
  });

  it('keeps a newly created empty project visible while search is active', async () => {
    const user = userEvent.setup();
    const currentProject = { id: 'project-1', worktree: '/tmp/project', name: 'Security Project' };
    client.get.mockImplementation((url: string) => {
      if (url === '/api/project/current') {
        return Promise.resolve({ data: currentProject });
      }
      return Promise.resolve({ data: [currentProject] });
    });
    client.post.mockImplementation((url: string, payload: Record<string, unknown>) => {
      if (url === '/api/project') {
        const created = { id: 'prj_project2', worktree: '/tmp/project', name: payload.name as string };
        return Promise.resolve({ data: created });
      }
      return Promise.resolve({ data: secondSession });
    });

    renderSessionPage();

    await user.type(screen.getByPlaceholderText('filterConversations'), 'nothing matches');
    await user.click(await screen.findByRole('button', { name: 'projectDialog.createTitle' }));
    await user.type(screen.getByLabelText('projectDialog.nameLabel'), 'Labs');
    await user.click(screen.getByRole('button', { name: 'save' }));

    expect(await screen.findByText('Labs')).toBeInTheDocument();
    expect(screen.getByText('noProjectSessions')).toBeInTheDocument();
  });

  it('renames a project from the sidebar', async () => {
    const user = userEvent.setup();
    client.get.mockImplementation((url: string) => {
      const currentProject = { id: 'project-1', worktree: '/tmp/project', name: 'project' };
      if (url === '/api/project/current') {
        return Promise.resolve({ data: currentProject });
      }
      return Promise.resolve({
        data: [
          currentProject,
          { id: 'historical-project-id', worktree: '/tmp/labs', name: 'Labs' },
        ],
      });
    });

    renderSessionPage();

    const projectLabel = await screen.findByText('Labs');
    const projectRow = projectLabel.closest('[class*="group/project"]');
    expect(projectRow).not.toBeNull();
    await user.click(within(projectRow as HTMLElement).getByRole('button', { name: 'projectActions' }));
    await user.click(within(projectRow as HTMLElement).getByRole('menuitem', { name: 'projectDialog.renameAction' }));
    const input = screen.getByLabelText('projectDialog.nameLabel');
    await user.clear(input);
    await user.type(input, 'Renamed Project');
    await user.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => {
      expect(client.patch).toHaveBeenCalledWith('/api/project/historical-project-id', { name: 'Renamed Project' });
    });
  });

  it('opens project actions and renames the default project', async () => {
    const user = userEvent.setup();
    client.patch.mockResolvedValue({
      data: { id: 'project-1', worktree: '/tmp/project', name: 'Main Project' },
    });

    renderSessionPage();

    const projectLabel = await screen.findByText('defaultProjectName');
    const projectRow = projectLabel.closest('[class*="group/project"]');
    expect(projectRow).not.toBeNull();
    await user.click(within(projectRow as HTMLElement).getByRole('button', { name: 'projectActions' }));
    await user.click(within(projectRow as HTMLElement).getByRole('menuitem', { name: 'projectDialog.renameAction' }));

    const input = screen.getByLabelText('projectDialog.nameLabel');
    await user.clear(input);
    await user.type(input, 'Main Project');
    await user.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => {
      expect(client.patch).toHaveBeenCalledWith('/api/project/project-1', { name: 'Main Project' });
    });
    expect(await screen.findByText('Main Project')).toBeInTheDocument();
  });

  it('deletes an empty user-managed project after confirmation', async () => {
    const user = userEvent.setup();
    const currentProject = { id: 'project-1', worktree: '/tmp/project', name: 'project' };
    client.get.mockImplementation((url: string) => {
      if (url === '/api/project/current') return Promise.resolve({ data: currentProject });
      return Promise.resolve({
        data: [
          currentProject,
          { id: 'prj_project2', worktree: '/tmp/project', name: 'Labs' },
        ],
      });
    });
    client.delete.mockResolvedValue({ data: true });

    renderSessionPage();

    const projectLabel = await screen.findByText('Labs');
    const projectRow = projectLabel.closest('[class*="group/project"]');
    expect(projectRow).not.toBeNull();
    await user.click(within(projectRow as HTMLElement).getByRole('button', { name: 'projectActions' }));
    await user.click(within(projectRow as HTMLElement).getByRole('menuitem', { name: 'projectDialog.deleteAction' }));
    await user.click(screen.getByRole('button', { name: 'projectDialog.confirmDelete' }));

    await waitFor(() => {
      expect(client.delete).toHaveBeenCalledWith('/api/project/prj_project2');
      expect(screen.queryByText('Labs')).not.toBeInTheDocument();
    });
  });

  it('creates a session from a specific project row', async () => {
    const user = userEvent.setup();
    client.get.mockImplementation((url: string) => {
      const currentProject = { id: 'project-1', worktree: '/tmp/project', name: 'project' };
      if (url === '/api/project/current') {
        return Promise.resolve({ data: currentProject });
      }
      return Promise.resolve({
        data: [
          currentProject,
          { id: 'prj_project2', worktree: '/tmp/project', name: 'Labs' },
        ],
      });
    });
    client.post.mockResolvedValue({
      data: {
        ...secondSession,
        id: 'session-labs',
        projectID: 'prj_project2',
        title: 'New Session',
      },
    });

    renderSessionPage();

    const projectLabel = await screen.findByText('Labs');
    const projectRow = projectLabel.closest('[class*="group/project"]');
    expect(projectRow).not.toBeNull();
    await user.click(within(projectRow as HTMLElement).getByRole('button', { name: 'createSessionInProject' }));

    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith('/api/session', {
        title: 'New Session',
        projectID: 'prj_project2',
      });
    });
    expect(addSession).toHaveBeenCalledWith(expect.objectContaining({
      id: 'session-labs',
      projectID: 'prj_project2',
    }));
  });

  it('opens the actions menu for a session item', async () => {
    const user = userEvent.setup();

    renderSessionPage();

    await user.click(screen.getByRole('button', { name: 'moreActions' }));

    expect(screen.getByRole('button', { name: 'rename' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'downloadJson' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'deleteAction' })).toBeInTheDocument();
  });

  it('renames a session inline from the actions menu', async () => {
    const user = userEvent.setup();

    renderSessionPage();

    await user.click(screen.getByRole('button', { name: 'moreActions' }));
    await user.click(screen.getByRole('button', { name: 'rename' }));

    const input = screen.getByRole('textbox', { name: 'rename' });
    await user.clear(input);
    await user.type(input, 'Renamed Session{enter}');

    await waitFor(() => {
      expect(sessionApi.update).toHaveBeenCalledWith('session-1', { title: 'Renamed Session' });
    });
    expect(updateSessionTitle).toHaveBeenCalledWith('session-1', 'Renamed Session');
    expect(sessionApi.update).toHaveBeenCalledTimes(1);
  });

  it('coalesces bursty session.updated events into one sidebar refetch', () => {
    vi.useFakeTimers();
    try {
      renderSessionPage();

      const emitSessionUpdated = screen.getByRole('button', { name: 'mock-session-updated' });
      act(() => {
        emitSessionUpdated.click();
        emitSessionUpdated.click();
      });

      expect(updateSessionTitle).toHaveBeenCalledTimes(2);
      expect(refetchSessions).not.toHaveBeenCalled();

      act(() => {
        vi.advanceTimersByTime(499);
      });
      expect(refetchSessions).not.toHaveBeenCalled();

      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(refetchSessions).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('downloads session data as CLI-compatible JSON', async () => {
    const user = userEvent.setup();
    const OriginalBlob = Blob;
    const originalCreateElement = document.createElement.bind(document);
    let createdAnchor: HTMLAnchorElement | null = null;
    let blobArg: Blob | null = null;
    let blobParts: BlobPart[] = [];

    class BlobMock extends OriginalBlob {
      constructor(parts: BlobPart[], options?: BlobPropertyBag) {
        blobParts = parts;
        super(parts, options);
      }
    }
    vi.stubGlobal('Blob', BlobMock);

    const createElementSpy = vi.spyOn(document, 'createElement').mockImplementation(((tagName: string, options?: ElementCreationOptions) => {
      if (tagName === 'a') {
        const anchor = originalCreateElement('a');
        vi.spyOn(anchor, 'click').mockImplementation(() => {});
        createdAnchor = anchor;
        return anchor;
      }
      return originalCreateElement(tagName, options);
    }) as typeof document.createElement);

    const createObjectUrlSpy = vi.spyOn(URL, 'createObjectURL').mockImplementation((blob: Blob | MediaSource) => {
      blobArg = blob as Blob;
      return 'blob:session-export';
    });
    const revokeObjectUrlSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    renderSessionPage();

    await user.click(screen.getByRole('button', { name: 'moreActions' }));
    await user.click(screen.getByRole('button', { name: 'downloadJson' }));

    await waitFor(() => {
      expect(sessionApi.get).toHaveBeenCalledWith('session-1');
      expect(sessionApi.getMessages).toHaveBeenCalledWith('session-1');
    });

    await waitFor(() => {
      expect(createdAnchor?.download).toBe('session-Original-Session.json');
      expect(createdAnchor?.click).toHaveBeenCalled();
      expect(revokeObjectUrlSpy).toHaveBeenCalledWith('blob:session-export');
    });

    const payload = JSON.parse(String(blobParts[0]));
    expect(payload).toEqual({
      info: session,
      messages: [
        {
          info: {
            id: 'message-1',
            sessionID: 'session-1',
            role: 'user',
            time: { created: 1710000000000 },
          },
          parts: [{ id: 'part-1', type: 'text', text: 'hello export' }],
        },
      ],
    });

    createElementSpy.mockRestore();
    createObjectUrlSpy.mockRestore();
    revokeObjectUrlSpy.mockRestore();
    vi.stubGlobal('Blob', OriginalBlob);
  });

  it('deletes a session from the actions menu', async () => {
    const user = userEvent.setup();

    renderSessionPage();

    await user.click(screen.getByRole('button', { name: 'moreActions' }));
    await user.click(screen.getByRole('button', { name: 'deleteAction' }));

    await waitFor(() => {
      expect(sessionApi.delete).toHaveBeenCalledWith('session-1');
    });
    expect(removeSession).toHaveBeenCalledWith('session-1');
    expect(global.confirm).toHaveBeenCalledWith('confirmDelete');
  });

  it('does not auto-attach any session on first load without history', () => {
    renderSessionPage();

    expect(screen.getByTestId('session-chat')).toHaveTextContent('no-session');
  });

  it('passes URL display text as an instruction label for initial session messages', async () => {
    const message = 'Create a SOC custom page with the scoped workspace constraints.';
    const display = '创建 SOC 自定义页面';

    renderSessionPage(`/sessions?session=session-1&message=${encodeURIComponent(message)}&display=${encodeURIComponent(display)}`);

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-initial-message', message);
    });
    expect(screen.getByTestId('session-chat')).toHaveAttribute(
      'data-initial-display',
      '@@flocks-instruction:创建 SOC 自定义页面',
    );
  });

  it('starts SOC alert operations setup when the component is already installed', async () => {
    const user = userEvent.setup();

    renderSessionPage();
    await user.click(screen.getByRole('button', { name: 'welcome.alertOperations' }));

    await waitFor(() => {
      expect(hubAPI.catalog).toHaveBeenCalledWith({ type: 'component', q: 'soc-workspace' });
    });
    expect(hubAPI.install).not.toHaveBeenCalled();
    expect(hubAPI.installStream).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith(
        '/api/session/session-2/prompt_async',
        expect.objectContaining({
          displayText: '@@flocks-instruction:welcome.alertOperations',
          parts: expect.arrayContaining([
            expect.objectContaining({
              text: 'welcome.alertOperationsSuggestion',
              type: 'text',
            }),
          ]),
        }),
      );
    });
    expect(screen.getByTestId('mock-chat-input')).toHaveTextContent('');
  });

  it('installs the SOC workspace component before starting alert operations setup', async () => {
    const user = userEvent.setup();
    hubAPI.catalog.mockResolvedValueOnce({
      data: [{
        id: 'soc-workspace',
        type: 'component',
        name: 'SOC Workspace Component',
        nameCn: 'SOC 工作区场景套件',
        state: 'available',
      }],
    });
    hubAPI.installStream.mockImplementationOnce(async (_type, _id, onProgress) => {
      onProgress({
        event: 'start',
        id: 'soc-workspace',
        type: 'component',
        name: 'SOC Workspace Component',
        nameCn: 'SOC 工作区场景套件',
        total: 1,
        items: [{
          type: 'webui',
          id: 'soc_ui',
          name: 'SOC Workspace WebUI',
          status: 'pending',
        }],
      });
      onProgress({
        event: 'item',
        id: 'soc-workspace',
        type: 'component',
        name: 'SOC Workspace Component',
        nameCn: 'SOC 工作区场景套件',
        total: 1,
        item: {
          type: 'webui',
          id: 'soc_ui',
          name: 'SOC Workspace WebUI',
          status: 'installed',
        },
      });
      onProgress({
        event: 'complete',
        id: 'soc-workspace',
        type: 'component',
        name: 'SOC Workspace Component',
        nameCn: 'SOC 工作区场景套件',
        total: 1,
      });
    });

    renderSessionPage();
    await user.click(screen.getByRole('button', { name: 'welcome.alertOperations' }));

    await waitFor(() => {
      expect(hubAPI.installStream).toHaveBeenCalledWith('component', 'soc-workspace', expect.any(Function));
    });
    expect(await screen.findByText('场景套件安装进度')).toBeInTheDocument();
    expect(screen.getByText('SOC Workspace WebUI')).toBeInTheDocument();
    expect(screen.getByText('已安装')).toBeInTheDocument();
    expect(global.confirm).toHaveBeenCalledWith('welcome.socComponentInstallConfirm');
    expect(toast.success).not.toHaveBeenCalled();
    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith(
        '/api/session/session-2/prompt_async',
        expect.objectContaining({
          displayText: '@@flocks-instruction:welcome.alertOperations',
          parts: expect.arrayContaining([
            expect.objectContaining({
              text: 'welcome.alertOperationsSuggestion',
              type: 'text',
            }),
          ]),
        }),
      );
    });
    expect(screen.getByTestId('mock-chat-input')).toHaveTextContent('');
  });

  it('shows a localized error when the SOC workspace component is missing', async () => {
    const user = userEvent.setup();
    hubAPI.catalog.mockResolvedValueOnce({ data: [] });

    renderSessionPage();
    await user.click(screen.getByRole('button', { name: 'welcome.alertOperations' }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        'welcome.socComponentMissingTitle',
        'welcome.socComponentMissingDescription',
      );
    });
    expect(hubAPI.install).not.toHaveBeenCalled();
    expect(hubAPI.installStream).not.toHaveBeenCalled();
    expect(client.post).not.toHaveBeenCalled();
    expect(screen.getByTestId('mock-chat-input')).toHaveTextContent('');
  });

  it('shows a localized error title when SOC workspace component installation fails', async () => {
    const user = userEvent.setup();
    hubAPI.catalog.mockResolvedValueOnce({
      data: [{
        id: 'soc-workspace',
        type: 'component',
        name: 'SOC Workspace Component',
        nameCn: 'SOC 工作区场景套件',
        state: 'available',
      }],
    });
    hubAPI.installStream.mockRejectedValueOnce(new Error('install failed'));

    renderSessionPage();
    await user.click(screen.getByRole('button', { name: 'welcome.alertOperations' }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        'welcome.socComponentInstallFailedTitle',
        'install failed',
      );
    });
    expect(await screen.findByText('场景套件安装进度')).toBeInTheDocument();
    expect(screen.getByText('安装失败: SOC 工作区场景套件')).toBeInTheDocument();
    expect(client.post).not.toHaveBeenCalled();
    expect(screen.getByTestId('mock-chat-input')).toHaveTextContent('');
  });

  it('does not start alert operations setup when component installation is declined', async () => {
    const user = userEvent.setup();
    vi.mocked(global.confirm).mockReturnValueOnce(false);
    hubAPI.catalog.mockResolvedValueOnce({
      data: [{ id: 'soc-workspace', type: 'component', state: 'available' }],
    });

    renderSessionPage();
    await user.click(screen.getByRole('button', { name: 'welcome.alertOperations' }));

    await waitFor(() => {
      expect(hubAPI.catalog).toHaveBeenCalledWith({ type: 'component', q: 'soc-workspace' });
    });
    expect(hubAPI.install).not.toHaveBeenCalled();
    expect(hubAPI.installStream).not.toHaveBeenCalled();
    expect(screen.getByTestId('mock-chat-input')).toHaveTextContent('');
  });

  it('does not auto-attach the previously selected session on first app visit', () => {
    localStorage.setItem('flocks:last-selected-session', 'session-1');

    renderSessionPage();

    expect(screen.getByTestId('session-chat')).toHaveTextContent('no-session');
  });

  it('attaches the previously selected session after the session page has been visited', () => {
    localStorage.setItem('flocks:last-selected-session', 'session-1');
    sessionStorage.setItem('flocks:sessions:visited', 'true');

    renderSessionPage();

    expect(screen.getByTestId('session-chat')).toHaveTextContent('session-1');
  });

  it('does not auto-attach the previously selected session when entering from home', () => {
    localStorage.setItem('flocks:last-selected-session', 'session-1');
    sessionStorage.setItem('flocks:sessions:visited', 'true');

    renderSessionPage({
      pathname: '/sessions',
      state: { skipLastSelectedSessionRestore: true },
    });

    expect(screen.getByTestId('session-chat')).toHaveTextContent('no-session');
  });

  it('keeps session process groups collapsed by default and open while running', () => {
    renderSessionPage();

    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-collapse-intermediate', 'true');
    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-process-groups-default-open', 'false');
    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-process-groups-open-while-active', 'true');
  });

  it('syncs selected session when query param changes after mount', async () => {
    const user = userEvent.setup();

    useSessions.mockReturnValue({
      sessions: [session, secondSession],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });

    function NavigateButton() {
      const navigate = useNavigate();
      return (
        <button type="button" onClick={() => navigate('/sessions?session=session-2')}>
          go-session-2
        </button>
      );
    }

    render(
      <MemoryRouter initialEntries={['/sessions']}>
        <NavigateButton />
        <SessionPage />
      </MemoryRouter>,
    );

    expect(screen.getByTestId('session-chat')).toHaveTextContent('no-session');

    await user.click(screen.getByRole('button', { name: 'go-session-2' }));

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveTextContent('session-2');
    });
  });

  it('keeps a selected session that is valid but missing from the current list', async () => {
    const request = deferred<typeof session & { canWrite: boolean }>();
    useSessions.mockReturnValue({
      sessions: [],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });
    sessionApi.get.mockReturnValue(request.promise);
    const fetchedSession = {
      ...session,
      id: 'session-missing-from-list',
      title: 'Fetched Session',
      canWrite: false,
    };

    renderSessionPage('/sessions?session=session-missing-from-list');

    await waitFor(() => {
      expect(sessionApi.get).toHaveBeenCalledWith('session-missing-from-list');
    });
    expect(screen.queryByTestId('session-chat')).not.toBeInTheDocument();
    expect(screen.getByText('loading-spinner')).toBeInTheDocument();

    await act(async () => {
      request.resolve(fetchedSession);
      await request.promise;
    });

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveTextContent('session-missing-from-list');
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-hide-input', 'true');
    });
  });

  it('clears the selected session after confirming it no longer exists', async () => {
    useSessions.mockReturnValue({
      sessions: [],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });
    sessionApi.get.mockRejectedValue({ response: { status: 404 } });

    renderSessionPage('/sessions?session=session-deleted');

    await waitFor(() => {
      expect(sessionApi.get).toHaveBeenCalledWith('session-deleted');
      expect(screen.getByTestId('session-chat')).toHaveTextContent('no-session');
    });
  });

  it('drops a URL initial message when the target session no longer exists', async () => {
    const user = userEvent.setup();
    const message = 'Do not send this to another session';
    sessionApi.get.mockRejectedValue({ response: { status: 404 } });

    renderSessionPage(`/sessions?session=session-deleted&message=${encodeURIComponent(message)}`);

    await waitFor(() => {
      expect(sessionApi.get).toHaveBeenCalledWith('session-deleted');
      expect(screen.getByTestId('session-chat')).toHaveTextContent('no-session');
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-initial-message', '');
    });

    await user.click(screen.getByText('Original Session'));

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveTextContent('session-1');
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-initial-message', '');
    });
  });

  it('lists the same visible agents as the Agent page selector logic', async () => {
    const user = userEvent.setup();
    useAgents.mockReturnValue({
      agents: [
        {
          name: 'rex',
          description: 'Rex',
          mode: 'primary',
          permission: [],
          options: {},
          skills: [],
          tools: [],
        },
        {
          name: 'explore',
          description: 'Explore',
          mode: 'subagent',
          native: true,
          permission: [],
          options: {},
          skills: [],
          tools: [],
        },
        {
          name: 'hidden-system',
          description: 'System',
          mode: 'subagent',
          tags: ['system'],
          permission: [],
          options: {},
          skills: [],
          tools: [],
        },
        {
          name: 'oracle',
          description: 'Oracle',
          mode: 'subagent',
          native: true,
          delegatable: false,
          permission: [],
          options: {},
          skills: [],
          tools: [],
        },
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderSessionPage();

    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-mention-agents', 'rex,explore');

    await user.click(screen.getByRole('button', { name: /Rex/i }));

    expect(screen.getByRole('button', { name: /Explore/i })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /hidden-system/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Oracle/i })).not.toBeInTheDocument();
  });

  it('resets the chat agent to Rex when creating a new session', async () => {
    const user = userEvent.setup();
    useAgents.mockReturnValue({
      agents: [
        {
          name: 'rex',
          description: 'Rex',
          mode: 'primary',
          permission: [],
          options: {},
          skills: [],
          tools: [],
        },
        {
          name: 'explore',
          description: 'Explore',
          mode: 'subagent',
          native: true,
          permission: [],
          options: {},
          skills: [],
          tools: [],
        },
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderSessionPage();

    await user.click(screen.getByRole('button', { name: /Rex/i }));
    await user.click(screen.getByRole('button', { name: /Explore/i }));
    expect(screen.getByRole('button', { name: /Explore/i })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'newSession' }));

    await waitFor(() => {
      expect(addSession).toHaveBeenCalledWith(secondSession);
    });
    expect(screen.getByRole('button', { name: /Rex/i })).toBeInTheDocument();
  });

  it('shows the pinned model for the selected session on load', async () => {
    useSessions.mockReturnValue({
      sessions: [{
        ...session,
        provider: 'minimax',
        model: 'minimax-m3',
        model_pinned: true,
      }],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });
    useProviders.mockReturnValue({
      providers: modelProviders,
      connectedIds: [],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    defaultModelAPI.getResolved.mockResolvedValue({ data: { provider_id: 'openai', model_id: 'gpt-4o' } });
    modelV2API.listDefinitions.mockResolvedValue({ data: { models: modelDefinitions } });

    renderSessionPage('/sessions?session=session-1');

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-model', 'minimax/minimax-m3');
    });
    expect(defaultModelAPI.getResolved).not.toHaveBeenCalled();
  });

  it('persists model changes to the selected session', async () => {
    const user = userEvent.setup();
    useSessions.mockReturnValue({
      sessions: [session],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });
    useProviders.mockReturnValue({
      providers: modelProviders,
      connectedIds: [],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    defaultModelAPI.getResolved.mockResolvedValue({ data: { provider_id: 'openai', model_id: 'gpt-4o' } });
    modelV2API.listDefinitions.mockResolvedValue({ data: { models: modelDefinitions } });
    sessionApi.update.mockResolvedValue({
      ...session,
      provider: 'minimax',
      model: 'minimax-m3',
      model_pinned: true,
    });

    renderSessionPage('/sessions?session=session-1');

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-model', 'openai/gpt-4o');
    });

    await user.click(screen.getByRole('button', { name: /GPT-4o/i }));
    await user.click(screen.getByRole('button', { name: /MiniMax M3/i }));

    await waitFor(() => {
      expect(sessionApi.update).toHaveBeenCalledWith('session-1', {
        provider: 'minimax',
        model: 'minimax-m3',
        model_pinned: true,
      });
    });
    expect(refetchSessions).toHaveBeenCalled();
  });

  it('resets the selected model to the default when creating a new session', async () => {
    const user = userEvent.setup();
    useSessions.mockReturnValue({
      sessions: [{
        ...session,
        provider: 'minimax',
        model: 'minimax-m3',
        model_pinned: true,
      }],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });
    useProviders.mockReturnValue({
      providers: modelProviders,
      connectedIds: [],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    defaultModelAPI.getResolved.mockResolvedValue({ data: { provider_id: 'openai', model_id: 'gpt-4o' } });
    modelV2API.listDefinitions.mockResolvedValue({ data: { models: modelDefinitions } });

    renderSessionPage('/sessions?session=session-1');

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-model', 'minimax/minimax-m3');
    });

    await user.click(screen.getByRole('button', { name: 'newSession' }));

    await waitFor(() => {
      expect(addSession).toHaveBeenCalledWith(secondSession);
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-model', 'openai/gpt-4o');
    });
  });

  it('uses the selected agent for the first message when an empty session is created by sending', async () => {
    const user = userEvent.setup();
    useAgents.mockReturnValue({
      agents: [
        {
          name: 'rex',
          description: 'Rex',
          mode: 'primary',
          permission: [],
          options: {},
          skills: [],
          tools: [],
        },
        {
          name: 'explore',
          description: 'Explore',
          mode: 'subagent',
          native: true,
          permission: [],
          options: {},
          skills: [],
          tools: [],
        },
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderSessionPage();

    await user.click(screen.getByRole('button', { name: /Rex/i }));
    await user.click(screen.getByRole('button', { name: /Explore/i }));
    expect(screen.getByRole('button', { name: /Explore/i })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'mock-create-and-send' }));

    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith(
        '/api/session/session-2/prompt_async',
        expect.objectContaining({ agent: 'explore' }),
      );
    });
    expect(screen.getByRole('button', { name: /Explore/i })).toBeInTheDocument();
  });
});
