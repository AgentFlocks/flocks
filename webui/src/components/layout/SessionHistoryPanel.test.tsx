import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Link, MemoryRouter } from 'react-router-dom';
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
  sessionApi.get.mockRejectedValue(new Error('not loaded'));
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

  it('marks shared folders and sessions, including a folder that was just shared', async () => {
    // Regression (INC-BB67AF4D): the panel offered 本地共享 but never showed what was shared.
    let productShared = false;
    setSessions([
      session('s1', 'prj_a', '会话一'),
      { ...session('s3', 'prj_b', '共享目录里的会话'), isShared: true },
      { ...session('s4', '', '单独共享的会话'), isShared: true },
      session('s2', ''),
    ]);
    apiGet.mockImplementation((url: string) => {
      if (url === '/api/project') return Promise.resolve({ data: [
        { id: 'prj_a', name: '产品设计', worktree: '/root/a', pathStatus: 'available', canWrite: true, isShared: productShared },
        { id: 'prj_b', name: '共享目录', worktree: '/root/b', pathStatus: 'available', canWrite: true, isShared: true },
      ] });
      if (url === '/api/session/status') return Promise.resolve({ data: {} });
      return Promise.resolve({ data: {} });
    });
    apiPost.mockImplementation(async (url: string) => {
      if (url === '/api/project/prj_a/share-local') productShared = true;
      return { data: {} };
    });
    const rowOf = (text: string) => screen.getByText(text).closest('button') as HTMLElement;
    const mark = { name: 'history.sharedMark' };
    renderPanel();

    await waitFor(() => expect(within(rowOf('共享目录')).getByRole('img', mark)).toBeInTheDocument());
    // The mark takes room from the name; the full name stays in the tooltip.
    expect(rowOf('共享目录')).toHaveAttribute('title', '共享目录');
    expect(within(rowOf('产品设计')).queryByRole('img', mark)).toBeNull();
    // Shared through its folder: the folder carries the mark, the row does not repeat it.
    expect(within(rowOf('共享目录里的会话')).queryByRole('img', mark)).toBeNull();
    expect(within(rowOf('单独共享的会话')).getByRole('img', mark)).toBeInTheDocument();
    expect(within(rowOf('s2')).queryByRole('img', mark)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'history.folderActions:产品设计' }));
    fireEvent.click(screen.getByRole('button', { name: /history.share$/ }));
    await waitFor(() => expect(apiPost).toHaveBeenCalledWith('/api/project/prj_a/share-local'));
    await waitFor(() => expect(within(rowOf('产品设计')).getByRole('img', mark)).toBeInTheDocument());
    expect(toastMock.success).toHaveBeenCalledWith('history.shared');
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

  it('reveals folder actions only on their header hover or keyboard focus', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText('产品设计')).toBeInTheDocument());
    for (const name of ['history.folderActions:产品设计', 'history.newSessionIn:产品设计']) {
      const button = screen.getByRole('button', { name });
      expect(button).toHaveClass('opacity-0', 'pointer-events-none', 'group-hover/folder:opacity-100', 'focus-visible:opacity-100');
      expect(button.parentElement).toHaveClass('group/folder');
      expect(button.parentElement).not.toContainElement(screen.getByText('会话一'));
    }
  });

  it('deletes a project after confirmation and archives its sessions', async () => {
    const announced = vi.fn();
    window.addEventListener('flocks:projects-changed', announced);
    try {
      renderPanel();
      await waitFor(() => expect(screen.getByText('产品设计')).toBeInTheDocument());
      fireEvent.click(screen.getByRole('button', { name: 'history.folderActions:产品设计' }));
      fireEvent.click(screen.getByRole('button', { name: 'history.deleteFolder' }));
      await waitFor(() => expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({ variant: 'danger' })));
      await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/api/project/prj_a'));
      expect(toastMock.success).toHaveBeenCalledWith('history.folderDeleted');
      expect(announced).toHaveBeenCalled();
      // The open session lived in the deleted project, so the panel starts a new chat.
      expect(navigateMock).toHaveBeenCalledWith('/sessions', { state: { skipLastSelectedSessionRestore: true } });
    } finally {
      window.removeEventListener('flocks:projects-changed', announced);
    }
  });

  it('does not delete a project when the confirmation is cancelled', async () => {
    confirmMock.mockResolvedValueOnce(false);
    renderPanel();
    await waitFor(() => expect(screen.getByText('产品设计')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'history.folderActions:产品设计' }));
    fireEvent.click(screen.getByRole('button', { name: 'history.deleteFolder' }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    expect(apiDelete).not.toHaveBeenCalled();
  });

  it.each(['tasks', 'prj_a'])('reveals 50 more sessions per click in %s, including after collapse', async (sectionId) => {
    const loadMore = vi.fn();
    const sessions = Array.from({ length: 130 }, (_, i) => session(`u${i + 1}`, sectionId === 'tasks' ? '' : sectionId, `会话 ${i + 1}`));
    setSessions(sessions, { hasMoreByProject: { [sectionId]: true }, loadMore });
    render(<MemoryRouter initialEntries={['/sessions/u130']}><SessionHistoryPanel collapsed={false} onExpandSidebar={vi.fn()} onNavigate={vi.fn()} /></MemoryRouter>);
    // Every list starts with 20 sessions, including when project folders exist.
    await waitFor(() => expect(screen.getByText('产品设计')).toBeInTheDocument());
    expect(useSessionsMock).toHaveBeenLastCalledWith('', { projectIds: ['tasks', 'prj_a'], pageSize: 20, loadMorePageSize: 50 });
    expect(screen.getByText('会话 20')).toBeInTheDocument();
    expect(screen.queryByText('会话 21')).not.toBeInTheDocument();
    expect(screen.getByText('会话 130')).toBeInTheDocument(); // the open session stays visible
    expect(screen.queryByRole('button', { name: 'history.collapse' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'history.loadMore' }));
    expect(screen.getByText('会话 70')).toBeInTheDocument();
    expect(screen.queryByText('会话 71')).not.toBeInTheDocument();
    expect(loadMore).not.toHaveBeenCalled(); // reveal cached rows before requesting another page
    fireEvent.click(screen.getByRole('button', { name: 'history.loadMore' }));
    expect(screen.getByText('会话 120')).toBeInTheDocument();
    expect(screen.queryByText('会话 121')).not.toBeInTheDocument();
    expect(loadMore).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'history.loadMore' }));
    expect(loadMore).toHaveBeenCalledWith(sectionId);

    fireEvent.click(screen.getByRole('button', { name: 'history.collapse' }));
    expect(screen.queryByText('会话 21')).not.toBeInTheDocument();
    expect(screen.getByText('会话 130')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'history.loadMore' }));
    expect(screen.getByText('会话 70')).toBeInTheDocument();
    expect(screen.queryByText('会话 71')).not.toBeInTheDocument();
    expect(loadMore).toHaveBeenCalledOnce();
  });

  it('archives selected sessions in batch mode', async () => {
    const removeSession = vi.fn();
    setSessions([session('s1', 'prj_a', '会话一'), session('s2', '', '会话二'), session('s3', '', '会话三')], { removeSession });
    sessionApi.archive.mockResolvedValue({});
    renderPanel();
    await waitFor(() => expect(screen.getByText('会话二')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'history.selectMode' }));
    const archive = screen.getByRole('button', { name: 'history.archive' });
    expect(archive).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: '会话二' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '会话三' }));
    fireEvent.click(archive);
    await waitFor(() => expect(sessionApi.archive).toHaveBeenCalledTimes(2));
    expect(sessionApi.archive).toHaveBeenCalledWith('s2');
    expect(sessionApi.archive).toHaveBeenCalledWith('s3');
    expect(removeSession).toHaveBeenCalledWith('s2');
    expect(toastMock.success).toHaveBeenCalledWith('history.archivedCount');
    await waitFor(() => expect(screen.queryByRole('checkbox')).not.toBeInTheDocument());
  });

  it('archives a long selection a few at a time and reports the ones that failed', async () => {
    const removeSession = vi.fn();
    const items = Array.from({ length: 6 }, (_, index) => session(`b${index}`, '', `批量${index}`));
    setSessions(items, { removeSession });
    const pending: Array<{ id: string; resolve: () => void; reject: () => void }> = [];
    sessionApi.archive.mockImplementation((id: string) => new Promise<void>((resolve, reject) => {
      pending.push({ id, resolve, reject: () => reject(new Error('busy')) });
    }));
    renderPanel();
    await waitFor(() => expect(screen.getByText('批量0')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'history.selectMode' }));
    for (const item of items) fireEvent.click(screen.getByRole('checkbox', { name: item.title }));
    fireEvent.click(screen.getByRole('button', { name: 'history.archive' }));
    // At most four requests are in flight; the rest start as earlier ones finish.
    await waitFor(() => expect(sessionApi.archive).toHaveBeenCalledTimes(4));
    pending.find((call) => call.id === 'b1')!.reject();
    pending.find((call) => call.id === 'b0')!.resolve();
    await waitFor(() => expect(sessionApi.archive).toHaveBeenCalledTimes(6));
    for (const call of pending.filter((entry) => entry.id !== 'b0' && entry.id !== 'b1')) call.resolve();
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('history.archiveFailed', '批量1'));
    expect(removeSession).toHaveBeenCalledTimes(5);
    expect(removeSession).not.toHaveBeenCalledWith('b1');
    expect(toastMock.success).not.toHaveBeenCalled();
  });

  it('leaves the open session once it is archived even when another selected one fails', async () => {
    setSessions([session('s1', '', '当前会话'), session('s2', '', '其它会话')]);
    sessionApi.archive.mockImplementation((id: string) => (id === 's2' ? Promise.reject(new Error('busy')) : Promise.resolve({})));
    renderPanel();
    await waitFor(() => expect(screen.getByText('当前会话')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'history.selectMode' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '当前会话' }));
    fireEvent.click(screen.getByRole('checkbox', { name: '其它会话' }));
    fireEvent.click(screen.getByRole('button', { name: 'history.archive' }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalledWith('history.archiveFailed', '其它会话'));
    expect(navigateMock).toHaveBeenCalledWith('/sessions', { state: { skipLastSelectedSessionRestore: true } });
  });

  it('lists the open session from an older page and leaves it when its folder is deleted', async () => {
    setSessions([session('s9', 'prj_a', '新会话')]);
    sessionApi.get.mockResolvedValue({ ...session('s1', 'prj_a', '很早的会话') });
    renderPanel();
    // Not in the loaded page, still shown (under its folder) because it is open.
    expect(await screen.findByText('很早的会话')).toBeInTheDocument();
    expect(sessionApi.get).toHaveBeenCalledWith('s1');
    fireEvent.click(screen.getByRole('button', { name: 'history.folderActions:产品设计' }));
    fireEvent.click(await screen.findByRole('button', { name: 'history.deleteFolder' }));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/api/project/prj_a'));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/sessions', { state: { skipLastSelectedSessionRestore: true } }));
  });

  it('leaves the open older session when its folder is deleted even if its lookup was still pending', async () => {
    setSessions([session('s9', 'prj_a', '新会话')]);
    // The list-side lookup never answers; the delete reads the session itself after confirming.
    sessionApi.get.mockReturnValueOnce(new Promise(() => {}))
      .mockResolvedValueOnce({ ...session('s1', 'prj_a', '很早的会话') });
    renderPanel();
    await waitFor(() => expect(screen.getByText('新会话')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'history.folderActions:产品设计' }));
    fireEvent.click(await screen.findByRole('button', { name: 'history.deleteFolder' }));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/api/project/prj_a'));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/sessions', { state: { skipLastSelectedSessionRestore: true } }));
    // The pending list lookup did not decide it: the delete read the session itself.
    expect(sessionApi.get.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('checks the open session after the delete when its folder could not be read first', async () => {
    setSessions([session('s9', 'prj_a', '新会话')]);
    // As the API answers once the folder delete archived it: 404.
    const archivedNow = Object.assign(new Error('Session not found'), { response: { status: 404 } });
    sessionApi.get.mockRejectedValueOnce(new Error('list lookup failed'))
      .mockRejectedValueOnce(new Error('folder lookup failed'))
      .mockRejectedValue(archivedNow);
    renderPanel();
    await waitFor(() => expect(screen.getByText('新会话')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'history.folderActions:产品设计' }));
    fireEvent.click(await screen.findByRole('button', { name: 'history.deleteFolder' }));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/api/project/prj_a'));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/sessions', { state: { skipLastSelectedSessionRestore: true } }));
  });

  it('does not leave a session the user opened while the post-delete check was pending', async () => {
    setSessions([session('s9', 'prj_a', '新会话'), session('s2', '', '另一个会话')]);
    let answerCheck: (() => void) | undefined;
    sessionApi.get.mockRejectedValueOnce(new Error('list lookup failed'))
      .mockRejectedValueOnce(new Error('folder lookup failed'))
      .mockImplementationOnce(() => new Promise((_, reject) => {
        answerCheck = () => reject(Object.assign(new Error('Session not found'), { response: { status: 404 } }));
      }))
      .mockRejectedValue(new Error('not loaded'));
    render(
      <MemoryRouter initialEntries={['/sessions/s1']}>
        <SessionHistoryPanel collapsed={false} onExpandSidebar={vi.fn()} onNavigate={vi.fn()} />
        <Link to="/sessions/s2">open-b</Link>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('新会话')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'history.folderActions:产品设计' }));
    fireEvent.click(await screen.findByRole('button', { name: 'history.deleteFolder' }));
    await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/api/project/prj_a'));
    await waitFor(() => expect(answerCheck).toBeDefined());
    fireEvent.click(screen.getByText('open-b'));
    await act(async () => { answerCheck?.(); });
    expect(navigateMock).not.toHaveBeenCalled();
  });

  describe('when the user opens another session while the folder delete is out', () => {
    const notFound = () => Object.assign(new Error('Session not found'), { response: { status: 404 } });
    const startDelete = async (to: string) => {
      let finishDelete: (() => void) | undefined;
      apiDelete.mockImplementationOnce(() => new Promise((resolve) => {
        finishDelete = () => resolve({ data: true });
      }));
      render(
        <MemoryRouter initialEntries={['/sessions/s1']}>
          <SessionHistoryPanel collapsed={false} onExpandSidebar={vi.fn()} onNavigate={vi.fn()} />
          <Link to={to}>open-other</Link>
        </MemoryRouter>,
      );
      await waitFor(() => expect(screen.getByText('产品设计')).toBeInTheDocument());
      fireEvent.click(screen.getByRole('button', { name: 'history.folderActions:产品设计' }));
      fireEvent.click(await screen.findByRole('button', { name: 'history.deleteFolder' }));
      await waitFor(() => expect(apiDelete).toHaveBeenCalledWith('/api/project/prj_a'));
      fireEvent.click(screen.getByText('open-other'));
      await act(async () => { finishDelete?.(); });
    };

    it('leaves it when it lived in the deleted folder too', async () => {
      setSessions([session('s1', 'prj_a', '会话一'), session('s3', 'prj_a', '会话三'), session('s2', '')]);
      await startDelete('/sessions/s3');
      await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/sessions', { state: { skipLastSelectedSessionRestore: true } }));
    });

    it('stays on it when it lives elsewhere', async () => {
      setSessions([session('s1', 'prj_a', '会话一'), session('s2', '')]);
      await startDelete('/sessions/s2');
      await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('history.folderDeleted'));
      expect(navigateMock).not.toHaveBeenCalled();
    });

    it('reads a session missing from the list after the delete and leaves it once archived', async () => {
      setSessions([session('s1', 'prj_a', '会话一'), session('s2', '')]);
      sessionApi.get.mockImplementation((id: string) => Promise.reject(id === 's7' ? notFound() : new Error('not loaded')));
      await startDelete('/sessions/s7');
      await waitFor(() => expect(navigateMock).toHaveBeenCalledWith('/sessions', { state: { skipLastSelectedSessionRestore: true } }));
    });
  });

  it('opens the row menu of the open older session and renames it in place', async () => {
    setSessions([session('s9', 'prj_a', '新会话')]);
    sessionApi.get.mockResolvedValue({ ...session('s1', 'prj_a', '很早的会话') });
    sessionApi.update.mockResolvedValue({});
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'history.sessionActions:很早的会话' }));
    fireEvent.click(await screen.findByRole('button', { name: 'history.rename' }));
    const input = await screen.findByDisplayValue('很早的会话');
    fireEvent.change(input, { target: { value: '改过的标题' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(sessionApi.update).toHaveBeenCalledWith('s1', { title: '改过的标题' }));
    expect(await screen.findByText('改过的标题')).toBeInTheDocument();
  });

  it('does not list an archived or sub-agent session that happens to be open', async () => {
    setSessions([session('s9', 'prj_a', '新会话')]);
    sessionApi.get.mockResolvedValue({ ...session('s1', 'prj_a', '已归档会话'), status: 'archived' });
    renderPanel();
    await waitFor(() => expect(sessionApi.get).toHaveBeenCalledWith('s1'));
    expect(screen.queryByText('已归档会话')).not.toBeInTheDocument();
  });

  it('renders row menus on the document body so the transformed sidebar cannot offset them', async () => {
    renderPanel();
    await waitFor(() => expect(screen.getByText('会话一')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'history.sessionActions:会话一' }));
    const menu = document.querySelector('[data-history-menu]');
    expect(menu?.parentElement).toBe(document.body);
  });
});
