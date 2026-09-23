import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import KeepAlivePanes from '@/components/layout/KeepAlivePanes';
import { findActiveTabHref } from '@/utils/layoutTabs';
import { __resetChatModelResourcesForTesting } from '@/hooks/useChatModelResources';
import { formatRelativeTime } from '@/utils/time';
import SessionPage from './index';
import type { SessionContextFile, SessionContextSnapshot } from '@/api/session';
import type { SessionContextPanelProps } from './SessionContextPanel';
import type { SSEChatEvent } from '@/features/session-chat/sseRouting';

const sessionChatSSERef = vi.hoisted(() => ({ current: undefined as ((event: SSEChatEvent) => void) | undefined }));
const contextPanelPropsRef = vi.hoisted(() => ({ current: null as SessionContextPanelProps | null }));
vi.mock('./SessionContextPanel', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./SessionContextPanel')>();
  return {
    default: (props: SessionContextPanelProps) => {
      contextPanelPropsRef.current = props;
      return <actual.default {...props} />;
    },
  };
});
vi.mock('@/components/common/FilePreview', () => ({
  FilePreviewRenderer: ({ node, content }: any) => <div data-testid="file-preview">{node.name}:{content}</div>,
  PreviewModal: () => null,
}));

const initialActionProbe = vi.hoisted(() => ({ enabled: false, delivered: vi.fn() }));

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
  workflowAPI,
  skillAPI,
  toast,
} = vi.hoisted(() => ({
  client: {
    delete: vi.fn(),
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
  },
  sessionApi: {
    archive: vi.fn(),
    delete: vi.fn(),
    get: vi.fn(),
    getContext: vi.fn(),
    getContextFile: vi.fn(),
    getMessages: vi.fn(),
    moveToProject: vi.fn(),
    readContextFile: vi.fn(),
    contextFilePreviewUrl: vi.fn((sessionId: string, resourceId: string) => `/api/session/${sessionId}/context/files/${resourceId}/preview`),
    contextFileDownloadUrl: vi.fn((sessionId: string, resourceId: string) => `/api/session/${sessionId}/context/files/${resourceId}/download`),
    listContextRoot: vi.fn(),
    readContextRootFile: vi.fn(),
    contextRootPreviewUrl: vi.fn(),
    contextRootDownloadUrl: vi.fn(),
    addContextFolder: vi.fn(),
    removeContextFolder: vi.fn(),
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
  workflowAPI: {
    listSummaries: vi.fn(),
  },
  skillAPI: {
    status: vi.fn(),
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

vi.mock('@/api/workflow', () => ({
  workflowAPI,
}));

vi.mock('@/api/skill', () => ({
  skillAPI,
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
    composerAddMenuSlot,
    onComposerAddMenuOpenChange,
    centerToolbarSlot,
    welcomeContent,
    initialMessage,
    onInitialMessageConsumed,
    initialDisplayText,
    initialOptimisticMessage,
    focusMessageId,
    onCreateAndSend,
    onSSEEvent,
    onOpenContextFile,
    onSseStatusChange,
    agentName,
    model,
    executionMode,
    onExecutionModeAccepted,
    supportsVision,
    contextWindowTokens,
    display,
    hideInput,
  }: {
    onInitialMessageConsumed?: () => void;
    sessionId?: string | null;
    agentName?: string;
    mentionAgents?: Array<{ name: string }>;
    toolbarSlot?: React.ReactNode;
    composerAddMenuSlot?: React.ReactNode | ((actions: {
      closeMenu: () => void;
      insertMention: (agentName: string) => void;
      insertReference: (value: string, kind: 'workflow' | 'skill') => void;
    }) => React.ReactNode);
    onComposerAddMenuOpenChange?: (open: boolean) => void;
    centerToolbarSlot?: React.ReactNode;
    welcomeContent?: React.ReactNode | ((setInput: (text: string) => void) => React.ReactNode);
    initialMessage?: string | null;
    initialDisplayText?: string | null;
    initialOptimisticMessage?: {
      id: string;
      sessionID: string;
      parts: Array<{
        type: string;
        text?: string;
        metadata?: { displayText?: string };
      }>;
    } | null;
    focusMessageId?: string | null;
    model?: { providerID: string; modelID: string } | null;
    executionMode?: 'build' | 'plan' | 'goal';
    onExecutionModeAccepted?: (mode: 'build' | 'plan' | 'goal') => void;
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
      executionModeOverride?: 'build' | 'plan' | 'goal',
    ) => Promise<unknown> | unknown;
    onSSEEvent?: (event: { type: string; properties?: Record<string, unknown> }) => void;
    onOpenContextFile?: (resourceID: string) => void;
    onSseStatusChange?: (status: 'connected' | 'disconnected') => void;
  }) {
    sessionChatSSERef.current = onSSEEvent;
    const [input, setInput] = React.useState('');
    const delivered = React.useRef(false);
    React.useEffect(() => {
      if (!initialActionProbe.enabled || !initialMessage || delivered.current) return;
      delivered.current = true;
      initialActionProbe.delivered(sessionId, initialMessage);
      onInitialMessageConsumed?.();
    }, [sessionId, initialMessage, onInitialMessageConsumed]);
    return (
      <div
        data-testid="session-chat"
        data-agent-name={agentName ?? ''}
        data-mention-agents={(mentionAgents ?? []).map((a) => a.name).join(',')}
        data-model={model ? `${model.providerID}/${model.modelID}` : ''}
        data-execution-mode={executionMode ?? ''}
        data-supports-vision={String(Boolean(supportsVision))}
        data-context-window={contextWindowTokens ?? ''}
        data-collapse-intermediate={String(Boolean(display?.collapseIntermediateSteps))}
        data-process-groups-default-open={String(Boolean(display?.processGroupsDefaultOpen))}
        data-process-groups-open-while-active={String(Boolean(display?.processGroupsOpenWhileActive))}
        data-hide-input={String(Boolean(hideInput))}
        data-initial-message={initialMessage ?? ''}
        data-initial-display={initialDisplayText ?? ''}
        data-optimistic-id={initialOptimisticMessage?.id ?? ''}
        data-optimistic-text={initialOptimisticMessage?.parts.find((part) => part.type === 'text')?.text ?? ''}
        data-optimistic-display={initialOptimisticMessage?.parts.find((part) => part.type === 'text')?.metadata?.displayText ?? ''}
        data-focus-message={focusMessageId ?? ''}
      >
        {sessionId ?? 'no-session'}
        <button type="button" onClick={() => onOpenContextFile?.('requested-file')}>mock-open-context-file</button>
        <button type="button" onClick={() => onSSEEvent?.({ type: 'session.context.updated', properties: { sessionID: sessionId } })}>mock-context-updated</button>
        <button type="button" onClick={() => onSSEEvent?.({ type: 'session.context.updated', properties: { sessionID: 'unrelated-session' } })}>mock-other-context-updated</button>
        <button type="button" onClick={() => onSseStatusChange?.('connected')}>mock-connected</button>
        <button type="button" onClick={() => onSseStatusChange?.('disconnected')}>mock-disconnected</button>
        <button type="button" onClick={() => onComposerAddMenuOpenChange?.(true)}>
          mock-open-add-menu
        </button>
        {typeof composerAddMenuSlot === 'function'
          ? composerAddMenuSlot({
            closeMenu: vi.fn(),
            insertMention: (agentName) => setInput(`subagent:${agentName} `),
            insertReference: (value, kind) => {
              setInput(`${kind}:${value} `);
            },
          })
          : composerAddMenuSlot}
        {toolbarSlot}
        {centerToolbarSlot}
        {!sessionId && welcomeContent ? (
          typeof welcomeContent === 'function' ? welcomeContent(setInput) : welcomeContent
        ) : null}
        <div data-testid="mock-chat-input">{input}</div>
        <button
          type="button"
          onClick={() => {
            void Promise.resolve(onCreateAndSend?.(
              'hello from empty session',
              [],
              agentName,
              undefined,
              undefined,
              executionMode,
            )).catch(() => {});
          }}
        >
          mock-create-and-send
        </button>
        <button
          type="button"
          onClick={() => executionMode && onExecutionModeAccepted?.(executionMode)}
        >
          mock-accept-mode
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
        <button
          type="button"
          onClick={() => onSSEEvent?.({
            type: 'session.execution_mode.changed',
            properties: {
              sessionID: sessionId,
              executionMode: 'build',
              reason: 'plan-approved',
            },
          })}
        >
          mock-plan-approved
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

function SessionRoutes() {
  return <Routes>
    <Route path="/sessions/:sessionId?" element={<SessionPage />} />
    <Route path="*" element={<div />} />
  </Routes>;
}

function NavigationProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return <>
    <output data-testid="session-location">{location.pathname}{location.search}</output>
    <button onClick={() => navigate(-1)}>history-back</button>
    <button onClick={() => navigate(1)}>history-forward</button>
    <button onClick={() => navigate('/sessions/session-2')}>open-second</button>
    <button onClick={() => navigate('/agents')}>leave-session-page</button>
    <button onClick={() => navigate('/sessions/session-1')}>return-to-first</button>
  </>;
}

// The layout keeps one pane per tab alive: leaving the sessions tab hides
// the page instead of unmounting it. These routes mirror contentRoutes.
const KEEP_ALIVE_TAB_HREFS = ['/sessions', '/agents'];
const keepAliveRoutes = [
  { path: 'sessions/:sessionId?', element: <SessionPage /> },
  { path: 'agents', element: <div data-testid="agents-page" /> },
  { path: '*', element: <div /> },
];

function KeepAliveTabs() {
  const location = useLocation();
  return (
    <KeepAlivePanes
      panes={KEEP_ALIVE_TAB_HREFS.map((href) => ({ href }))}
      activeHref={findActiveTabHref(KEEP_ALIVE_TAB_HREFS, location.pathname)}
      location={location}
      routes={keepAliveRoutes}
    />
  );
}

function renderSessionPageInKeepAliveTabs(initialEntry = '/sessions') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <NavigationProbe />
      <KeepAliveTabs />
    </MemoryRouter>,
  );
}

function sessionsPaneState() {
  return screen.getByTestId('session-chat').closest('[data-keep-alive-pane]')?.getAttribute('data-keep-alive-pane');
}

function renderSessionPage(
  initialEntry: string | { pathname: string; state?: unknown } = '/sessions',
) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <NavigationProbe />
      <SessionRoutes />
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

function contextFile(resourceID: string, overrides: Partial<SessionContextFile> = {}): SessionContextFile {
  return {
    resourceID, fileKey: resourceID, displayName: `${resourceID}.md`, logicalPath: `Outputs/${resourceID}.md`,
    mimeType: 'text/markdown', status: 'ready', previewStatus: 'text', canPreview: true, isTextFile: true,
    origin: 'agent_output', section: 'outputs', sourceMessageID: `msg-${resourceID}`, ...overrides,
  };
}

function contextPage(overrides: Partial<SessionContextSnapshot> = {}): SessionContextSnapshot {
  return {
    sessionID: session.id, canManageFolders: true, hasMore: false, nextBefore: null,
    outputs: [], contextFiles: [], progress: [], roots: [], skills: [],
    counts: { total: 0, outputs: 0, contextFiles: 0, roots: 0, progress: 0 }, ...overrides,
  };
}

function skillEvent(
  tool: 'skill_load' | 'load_skill',
  status: string,
  {
    sessionID = session.id, id = 'skill-part', messageID = 'skill-message',
    input = { name: 'docx' }, error, output = '',
  }: {
    sessionID?: string; id?: string; messageID?: string;
    input?: Record<string, string>; error?: string; output?: string;
  } = {},
): SSEChatEvent {
  return {
    type: 'message.part.updated',
    properties: { part: { id, sessionID, messageID, type: 'tool', tool, state: { status, input, error, output } } },
  };
}

function emitChatEvent(event: SSEChatEvent) {
  act(() => sessionChatSSERef.current!(event));
}

async function advanceContextDebounce(ms = 250) {
  await act(async () => { vi.advanceTimersByTime(ms); });
}

describe('SessionPage session actions menu', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    initialActionProbe.enabled = false;
    __resetChatModelResourcesForTesting();
    sessionStatusSSEOptionsRef.current = null;
    sessionChatSSERef.current = undefined;
    contextPanelPropsRef.current = null;
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
    workflowAPI.listSummaries.mockResolvedValue({ data: [] });
    skillAPI.status.mockResolvedValue({ data: [] });
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
    sessionApi.moveToProject.mockResolvedValue({
      ...session,
      projectID: 'prj_labs',
      effectiveProjectID: 'prj_labs',
      directory: '/tmp/labs',
    });
    client.patch.mockResolvedValue({ data: { id: 'prj_project2', worktree: '/tmp/labs', name: 'Renamed Project' } });
    client.post.mockResolvedValue({ data: secondSession });
    sessionApi.get.mockResolvedValue(session);
    sessionApi.getContext.mockReset().mockResolvedValue(contextPage());
    sessionApi.getContextFile.mockReset().mockResolvedValue(contextFile('requested-file'));
    sessionApi.readContextFile.mockReset().mockResolvedValue({ content: 'Requested content' });
    sessionApi.addContextFolder.mockReset().mockResolvedValue({});
    sessionApi.removeContextFolder.mockReset().mockResolvedValue({});
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
    sessionApi.archive.mockResolvedValue({ id: 'session-1', status: 'archived' });
    sessionApi.delete.mockResolvedValue(true);

    vi.stubGlobal('confirm', vi.fn(() => true));
  });

  it('consumes a legacy action once across StrictMode, back and remount', async () => {
    initialActionProbe.enabled = true;
    useSessions.mockReturnValue({ ...useSessions(), sessions: [session, secondSession] });
    const user = userEvent.setup();
    const view = render(<React.StrictMode><MemoryRouter initialEntries={['/sessions?session=session-1&message=once']}>
      <NavigationProbe /><SessionRoutes />
    </MemoryRouter></React.StrictMode>);
    await waitFor(() => expect(initialActionProbe.delivered).toHaveBeenCalledTimes(1));
    expect(initialActionProbe.delivered).toHaveBeenCalledWith('session-1', 'once');
    await user.click(screen.getByText('open-second'));
    await user.click(screen.getByText('history-back'));
    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-initial-message', '');
    expect(initialActionProbe.delivered).toHaveBeenCalledTimes(1);
    view.unmount();
    renderSessionPage('/sessions/session-1');
    expect(initialActionProbe.delivered).toHaveBeenCalledTimes(1);
  });

  it('keeps a new session empty after restoring an earlier session', async () => {
    localStorage.setItem('flocks:last-selected-session', 'session-1');
    sessionStorage.setItem('flocks:sessions:visited', 'true');
    const user = userEvent.setup();
    renderSessionPage();
    expect(screen.getByTestId('session-location')).toHaveTextContent('/sessions/session-1');
    await user.click(screen.getByRole('button', { name: 'newSession' }));
    expect(screen.getByTestId('session-location').textContent).toBe('/sessions');
    expect(screen.getByTestId('session-chat')).toHaveTextContent('no-session');
    await user.click(screen.getByText('history-back'));
    expect(screen.getByTestId('session-chat')).toHaveTextContent('session-1');
    await user.click(screen.getByText('history-forward'));
    expect(screen.getByTestId('session-chat')).toHaveTextContent('no-session');
  });

  it('does not navigate away when an earlier create-and-send request finishes', async () => {
    const request = deferred<{ data: typeof secondSession }>();
    client.post.mockImplementation((url: string) => url === '/api/session' ? request.promise : Promise.resolve({ data: {} }));
    const user = userEvent.setup();
    renderSessionPage();
    await user.click(screen.getByText('mock-create-and-send'));
    await user.click(screen.getByText('Original Session'));
    await act(async () => { request.resolve({ data: secondSession }); await request.promise; });
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/api/session/session-2/prompt_async', expect.anything()));
    expect(screen.getByTestId('session-location')).toHaveTextContent('/sessions/session-1');
    expect(screen.getByTestId('session-chat')).toHaveTextContent('session-1');
  });

  describe.each(['create', 'create-and-send'] as const)('%s completion under StrictMode', (action) => {
    it.each(['stay', 'leave', 'return'] as const)('respects navigation when users %s', async (destination) => {
      const request = deferred<{ data: typeof secondSession }>();
      client.post.mockImplementation((url: string) => url === '/api/session' ? request.promise : Promise.resolve({ data: {} }));
      const user = userEvent.setup();
      render(<React.StrictMode><MemoryRouter initialEntries={['/sessions']}>
        <NavigationProbe /><SessionRoutes />
      </MemoryRouter></React.StrictMode>);
      await user.click(action === 'create'
        ? screen.getByRole('button', { name: 'createTaskSession' })
        : screen.getByText('mock-create-and-send'));
      expect(client.post).toHaveBeenCalledWith('/api/session', expect.anything());

      if (destination !== 'stay') {
        await user.click(screen.getByText('leave-session-page'));
        expect(screen.queryByTestId('session-chat')).not.toBeInTheDocument();
        if (destination === 'return') {
          await user.click(screen.getByText('return-to-first'));
          expect(screen.getByTestId('session-chat')).toHaveTextContent('session-1');
        }
      }
      const savedSelection = localStorage.getItem('flocks:last-selected-session');
      await act(async () => { request.resolve({ data: secondSession }); await request.promise; });
      await waitFor(() => expect(addSession).toHaveBeenCalledWith(secondSession));
      if (action === 'create-and-send') {
        expect(client.post).toHaveBeenCalledWith('/api/session/session-2/prompt_async', expect.anything());
      }
      expect(screen.getByTestId('session-location').textContent).toBe(
        destination === 'stay' ? '/sessions/session-2'
          : destination === 'leave' ? '/agents' : '/sessions/session-1',
      );
      if (destination !== 'stay') {
        expect(localStorage.getItem('flocks:last-selected-session')).toBe(savedSelection);
      }
    });
  });

  describe.each(['archive', 'batch-archive'] as const)('%s completion under StrictMode', (action) => {
    it.each(['stay', 'leave', 'return'] as const)('respects navigation when users %s', async (destination) => {
      useSessions.mockReturnValue({ ...useSessions(), sessions: [session, secondSession] });
      const request = deferred<{ id: string; status: string }>();
      sessionApi.archive.mockReturnValue(request.promise);
      const user = userEvent.setup();
      render(<React.StrictMode><MemoryRouter initialEntries={['/sessions/session-1']}>
        <NavigationProbe /><SessionRoutes />
      </MemoryRouter></React.StrictMode>);
      if (action === 'archive') {
        await user.click(screen.getAllByRole('button', { name: 'moreActions' })[0]);
        await user.click(screen.getByRole('button', { name: 'archiveAction' }));
      } else {
        await user.click(screen.getByRole('button', { name: 'selectMode' }));
        await user.click(screen.getAllByText('Original Session')[0]);
        await user.click(screen.getByRole('button', { name: 'archiveSelected' }));
      }
      expect(sessionApi.archive).toHaveBeenCalledWith(session.id);
      if (destination !== 'stay') {
        await user.click(screen.getByText('leave-session-page'));
        expect(screen.queryByTestId('session-chat')).not.toBeInTheDocument();
        if (destination === 'return') {
          await user.click(screen.getByText('open-second'));
          expect(screen.getByTestId('session-chat')).toHaveTextContent('session-2');
        }
      }
      const savedSelection = localStorage.getItem('flocks:last-selected-session');
      await act(async () => { request.resolve({ id: session.id, status: 'archived' }); await request.promise; });
      expect(action === 'archive' ? removeSession : removeSessions).toHaveBeenCalledWith(
        action === 'archive' ? session.id : [session.id],
      );
      expect(screen.getByTestId('session-location').textContent).toBe(
        destination === 'stay' ? '/sessions'
          : destination === 'leave' ? '/agents' : '/sessions/session-2',
      );
      if (destination !== 'stay') {
        expect(localStorage.getItem('flocks:last-selected-session')).toBe(savedSelection);
      }
    });
  });

  describe('inside a hidden keep-alive pane', () => {
    it('does not navigate when a create-and-send request finishes after the user switched tabs', async () => {
      const request = deferred<{ data: typeof secondSession }>();
      client.post.mockImplementation((url: string) => url === '/api/session' ? request.promise : Promise.resolve({ data: {} }));
      const user = userEvent.setup();
      renderSessionPageInKeepAliveTabs();
      await user.click(screen.getByText('mock-create-and-send'));
      expect(client.post).toHaveBeenCalledWith('/api/session', expect.anything());

      await user.click(screen.getByText('leave-session-page'));
      expect(screen.getByTestId('agents-page')).toBeInTheDocument();
      // The page is still mounted, only hidden.
      expect(sessionsPaneState()).toBe('inactive');

      await act(async () => { request.resolve({ data: secondSession }); await request.promise; });
      await waitFor(() => expect(addSession).toHaveBeenCalledWith(secondSession));
      expect(client.post).toHaveBeenCalledWith('/api/session/session-2/prompt_async', expect.anything());
      expect(screen.getByTestId('session-location').textContent).toBe('/agents');

      // Coming back lands on the tab as it was left, not on the new session.
      await user.click(screen.getByText('history-back'));
      expect(sessionsPaneState()).toBe('active');
      expect(screen.getByTestId('session-location').textContent).toBe('/sessions');
    });

    it('does not replace the current history entry when an archive finishes after the user switched tabs', async () => {
      useSessions.mockReturnValue({ ...useSessions(), sessions: [session, secondSession] });
      const request = deferred<{ id: string; status: string }>();
      sessionApi.archive.mockReturnValue(request.promise);
      const user = userEvent.setup();
      renderSessionPageInKeepAliveTabs('/sessions/session-1');
      await user.click(screen.getAllByRole('button', { name: 'moreActions' })[0]);
      await user.click(screen.getByRole('button', { name: 'archiveAction' }));
      expect(sessionApi.archive).toHaveBeenCalledWith(session.id);

      await user.click(screen.getByText('leave-session-page'));
      expect(sessionsPaneState()).toBe('inactive');

      await act(async () => { request.resolve({ id: session.id, status: 'archived' }); await request.promise; });
      expect(removeSession).toHaveBeenCalledWith(session.id);
      expect(screen.getByTestId('session-location').textContent).toBe('/agents');

      // `/agents` was not replaced by `/sessions`: back returns to where the tab was.
      await user.click(screen.getByText('history-back'));
      expect(screen.getByTestId('session-location').textContent).toBe('/sessions/session-1');
    });

    it('defers the last-session restore until the tab is back on screen', async () => {
      localStorage.setItem('flocks:last-selected-session', 'session-1');
      sessionStorage.setItem('flocks:sessions:visited', 'true');
      useSessions.mockReturnValue({ ...useSessions(), loading: true });
      const user = userEvent.setup();
      const view = renderSessionPageInKeepAliveTabs();
      expect(screen.getByTestId('session-location').textContent).toBe('/sessions');

      await user.click(screen.getByText('leave-session-page'));
      expect(sessionsPaneState()).toBe('inactive');

      // The session list arrives while the tab is hidden.
      useSessions.mockReturnValue({ ...useSessions(), loading: false });
      view.rerender(
        <MemoryRouter initialEntries={['/sessions']}>
          <NavigationProbe />
          <KeepAliveTabs />
        </MemoryRouter>,
      );
      expect(screen.getByTestId('session-location').textContent).toBe('/agents');

      await user.click(screen.getByText('history-back'));
      await waitFor(() => expect(screen.getByTestId('session-location').textContent).toBe('/sessions/session-1'));
      expect(screen.getByTestId('session-chat')).toHaveTextContent('session-1');
    });
  });

  it('does not leave the current session when another session finishes archiving', async () => {
    useSessions.mockReturnValue({ ...useSessions(), sessions: [session, secondSession] });
    const request = deferred<{ id: string; status: string }>();
    sessionApi.archive.mockReturnValue(request.promise);
    const user = userEvent.setup();
    renderSessionPage('/sessions/session-1');
    await user.click(screen.getAllByRole('button', { name: 'moreActions' })[0]);
    await user.click(screen.getByRole('button', { name: 'archiveAction' }));
    await user.click(screen.getByText('open-second'));
    await act(async () => { request.resolve({ id: session.id, status: 'archived' }); await request.promise; });
    expect(screen.getByTestId('session-location')).toHaveTextContent('/sessions/session-2');
    expect(screen.getByTestId('session-chat')).toHaveTextContent('session-2');
  });

  it('keeps a canonical URL through selection, back and forward without sending', async () => {
    const user = userEvent.setup();
    useSessions.mockReturnValue({ ...useSessions(), sessions: [session, secondSession] });
    renderSessionPage('/sessions/session-1');
    expect(screen.getByTestId('session-chat')).toHaveTextContent('session-1');
    await user.click(screen.getByText('Second Session'));
    expect(screen.getByTestId('session-location')).toHaveTextContent('/sessions/session-2');
    expect(screen.getByTestId('session-chat')).toHaveTextContent('session-2');
    await user.click(screen.getByText('history-back'));
    expect(screen.getByTestId('session-chat')).toHaveTextContent('session-1');
    await user.click(screen.getByText('history-forward'));
    expect(screen.getByTestId('session-chat')).toHaveTextContent('session-2');
    expect(client.post).not.toHaveBeenCalled();
  });

  it('normalizes legacy links and retains only navigation parameters', async () => {
    renderSessionPage('/sessions?session=session-1&focusMessage=msg-1&message=hello&display=label');
    expect(screen.getByTestId('session-location')).toHaveTextContent('/sessions/session-1?focusMessage=msg-1');
    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-initial-message', 'hello');
    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-focus-message', 'msg-1');
  });

  it('never sends a conflicting query action to the path session', async () => {
    renderSessionPage('/sessions/session-1?session=session-2&message=wrong');
    expect(screen.getByTestId('session-chat')).toHaveTextContent('session-1');
    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-initial-message', '');
    expect(toast.error).toHaveBeenCalledWith('linkTargetMismatch');
  });

  it('loads direct links independently of a slow sidebar and discards stale responses', async () => {
    const request = deferred<typeof session>();
    useSessions.mockReturnValue({ ...useSessions(), sessions: [], loading: true });
    sessionApi.get.mockImplementation((id: string) => id === session.id ? request.promise : Promise.resolve(secondSession));
    const user = userEvent.setup();
    renderSessionPage('/sessions/session-1');
    expect(sessionApi.get).toHaveBeenCalledWith('session-1');
    await user.click(screen.getByText('open-second'));
    await waitFor(() => expect(screen.getByTestId('session-chat')).toHaveTextContent('session-2'));
    await act(async () => { request.resolve(session); await request.promise; });
    expect(screen.getByTestId('session-chat')).toHaveTextContent('session-2');
    expect(screen.getByTestId('session-location')).toHaveTextContent('/sessions/session-2');
  });

  it('revalidates a list-external session after navigating away and back', async () => {
    useSessions.mockReturnValue({ ...useSessions(), sessions: [secondSession] });
    sessionApi.get.mockResolvedValueOnce(session).mockRejectedValueOnce({ response: { status: 404 } });
    const user = userEvent.setup();
    renderSessionPage('/sessions/session-1');
    await waitFor(() => expect(screen.getByTestId('session-chat')).toHaveTextContent('session-1'));
    await user.click(screen.getByText('open-second'));
    await user.click(screen.getByText('history-back'));
    expect(await screen.findByRole('alert')).toHaveTextContent('sessionAccess.unavailable');
    expect(sessionApi.get).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId('session-chat')).not.toBeInTheDocument();
  });

  it('shows a retryable network error instead of a blank composer', async () => {
    useSessions.mockReturnValue({ ...useSessions(), sessions: [] });
    sessionApi.get.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(session);
    const user = userEvent.setup();
    renderSessionPage('/sessions/session-1');
    expect(await screen.findByRole('alert')).toHaveTextContent('sessionAccess.failed');
    expect(screen.queryByTestId('session-chat')).not.toBeInTheDocument();
    await user.click(screen.getByText('sessionAccess.retry'));
    await waitFor(() => expect(screen.getByTestId('session-chat')).toHaveTextContent('session-1'));
  });

  it('does not auto-send a legacy action to a read-only session', async () => {
    useSessions.mockReturnValue({ ...useSessions(), sessions: [{ ...session, canWrite: false }] });
    renderSessionPage('/sessions?session=session-1&message=do-not-send');
    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-initial-message', '');
  });

  it('renders Build by default and keeps Agent in the Add menu', async () => {
    renderSessionPage();

    await screen.findByRole('button', { name: 'executionMode.title' });
    const agentButton = screen.getByRole('button', { name: 'chat.addMenu.agent' });
    const agentIconContainer = agentButton.querySelector('svg')?.parentElement;

    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-execution-mode', 'build');
    expect(agentButton).toHaveAttribute('aria-haspopup', 'menu');
    expect(agentIconContainer).not.toHaveClass('rounded-lg', 'border', 'bg-white');
    expect(agentIconContainer?.className).not.toContain('shadow-');
  });

  it('opens the Session Context panel from the header', async () => {
    const user = userEvent.setup();
    renderSessionPage('/sessions?session=session-1');

    const button = await screen.findByRole('button', { name: 'context.title' });
    expect(sessionApi.getContext).not.toHaveBeenCalled();
    await user.click(button);

    expect(sessionApi.getContext).toHaveBeenCalledWith('session-1', {}, expect.any(AbortSignal));
    expect(screen.getAllByText('context.outputs').length).toBeGreaterThan(0);
    expect(screen.getAllByText('context.contextFiles').length).toBeGreaterThan(0);
  });

  it('opens requested metadata immediately when a closed panel has no snapshot yet', async () => {
    const head = deferred<SessionContextSnapshot>();
    sessionApi.getContext.mockReturnValue(head.promise);
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'mock-open-context-file' }));
    expect(await screen.findByTestId('file-preview')).toHaveTextContent('requested-file.md:Requested content');
    expect(sessionApi.getContextFile).toHaveBeenCalledWith('session-1', 'requested-file', expect.any(AbortSignal));
    expect(sessionApi.getContext).toHaveBeenCalledTimes(1);
    await act(async () => head.resolve(contextPage()));
    expect(screen.getByTestId('file-preview')).toHaveTextContent('requested-file.md:Requested content');
  });

  it.each([
    ['text/markdown', false], ['application/pdf', false],
    ['text/markdown', true], ['application/pdf', true],
  ] as const)('keeps a closed-panel card request alive through StrictMode mount replay (%s, cached=%s)', async (mimeType, cached) => {
    const head = deferred<SessionContextSnapshot>();
    const metadata = deferred<SessionContextFile>();
    sessionApi.getContext.mockReturnValue(head.promise);
    if (cached) sessionApi.getContext.mockResolvedValueOnce(contextPage());
    sessionApi.getContextFile.mockImplementation((_sessionId: string, _id: string, signal: AbortSignal) => (
      new Promise((resolve, reject) => {
        signal.addEventListener('abort', () => reject(new Error('Canceled')), { once: true });
        metadata.promise.then(resolve, reject);
      })
    ));
    render(
      <React.StrictMode>
        <MemoryRouter initialEntries={['/sessions?session=session-1']}>
          <SessionRoutes />
        </MemoryRouter>
      </React.StrictMode>,
    );
    if (cached) {
      fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
      await waitFor(() => expect(contextPanelPropsRef.current?.loading).toBe(false));
      fireEvent.click(screen.getByRole('button', { name: 'context.close' }));
    }
    fireEvent.click(await screen.findByRole('button', { name: 'mock-open-context-file' }));
    await waitFor(() => expect(sessionApi.getContextFile).toHaveBeenCalled());
    await act(async () => metadata.resolve(contextFile('requested-file', {
      displayName: 'earlier-file', mimeType, isTextFile: mimeType.startsWith('text/'),
    })));
    expect(await screen.findByTestId('file-preview')).toHaveTextContent('earlier-file');
    await act(async () => head.resolve(contextPage()));
    expect(screen.getByTestId('file-preview')).toHaveTextContent('earlier-file');
    expect(contextPanelPropsRef.current?.requestedResourceID).toBeNull();
    const signals = sessionApi.getContextFile.mock.calls.map((call) => call[2] as AbortSignal);
    expect(signals.some((signal) => !signal.aborted)).toBe(true);
  });

  it('coalesces refresh, SSE, and reconnect bursts into one single-flight trailing read', async () => {
    const first = deferred<SessionContextSnapshot>();
    const trailing = deferred<SessionContextSnapshot>();
    sessionApi.getContext.mockReturnValueOnce(first.promise).mockReturnValueOnce(trailing.promise);
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    const refresh = screen.getByTitle('context.refresh');
    fireEvent.click(screen.getByRole('button', { name: 'mock-connected' }));
    for (let i = 0; i < 5; i += 1) {
      fireEvent.click(refresh);
      fireEvent.click(screen.getByRole('button', { name: 'mock-context-updated' }));
    }
    expect(sessionApi.getContext).toHaveBeenCalledTimes(1);
    expect((sessionApi.getContext.mock.calls[0][2] as AbortSignal).aborted).toBe(false);
    await act(async () => first.resolve(contextPage({ outputs: [contextFile('initial')] })));
    expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
    expect(contextPanelPropsRef.current?.loading).toBe(true);
    expect(screen.getByText('initial.md')).toBeInTheDocument();
    await act(async () => trailing.resolve(contextPage({ outputs: [contextFile('latest')] })));
    expect(screen.getByText('latest.md')).toBeInTheDocument();
    expect(contextPanelPropsRef.current?.loading).toBe(false);
    expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
  });

  it('debounces idle SSE bursts and ignores events for other sessions', async () => {
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    await waitFor(() => expect(contextPanelPropsRef.current?.loading).toBe(false));
    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole('button', { name: 'mock-other-context-updated' }));
      await act(async () => { vi.advanceTimersByTime(300); });
      expect(sessionApi.getContext).toHaveBeenCalledTimes(1);
      for (let i = 0; i < 5; i += 1) fireEvent.click(screen.getByRole('button', { name: 'mock-context-updated' }));
      await act(async () => { vi.advanceTimersByTime(249); });
      expect(sessionApi.getContext).toHaveBeenCalledTimes(1);
      await act(async () => { vi.advanceTimersByTime(1); });
      expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each(['skill_load', 'load_skill'] as const)('refreshes skill-only %s lifecycle events and clears retry errors', async (tool) => {
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    await waitFor(() => expect(contextPanelPropsRef.current?.loading).toBe(false));
    vi.useFakeTimers();
    try {
      let requests = 1;
      for (const status of ['pending', 'running', 'error', 'running', 'completed'] as const) {
        const error = status === 'error' ? 'Skill dependency missing' : undefined;
        const skill = {
          name: 'docx', description: null,
          status: status === 'completed' ? 'loaded' as const : status === 'error' ? 'error' as const : 'loading' as const,
          ...(error ? { error } : {}),
        };
        sessionApi.getContext.mockResolvedValueOnce(contextPage({ skills: [skill] }));
        emitChatEvent(skillEvent(tool, status, { input: tool === 'skill_load' ? { name: 'docx' } : { skill: 'docx' }, error }));
        await advanceContextDebounce(249);
        expect(sessionApi.getContext).toHaveBeenCalledTimes(requests);
        await advanceContextDebounce(1);
        requests += 1;
        expect(sessionApi.getContext).toHaveBeenCalledTimes(requests);
        expect(sessionApi.getContext).toHaveBeenLastCalledWith('session-1', {}, expect.any(AbortSignal));
        expect(contextPanelPropsRef.current?.snapshot?.skills).toEqual([skill]);
      }
      // No write/todo/context-updated or streaming-done event is needed above.
      for (let i = 0; i < 5; i += 1) emitChatEvent(skillEvent(tool, 'completed', { output: `chunk ${i}` }));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(requests);
      expect(contextPanelPropsRef.current?.snapshot?.skills[0]).not.toHaveProperty('error');
    } finally {
      vi.useRealTimers();
    }
  });

  it('deduplicates skill descriptors by part, including newly available names and changed errors', async () => {
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    await waitFor(() => expect(contextPanelPropsRef.current?.loading).toBe(false));
    vi.useFakeTimers();
    try {
      emitChatEvent(skillEvent('skill_load', 'unknown'));
      const unrelated = skillEvent('skill_load', 'running');
      unrelated.properties!.part.tool = 'read';
      emitChatEvent(unrelated);
      emitChatEvent({ ...skillEvent('skill_load', 'running'), type: 'message.updated' });
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(1);

      emitChatEvent(skillEvent('skill_load', 'running', { input: {} }));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
      emitChatEvent(skillEvent('skill_load', 'running', { input: {}, output: 'partial' }));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
      emitChatEvent(skillEvent('skill_load', 'running', { input: { skill: 'docx' } }));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(3);
      emitChatEvent(skillEvent('skill_load', 'running', { input: { name: 'docx' }, output: 'more' }));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(3);

      emitChatEvent(skillEvent('skill_load', 'error', { error: 'First reason' }));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(4);
      emitChatEvent(skillEvent('skill_load', 'error', { error: 'First reason', output: 'ignored output' }));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(4);
      emitChatEvent(skillEvent('skill_load', 'error', { error: 'Full reason' }));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(5);
      emitChatEvent(skillEvent('skill_load', 'error', { error: 'Full reason', id: 'another-part' }));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(6);
      emitChatEvent(skillEvent('skill_load', 'error', { error: 'Full reason', messageID: 'another-message' }));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(7);
    } finally {
      vi.useRealTimers();
    }
  });

  it('coalesces skill bursts into one debounced request and one dirty trailing read', async () => {
    const refresh = deferred<SessionContextSnapshot>();
    const trailing = deferred<SessionContextSnapshot>();
    sessionApi.getContext.mockResolvedValueOnce(contextPage()).mockReturnValueOnce(refresh.promise).mockReturnValueOnce(trailing.promise);
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    await waitFor(() => expect(contextPanelPropsRef.current?.loading).toBe(false));
    vi.useFakeTimers();
    try {
      emitChatEvent(skillEvent('skill_load', 'pending'));
      emitChatEvent(skillEvent('skill_load', 'running'));
      emitChatEvent(skillEvent('load_skill', 'pending', { id: 'second-part' }));
      await advanceContextDebounce(249);
      expect(sessionApi.getContext).toHaveBeenCalledTimes(1);
      await advanceContextDebounce(1);
      expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
      for (const status of ['error', 'running', 'completed']) {
        emitChatEvent(skillEvent('skill_load', status));
      }
      emitChatEvent(skillEvent('load_skill', 'completed', { id: 'second-part' }));
      await advanceContextDebounce(500);
      expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
      await act(async () => refresh.resolve(contextPage({ skills: [{ name: 'docx', status: 'loading' }] })));
      expect(sessionApi.getContext).toHaveBeenCalledTimes(3);
      expect(contextPanelPropsRef.current?.loading).toBe(true);
      // Repeated output-only updates during the trailing flight must not dirty it again.
      for (let i = 0; i < 5; i += 1) emitChatEvent(skillEvent('skill_load', 'completed', { output: `chunk ${i}` }));
      await act(async () => trailing.resolve(contextPage({ skills: [{ name: 'docx', status: 'loaded' }] })));
      await advanceContextDebounce(500);
      expect(sessionApi.getContext).toHaveBeenCalledTimes(3);
      expect(contextPanelPropsRef.current?.loading).toBe(false);
      expect(contextPanelPropsRef.current?.snapshot?.skills).toEqual([{ name: 'docx', status: 'loaded' }]);
    } finally {
      vi.useRealTimers();
    }
  });

  it('resets skill deduplication on close/reopen and A -> B -> A without caching foreign or stale events', async () => {
    useSessions.mockReturnValue({ ...useSessions(), sessions: [session, secondSession] });
    sessionApi.getContext.mockImplementation((sessionId: string) => Promise.resolve(contextPage({ sessionID: sessionId })));
    renderSessionPage('/sessions?session=session-1');
    await screen.findByRole('button', { name: 'context.title' });
    const closedHandler = sessionChatSSERef.current!;
    emitChatEvent(skillEvent('skill_load', 'running'));
    expect(sessionApi.getContext).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'context.title' }));
    await waitFor(() => expect(contextPanelPropsRef.current?.loading).toBe(false));
    const firstAHandler = sessionChatSSERef.current!;
    vi.useFakeTimers();
    try {
      emitChatEvent(skillEvent('skill_load', 'running', { sessionID: 'session-2' }));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(1);
      emitChatEvent(skillEvent('skill_load', 'running'));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
      fireEvent.click(screen.getByRole('button', { name: 'context.close' }));
      emitChatEvent(skillEvent('skill_load', 'running'));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
      fireEvent.click(screen.getByRole('button', { name: 'context.title' }));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(3);
      act(() => { closedHandler(skillEvent('skill_load', 'running')); firstAHandler(skillEvent('skill_load', 'running')); });
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(3);
      emitChatEvent(skillEvent('skill_load', 'running'));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(4);

      // A scheduled descriptor change is discarded on switch, not carried into B.
      emitChatEvent(skillEvent('skill_load', 'completed'));
      fireEvent.click(screen.getByText('Second Session'));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(5);
      emitChatEvent(skillEvent('skill_load', 'running'));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(5);
      emitChatEvent(skillEvent('skill_load', 'running', { sessionID: 'session-2' }));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(6);
      fireEvent.click(screen.getByText('Original Session'));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(7);
      act(() => firstAHandler(skillEvent('skill_load', 'running')));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(7);
      emitChatEvent(skillEvent('skill_load', 'running'));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(8);
      emitChatEvent(skillEvent('skill_load', 'completed'));
      fireEvent.click(screen.getByRole('button', { name: 'context.close' }));
      await advanceContextDebounce();
      expect(sessionApi.getContext.mock.calls.map((call) => call[0])).toEqual([
        'session-1', 'session-1', 'session-1', 'session-1', 'session-2', 'session-2', 'session-1', 'session-1',
      ]);
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps newer failed skill attempts above older loaded pages and refreshes retries from skill SSE alone', async () => {
    const older = deferred<SessionContextSnapshot>();
    const refresh = deferred<SessionContextSnapshot>();
    const failed = { name: 'docx', status: 'error' as const, error: 'Latest attempt failed' };
    sessionApi.getContext
      .mockResolvedValueOnce(contextPage({ hasMore: true, nextBefore: 'old-cursor', messageIDs: ['head'], skills: [failed] }))
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(refresh.promise)
      .mockResolvedValueOnce(contextPage({ skills: [{ name: 'docx', status: 'loaded' }] }));
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    fireEvent.click(await screen.findByRole('button', { name: 'context.loadEarlier' }));
    emitChatEvent(skillEvent('load_skill', 'running'));
    expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
    await act(async () => older.resolve(contextPage({ skills: [
      { name: 'docx', status: 'loaded' }, { name: 'older-skill', status: 'loaded' },
    ] })));
    expect(contextPanelPropsRef.current?.snapshot?.skills).toEqual([failed, { name: 'older-skill', status: 'loaded' }]);
    expect(sessionApi.getContext).toHaveBeenCalledTimes(3);
    expect(sessionApi.getContext.mock.calls[2][1]).toEqual({});
    await act(async () => refresh.resolve(contextPage({ skills: [{ name: 'docx', status: 'loading' }] })));
    expect(contextPanelPropsRef.current?.snapshot?.skills[0]).toEqual({ name: 'docx', status: 'loading' });
    vi.useFakeTimers();
    try {
      emitChatEvent(skillEvent('load_skill', 'completed'));
      await advanceContextDebounce();
      expect(sessionApi.getContext).toHaveBeenCalledTimes(4);
      expect(contextPanelPropsRef.current?.snapshot?.skills).toEqual([
        { name: 'docx', status: 'loaded' }, { name: 'older-skill', status: 'loaded' },
      ]);
    } finally {
      vi.useRealTimers();
    }
  });

  it('cancels scheduled SSE refreshes on session switch and close', async () => {
    useSessions.mockReturnValue({ ...useSessions(), sessions: [session, secondSession] });
    sessionApi.getContext.mockImplementation((sessionId: string) => Promise.resolve(contextPage({ sessionID: sessionId })));
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    await waitFor(() => expect(contextPanelPropsRef.current?.loading).toBe(false));
    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole('button', { name: 'mock-context-updated' }));
      fireEvent.click(screen.getByText('Second Session'));
      await act(async () => { vi.advanceTimersByTime(300); });
      expect(sessionApi.getContext.mock.calls.map((call) => call[0])).toEqual(['session-1', 'session-2']);
      fireEvent.click(screen.getByRole('button', { name: 'mock-context-updated' }));
      fireEvent.click(screen.getByRole('button', { name: 'context.close' }));
      fireEvent.click(screen.getByRole('button', { name: 'mock-context-updated' }));
      await act(async () => { vi.advanceTimersByTime(300); });
      expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('retries a dirty failed flight once and renders validation errors as strings', async () => {
    const first = deferred<SessionContextSnapshot>();
    const retry = deferred<SessionContextSnapshot>();
    sessionApi.getContext.mockReturnValueOnce(first.promise).mockReturnValueOnce(retry.promise);
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    fireEvent.click(screen.getByTitle('context.refresh'));
    await act(async () => first.reject({ response: { data: { detail: [{ msg: 'Invalid cursor' }] } } }));
    expect(screen.getByText('Invalid cursor')).toBeInTheDocument();
    expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
    await act(async () => retry.resolve(contextPage({ outputs: [contextFile('recovered')] })));
    expect(screen.getByText('recovered.md')).toBeInTheDocument();
    expect(screen.queryByText('Invalid cursor')).not.toBeInTheDocument();
    expect(contextPanelPropsRef.current?.loading).toBe(false);
  });

  it('isolates A -> B -> A requests and rejects stale refresh callbacks without invalidating B', async () => {
    useSessions.mockReturnValue({ ...useSessions(), sessions: [session, secondSession] });
    const firstA = deferred<SessionContextSnapshot>();
    const b = deferred<SessionContextSnapshot>();
    const latestA = deferred<SessionContextSnapshot>();
    sessionApi.getContext.mockReturnValueOnce(firstA.promise).mockReturnValueOnce(b.promise).mockReturnValueOnce(latestA.promise);
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    const staleRefresh = contextPanelPropsRef.current!.onRefresh;
    const firstSignal = sessionApi.getContext.mock.calls[0][2] as AbortSignal;
    fireEvent.click(screen.getByText('Second Session'));
    await waitFor(() => expect(sessionApi.getContext).toHaveBeenCalledTimes(2));
    const bSignal = sessionApi.getContext.mock.calls[1][2] as AbortSignal;
    expect(firstSignal.aborted).toBe(true);
    await act(async () => { await staleRefresh(); });
    expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
    expect(bSignal.aborted).toBe(false);
    await act(async () => b.resolve(contextPage({ sessionID: 'session-2', outputs: [contextFile('B')] })));
    expect(screen.getByText('B.md')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Original Session'));
    await waitFor(() => expect(sessionApi.getContext).toHaveBeenCalledTimes(3));
    await act(async () => { await staleRefresh(); });
    expect(sessionApi.getContext).toHaveBeenCalledTimes(3);
    await act(async () => latestA.resolve(contextPage({ outputs: [contextFile('latest-A')] })));
    await act(async () => firstA.resolve(contextPage({ outputs: [contextFile('stale-A')] })));
    expect(screen.getByText('latest-A.md')).toBeInTheDocument();
    expect(screen.queryByText('stale-A.md')).not.toBeInTheDocument();
    expect(screen.queryByText('B.md')).not.toBeInTheDocument();
    expect(contextPanelPropsRef.current?.loading).toBe(false);
  });

  it('aborts a dirty request on close and ignores stale callbacks after reopening', async () => {
    const old = deferred<SessionContextSnapshot>();
    const reopened = deferred<SessionContextSnapshot>();
    sessionApi.getContext.mockReturnValueOnce(old.promise).mockReturnValueOnce(reopened.promise);
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    const staleRefresh = contextPanelPropsRef.current!.onRefresh;
    fireEvent.click(screen.getByTitle('context.refresh'));
    fireEvent.click(screen.getByRole('button', { name: 'context.close' }));
    expect((sessionApi.getContext.mock.calls[0][2] as AbortSignal).aborted).toBe(true);
    await act(async () => { await staleRefresh(); });
    expect(sessionApi.getContext).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'context.title' }));
    await waitFor(() => expect(sessionApi.getContext).toHaveBeenCalledTimes(2));
    await act(async () => { await staleRefresh(); old.reject(new Error('Stale close error')); });
    expect((sessionApi.getContext.mock.calls[1][2] as AbortSignal).aborted).toBe(false);
    expect(contextPanelPropsRef.current?.loading).toBe(true);
    expect(screen.queryByText('Stale close error')).not.toBeInTheDocument();
    await act(async () => reopened.resolve(contextPage({ outputs: [contextFile('reopened')] })));
    expect(screen.getByText('reopened.md')).toBeInTheDocument();
    expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
  });

  it('cancels context requests on unmount and does not let retained callbacks restart them', async () => {
    const pending = deferred<SessionContextSnapshot>();
    sessionApi.getContext.mockReturnValue(pending.promise);
    const view = renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    const staleRefresh = contextPanelPropsRef.current!.onRefresh;
    const signal = sessionApi.getContext.mock.calls[0][2] as AbortSignal;
    view.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => { await staleRefresh(); pending.resolve(contextPage()); });
    expect(sessionApi.getContext).toHaveBeenCalledTimes(1);
  });

  it('serializes older pages with refresh and retains history, stable identities, and latest state', async () => {
    const older = deferred<SessionContextSnapshot>();
    const refresh = deferred<SessionContextSnapshot>();
    sessionApi.getContext
      .mockResolvedValueOnce(contextPage({
        hasMore: true, nextBefore: 'cursor-1', messageIDs: ['msg-head'],
        outputs: [contextFile('new', { fileKey: 'same-file' }), contextFile('legacy', { fileKey: '' })],
        progress: [{ id: 'todo', content: 'Latest progress', status: 'in_progress' }],
        skills: [{ name: 'docx', status: 'loaded', description: 'Latest skill' }],
      }))
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(refresh.promise)
      .mockResolvedValueOnce(contextPage({ outputs: [contextFile('oldest')], hasMore: false, nextBefore: null }));
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    fireEvent.click(await screen.findByRole('button', { name: 'context.loadEarlier' }));
    expect(sessionApi.getContext.mock.calls[1][1]).toEqual({ before: 'cursor-1' });
    fireEvent.click(screen.getByTitle('context.refresh'));
    fireEvent.click(screen.getByRole('button', { name: 'mock-context-updated' }));
    expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
    await act(async () => older.resolve(contextPage({
      hasMore: true, nextBefore: 'cursor-2',
      outputs: [contextFile('historic'), contextFile('legacy', { fileKey: '', displayName: 'stale-legacy.md' })],
      contextFiles: [contextFile('stale', { fileKey: 'same-file', section: 'context' })],
      progress: [{ id: 'todo', content: 'Old progress', status: 'pending' }],
      skills: [{ name: 'docx', status: 'loading', description: 'Old skill' }, { name: 'older-skill', status: 'loaded' }],
    })));
    expect(sessionApi.getContext).toHaveBeenCalledTimes(3);
    expect(sessionApi.getContext.mock.calls[2][1]).toEqual({});
    expect(screen.getByText('new.md')).toBeInTheDocument();
    expect(screen.getByText('historic.md')).toBeInTheDocument();
    expect(screen.queryByText('stale.md')).not.toBeInTheDocument();
    expect(screen.queryByText('stale-legacy.md')).not.toBeInTheDocument();
    expect(contextPanelPropsRef.current?.snapshot?.progress[0].content).toBe('Latest progress');
    expect(contextPanelPropsRef.current?.snapshot?.skills.find((skill) => skill.name === 'docx')?.description).toBe('Latest skill');
    await act(async () => refresh.resolve(contextPage({
      hasMore: true, nextBefore: 'new-head-cursor', messageIDs: ['msg-head', 'msg-fresh'],
      outputs: [contextFile('updated', { fileKey: 'same-file' }), contextFile('fresh')],
      progress: [{ id: 'todo', content: 'Refreshed progress', status: 'completed' }],
      skills: [{ name: 'docx', status: 'loaded', description: 'Refreshed skill' }],
    })));
    expect(screen.getByText('updated.md')).toBeInTheDocument();
    expect(screen.getByText('historic.md')).toBeInTheDocument();
    expect(screen.getByText('legacy.md')).toBeInTheDocument();
    expect(screen.queryByText('new.md')).not.toBeInTheDocument();
    expect(contextPanelPropsRef.current?.snapshot?.nextBefore).toBe('cursor-2');
    expect(contextPanelPropsRef.current?.snapshot?.skills).toHaveLength(2);
    expect(contextPanelPropsRef.current?.snapshot?.progress[0].content).toBe('Refreshed progress');
    fireEvent.click(screen.getByRole('button', { name: 'context.loadEarlier' }));
    await screen.findByText('oldest.md');
    expect(sessionApi.getContext.mock.calls[3][1]).toEqual({ before: 'cursor-2' });
    expect(screen.getByText('historic.md')).toBeInTheDocument();
    expect(contextPanelPropsRef.current?.snapshot?.hasMore).toBe(false);
    expect(screen.queryByRole('button', { name: 'context.loadEarlier' })).not.toBeInTheDocument();
  });

  it('reconciles deleted and replaced files in a refreshed message while preserving older history', async () => {
    const renamed = contextFile('renamed', { fileKey: 'old-path', displayName: 'old-name.md', sourceMessageID: 'msg-head' });
    sessionApi.getContext
      .mockResolvedValueOnce(contextPage({
        messageIDs: ['msg-head'], hasMore: true, nextBefore: 'msg-head',
        outputs: [contextFile('deleted-output', { sourceMessageID: 'msg-head' }), renamed],
        contextFiles: [contextFile('deleted-upload', { section: 'context', origin: 'user_upload', sourceMessageID: 'msg-head' })],
      }))
      .mockResolvedValueOnce(contextPage({
        messageIDs: ['msg-old'], outputs: [contextFile('historical', { sourceMessageID: 'msg-old' })],
      }))
      .mockResolvedValueOnce(contextPage({
        messageIDs: ['msg-head'], hasMore: true, nextBefore: 'msg-head',
        outputs: [{ ...renamed, fileKey: 'new-path', displayName: 'new-name.md' }],
      }));
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    fireEvent.click(await screen.findByRole('button', { name: 'context.loadEarlier' }));
    await screen.findByText('historical.md');
    fireEvent.click(screen.getByTitle('context.refresh'));
    await screen.findByText('new-name.md');
    expect(screen.queryByText('deleted-output.md')).not.toBeInTheDocument();
    expect(screen.queryByText('deleted-upload.md')).not.toBeInTheDocument();
    expect(screen.queryByText('old-name.md')).not.toBeInTheDocument();
    expect(screen.getByText('historical.md')).toBeInTheDocument();
    expect(contextPanelPropsRef.current?.snapshot?.counts).toEqual({
      total: 2, outputs: 2, contextFiles: 0, roots: 0, progress: 0,
    });
  });

  it('reconciles a reloaded historical interval without dropping newer files', async () => {
    const newer = contextFile('newer', { sourceMessageID: 'msg-new' });
    sessionApi.getContext
      .mockResolvedValueOnce(contextPage({ messageIDs: ['msg-new'], hasMore: true, nextBefore: 'msg-new', outputs: [newer] }))
      .mockResolvedValueOnce(contextPage({ messageIDs: ['msg-old'], outputs: [contextFile('removed-old', { sourceMessageID: 'msg-old' })] }))
      .mockResolvedValueOnce(contextPage({
        messageIDs: ['msg-latest'], hasMore: true, nextBefore: 'msg-latest',
        outputs: [contextFile('latest', { sourceMessageID: 'msg-latest' })],
      }))
      .mockResolvedValueOnce(contextPage({ messageIDs: ['msg-new', 'msg-old'], outputs: [newer] }));
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    fireEvent.click(await screen.findByRole('button', { name: 'context.loadEarlier' }));
    await screen.findByText('removed-old.md');
    fireEvent.click(screen.getByTitle('context.refresh'));
    await screen.findByText('latest.md');
    fireEvent.click(screen.getByRole('button', { name: 'context.loadEarlier' }));
    await waitFor(() => expect(contextPanelPropsRef.current?.loading).toBe(false));
    expect(screen.queryByText('removed-old.md')).not.toBeInTheDocument();
    expect(screen.getByText('newer.md')).toBeInTheDocument();
    expect(screen.getByText('latest.md')).toBeInTheDocument();
  });

  it.each([false, true])('replaces cached files and pagination when a refreshed head covers all remaining messages (empty=%s)', async (empty) => {
    const retained = contextFile('retained', { sourceMessageID: 'msg-retained' });
    sessionApi.getContext
      .mockResolvedValueOnce(contextPage({
        messageIDs: ['msg-deleted', 'msg-retained'], hasMore: true, nextBefore: 'msg-deleted',
        outputs: [contextFile('deleted-message', { sourceMessageID: 'msg-deleted' }), retained],
      }))
      .mockResolvedValueOnce(contextPage({
        messageIDs: empty ? [] : ['msg-retained'], outputs: empty ? [] : [retained],
      }));
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    await screen.findByText('deleted-message.md');
    fireEvent.click(screen.getByTitle('context.refresh'));
    await waitFor(() => expect(contextPanelPropsRef.current?.loading).toBe(false));
    expect(contextPanelPropsRef.current?.snapshot?.outputs).toEqual(empty ? [] : [retained]);
    expect(contextPanelPropsRef.current?.snapshot?.hasMore).toBe(false);
    expect(contextPanelPropsRef.current?.snapshot?.nextBefore).toBeNull();
    expect(screen.queryByRole('button', { name: 'context.loadEarlier' })).not.toBeInTheDocument();
  });

  it.each([0, 1, 3])('counts %s Todo items as at most one Progress resource', async (count) => {
    sessionApi.getContext.mockResolvedValue(contextPage({
      outputs: [contextFile('output')],
      contextFiles: [contextFile('upload', { section: 'context', origin: 'user_upload' })],
      roots: [{ id: 'project', kind: 'project', displayName: 'Project', status: 'available' }],
      progressKnown: true,
      progress: Array.from({ length: count }, (_, index) => ({ id: `todo-${index}`, content: `Task ${index}`, status: 'pending' as const })),
    }));
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    await screen.findByText('output.md');
    expect(contextPanelPropsRef.current?.snapshot?.counts).toEqual({
      total: 3 + Number(count > 0), outputs: 1, contextFiles: 1, roots: 1, progress: Number(count > 0),
    });
    expect(contextPanelPropsRef.current?.snapshot?.progress).toHaveLength(count);
  });

  it.each([null, 'previous-cursor'])('reopens pagination across disjoint head pages after the previous boundary %s', async (previousCursor) => {
    sessionApi.getContext
      .mockResolvedValueOnce(contextPage({
        messageIDs: ['msg-1'], hasMore: previousCursor !== null, nextBefore: previousCursor,
        outputs: [contextFile('first')],
      }))
      .mockResolvedValueOnce(contextPage({
        messageIDs: Array.from({ length: 100 }, (_, index) => `msg-${index + 3}`),
        hasMore: true, nextBefore: 'msg-3', outputs: [contextFile('latest')],
      }))
      .mockResolvedValueOnce(contextPage({
        messageIDs: ['msg-1', 'msg-2'], outputs: [contextFile('gap'), contextFile('first')],
      }));
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    await screen.findByText('first.md');
    fireEvent.click(screen.getByRole('button', { name: 'context.close' }));
    fireEvent.click(screen.getByRole('button', { name: 'context.title' }));
    await screen.findByText('latest.md');
    expect(screen.getByText('first.md')).toBeInTheDocument();
    expect(contextPanelPropsRef.current?.snapshot?.nextBefore).toBe('msg-3');
    fireEvent.click(screen.getByRole('button', { name: 'context.loadEarlier' }));
    await screen.findByText('gap.md');
    expect(sessionApi.getContext.mock.calls[2][1]).toEqual({ before: 'msg-3' });
    expect(contextPanelPropsRef.current?.snapshot?.outputs).toHaveLength(3);
    expect(screen.queryByRole('button', { name: 'context.loadEarlier' })).not.toBeInTheDocument();
  });

  it('replaces a retained old file with a newer revision loaded from a previously missed interval', async () => {
    sessionApi.getContext
      .mockResolvedValueOnce(contextPage({
        messageIDs: ['msg-1'], outputs: [contextFile('old-revision', { fileKey: 'report', createdAt: 1 })],
      }))
      .mockResolvedValueOnce(contextPage({ messageIDs: ['msg-102'], hasMore: true, nextBefore: 'msg-3' }))
      .mockResolvedValueOnce(contextPage({
        messageIDs: ['msg-1', 'msg-2'],
        outputs: [contextFile('new-revision', { fileKey: 'report', createdAt: 2 })],
      }));
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    await screen.findByText('old-revision.md');
    fireEvent.click(screen.getByTitle('context.refresh'));
    fireEvent.click(await screen.findByRole('button', { name: 'context.loadEarlier' }));
    await screen.findByText('new-revision.md');
    expect(screen.queryByText('old-revision.md')).not.toBeInTheDocument();
    expect(contextPanelPropsRef.current?.snapshot?.outputs).toHaveLength(1);
  });

  it('honors an explicitly cleared Progress on refresh and does not revive it from older pages', async () => {
    sessionApi.getContext
      .mockResolvedValueOnce(contextPage({
        messageIDs: ['msg-2'], hasMore: true, nextBefore: 'msg-2', progressKnown: true,
        progress: [{ id: 'todo', content: 'Previous task', status: 'in_progress' }],
      }))
      .mockResolvedValueOnce(contextPage({
        messageIDs: ['msg-2', 'msg-3'], hasMore: true, nextBefore: 'msg-2', progressKnown: true, progress: [],
      }))
      .mockResolvedValueOnce(contextPage({
        messageIDs: ['msg-1'], progressKnown: true,
        progress: [{ id: 'todo', content: 'Previous task', status: 'pending' }],
      }));
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    await waitFor(() => expect(contextPanelPropsRef.current?.snapshot?.progress).toHaveLength(1));
    fireEvent.click(screen.getByTitle('context.refresh'));
    await waitFor(() => expect(contextPanelPropsRef.current?.loading).toBe(false));
    expect(contextPanelPropsRef.current?.snapshot?.progress).toEqual([]);
    expect(contextPanelPropsRef.current?.snapshot?.progressKnown).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'context.loadEarlier' }));
    await waitFor(() => expect(contextPanelPropsRef.current?.loading).toBe(false));
    expect(contextPanelPropsRef.current?.snapshot?.progress).toEqual([]);
    expect(sessionApi.getContext).toHaveBeenCalledTimes(3);
  });

  it('fills Progress from history only when the newer page has no known Todo state', async () => {
    sessionApi.getContext
      .mockResolvedValueOnce(contextPage({ hasMore: true, nextBefore: 'msg-2', progressKnown: false }))
      .mockResolvedValueOnce(contextPage({
        progressKnown: true, progress: [{ id: 'todo', content: 'Historical task', status: 'pending' }],
      }));
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    fireEvent.click(await screen.findByRole('button', { name: 'context.loadEarlier' }));
    await waitFor(() => expect(contextPanelPropsRef.current?.snapshot?.progress).toHaveLength(1));
    expect(contextPanelPropsRef.current?.snapshot?.progressKnown).toBe(true);
  });

  it('keeps an empty history page pageable and prevents duplicate load-more requests', async () => {
    const older = deferred<SessionContextSnapshot>();
    sessionApi.getContext.mockResolvedValueOnce(contextPage({ hasMore: true, nextBefore: 'empty-cursor' })).mockReturnValueOnce(older.promise);
    renderSessionPage('/sessions?session=session-1');
    fireEvent.click(await screen.findByRole('button', { name: 'context.title' }));
    fireEvent.click(await screen.findByRole('button', { name: 'context.loadEarlier' }));
    act(() => { void contextPanelPropsRef.current?.onLoadMore?.(); });
    expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
    await act(async () => older.resolve(contextPage({ hasMore: true, nextBefore: 'next-empty-cursor' })));
    expect(screen.getByRole('button', { name: 'context.loadEarlier' })).toBeEnabled();
    expect(contextPanelPropsRef.current?.snapshot?.nextBefore).toBe('next-empty-cursor');
    expect(sessionApi.getContext).toHaveBeenCalledTimes(2);
  });

  it('shows full execution-mode titles in a portaled menu and preserves dismissal', async () => {
    const user = userEvent.setup();
    renderSessionPage();

    const trigger = await screen.findByRole('button', { name: 'executionMode.title' });
    await user.click(trigger);
    const menu = screen.getByRole('menu', { name: 'executionMode.title' });
    const plan = within(menu).getByRole('menuitemradio', { name: /executionMode.options.plan.label/ });

    expect(menu.parentElement).toBe(document.body);
    expect(menu).toHaveAttribute('data-execution-mode-selector');
    expect(menu).toHaveStyle({ width: '520px' });
    await user.hover(plan);
    expect(plan).toHaveAttribute('title', 'executionMode.options.plan.label\nexecutionMode.options.plan.description');
    expect(plan).toHaveAttribute('aria-checked', 'false');
    await user.click(plan);
    expect(screen.queryByRole('menu', { name: 'executionMode.title' })).not.toBeInTheDocument();
    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-execution-mode', 'plan');

    await user.click(trigger);
    expect(screen.getByRole('menuitemradio', { name: /executionMode.options.plan.label/ })).toHaveAttribute('aria-checked', 'true');
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu', { name: 'executionMode.title' })).not.toBeInTheDocument();
    await user.click(trigger);
    await user.click(document.body);
    expect(screen.queryByRole('menu', { name: 'executionMode.title' })).not.toBeInTheDocument();
  });

  it('uses the same width for execution, model and security menus with full hover text', async () => {
    const user = userEvent.setup();
    useProviders.mockReturnValue({
      providers: modelProviders,
      connectedIds: ['openai', 'minimax'],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    defaultModelAPI.getResolved.mockResolvedValue({ data: { provider_id: 'openai', model_id: 'gpt-4o' } });
    modelV2API.listDefinitions.mockResolvedValue({ data: { models: modelDefinitions } });
    client.get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/api/flockspro/license/status'
        ? { pro_enabled: true }
        : [{ id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true, pathStatus: 'available', sessionCount: 1 }],
    }));
    renderSessionPage();

    await user.click(await screen.findByRole('button', { name: 'executionMode.title' }));
    const executionMenu = screen.getByRole('menu', { name: 'executionMode.title' });
    const menuWidth = executionMenu.style.width;
    expect(menuWidth).toBe('520px');
    await user.click(document.body);

    await user.click(await screen.findByRole('button', { name: /GPT-4o/i }));
    const modelMenu = screen.getByText('modelPicker.title').closest('[data-model-selector]') as HTMLElement;
    expect(modelMenu.parentElement).toBe(document.body);
    expect(modelMenu.style.width).toBe(menuWidth);
    expect(within(modelMenu).getByText('OpenAI')).toHaveAttribute('title', 'OpenAI');
    expect(within(modelMenu).getByText('modelPicker.hint')).toHaveAttribute('title', 'modelPicker.hint');
    const modelOption = within(modelMenu).getByRole('button', { name: /MiniMax M3/i });
    await user.hover(modelOption);
    expect(modelOption.getAttribute('title')).toContain('MiniMax M3\nMiniMax / minimax-m3');
    const info = modelOption.querySelector('.lucide-info')?.parentElement;
    expect(info).not.toBeNull();
    await user.hover(info as HTMLElement);
    expect(screen.getByRole('tooltip')).toHaveTextContent('MiniMax M3');
    expect(within(modelMenu).getByRole('button', { name: 'modelPicker.addModel' })).toBeInTheDocument();
    await user.click(document.body);
    expect(screen.queryByText('modelPicker.title')).not.toBeInTheDocument();

    await user.click(await screen.findByRole('button', { name: 'permissionMode.requireConfirm' }));
    const securityMenu = screen.getByText('permissionMode.runtimeTitle').closest('[data-permission-mode-selector]') as HTMLElement;
    expect(securityMenu.parentElement).toBe(document.body);
    expect(securityMenu.style.width).toBe(menuWidth);
    for (const [label, description] of [
      ['permissionMode.runtimeExe', 'permissionMode.runtimeExeDesc'],
      ['permissionMode.networkAutoDenyAll', 'permissionMode.networkAutoDenyAllDesc'],
      ['permissionMode.autoAllowAll', 'permissionMode.autoAllowAllDesc'],
    ]) {
      const option = within(securityMenu).getByRole('button', { name: new RegExp(label) });
      await user.hover(option);
      expect(option).toHaveAttribute('title', `${label}\n${description}`);
    }
    await user.click(document.body);
    expect(screen.queryByText('permissionMode.runtimeTitle')).not.toBeInTheDocument();
  });

  it.each([
    ['permissionMode.requireConfirm', 'permissionMode.viewDetails', '/settings/security-config'],
    ['GPT-4o', 'modelPicker.addModel', '/models'],
  ])('keeps navigation from the %s popup working', async (triggerName, actionName, destination) => {
    const user = userEvent.setup();
    useProviders.mockReturnValue({
      providers: modelProviders,
      connectedIds: ['openai', 'minimax'],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
    defaultModelAPI.getResolved.mockResolvedValue({ data: { provider_id: 'openai', model_id: 'gpt-4o' } });
    modelV2API.listDefinitions.mockResolvedValue({ data: { models: modelDefinitions } });
    client.get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/api/flockspro/license/status'
        ? { pro_enabled: true }
        : [{ id: 'default', worktree: '/tmp/project', name: '默认', isDefault: true, pathStatus: 'available', sessionCount: 1 }],
    }));
    function LocationProbe() {
      return <output data-testid="location">{useLocation().pathname}</output>;
    }
    render(
      <MemoryRouter initialEntries={['/sessions']}>
        <SessionRoutes />
        <LocationProbe />
      </MemoryRouter>,
    );

    await user.click(await screen.findByRole('button', { name: triggerName }));
    await user.click(await screen.findByRole('button', { name: actionName }));
    expect(screen.getByTestId('location')).toHaveTextContent(destination);
    expect(screen.queryByRole('button', { name: actionName })).not.toBeInTheDocument();
  });

  it('persists Plan per session', async () => {
    const user = userEvent.setup();
    renderSessionPage('/sessions?session=session-1');

    const modeButton = await screen.findByRole('button', { name: 'executionMode.title' });
    await user.click(modeButton);
    await user.click(screen.getByRole('menuitemradio', {
      name: /executionMode.options.plan.label/,
    }));

    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-execution-mode', 'plan');
    expect(localStorage.getItem('flocks:session-execution-mode:session-1')).toBe('plan');
  });

  it('restores a persisted session execution mode', async () => {
    localStorage.setItem('flocks:session-execution-mode:session-1', 'plan');
    renderSessionPage('/sessions?session=session-1');

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-execution-mode', 'plan');
    });
  });

  it('switches an approved Plan to Build from the session event', async () => {
    const user = userEvent.setup();
    renderSessionPage('/sessions?session=session-1');

    await user.click(await screen.findByRole('button', { name: 'executionMode.title' }));
    await user.click(screen.getByRole('menuitemradio', {
      name: /executionMode.options.plan.label/,
    }));
    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-execution-mode', 'plan');

    await user.click(screen.getByRole('button', { name: 'mock-plan-approved' }));

    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-execution-mode', 'build');
    expect(localStorage.getItem('flocks:session-execution-mode:session-1')).toBeNull();
  });

  it('resets Goal to Build after the prompt is accepted', async () => {
    const user = userEvent.setup();
    renderSessionPage('/sessions?session=session-1');

    await user.click(await screen.findByRole('button', { name: 'executionMode.title' }));
    await user.click(screen.getByRole('menuitemradio', {
      name: /executionMode.options.goal.label/,
    }));
    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-execution-mode', 'goal');

    await user.click(screen.getByRole('button', { name: 'mock-accept-mode' }));

    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-execution-mode', 'build');
    expect(localStorage.getItem('flocks:session-execution-mode:session-1')).toBeNull();
  });

  it('promotes a draft Plan mode when the first message creates a session', async () => {
    const user = userEvent.setup();
    renderSessionPage();

    await user.click(await screen.findByRole('button', { name: 'executionMode.title' }));
    await user.click(screen.getByRole('menuitemradio', {
      name: /executionMode.options.plan.label/,
    }));
    await user.click(screen.getByRole('button', { name: 'mock-create-and-send' }));

    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith(
        '/api/session/session-2/prompt_async',
        expect.objectContaining({ executionMode: 'plan' }),
      );
    });
    expect(localStorage.getItem('flocks:session-execution-mode:session-2')).toBe('plan');
    expect(localStorage.getItem('flocks:session-execution-mode:draft')).toBeNull();
  });

  it('writes Pro execution settings before sending a new session’s first prompt', async () => {
    const user = userEvent.setup();
    const settings = deferred<{ data: {
      permissionMode: 'require-confirm';
      runtimeMode: 'dev-mode';
      networkMode: 'require-confirm';
      networkModeDefault: 'require-confirm';
      networkModeOverridden: false;
      entry: 'webui';
      revision: 1;
    } }>();
    client.get.mockImplementation((url: string) => {
      if (url === '/api/flockspro/license/status') {
        return Promise.resolve({ data: { pro_enabled: true } });
      }
      return Promise.resolve({
        data: [{
          id: 'default',
          worktree: '/tmp/project',
          name: '默认',
          isDefault: true,
          pathStatus: 'available',
          sessionCount: 0,
        }],
      });
    });
    client.patch.mockReturnValue(settings.promise);

    renderSessionPage();
    await waitFor(() => {
      expect(client.get).toHaveBeenCalledWith('/api/flockspro/license/status');
    });
    await user.click(screen.getByRole('button', { name: 'mock-create-and-send' }));

    await waitFor(() => {
      expect(client.patch).toHaveBeenCalledWith(
        '/api/flockspro/policy/sessions/session-2/execution-settings',
        {
          permissionMode: 'require-confirm',
          runtimeMode: 'dev-mode',
          networkMode: 'require-confirm',
        },
      );
    });
    expect(client.post).not.toHaveBeenCalledWith(
      '/api/session/session-2/prompt_async',
      expect.anything(),
    );

    settings.resolve({
      data: {
        permissionMode: 'require-confirm',
        runtimeMode: 'dev-mode',
        networkMode: 'require-confirm',
        networkModeDefault: 'require-confirm',
        networkModeOverridden: false,
        entry: 'webui',
        revision: 1,
      },
    });

    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith(
        '/api/session/session-2/prompt_async',
        expect.anything(),
      );
    });
  });

  it('keeps the first new-session message optimistic with the persisted message id', async () => {
    const user = userEvent.setup();
    renderSessionPage();

    await user.click(screen.getByRole('button', { name: 'mock-create-and-send' }));

    await waitFor(() => {
      expect(client.post).toHaveBeenCalledWith(
        '/api/session/session-2/prompt_async',
        expect.objectContaining({ messageID: expect.stringMatching(/^msg_/) }),
      );
    });

    const promptCall = client.post.mock.calls.find(
      ([url]) => url === '/api/session/session-2/prompt_async',
    );
    const messageId = promptCall?.[1]?.messageID;
    const chat = screen.getByTestId('session-chat');
    expect(chat).toHaveAttribute('data-optimistic-id', messageId);
    expect(chat).toHaveAttribute('data-optimistic-text', 'hello from empty session');
  });

  it('does not switch sessions or leave an optimistic message when the first send fails', async () => {
    const user = userEvent.setup();
    client.post.mockImplementation((url: string) => {
      if (url === '/api/session') return Promise.resolve({ data: secondSession });
      if (url === '/api/session/session-2/prompt_async') {
        return Promise.reject(new Error('prompt rejected'));
      }
      return Promise.resolve({ data: {} });
    });
    renderSessionPage();

    await user.click(screen.getByRole('button', { name: 'mock-create-and-send' }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('chat.sendFailed', 'prompt rejected');
    });
    const chat = screen.getByTestId('session-chat');
    expect(chat).toHaveTextContent('no-session');
    expect(chat).toHaveAttribute('data-optimistic-id', '');
    expect(addSession).not.toHaveBeenCalled();
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
    const searchButton = screen.getByRole('button', { name: 'openTaskSearch' });
    expect(screen.queryByPlaceholderText('filterConversations')).not.toBeInTheDocument();
    await user.click(searchButton);
    const searchDialog = screen.getByRole('dialog', { name: 'taskSearchDialog' });
    const searchInput = within(searchDialog).getByPlaceholderText('filterConversations');
    expect(searchDialog).toHaveClass('fixed', 'inset-0', 'justify-center');
    expect(searchDialog.firstElementChild).toHaveClass('max-w-[620px]', 'rounded-2xl');
    expect(newSessionButton.previousElementSibling).toHaveClass('left-2', 'h-3.5', 'w-3.5');
    expect(searchButton.closest('div')).toContainElement(screen.getByText('managementTitle'));
    expect(searchInput).toHaveFocus();
    expect(searchInput).toHaveClass('text-[15px]', 'font-medium');
    await user.click(within(searchDialog).getByRole('button', { name: 'closeTaskSearch' }));
    expect(tasksHeading.closest('div')).toHaveClass('px-2', 'text-xs', 'text-zinc-500');
    expect(projectsHeading.closest('div')).toHaveClass('px-2', 'text-xs', 'text-zinc-500');
    expect(tasksHeading.nextElementSibling).toHaveTextContent('(1)');
    expect(projectsHeading.nextElementSibling).toHaveTextContent('(0)');
    expect(tasksSection).not.toBeNull();
    expect(projectsSection).not.toBeNull();
    expect(tasksSection?.parentElement).toBe(projectsSection?.parentElement);
    expect(projectsSection).not.toContainElement(tasksHeading);
    expect(useSessions).toHaveBeenLastCalledWith('', {
      projectIds: ['tasks'],
      pageSize: 20,
    });
    expect(screen.queryByText('defaultProjectName')).not.toBeInTheDocument();
    const taskTitle = screen.getByText('Original Session').closest('h3');
    expect(taskTitle).toHaveClass('text-sm', 'font-medium');
    expect(taskTitle?.parentElement?.parentElement).toHaveClass('px-3');

    const tasksToggle = screen.getByRole('button', { name: 'toggleTasks' });
    expect(tasksToggle).toContainElement(tasksHeading);
    expect(tasksToggle.querySelector('svg')).toHaveClass('h-3.5', 'w-3.5');
    expect(screen.queryByRole('button', { name: 'selectTasks' })).not.toBeInTheDocument();
    const createTaskButton = screen.getByRole('button', { name: 'createTaskSession' });
    expect(createTaskButton).toHaveClass(
      'opacity-0',
      'group-hover/tasks-section:opacity-100',
      'group-focus-within/tasks-section:opacity-100',
    );

    await user.click(tasksToggle);
    expect(screen.queryByText('Original Session')).not.toBeInTheDocument();

    await user.click(tasksToggle);
    expect(screen.getByText('Original Session')).toBeInTheDocument();
  });

  it('shows the five most recently updated sessions when search opens', async () => {
    const user = userEvent.setup();
    const recentSessions = Array.from({ length: 6 }, (_, index) => ({
      ...session,
      id: `recent-${index + 1}`,
      slug: `recent-${index + 1}`,
      title: `Recent ${index + 1}`,
      time: {
        ...session.time,
        updated: session.time.updated + index,
      },
    }));
    useSessions.mockReturnValue({
      sessions: recentSessions,
      loading: false,
      error: null,
      refetch: refetchSessions,
      updateSessionTitle,
      removeSession,
      removeSessions,
      addSession,
    });

    renderSessionPage();
    await user.click(screen.getByRole('button', { name: 'openTaskSearch' }));

    const searchDialog = screen.getByRole('dialog', { name: 'taskSearchDialog' });
    expect(within(searchDialog).getAllByRole('button')).toHaveLength(6);
    expect(within(searchDialog).queryByText('Recent 1')).not.toBeInTheDocument();
    expect(within(searchDialog).getByText('Recent 6')).toBeInTheDocument();
    expect(within(searchDialog).getByText('recentTasks')).toBeInTheDocument();

    await user.click(within(searchDialog).getByText('Recent 6'));
    expect(screen.queryByRole('dialog', { name: 'taskSearchDialog' })).not.toBeInTheDocument();
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

  it('keeps the workbench canvas, sidebar, selected row, and dark palette classes stable', async () => {
    renderSessionPage('/sessions?session=session-1');

    const workbenchSidebar = screen.getByLabelText('managementTitle');
    const workbenchCanvas = workbenchSidebar.parentElement;
    const mainCanvas = workbenchSidebar.nextElementSibling;
    const sessionTitle = await within(workbenchSidebar).findByText('Original Session');
    const selectedRow = sessionTitle.closest('div.group');

    expect(workbenchCanvas).toHaveClass('bg-[#fcfcfd]', 'dark:bg-[#303842]');
    expect(workbenchSidebar).toHaveClass('bg-gray-50', 'dark:bg-[#252c35]');
    expect(workbenchSidebar).toHaveClass('h-full', 'border-r');
    expect(workbenchSidebar).toHaveClass('border-black/[0.10]');
    expect(workbenchSidebar).not.toHaveClass('rounded-2xl');
    expect(workbenchSidebar.className).not.toContain('shadow-');
    expect(mainCanvas).toHaveClass('bg-[#fcfcfd]', 'dark:bg-[#303842]');
    expect(within(mainCanvas as HTMLElement).getByRole('heading', { level: 2 })).toHaveClass('text-[#555a61]');
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
            [secondSession.id]: { type: 'dreaming', message: 'Dreaming...' },
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
    const projectButton = screen.getByRole('button', { name: 'selectProject' });
    expect(projectButton.querySelector('svg')).toHaveClass('h-3.5', 'w-3.5');
    expect(projectButton.parentElement).toHaveClass('px-3');
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

  it('hides the channel title prefix until the session is renamed', async () => {
    const user = userEvent.setup();
    useSessions.mockReturnValue({
      sessions: [{
        ...session,
        title: '[Wecom] 你能干什么事情',
        channelID: 'wecom',
        channelChatType: 'direct',
      }, {
        ...session,
        id: 'session-2',
        title: '[Wecom] 群聊问题',
        channelID: 'wecom',
        channelChatType: 'group',
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

    const displayTitle = await screen.findByText('你能干什么事情');
    const sessionRow = displayTitle.closest('div.group');
    expect(sessionRow).not.toBeNull();
    expect(within(sessionRow as HTMLElement).getByRole('img', { name: 'wecom' }))
      .toHaveAttribute('src', '/channel-wecom.png');
    expect(within(sessionRow as HTMLElement).getByRole('img', { name: 'channelDirectChat' }))
      .toHaveAttribute('data-channel-chat-type', 'direct');
    expect(screen.queryByText('[Wecom] 你能干什么事情')).not.toBeInTheDocument();

    const groupTitle = screen.getByText('群聊问题');
    const groupRow = groupTitle.closest('div.group');
    expect(groupRow).not.toBeNull();
    expect(within(groupRow as HTMLElement).getByRole('img', { name: 'channelGroupChat' }))
      .toHaveAttribute('data-channel-chat-type', 'group');

    await user.click(within(sessionRow as HTMLElement).getByRole('button', { name: 'moreActions' }));
    await user.click(screen.getByRole('button', { name: 'rename' }));

    expect(screen.getByRole('textbox', { name: 'rename' }))
      .toHaveValue('[Wecom] 你能干什么事情');
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

    await user.click(screen.getByRole('button', { name: 'openTaskSearch' }));
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
    expect(renamedProject.closest('[class*="group/project"]')).not.toHaveTextContent('8');
    expect(screen.getByText('projectsSection').nextElementSibling).toHaveTextContent('(1)');
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
    await userEvent.setup().click(screen.getByRole('button', { name: 'openTaskSearch' }));
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
    const projectActionsButton = within(projectRow as HTMLElement).getByRole('button', { name: 'projectActions' });
    const createProjectSessionButton = within(projectRow as HTMLElement).getByRole('button', {
      name: 'createSessionInProject',
    });
    expect(projectActionsButton.nextElementSibling).toBe(createProjectSessionButton);
    await user.click(createProjectSessionButton);

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
    expect(screen.getByRole('button', { name: 'moveToProjectAction' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'archiveAction' })).toBeInTheDocument();
  });

  it('moves a regular task into a selected project', async () => {
    const user = userEvent.setup();
    client.get.mockResolvedValue({
      data: [
        { id: 'prj_labs', worktree: '/tmp/labs', name: 'Labs', canWrite: true, pathStatus: 'available' },
      ],
    });

    renderSessionPage();

    await screen.findByText('Original Session');
    await user.click(screen.getByRole('button', { name: 'moreActions' }));
    await user.click(screen.getByRole('button', { name: 'moveToProjectAction' }));
    await user.click(screen.getByRole('menuitem', { name: 'Labs' }));

    await waitFor(() => {
      expect(sessionApi.moveToProject).toHaveBeenCalledWith('session-1', 'prj_labs');
    });
    expect(refetchSessions).toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalledWith('moveToProjectSuccess');
  });

  it('keeps the project picker inside the viewport near the bottom edge', async () => {
    const user = userEvent.setup();
    client.get.mockResolvedValue({
      data: [
        { id: 'prj_labs', worktree: '/tmp/labs', name: 'Labs', canWrite: true, pathStatus: 'available' },
      ],
    });
    const originalInnerHeight = window.innerHeight;
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 400 });

    renderSessionPage();

    await screen.findByText('Original Session');
    const actionsTrigger = screen.getByRole('button', { name: 'moreActions' });
    vi.spyOn(actionsTrigger, 'getBoundingClientRect').mockReturnValue({
      bottom: 380,
      right: 300,
    } as DOMRect);
    await user.click(actionsTrigger);
    await user.click(screen.getByRole('button', { name: 'moveToProjectAction' }));

    const menu = document.querySelector('[data-session-menu-portal]') as HTMLElement;
    expect(menu.style.top).toBe('300px');
    expect(menu.style.maxHeight).toBe('calc(100vh - 16px)');

    Object.defineProperty(window, 'innerHeight', {
      configurable: true,
      value: originalInnerHeight,
    });
  });

  it('shows the backend detail when moving a task fails', async () => {
    const user = userEvent.setup();
    client.get.mockResolvedValue({
      data: [
        { id: 'prj_labs', worktree: '/tmp/labs', name: 'Labs', canWrite: true, pathStatus: 'available' },
      ],
    });
    sessionApi.moveToProject.mockRejectedValue({
      message: 'Request failed with status code 409',
      response: { data: { detail: '任务正在运行，请稍后再移动' } },
    });

    renderSessionPage();

    await screen.findByText('Original Session');
    await user.click(screen.getByRole('button', { name: 'moreActions' }));
    await user.click(screen.getByRole('button', { name: 'moveToProjectAction' }));
    await user.click(screen.getByRole('menuitem', { name: 'Labs' }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        'moveToProjectFailed',
        '任务正在运行，请稍后再移动',
      );
    });
  });

  it('shows the move spinner only on the selected target project', async () => {
    const user = userEvent.setup();
    client.get.mockResolvedValue({
      data: [
        { id: 'prj_labs', worktree: '/tmp/labs', name: 'Labs', canWrite: true, pathStatus: 'available' },
        { id: 'prj_docs', worktree: '/tmp/docs', name: 'Docs', canWrite: true, pathStatus: 'available' },
      ],
    });
    sessionApi.moveToProject.mockImplementation(() => new Promise(() => {}));

    renderSessionPage();

    await screen.findByText('Original Session');
    await user.click(screen.getByRole('button', { name: 'moreActions' }));
    await user.click(screen.getByRole('button', { name: 'moveToProjectAction' }));
    const labsTarget = screen.getByRole('menuitem', { name: 'Labs' });
    const docsTarget = screen.getByRole('menuitem', { name: 'Docs' });
    await user.click(labsTarget);

    await waitFor(() => {
      expect(labsTarget.querySelector('.animate-spin')).not.toBeNull();
    });
    expect(docsTarget.querySelector('.animate-spin')).toBeNull();
  });

  it('moves a project task to a different project and marks its current project', async () => {
    const user = userEvent.setup();
    client.get.mockResolvedValue({
      data: [
        { id: 'prj_source', worktree: '/tmp/source', name: 'Source', canWrite: true, pathStatus: 'available' },
        { id: 'prj_target', worktree: '/tmp/target', name: 'Target', canWrite: true, pathStatus: 'available' },
      ],
    });
    useSessions.mockReturnValue({
      sessions: [{
        ...session,
        projectID: 'prj_source',
        effectiveProjectID: 'prj_source',
        directory: '/tmp/source',
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

    await screen.findByText('Original Session');
    await user.click(screen.getByRole('button', { name: 'moreActions' }));
    await user.click(screen.getByRole('button', { name: 'moveToProjectAction' }));
    expect(screen.getByRole('menuitem', { name: 'Source' })).toBeDisabled();
    await user.click(screen.getByRole('menuitem', { name: 'Target' }));

    await waitFor(() => {
      expect(sessionApi.moveToProject).toHaveBeenCalledWith('session-1', 'prj_target');
    });
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

  it('archives a session from the actions menu', async () => {
    const user = userEvent.setup();

    renderSessionPage();

    await screen.findByText('Original Session');
    await user.click(screen.getByRole('button', { name: 'moreActions' }));
    await user.click(screen.getByRole('button', { name: 'archiveAction' }));

    await waitFor(() => {
      expect(sessionApi.archive).toHaveBeenCalledWith('session-1');
    });
    expect(removeSession).toHaveBeenCalledWith('session-1');
    expect(global.confirm).toHaveBeenCalledWith('confirmArchive');
  });

  it('keeps a successful archive successful when project-count refresh fails', async () => {
    const user = userEvent.setup();
    renderSessionPage();

    await screen.findByText('Original Session');
    await waitFor(() => expect(client.get).toHaveBeenCalled());
    client.get.mockRejectedValueOnce(new Error('project refresh failed'));
    await user.click(screen.getByRole('button', { name: 'moreActions' }));
    await user.click(screen.getByRole('button', { name: 'archiveAction' }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('archiveSuccess'));
    expect(removeSession).toHaveBeenCalledWith('session-1');
    expect(toast.error).not.toHaveBeenCalledWith('archiveFailed', expect.anything());
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

  it('passes URL focusMessage to chat without treating it as an initial message', async () => {
    renderSessionPage('/sessions?session=session-1&focusMessage=message-42');

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-focus-message', 'message-42');
    });
    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-initial-message', '');
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
    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveAttribute(
        'data-optimistic-text',
        'welcome.alertOperationsSuggestion',
      );
      expect(screen.getByTestId('session-chat')).toHaveAttribute(
        'data-optimistic-display',
        '@@flocks-instruction:welcome.alertOperations',
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
        <SessionRoutes />
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
    expect(screen.getByTestId('session-chat-skeleton')).not.toHaveClass('animate-pulse');
    expect(screen.getByTestId('workbench-refresh-status')).toHaveTextContent('restoringTask');

    await act(async () => {
      request.resolve(fetchedSession);
      await request.promise;
    });

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveTextContent('session-missing-from-list');
      expect(screen.getByTestId('session-chat')).toHaveAttribute('data-hide-input', 'true');
    });
  });

  it('keeps the target URL and shows an error when the session no longer exists', async () => {
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
      expect(screen.getByRole('alert')).toHaveTextContent('sessionAccess.unavailable');
      expect(screen.queryByTestId('session-chat')).not.toBeInTheDocument();
      expect(screen.getByTestId('session-location')).toHaveTextContent('/sessions/session-deleted');
    });
  });

  it('drops a URL initial message when the target session no longer exists', async () => {
    const user = userEvent.setup();
    const message = 'Do not send this to another session';
    sessionApi.get.mockRejectedValue({ response: { status: 404 } });

    renderSessionPage(`/sessions?session=session-deleted&message=${encodeURIComponent(message)}`);

    await waitFor(() => {
      expect(sessionApi.get).toHaveBeenCalledWith('session-deleted');
      expect(screen.getByRole('alert')).toHaveTextContent('sessionAccess.unavailable');
      expect(screen.queryByTestId('session-chat')).not.toBeInTheDocument();
      expect(screen.getByTestId('session-location')).toHaveTextContent('/sessions/session-deleted');
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

    await user.click(screen.getByRole('button', { name: 'chat.addMenu.agent' }));

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
    await user.click(screen.getByRole('button', { name: 'chat.addMenu.agent' }));
    await user.click(screen.getByRole('button', { name: /Explore/i }));
    expect(screen.getByTestId('mock-chat-input')).toHaveTextContent('subagent:explore');
    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-agent-name', 'rex');

    await user.click(screen.getByRole('button', { name: 'newSession' }));

    expect(screen.getByTestId('session-chat')).toHaveTextContent('no-session');
    expect(client.post).not.toHaveBeenCalledWith('/api/session', expect.anything());
    expect(screen.getByRole('button', { name: 'projectPicker.title' })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('menu', { name: 'projectPicker.title' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'chat.addMenu.agent' })).toBeInTheDocument();
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

  it('keeps Rex as the default while selecting a one-turn subagent reference', async () => {
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

    await user.click(screen.getByRole('button', { name: 'chat.addMenu.agent' }));
    expect(screen.queryByRole('button', { name: /Rex/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Explore/i }));
    expect(screen.getByTestId('mock-chat-input')).toHaveTextContent('subagent:explore');
    expect(screen.getByTestId('session-chat')).toHaveAttribute('data-agent-name', 'rex');
  });

  it('inserts the selected agent as a structured subagent reference', async () => {
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

    await user.click(screen.getByRole('button', { name: 'chat.addMenu.agent' }));
    await user.click(screen.getByRole('button', { name: /Explore/i }));

    expect(screen.getByTestId('mock-chat-input')).toHaveTextContent('subagent:explore');
  });

  it('inserts selected workflows and skills as composer references', async () => {
    const user = userEvent.setup();
    workflowAPI.listSummaries.mockResolvedValue({
      data: [{
        id: 'alert-triage',
        name: 'Alert triage',
        description: 'Investigate security alerts',
        category: 'security',
        status: 'active',
        source: 'project',
        createdAt: 1,
        updatedAt: 1,
        nodeCount: 2,
        stats: {
          callCount: 0,
          successCount: 0,
          errorCount: 0,
          totalRuntime: 0,
          avgRuntime: 0,
          thumbsUp: 0,
          thumbsDown: 0,
        },
      }],
    });
    skillAPI.status.mockResolvedValue({
      data: [{
        name: 'diagnose',
        description: 'Debug difficult failures',
        location: '/skills/diagnose',
        source: 'project',
        eligible: true,
      }],
    });

    renderSessionPage();

    await user.click(screen.getByRole('button', { name: 'mock-open-add-menu' }));
    const skillButton = screen.getByRole('button', { name: 'chat.addMenu.skills' });
    const workflowButton = screen.getByRole('button', { name: 'chat.addMenu.workflows' });
    const menuButtons = screen.getAllByRole('button');
    expect(menuButtons.indexOf(skillButton)).toBeLessThan(menuButtons.indexOf(workflowButton));
    for (const button of [skillButton, workflowButton]) {
      const iconContainer = button.querySelector('svg')?.parentElement;
      expect(iconContainer).not.toHaveClass('rounded-lg', 'border', 'bg-white');
      expect(iconContainer?.className).not.toContain('shadow-');
    }

    await user.click(workflowButton);
    expect(screen.queryByText('security')).not.toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: /Alert triage/i }));
    expect(screen.getByTestId('mock-chat-input')).toHaveTextContent('workflow:alert-triage');

    await user.click(screen.getByRole('button', { name: 'mock-open-add-menu' }));
    await user.click(screen.getByRole('button', { name: 'chat.addMenu.skills' }));
    await user.click(await screen.findByRole('button', { name: /diagnose/i }));
    expect(screen.getByTestId('mock-chat-input')).toHaveTextContent('skill:diagnose');
    expect(screen.getByText('chat.addMenu.selectSkill')).toBeInTheDocument();
  });

  it('updates permission, runtime and network independently from portaled menus with the current revision', async () => {
    const user = userEvent.setup();
    client.get.mockImplementation((url: string) => {
      if (url === '/api/flockspro/license/status') {
        return Promise.resolve({ data: { pro_enabled: true } });
      }
      if (url.endsWith('/execution-settings')) {
        return Promise.resolve({
          data: {
            permissionMode: 'require-confirm',
            runtimeMode: 'dev-mode',
            networkMode: 'require-confirm',
            networkModeDefault: 'require-confirm',
            networkModeOverridden: false,
            entry: 'webui',
            revision: 7,
          },
        });
      }
      return Promise.resolve({
        data: [{
          id: 'default',
          worktree: '/tmp/project',
          name: '默认',
          isDefault: true,
          pathStatus: 'available',
          sessionCount: 1,
        }],
      });
    });
    client.patch
      .mockResolvedValueOnce({
        data: {
          permissionMode: 'readonly',
          runtimeMode: 'dev-mode',
          networkMode: 'require-confirm',
          networkModeDefault: 'require-confirm',
          networkModeOverridden: false,
          entry: 'webui',
          revision: 8,
        },
      })
      .mockResolvedValueOnce({
        data: {
          permissionMode: 'readonly',
          runtimeMode: 'exe-mode',
          networkMode: 'require-confirm',
          networkModeDefault: 'require-confirm',
          networkModeOverridden: false,
          entry: 'webui',
          revision: 9,
        },
      })
      .mockResolvedValueOnce({
        data: {
          permissionMode: 'readonly',
          runtimeMode: 'exe-mode',
          networkMode: 'auto-deny-all',
          networkModeDefault: 'require-confirm',
          networkModeOverridden: true,
          entry: 'webui',
          revision: 10,
        },
      });

    renderSessionPage('/sessions?session=session-1');

    const selector = await waitFor(() => {
      const element = document.querySelector('[data-permission-mode-selector]');
      expect(element).not.toBeNull();
      return element as HTMLElement;
    });
    await user.click(within(selector).getByRole('button', { name: /permissionMode\.requireConfirm/ }));
    await user.click(screen.getByRole('button', { name: /permissionMode\.readonlyDesc/ }));

    await waitFor(() => {
      expect(client.patch).toHaveBeenNthCalledWith(
        1,
        '/api/flockspro/policy/sessions/session-1/execution-settings',
        { permissionMode: 'readonly', revision: 7 },
      );
    });

    await user.click(within(selector).getByRole('button', { name: /permissionMode\.readonly/ }));
    await user.click(screen.getByRole('button', { name: /permissionMode\.runtimeExeDesc/ }));

    await waitFor(() => {
      expect(client.patch).toHaveBeenNthCalledWith(
        2,
        '/api/flockspro/policy/sessions/session-1/execution-settings',
        { runtimeMode: 'exe-mode', revision: 8 },
      );
    });

    await user.click(within(selector).getByRole('button', { name: /permissionMode\.readonly/ }));
    await user.click(screen.getByRole('button', { name: /permissionMode\.networkAutoDenyAllDesc/ }));

    await waitFor(() => {
      expect(client.patch).toHaveBeenNthCalledWith(
        3,
        '/api/flockspro/policy/sessions/session-1/execution-settings',
        { networkMode: 'auto-deny-all', revision: 9 },
      );
    });
    expect(screen.queryByText('permissionMode.runtimeTitle')).not.toBeInTheDocument();
  });
});
