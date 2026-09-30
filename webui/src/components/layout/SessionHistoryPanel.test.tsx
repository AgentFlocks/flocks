import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import SessionHistoryPanel, { sessionIdFromLocation } from './SessionHistoryPanel';

const {
  apiGet, apiDelete, apiPost, sessionApi, useSessionsMock, navigateMock,
  confirmMock, toastMock, useAuth,
} = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiDelete: vi.fn(),
  apiPost: vi.fn(),
  sessionApi: {
    list: vi.fn(), create: vi.fn(), update: vi.fn(), archive: vi.fn(),
    moveToProject: vi.fn(), shareLocal: vi.fn(), unshareLocal: vi.fn(),
    get: vi.fn(), getMessages: vi.fn(),
  },
  useSessionsMock: vi.fn(),
  navigateMock: vi.fn(),
  confirmMock: vi.fn(() => Promise.resolve(true)),
  toastMock: { success: vi.fn(), error: vi.fn() },
  useAuth: vi.fn(() => ({ user: { id: 'u1', username: 'admin', role: 'admin' } })),
}));

vi.mock('@/api/client', () => ({ default: { get: apiGet, delete: apiDelete, post: apiPost } }));
vi.mock('@/api/session', () => ({ sessionApi }));
vi.mock('@/hooks/useSessions', () => ({ useSessions: useSessionsMock }));
vi.mock('@/hooks/useSSE', () => ({ useSSE: vi.fn() }));
vi.mock('@/contexts/AuthContext', () => ({ useAuth }));
vi.mock('@/components/common/Toast', () => ({ useToast: () => toastMock }));
vi.mock('@/components/common/ConfirmDialog', () => ({ useConfirm: () => confirmMock }));
vi.mock('./ProjectFolderDialog', () => ({
  default: ({ mode, onSaved }: any) => (
    <div role="dialog" aria-label="folder-dialog">
      <span>{mode}</span>
      <button onClick={() => onSaved({ id: 'prj_new', name: '新建', worktree: '/root/new' })}>save-folder</button>
    </div>
  ),
  pathBasename: (p: string) => p.split('/').pop() || p,
}));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<any>('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, o?: Record<string, string>) => (o?.folder ? `${key}:${o.folder}` : o?.title ? `${key}:${o.title}` : key),
    i18n: { language: 'zh-CN' },
  }),
}));

const session = (id: string, projectID: string, title = id) => ({
  id, title, projectID, effectiveProjectID: projectID, isShared: false, canWrite: true, canDelete: true,
});

function setSessions(sessions: any[], extra: Record<string, any> = {}) {
  useSessionsMock.mockReturnValue({
    sessions, loading: false, refetch: vi.fn(), updateSessionTitle: vi.fn(), removeSession: vi.fn(),
    hasMoreByProject: {}, loadingMoreProjectIds: new Set(), loadMore: vi.fn(), ...extra,
  });
}

function renderPanel() {
  return render(<MemoryRouter initialEntries={['/sessions/s1']}><SessionHistoryPanel collapsed={false} onExpandSidebar={vi.fn()} onNavigate={vi.fn()} /></MemoryRouter>);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  apiGet.mockImplementation((url: string) => {
    if (url === '/api/project') return Promise.resolve({ data: [{ id: 'prj_a', name: '产品设计', worktree: '/root/a', pathStatus: 'available', canWrite: true }] });
    if (url === '/api/session/status') return Promise.resolve({ data: {} });
    return Promise.resolve({ data: {} });
  });
  apiDelete.mockResolvedValue({ data: true });
  apiPost.mockResolvedValue({ data: {} });
  sessionApi.create.mockResolvedValue({ id: 's_new' });
  sessionApi.moveToProject.mockResolvedValue({});
  confirmMock.mockResolvedValue(true);
  setSessions([session('s1', 'prj_a', '会话一'), session('s2', '')]);
});

describe('sessionIdFromLocation', () => {
  it('reads the id from the path and the legacy query', () => {
    expect(sessionIdFromLocation('/sessions/abc', '')).toBe('abc');
    expect(sessionIdFromLocation('/sessions', '?session=xyz')).toBe('xyz');
    expect(sessionIdFromLocation('/tasks', '')).toBeNull();
  });
});

describe('SessionHistoryPanel', () => {
  it('groups sessions by folder and lists the ungrouped bucket', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText('产品设计')).toBeInTheDocument());
    expect(screen.getByText('会话一')).toBeInTheDocument();
    expect(screen.getByText('history.ungrouped')).toBeInTheDocument();
    expect(screen.getByText('s2')).toBeInTheDocument();
  });

  it('creates a session inside a folder', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText('产品设计')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'history.newSessionIn:产品设计' }));
    await waitFor(() => expect(sessionApi.create).toHaveBeenCalledWith({ title: 'New Session', projectID: 'prj_a' }));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/sessions/s_new'));
  });

  it('removes a folder while keeping its sessions', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText('产品设计')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'history.folderActions:产品设计' }));
    fireEvent.click(screen.getByRole('button', { name: /history.removeFolder$/ }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/api/project/prj_a', { params: { keepSessions: true } }));
  });

  it('pins a folder to the top and persists it', async () => {
    setSessions([session('s1', 'prj_a'), session('s3', 'prj_b')]);
    apiGet.mockImplementation((url: string) => {
      if (url === '/api/project') return Promise.resolve({ data: [
        { id: 'prj_a', name: 'A 目录', worktree: '/root/a', pathStatus: 'available', canWrite: true },
        { id: 'prj_b', name: 'B 目录', worktree: '/root/b', pathStatus: 'available', canWrite: true },
      ] });
      if (url === '/api/session/status') return Promise.resolve({ data: {} });
      return Promise.resolve({ data: {} });
    });
    renderPanel();
    await waitFor(() => expect(screen.getByText('B 目录')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'history.folderActions:B 目录' }));
    fireEvent.click(screen.getByRole('button', { name: /history.pin$/ }));
    await waitFor(() => {
      const names = screen.getAllByText(/目录$/).map((el) => el.textContent);
      expect(names[0]).toBe('B 目录');
    });
    expect(localStorage.getItem('flocks:history:pinned-folders:u1')).toContain('prj_b');
  });

  it('archives a session after confirmation', async () => {
    const removeSession = vi.fn();
    setSessions([session('s1', 'prj_a', '会话一'), session('s2', '')], { removeSession });
    sessionApi.archive.mockResolvedValue({});
    renderPanel();
    await waitFor(() => expect(screen.getByText('会话一')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'history.sessionActions:会话一' }));
    fireEvent.click(screen.getByRole('button', { name: 'history.archive' }));
    await waitFor(() => expect(sessionApi.archive).toHaveBeenCalledWith('s1'));
    await waitFor(() => expect(removeSession).toHaveBeenCalledWith('s1'));
  });

  it('searches sessions by title', async () => {
    sessionApi.list.mockResolvedValue([session('s9', 'prj_a', '周报生成')]);
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: 'history.search' }));
    fireEvent.change(screen.getByPlaceholderText('history.searchPlaceholder'), { target: { value: '周报' } });
    await waitFor(() => expect(sessionApi.list).toHaveBeenCalledWith(expect.objectContaining({ search: '周报' })));
    await waitFor(() => expect(screen.getByText('周报生成')).toBeInTheDocument());
  });

  it('collapses to icons only', () => {
    render(<MemoryRouter><SessionHistoryPanel collapsed onExpandSidebar={vi.fn()} onNavigate={vi.fn()} /></MemoryRouter>);
    expect(screen.getByRole('button', { name: 'history.title' })).toBeInTheDocument();
    expect(screen.queryByText('history.ungrouped')).not.toBeInTheDocument();
  });
});
