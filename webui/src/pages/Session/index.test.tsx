import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { __resetChatModelResourcesForTesting } from '@/hooks/useChatModelResources';
import SessionPage from './index';

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

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'user-1', username: 'admin', role: 'admin' } }),
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
  effectiveProjectID: 'default',
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
    client.get.mockResolvedValue({
      data: [{
        id: 'default',
        worktree: '/tmp/project',
        name: '默认',
        isDefault: true,
        pathStatus: 'available',
        sessionCount: 1,
      }],
    });
    hubAPI.catalog.mockResolvedValue({
      data: [{ id: 'soc-workspace', type: 'component', state: 'installed' }],
    });
    hubAPI.install.mockResolvedValue({ data: { id: 'soc-workspace' } });
    hubAPI.installStream.mockResolvedValue(undefined);

    sessionApi.update.mockResolvedValue({ ...session, title: 'Renamed Session' });
    client.patch.mockResolvedValue({ data: { id: 'prj_project2', worktree: '/tmp/labs', name: 'Renamed Project' } });
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

  it('shows default sessions under tasks without a default project row', async () => {
    const user = userEvent.setup();
    renderSessionPage();

    const tasksHeading = await screen.findByText('tasksSection');
    const projectsHeading = screen.getByText('projectsSection');
    const tasksSection = tasksHeading.closest('section');
    const projectsSection = projectsHeading.closest('section');
    expect(tasksSection).not.toBeNull();
    expect(projectsSection).not.toBeNull();
    expect(tasksSection?.parentElement).toBe(projectsSection?.parentElement);
    expect(projectsSection).not.toContainElement(tasksHeading);
    expect(useSessions).toHaveBeenLastCalledWith('', {
      projectIds: ['default'],
      pageSize: 20,
    });
    expect(screen.queryByText('defaultProjectName')).not.toBeInTheDocument();
    expect(screen.getByText('Original Session')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'toggleTasks' }));
    expect(screen.queryByText('Original Session')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'selectTasks' }));
    expect(screen.getByText('Original Session')).toBeInTheDocument();
  });

  it('creates a new session from the tasks row', async () => {
    const user = userEvent.setup();
    renderSessionPage();

    await screen.findByText('tasksSection');
    await user.click(screen.getByRole('button', { name: 'createTaskSession' }));

    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith('/api/session', {
        title: 'New Session',
        projectID: 'default',
      });
    });
  });

  it('collapses the projects section and restores it after remounting', async () => {
    const user = userEvent.setup();
    client.get.mockResolvedValue({
      data: [
        { id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true },
        { id: 'prj_labs', worktree: '/tmp/labs', name: 'Labs', isDefault: false },
      ],
    });
    const firstRender = renderSessionPage();

    await screen.findByText('Labs');
    await user.click(screen.getByRole('button', { name: 'toggleProjects' }));
    expect(screen.queryByText('Labs')).not.toBeInTheDocument();

    firstRender.unmount();
    renderSessionPage();

    await screen.findByText('projectsSection');
    expect(screen.queryByText('Labs')).not.toBeInTheDocument();
  });

  it('restores collapsed projects after the session page remounts', async () => {
    const user = userEvent.setup();
    client.get.mockResolvedValue({
      data: [
        { id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true },
        { id: 'prj_labs', worktree: '/tmp/labs', name: 'Labs', isDefault: false },
      ],
    });
    useSessions.mockReturnValue({
      sessions: [{
        ...session,
        projectID: 'prj_labs',
        effectiveProjectID: 'prj_labs',
        directory: '/tmp/labs',
      }],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });
    const firstRender = renderSessionPage();

    await screen.findByText('Labs');
    await user.click(screen.getByRole('button', { name: 'toggleProject' }));
    expect(screen.queryByText('Original Session')).not.toBeInTheDocument();

    firstRender.unmount();
    renderSessionPage();

    await screen.findByText('Labs');
    expect(screen.queryByText('Original Session')).not.toBeInTheDocument();
  });

  it('uses six sessions per page when multiple projects exist', async () => {
    client.get.mockResolvedValue({
      data: [
        {
          id: 'default',
          worktree: '/tmp/project',
          name: '默认',
          isDefault: true,
          pathStatus: 'available',
          sessionCount: 1,
        },
        {
          id: 'prj_labs',
          worktree: '/tmp/labs',
          name: 'Labs',
          isDefault: false,
          pathStatus: 'available',
          sessionCount: 0,
        },
      ],
    });

    renderSessionPage();

    await screen.findByText('Labs');
    expect(useSessions).toHaveBeenLastCalledWith('', {
      projectIds: ['default', 'prj_labs'],
      pageSize: 6,
    });
  });

  it('toggles project sessions when clicking the selected project row', async () => {
    const user = userEvent.setup();
    client.get.mockResolvedValue({
      data: [
        { id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true },
        { id: 'prj_labs', worktree: '/tmp/labs', name: 'Labs', isDefault: false },
      ],
    });
    useSessions.mockReturnValue({
      sessions: [{
        ...session,
        projectID: 'prj_labs',
        effectiveProjectID: 'prj_labs',
        directory: '/tmp/labs',
      }],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });

    renderSessionPage();

    await screen.findByText('Labs');
    const toggle = screen.getByRole('button', { name: 'toggleProject' });
    const projectRow = screen.getByRole('button', { name: 'selectProject' });

    await user.click(toggle);
    expect(screen.queryByText('Original Session')).not.toBeInTheDocument();

    await user.click(projectRow);
    expect(screen.getByText('Original Session')).toBeInTheDocument();

    await user.click(projectRow);
    expect(screen.queryByText('Original Session')).not.toBeInTheDocument();
  });

  it('groups legacy sessions by the effective project returned by the backend', async () => {
    client.get.mockResolvedValue({
      data: [{
        id: 'default',
        worktree: '/tmp/project',
        name: '默认',
        isDefault: true,
        pathStatus: 'available',
        sessionCount: 2,
      }],
    });
    useSessions.mockReturnValue({
      sessions: [
        session,
        {
          ...secondSession,
          projectID: 'old-project-id',
          effectiveProjectID: 'default',
          directory: '/tmp/project',
          title: 'Legacy Session',
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

    await screen.findByText('tasksSection');
    expect(screen.queryByText('defaultProjectName')).not.toBeInTheDocument();
    expect(screen.getByText('Original Session')).toBeInTheDocument();
    expect(screen.getByText('Legacy Session')).toBeInTheDocument();
  });

  it('creates a user-managed project from the sidebar', async () => {
    const user = userEvent.setup();
    let projectRows = [
      { id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true },
    ];
    client.get.mockImplementation(() => Promise.resolve({ data: projectRows }));
    client.post.mockImplementation((url: string, payload: Record<string, unknown>) => {
      if (url === '/api/project') {
        const created = { id: 'prj_project2', worktree: payload.worktree as string, name: payload.name as string };
        projectRows = [projectRows[0], created];
        return Promise.resolve({ data: created });
      }
      return Promise.resolve({ data: secondSession });
    });

    renderSessionPage();

    await user.click(await screen.findByRole('button', { name: 'projectDialog.createTitle' }));
    const nameInput = screen.getByLabelText('projectDialog.nameLabel');
    await user.clear(nameInput);
    await user.type(nameInput, 'Labs');
    const folderInput = screen.getByLabelText('projectDialog.folderLabel');
    await user.clear(folderInput);
    await user.type(folderInput, '/tmp/labs');
    await user.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith('/api/project', { name: 'Labs', worktree: '/tmp/labs' });
      expect(screen.getByText('Labs')).toBeInTheDocument();
    });
  });

  it('shows the backend detail when project creation fails', async () => {
    const user = userEvent.setup();
    client.post.mockRejectedValue({
      message: 'Request failed with status code 400',
      response: { data: { detail: 'Project directory does not exist' } },
    });

    renderSessionPage();

    await user.click(await screen.findByRole('button', { name: 'projectDialog.createTitle' }));
    const nameInput = screen.getByLabelText('projectDialog.nameLabel');
    await user.clear(nameInput);
    await user.type(nameInput, 'Missing project');
    const folderInput = screen.getByLabelText('projectDialog.folderLabel');
    await user.clear(folderInput);
    await user.type(folderInput, '/tmp/missing-project');
    await user.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        'projectDialog.saveFailed',
        'Project directory does not exist',
      );
    });
  });

  it('submits project creation only once when save is clicked twice quickly', async () => {
    const user = userEvent.setup();
    let resolveCreate: ((value: { data: Record<string, unknown> }) => void) | undefined;
    client.post.mockImplementation(() => new Promise((resolve) => {
      resolveCreate = resolve;
    }));

    renderSessionPage();

    await user.click(await screen.findByRole('button', { name: 'projectDialog.createTitle' }));
    const nameInput = screen.getByLabelText('projectDialog.nameLabel');
    await user.clear(nameInput);
    await user.type(nameInput, 'Labs');
    const folderInput = screen.getByLabelText('projectDialog.folderLabel');
    await user.clear(folderInput);
    await user.type(folderInput, '/tmp/labs');
    const saveButton = screen.getByRole('button', { name: 'save' });

    act(() => {
      saveButton.click();
      saveButton.click();
    });

    expect(client.post).toHaveBeenCalledTimes(1);
    resolveCreate?.({ data: { id: 'prj_project2', name: 'Labs', worktree: '/tmp/labs' } });
    await waitFor(() => expect(screen.queryByRole('button', { name: 'save' })).not.toBeInTheDocument());
  });

  it('keeps a newly created empty project visible while search is active', async () => {
    const user = userEvent.setup();
    const currentProject = { id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true };
    client.get.mockResolvedValue({ data: [currentProject] });
    client.post.mockImplementation((url: string, payload: Record<string, unknown>) => {
      if (url === '/api/project') {
        const created = { id: 'prj_project2', worktree: payload.worktree as string, name: payload.name as string };
        return Promise.resolve({ data: created });
      }
      return Promise.resolve({ data: secondSession });
    });

    renderSessionPage();

    await user.type(screen.getByPlaceholderText('filterConversations'), 'nothing matches');
    await user.click(await screen.findByRole('button', { name: 'projectDialog.createTitle' }));
    const nameInput = screen.getByLabelText('projectDialog.nameLabel');
    await user.clear(nameInput);
    await user.type(nameInput, 'Labs');
    const folderInput = screen.getByLabelText('projectDialog.folderLabel');
    await user.clear(folderInput);
    await user.type(folderInput, '/tmp/labs');
    await user.click(screen.getByRole('button', { name: 'save' }));

    expect(await screen.findByText('Labs')).toBeInTheDocument();
    expect(screen.getByText('noProjectSessions')).toBeInTheDocument();
  });

  it('renames a project from the sidebar', async () => {
    const user = userEvent.setup();
    const defaultProject = { id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true };
    client.get.mockResolvedValue({
      data: [defaultProject, { id: 'prj_project2', worktree: '/tmp/labs', name: 'Labs' }],
    });
    client.patch.mockResolvedValue({
      data: { id: 'prj_project2', worktree: '/tmp/labs', name: 'Renamed Project' },
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
      expect(client.patch).toHaveBeenCalledWith('/api/project/prj_project2', { name: 'Renamed Project' });
    });
  });

  it('does not render project actions for the default task group', async () => {
    renderSessionPage();

    await screen.findByText('tasksSection');
    expect(screen.queryByText('defaultProjectName')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'projectActions' })).not.toBeInTheDocument();
    expect(client.patch).not.toHaveBeenCalled();
  });

  it('deletes an empty user-managed project after confirmation', async () => {
    const user = userEvent.setup();
    const currentProject = { id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true };
    let projectRows = [
      currentProject,
      { id: 'prj_project2', worktree: '/tmp/labs', name: 'Labs' },
    ];
    client.get.mockImplementation(() => Promise.resolve({ data: projectRows }));
    client.delete.mockImplementation(() => {
      projectRows = [currentProject];
      return Promise.resolve({ data: true });
    });

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
    const currentProject = { id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true };
    client.get.mockResolvedValue({
      data: [currentProject, { id: 'prj_project2', worktree: '/tmp/labs', name: 'Labs' }],
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

    await screen.findByText('Original Session');
    await user.click(screen.getByRole('button', { name: 'moreActions' }));

    expect(screen.getByRole('button', { name: 'rename' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'downloadJson' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'deleteAction' })).toBeInTheDocument();
  });

  it('renames a session inline from the actions menu', async () => {
    const user = userEvent.setup();

    renderSessionPage();

    await screen.findByText('Original Session');
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

    await screen.findByText('Original Session');
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

    await screen.findByText('Original Session');
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
