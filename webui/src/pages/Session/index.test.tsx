import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import SessionPage from './index';

const {
  sessionApi,
  updateSessionTitle,
  removeSession,
  removeSessions,
  addSession,
  refetchSessions,
  useSessions,
  useAgents,
  toast,
} = vi.hoisted(() => ({
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
  toast: {
    error: vi.fn(),
    info: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock('@/api/session', () => ({
  sessionApi,
}));

vi.mock('@/hooks/useSessions', () => ({
  useSessions,
}));

vi.mock('@/hooks/useAgents', () => ({
  useAgents,
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => toast,
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading-spinner</div>,
}));

vi.mock('@/components/common/SessionChat', () => ({
  __esModule: true,
  default: ({ sessionId }: { sessionId?: string | null }) => (
    <div data-testid="session-chat">{sessionId ?? 'no-session'}</div>
  ),
}));

vi.mock('@/utils/agentDisplay', () => ({
  getAgentDisplayDescription: () => 'agent-description',
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

function renderSessionPage() {
  return render(
    <MemoryRouter initialEntries={['/sessions']}>
      <SessionPage />
    </MemoryRouter>,
  );
}

describe('SessionPage session actions menu', () => {
  beforeEach(() => {
    vi.clearAllMocks();

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

    sessionApi.update.mockResolvedValue({ ...session, title: 'Renamed Session' });
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

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveTextContent('session-1');
    });

    await user.click(screen.getByRole('button', { name: 'go-session-2' }));

    await waitFor(() => {
      expect(screen.getByTestId('session-chat')).toHaveTextContent('session-2');
    });
  });
});
