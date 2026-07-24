import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { __resetChatModelResourcesForTesting } from '@/hooks/useChatModelResources';
import { formatRelativeTime } from '@/utils/time';
import SessionPage from './index';

const sessionStatusSSEOptionsRef = vi.hoisted(() => ({
  current: null as null | {
    onEvent: (event: { type: string; properties?: Record<string, unknown> }) => void;
    onReconnect?: () => void;
  },
}));

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
  getApiBase: () => '',
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

vi.mock('@/hooks/useSSE', () => ({
  useSSE: (options: typeof sessionStatusSSEOptionsRef.current) => {
    sessionStatusSSEOptionsRef.current = options;
    return {
      status: 'connected',
      retryCount: 0,
      reconnect: vi.fn(),
      disconnect: vi.fn(),
    };
  },
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
    supportsVision,
    contextWindowTokens,
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
    supportsVision?: boolean;
    contextWindowTokens?: number | null;
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
        data-supports-vision={String(Boolean(supportsVision))}
        data-context-window={contextWindowTokens ?? ''}
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
  formatRelativeTime: vi.fn(() => '17小时前'),
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
    sessionStatusSSEOptionsRef.current = null;
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

  it('keeps the workbench visible and shows a page refresh state while sessions load', () => {
    useSessions.mockReturnValue({
      sessions: [],
      loading: true,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });

    renderSessionPage();

    expect(screen.getByLabelText('managementTitle')).toBeInTheDocument();
    expect(screen.getByTestId('workbench-refresh-status')).toHaveTextContent('refreshingWorkbench');
    expect(screen.getByTestId('session-list-skeleton')).toBeInTheDocument();
    expect(screen.getByTestId('session-chat')).toHaveTextContent('no-session');
    expect(screen.queryByText('loading-spinner')).not.toBeInTheDocument();
    expect(screen.getByTestId('session-list-scroll')).toHaveClass(
      'session-sidebar-scrollbar',
      'overflow-y-auto',
    );
    expect(screen.getByTestId('session-list-scroll')).not.toHaveClass('scrollbar-hide');
  });

  it('shows default sessions under tasks without a default project row', async () => {
    const user = userEvent.setup();
    renderSessionPage();

    const tasksHeading = await screen.findByText('tasksSection');
    const projectsHeading = screen.getByText('projectsSection');
    const tasksSection = tasksHeading.closest('section');
    const projectsSection = projectsHeading.closest('section');
    const newSessionButton = screen.getByRole('button', { name: 'newSession' });
    const searchInput = screen.getByPlaceholderText('filterConversations');
    expect(newSessionButton.previousElementSibling).toHaveClass('h-3.5', 'w-3.5');
    expect(searchInput.previousElementSibling).toHaveClass('h-3.5', 'w-3.5');
    expect(searchInput).toHaveClass('text-sm', 'font-medium');
    expect(tasksHeading.closest('div')).toHaveClass('text-xs', 'text-zinc-500');
    expect(projectsHeading.closest('div')).toHaveClass('text-xs', 'text-zinc-500');
    expect(tasksSection).not.toBeNull();
    expect(projectsSection).not.toBeNull();
    expect(tasksSection?.parentElement).toBe(projectsSection?.parentElement);
    expect(projectsSection).not.toContainElement(tasksHeading);
    expect(useSessions).toHaveBeenLastCalledWith('', {
      projectIds: ['tasks'],
      pageSize: 20,
    });
    expect(screen.queryByText('defaultProjectName')).not.toBeInTheDocument();
    expect(screen.getByText('Original Session').closest('h3')).toHaveClass('text-sm', 'font-medium');

    await user.click(screen.getByRole('button', { name: 'toggleTasks' }));
    expect(screen.queryByText('Original Session')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'selectTasks' }));
    expect(screen.getByText('Original Session')).toBeInTheDocument();
  });

  it('keeps the workbench canvas, sidebar, selected row, and dark palette classes stable', async () => {
    renderSessionPage('/sessions?session=session-1');

    const workbenchSidebar = screen.getByLabelText('managementTitle');
    const workbenchCanvas = workbenchSidebar.parentElement;
    const mainCanvas = workbenchSidebar.nextElementSibling;
    const sessionTitle = await within(workbenchSidebar).findByText('Original Session');
    const selectedRow = sessionTitle.closest('div.group');

    expect(workbenchCanvas).toHaveClass('bg-gray-50', 'dark:bg-[#252c35]');
    expect(workbenchSidebar).toHaveClass('bg-white', 'dark:bg-[#303842]');
    expect(mainCanvas).toHaveClass('bg-gray-50', 'dark:bg-[#252c35]');
    await waitFor(() => {
      expect(selectedRow).toHaveClass('bg-zinc-200/70', 'dark:bg-[#3a434e]');
    });
  });

  it('shows and clears the sidebar running state from recovered and live session status', async () => {
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
    client.get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/api/session/status'
        ? {
            [session.id]: { type: 'busy' },
            [secondSession.id]: { type: 'busy' },
          }
        : [{
            id: 'default',
            worktree: '/tmp/project',
            name: '默认',
            isDefault: true,
            pathStatus: 'available',
            sessionCount: 2,
          }],
    }));

    renderSessionPage();

    const runningStatuses = await screen.findAllByRole('status', { name: 'chat.tool.running' });
    expect(runningStatuses.map((status) => status.getAttribute('data-session-running')))
      .toEqual(expect.arrayContaining([session.id, secondSession.id]));

    act(() => {
      sessionStatusSSEOptionsRef.current?.onEvent({
        type: 'session.status',
        properties: {
          sessionID: session.id,
          status: { type: 'idle' },
        },
      });
    });

    expect(screen.getByRole('status', { name: 'chat.tool.running' }))
      .toHaveAttribute('data-session-running', secondSession.id);

    act(() => {
      sessionStatusSSEOptionsRef.current?.onEvent({
        type: 'session.status',
        properties: {
          sessionID: session.id,
          status: { type: 'retry' },
        },
      });
    });

    expect(screen.getAllByRole('status', { name: 'chat.tool.running' }))
      .toHaveLength(2);
  });

  it('shows load more as text without an idle arrow', async () => {
    useSessions.mockReturnValue({
      sessions: [session],
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
      hasMoreByProject: { tasks: true },
      loadingMoreProjectIds: new Set(),
      loadMore: vi.fn(),
    });

    renderSessionPage();

    const loadMoreButton = await screen.findByRole('button', { name: 'loadMore' });
    expect(loadMoreButton.querySelector('svg')).toBeNull();
  });

  it('collapses loaded task pages and reopens cached tasks without another request', async () => {
    const user = userEvent.setup();
    const loadMore = vi.fn();
    const loadedTasks = Array.from({ length: 8 }, (_, index) => ({
      ...session,
      id: `task-${index + 1}`,
      slug: `task-${index + 1}`,
      title: `Task ${index + 1}`,
    }));
    client.get.mockResolvedValue({
      data: [
        {
          id: 'default',
          worktree: '/tmp/project',
          name: '默认',
          isDefault: true,
          pathStatus: 'available',
          sessionCount: 8,
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
    useSessions.mockReturnValue({
      sessions: loadedTasks,
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
      hasMoreByProject: { tasks: false },
      loadingMoreProjectIds: new Set(),
      loadMore,
    });

    renderSessionPage();

    expect(await screen.findByText('Task 8')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'collapseLoaded' }));

    expect(screen.queryByText('Task 7')).not.toBeInTheDocument();
    expect(screen.queryByText('Task 8')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'loadMore' }));

    expect(screen.getByText('Task 7')).toBeInTheDocument();
    expect(screen.getByText('Task 8')).toBeInTheDocument();
    expect(loadMore).not.toHaveBeenCalled();
  });

  it('creates a new session from the tasks row', async () => {
    const user = userEvent.setup();
    renderSessionPage();

    await screen.findByText('tasksSection');
    await user.click(screen.getByRole('button', { name: 'createTaskSession' }));

    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith('/api/session', {
        title: 'New Session',
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
    expect(screen.getByRole('button', { name: 'selectProject' }).querySelector('svg')).toHaveClass('h-3.5', 'w-3.5');
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
    await user.click(screen.getByRole('button', { name: 'selectProject' }));
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
      projectIds: ['tasks', 'prj_labs'],
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
    const projectRow = screen.getByRole('button', { name: 'selectProject' });
    expect(screen.queryByRole('button', { name: 'toggleProject' })).not.toBeInTheDocument();
    expect(projectRow).toHaveAttribute('aria-expanded', 'true');

    await user.click(projectRow);
    expect(screen.queryByText('Original Session')).not.toBeInTheDocument();
    expect(projectRow).toHaveAttribute('aria-expanded', 'false');

    await user.click(projectRow);
    expect(screen.getByText('Original Session')).toBeInTheDocument();
    expect(projectRow).toHaveAttribute('aria-expanded', 'true');

    await user.click(projectRow);
    expect(screen.queryByText('Original Session')).not.toBeInTheDocument();
  });

  it('renders project sessions with the compact conversation row treatment', async () => {
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

    const sessionTitle = await screen.findByText('Original Session');
    const sessionCard = sessionTitle.closest('[class*="cursor-pointer"]');
    expect(sessionCard).not.toBeNull();
    expect(sessionCard).toHaveClass('min-h-[34px]', 'rounded-lg', 'border-transparent');
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
      expect(within(screen.getByLabelText('managementTitle')).getByText('Labs')).toBeInTheDocument();
    });
  });

  it('uses the current browser path when saving without selecting the folder', async () => {
    const user = userEvent.setup();
    const defaultProject = {
      id: 'default',
      worktree: '/tmp/project',
      name: '默认',
      isDefault: true,
    };
    client.get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/api/project/folders'
        ? {
            path: '/home/test-user',
            parent: null,
            roots: [],
            entries: [],
          }
        : [defaultProject],
    }));
    client.post.mockResolvedValue({
      data: { id: 'prj_home', name: 'test-user', worktree: '/home/test-user' },
    });

    renderSessionPage();

    await user.click(await screen.findByRole('button', { name: 'projectDialog.createTitle' }));
    expect(screen.getByLabelText('projectDialog.nameLabel')).toHaveValue('');
    expect(screen.getByLabelText('projectDialog.folderLabel')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'cancel' })).toBeEnabled();

    await user.click(screen.getByRole('button', { name: 'projectDialog.chooseFolder' }));

    expect(await screen.findByText('/home/test-user')).toBeInTheDocument();
    expect(client.get).toHaveBeenCalledWith('/api/project/folders', {
      params: { path: undefined },
    });
    expect(screen.getByLabelText('projectDialog.folderLabel')).toHaveValue('/home/test-user');

    await user.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith('/api/project', {
        name: 'test-user',
        worktree: '/home/test-user',
      });
    });
  });

  it('keeps the folder input and folder browser in sync', async () => {
    const user = userEvent.setup();
    client.get.mockImplementation((url: string, config?: { params?: { path?: string } }) => {
      if (url !== '/api/project/folders') {
        return Promise.resolve({
          data: [{ id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true }],
        });
      }
      const path = config?.params?.path;
      if (path === '/home/test-user/labs') {
        return Promise.resolve({
          data: { path, parent: '/home/test-user', roots: [], entries: [] },
        });
      }
      return Promise.resolve({
        data: {
          path: '/home/test-user',
          parent: null,
          roots: [],
          entries: [{ name: 'labs', path: '/home/test-user/labs' }],
        },
      });
    });

    renderSessionPage();

    await user.click(await screen.findByRole('button', { name: 'projectDialog.createTitle' }));
    const folderInput = screen.getByLabelText('projectDialog.folderLabel');
    await user.click(screen.getByRole('button', { name: 'projectDialog.chooseFolder' }));
    await waitFor(() => expect(folderInput).toHaveValue('/home/test-user'));

    await user.type(folderInput, '/');
    await waitFor(() => {
      expect(client.get).toHaveBeenCalledWith('/api/project/folders', {
        params: { path: '/home/test-user/' },
      });
    });
    expect(folderInput).toHaveValue('/home/test-user/');

    await user.clear(folderInput);
    await user.type(folderInput, '/home/test-user/labs');
    await waitFor(() => {
      expect(client.get).toHaveBeenCalledWith('/api/project/folders', {
        params: { path: '/home/test-user/labs' },
      });
      expect(screen.getByText('/home/test-user/labs')).toBeInTheDocument();
    });
  });

  it('does not submit when Enter is pressed before choosing a project folder', async () => {
    const user = userEvent.setup();
    client.post.mockResolvedValue({
      data: { id: 'prj_labs', name: 'Labs', worktree: '/tmp/labs' },
    });
    renderSessionPage();

    await user.click(await screen.findByRole('button', { name: 'projectDialog.createTitle' }));
    const nameInput = screen.getByLabelText('projectDialog.nameLabel');
    await user.type(nameInput, 'Labs');
    fireEvent.keyDown(nameInput, { key: 'Enter' });

    expect(client.post).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();

    const folderInput = screen.getByLabelText('projectDialog.folderLabel');
    await user.type(folderInput, '/tmp/labs');
    fireEvent.keyDown(nameInput, { key: 'Enter', isComposing: true });
    expect(client.post).not.toHaveBeenCalled();

    fireEvent.keyDown(nameInput, { key: 'Enter' });
    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith('/api/project', {
        name: 'Labs',
        worktree: '/tmp/labs',
      });
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

    expect(await within(screen.getByLabelText('managementTitle')).findByText('Labs')).toBeInTheDocument();
    expect(screen.getByText('noProjectSessions')).toBeInTheDocument();
  });

  it('renames a project from the sidebar', async () => {
    const user = userEvent.setup();
    const defaultProject = { id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true };
    const projectList = [defaultProject, {
      id: 'prj_project2',
      worktree: '/tmp/labs',
      name: 'Labs',
      sessionCount: 8,
      lastActivityAt: 10_000,
    }];
    client.get
      .mockResolvedValueOnce({ data: projectList })
      .mockResolvedValue({
        data: projectList.map((project) => (
          project.id === 'prj_project2' ? { ...project, name: 'Renamed Project' } : project
        )),
      });
    client.patch.mockResolvedValue({
      data: { id: 'prj_project2', worktree: '/tmp/labs', name: 'Renamed Project' },
    });

    renderSessionPage();

    const projectLabel = await screen.findByText('Labs');
    const projectRow = projectLabel.closest('[class*="group/project"]');
    expect(projectRow).not.toBeNull();
    expect(projectRow?.firstElementChild).toHaveClass('text-sm');
    await user.click(within(projectRow as HTMLElement).getByRole('button', { name: 'projectActions' }));
    await user.click(within(projectRow as HTMLElement).getByRole('menuitem', { name: 'projectDialog.renameAction' }));
    const input = screen.getByLabelText('projectDialog.nameLabel');
    await user.clear(input);
    await user.type(input, 'Renamed Project');
    await user.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => {
      expect(client.patch).toHaveBeenCalledWith('/api/project/prj_project2', { name: 'Renamed Project' });
    });
    const renamedProject = await screen.findByText('Renamed Project');
    expect(renamedProject.closest('[class*="group/project"]')).toHaveTextContent('8');
  });

  it('shares and unshares a project from the sidebar', async () => {
    const user = userEvent.setup();
    const defaultProject = { id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true };
    let projectRows = [
      defaultProject,
      {
        id: 'prj_project2', worktree: '/tmp/labs', name: 'Labs',
        canWrite: true, canDelete: true, isShared: false,
      },
    ];
    client.get.mockImplementation(() => Promise.resolve({ data: projectRows }));
    client.post.mockImplementation((url: string) => {
      if (url === '/api/project/prj_project2/share-local') {
        projectRows = projectRows.map((project) => (
          project.id === 'prj_project2' ? { ...project, isShared: true } : project
        ));
      }
      if (url === '/api/project/prj_project2/unshare-local') {
        projectRows = projectRows.map((project) => (
          project.id === 'prj_project2' ? { ...project, isShared: false } : project
        ));
      }
      return Promise.resolve({ data: true });
    });

    renderSessionPage();

    let projectRow = (await screen.findByText('Labs')).closest('[class*="group/project"]');
    expect(projectRow).not.toBeNull();
    await user.click(within(projectRow as HTMLElement).getByRole('button', { name: 'projectActions' }));
    await user.click(within(projectRow as HTMLElement).getByRole('menuitem', { name: 'shareAction' }));
    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith('/api/project/prj_project2/share-local');
      expect(screen.getByText('sharedTag')).toBeInTheDocument();
    });

    projectRow = screen.getByText('Labs').closest('[class*="group/project"]');
    await user.click(within(projectRow as HTMLElement).getByRole('button', { name: 'projectActions' }));
    await user.click(within(projectRow as HTMLElement).getByRole('menuitem', { name: 'unshareAction' }));
    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith('/api/project/prj_project2/unshare-local');
      expect(screen.queryByText('sharedTag')).not.toBeInTheDocument();
    });
  });

  it('keeps a shared project read-only for non-owners', async () => {
    const user = userEvent.setup();
    client.get.mockResolvedValue({
      data: [
        { id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true },
        {
          id: 'prj_shared', worktree: '/tmp/shared', name: 'Shared Labs',
          canWrite: false, canDelete: false, isShared: true,
        },
      ],
    });

    renderSessionPage();

    const projectRow = (await screen.findByText('Shared Labs')).closest('[class*="group/project"]');
    expect(projectRow).not.toBeNull();
    expect(within(projectRow as HTMLElement).getByRole('button', { name: 'createSessionInProject' })).toBeDisabled();
    await user.click(within(projectRow as HTMLElement).getByRole('button', { name: 'projectActions' }));
    expect(within(projectRow as HTMLElement).getByRole('menuitem', { name: 'projectDialog.copyPathAction' })).toBeInTheDocument();
    expect(within(projectRow as HTMLElement).queryByRole('menuitem', { name: 'shareAction' })).not.toBeInTheDocument();
    expect(within(projectRow as HTMLElement).queryByRole('menuitem', { name: 'unshareAction' })).not.toBeInTheDocument();
    expect(within(projectRow as HTMLElement).queryByRole('menuitem', { name: 'projectDialog.renameAction' })).not.toBeInTheDocument();
    expect(within(projectRow as HTMLElement).queryByRole('menuitem', { name: 'projectDialog.deleteAction' })).not.toBeInTheDocument();
  });

  it('ignores stale project results after the search changes', async () => {
    const initialProject = { id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true };
    const olderSearch = deferred<{ data: Array<Record<string, unknown>> }>();
    const latestSearch = deferred<{ data: Array<Record<string, unknown>> }>();
    client.get.mockImplementation((_url: string, config?: { params?: { search?: string } }) => {
      const query = config?.params?.search;
      if (query === 'a') return olderSearch.promise;
      if (query === 'ab') return latestSearch.promise;
      return Promise.resolve({ data: [initialProject] });
    });

    renderSessionPage();
    await screen.findByText('tasksSection');
    const searchInput = screen.getByPlaceholderText('filterConversations');
    fireEvent.change(searchInput, { target: { value: 'a' } });
    fireEvent.change(searchInput, { target: { value: 'ab' } });

    await act(async () => {
      latestSearch.resolve({
        data: [initialProject, {
          id: 'prj_latest',
          worktree: '/tmp/latest',
          name: 'Latest result',
          isDefault: false,
          matchedSessionCount: 1,
        }],
      });
      await latestSearch.promise;
    });
    expect(await screen.findByText('Latest result')).toBeInTheDocument();

    await act(async () => {
      olderSearch.resolve({
        data: [initialProject, {
          id: 'prj_stale',
          worktree: '/tmp/stale',
          name: 'Stale result',
          isDefault: false,
          matchedSessionCount: 1,
        }],
      });
      await olderSearch.promise;
    });
    expect(screen.getByText('Latest result')).toBeInTheDocument();
    expect(screen.queryByText('Stale result')).not.toBeInTheDocument();
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

    const menu = document.querySelector('[data-session-menu-portal]');
    expect(menu).toHaveClass('w-[132px]', 'rounded-[10px]', 'p-1');
    expect(menu).not.toHaveClass('w-36', 'rounded-lg');
    expect(screen.getByRole('button', { name: 'rename' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'downloadJson' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'deleteAction' })).toBeInTheDocument();
  });

  it('shows a compact relative session timestamp and keeps the actions trigger background-free', async () => {
    const user = userEvent.setup();

    renderSessionPage();

    const timestamp = await screen.findByText('17小时前');
    const actionsTrigger = screen.getByRole('button', { name: 'moreActions' });

    expect(timestamp).not.toHaveClass('group-hover:opacity-0');
    expect(timestamp).toHaveClass('text-zinc-500');
    expect(timestamp).toHaveAttribute('title', 'formatted-date');
    expect(actionsTrigger).not.toHaveClass('hover:bg-white/80');

    await user.click(actionsTrigger);

    expect(actionsTrigger).not.toHaveClass('bg-white/80');
  });

  it('refreshes relative session timestamps every minute', () => {
    vi.useFakeTimers();
    let relativeTimeLabel = '17小时前';
    vi.mocked(formatRelativeTime).mockImplementation(() => relativeTimeLabel);
    try {
      renderSessionPage();
      expect(screen.getByText('17小时前')).toBeInTheDocument();

      relativeTimeLabel = '18小时前';
      act(() => {
        vi.advanceTimersByTime(60_000);
      });

      expect(screen.getByText('18小时前')).toBeInTheDocument();
    } finally {
      vi.mocked(formatRelativeTime).mockImplementation(() => '17小时前');
      vi.useRealTimers();
    }
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
    expect(screen.getByTestId('session-chat-skeleton')).toBeInTheDocument();
    expect(screen.getByTestId('workbench-refresh-status')).toHaveTextContent('restoringTask');
    expect(screen.queryByText('loading-spinner')).not.toBeInTheDocument();

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

  it('opens a blank new-session draft without creating and resets the agent to Rex', async () => {
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

    renderSessionPage('/sessions?session=session-1');

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveTextContent('session-1');
    });
    await user.click(screen.getByRole('button', { name: /Rex/i }));
    await user.click(screen.getByRole('button', { name: /Explore/i }));
    expect(screen.getByRole('button', { name: /Explore/i })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'newSession' }));

    expect(screen.getByTestId('session-chat')).toHaveTextContent('no-session');
    expect(client.post).not.toHaveBeenCalledWith('/api/session', expect.anything());
    expect(screen.getByRole('button', { name: 'projectPicker.title' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menu', { name: 'projectPicker.title' })).not.toBeInTheDocument();
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
    expect(defaultModelAPI.getResolved).toHaveBeenCalledTimes(1);
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
        model_auto: false,
      });
    });
    expect(refetchSessions).toHaveBeenCalled();
  });

  it('switches a pinned session to Auto without sending a synthetic model', async () => {
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
      connectedIds: ['openai', 'minimax'],
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
    await user.click(screen.getByRole('button', { name: /MiniMax M3/i }));
    const autoButton = await screen.findByRole('button', { name: 'modelPicker.auto' });
    await user.click(autoButton);

    await waitFor(() => {
      expect(sessionApi.update).toHaveBeenCalledWith('session-1', {
        model_auto: true,
        model_pinned: false,
      });
    });
    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-model', '');
  });

  it('does not offer Auto for a non-WebUI task session', async () => {
    const user = userEvent.setup();
    useSessions.mockReturnValue({
      sessions: [{ ...session, category: 'task' }],
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
      connectedIds: ['openai', 'minimax'],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    defaultModelAPI.getResolved.mockResolvedValue({ data: { provider_id: 'openai', model_id: 'gpt-4o' } });
    modelV2API.listDefinitions.mockResolvedValue({ data: { models: modelDefinitions } });

    renderSessionPage('/sessions?session=session-1');

    await user.click(await screen.findByRole('button', { name: /GPT-4o/i }));
    const autoButton = await screen.findByRole('button', { name: 'modelPicker.auto' });
    expect(autoButton).toBeDisabled();
    expect(sessionApi.update).not.toHaveBeenCalled();
  });

  it('offers Auto for an entity configuration WebUI session', async () => {
    const user = userEvent.setup();
    useSessions.mockReturnValue({
      sessions: [{ ...session, category: 'entity-config' }],
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
      connectedIds: ['openai', 'minimax'],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    defaultModelAPI.getResolved.mockResolvedValue({ data: { provider_id: 'openai', model_id: 'gpt-4o' } });
    modelV2API.listDefinitions.mockResolvedValue({ data: { models: modelDefinitions } });

    renderSessionPage('/sessions?session=session-1');

    await user.click(await screen.findByRole('button', { name: /GPT-4o/i }));
    const autoButton = await screen.findByRole('button', { name: 'modelPicker.auto' });
    expect(autoButton).toBeEnabled();
    await user.click(autoButton);
    expect(sessionApi.update).toHaveBeenCalledWith('session-1', {
      model_auto: true,
      model_pinned: false,
    });
  });

  it('does not offer Auto without a valid primary model', async () => {
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
      connectedIds: ['openai', 'minimax'],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    defaultModelAPI.getResolved.mockResolvedValue({ data: { provider_id: '', model_id: '' } });
    modelV2API.listDefinitions.mockResolvedValue({ data: { models: modelDefinitions } });

    renderSessionPage('/sessions?session=session-1');

    await user.click(await screen.findByRole('button', { name: /GPT-4o/i }));
    const autoButton = await screen.findByRole('button', { name: 'modelPicker.auto' });
    expect(autoButton).toBeDisabled();
    expect(sessionApi.update).not.toHaveBeenCalled();
  });

  it('keeps Auto available for a historical user session without a category field', async () => {
    const user = userEvent.setup();
    useSessions.mockReturnValue({
      sessions: [{ ...session, category: undefined }],
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
      connectedIds: ['openai', 'minimax'],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    defaultModelAPI.getResolved.mockResolvedValue({ data: { provider_id: 'openai', model_id: 'gpt-4o' } });
    modelV2API.listDefinitions.mockResolvedValue({ data: { models: modelDefinitions } });

    renderSessionPage('/sessions?session=session-1');

    await user.click(await screen.findByRole('button', { name: /GPT-4o/i }));
    expect(await screen.findByRole('button', { name: 'modelPicker.auto' })).toBeEnabled();
  });

  it('uses primary model capabilities while Auto is selected', async () => {
    useSessions.mockReturnValue({
      sessions: [{ ...session, model_auto: true, model_pinned: false }],
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
      connectedIds: ['openai', 'minimax'],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    defaultModelAPI.getResolved.mockResolvedValue({ data: { provider_id: 'openai', model_id: 'gpt-4o' } });
    modelV2API.listDefinitions.mockResolvedValue({
      data: {
        models: modelDefinitions.map((definition) => definition.id === 'gpt-4o'
          ? {
              ...definition,
              capabilities: { supports_vision: true },
              limits: { context_window: 200000 },
            }
          : {
              ...definition,
              capabilities: { supports_vision: false },
              limits: { context_window: 32000 },
            }),
      },
    });

    renderSessionPage('/sessions?session=session-1');

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-supports-vision', 'true');
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-context-window', '200000');
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-model', '');
    });
  });

  it('turns Auto off when a concrete model is selected', async () => {
    const user = userEvent.setup();
    useSessions.mockReturnValue({
      sessions: [{ ...session, model_auto: true, model_pinned: false }],
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
      connectedIds: ['openai', 'minimax'],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    defaultModelAPI.getResolved.mockResolvedValue({ data: { provider_id: 'openai', model_id: 'gpt-4o' } });
    modelV2API.listDefinitions.mockResolvedValue({ data: { models: modelDefinitions } });

    renderSessionPage('/sessions?session=session-1');

    await user.click(await screen.findByRole('button', { name: /^modelPicker\.auto/i }));
    await user.click(screen.getByRole('button', { name: /MiniMax M3/i }));

    await waitFor(() => {
      expect(sessionApi.update).toHaveBeenCalledWith('session-1', {
        provider: 'minimax',
        model: 'minimax-m3',
        model_pinned: true,
        model_auto: false,
      });
    });
  });

  it('keeps an existing Auto session selected with a valid primary model', async () => {
    useSessions.mockReturnValue({
      sessions: [{ ...session, model_auto: true, model_pinned: false }],
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
      connectedIds: ['openai', 'minimax'],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    defaultModelAPI.getResolved.mockResolvedValue({ data: { provider_id: 'openai', model_id: 'gpt-4o' } });
    modelV2API.listDefinitions.mockResolvedValue({ data: { models: modelDefinitions } });

    renderSessionPage('/sessions?session=session-1');

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /^modelPicker\.auto/i })).toBeInTheDocument();
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-model', '');
    });
  });

  it('creates a blank-session Auto chat without a model override', async () => {
    const user = userEvent.setup();
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
    useProviders.mockReturnValue({
      providers: modelProviders,
      connectedIds: ['openai', 'minimax'],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    defaultModelAPI.getResolved.mockResolvedValue({ data: { provider_id: 'openai', model_id: 'gpt-4o' } });
    modelV2API.listDefinitions.mockResolvedValue({ data: { models: modelDefinitions } });

    renderSessionPage();

    await user.click(await screen.findByRole('button', { name: /GPT-4o/i }));
    await user.click(await screen.findByRole('button', { name: 'modelPicker.auto' }));
    await user.click(screen.getByRole('button', { name: 'mock-create-and-send' }));

    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith('/api/session', {
        title: 'New Session',
        model_auto: true,
      });
      expect(client.post).toHaveBeenCalledWith(
        '/api/session/session-2/prompt_async',
        expect.not.objectContaining({ model: expect.anything() }),
      );
    });
  });

  it('resets the selected model to the default when starting a new session', async () => {
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
      expect(screen.getByTestId('session-chat')).toHaveTextContent('no-session');
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-model', 'openai/gpt-4o');
    });
    expect(client.post).not.toHaveBeenCalledWith('/api/session', expect.anything());
  });

  it('lets the user choose a project before the first message creates the session', async () => {
    const user = userEvent.setup();
    client.get.mockResolvedValue({
      data: [
        { id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true },
        { id: 'prj_labs', worktree: '/tmp/labs', name: 'Labs', canWrite: true },
      ],
    });

    renderSessionPage('/sessions?session=session-1');

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveTextContent('session-1');
    });
    await user.click(screen.getByRole('button', { name: 'newSession' }));

    expect(client.post).not.toHaveBeenCalledWith('/api/session', expect.anything());
    await user.click(screen.getByRole('button', { name: 'projectPicker.title' }));
    await user.click(screen.getByRole('menuitemradio', { name: 'Labs' }));
    await user.click(screen.getByRole('button', { name: 'mock-create-and-send' }));

    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith('/api/session', {
        title: 'New Session',
        projectID: 'prj_labs',
      });
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
