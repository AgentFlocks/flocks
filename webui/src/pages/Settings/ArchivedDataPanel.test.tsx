import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ArchivedDataPanel from './ArchivedDataPanel';

const { sessionApi, toast } = vi.hoisted(() => ({
  sessionApi: {
    list: vi.fn(),
    restore: vi.fn(),
    delete: vi.fn(),
  },
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

vi.mock('@/api/session', () => ({ sessionApi }));
vi.mock('@/components/common/Toast', () => ({ useToast: () => toast }));

const archivedSession = {
  id: 'session-archived',
  slug: 'archived',
  projectID: 'project-1',
  projectName: 'Security Project',
  effectiveProjectID: 'project-1',
  directory: '/tmp/project',
  title: 'Archived Session',
  version: '1.0.0',
  category: 'user',
  status: 'archived',
  canDelete: true,
  ownerUserID: 'user-alice',
  ownerUsername: 'alice',
  time: {
    created: 1710000000000,
    updated: 1710000100000,
    archived: 1710000200000,
  },
};

describe('ArchivedDataPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionApi.list.mockResolvedValue([archivedSession]);
    sessionApi.restore.mockResolvedValue({ ...archivedSession, status: 'active' });
    sessionApi.delete.mockResolvedValue(true);
  });

  it('loads archived sessions and restores one', async () => {
    const user = userEvent.setup();
    render(<ArchivedDataPanel />);

    expect(await screen.findByText('Archived Session')).toBeInTheDocument();
    expect(sessionApi.list).toHaveBeenCalledWith(expect.objectContaining({
      status: 'archived',
      manager: true,
      roots: true,
    }));
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.getByText('Security Project')).toBeInTheDocument();
    expect(screen.queryByText('project-1')).not.toBeInTheDocument();
    expect(screen.queryByText('user')).not.toBeInTheDocument();

    const tableHeader = screen.getByText('archivedData.session').parentElement;
    expect(tableHeader).toHaveClass('text-sm', 'font-normal');
    expect(tableHeader).not.toHaveClass('text-xs', 'font-semibold');
    expect(screen.getByText('Archived Session')).toHaveClass('text-sm', 'font-normal');
    expect(screen.getByText('alice')).toHaveClass('text-sm', 'font-normal');
    expect(screen.getByText('Security Project')).toHaveClass('text-sm', 'font-normal');

    const refreshButton = screen.getByRole('button', { name: 'archivedData.refresh' });
    expect(refreshButton.textContent).toBe('');

    const restoreButton = screen.getByRole('button', { name: 'archivedData.restore' });
    expect(restoreButton).toHaveTextContent('archivedData.restore');
    expect(restoreButton).toHaveClass('text-sm', 'font-normal', 'text-blue-600', 'hover:text-blue-700');
    expect(screen.getByRole('button', { name: 'archivedData.delete' })).toHaveClass('text-sm', 'font-normal');
    await user.click(restoreButton);

    await waitFor(() => expect(sessionApi.restore).toHaveBeenCalledWith('session-archived'));
    expect(screen.queryByText('Archived Session')).not.toBeInTheDocument();
  });

  it('requires a dialog confirmation before permanent deletion', async () => {
    const user = userEvent.setup();
    render(<ArchivedDataPanel />);

    await screen.findByText('Archived Session');
    const deleteButton = screen.getByRole('button', { name: 'archivedData.delete' });
    expect(deleteButton).toHaveTextContent('archivedData.delete');
    await user.click(deleteButton);

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(sessionApi.delete).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'archivedData.confirmDelete' }));

    await waitFor(() => expect(sessionApi.delete).toHaveBeenCalledWith('session-archived'));
    expect(screen.queryByText('Archived Session')).not.toBeInTheDocument();
  });

  it('keeps failed rows after a partial bulk restore', async () => {
    const user = userEvent.setup();
    const failedSession = {
      ...archivedSession,
      id: 'session-failed',
      title: 'Failed Session',
    };
    sessionApi.list.mockResolvedValue([archivedSession, failedSession]);
    sessionApi.restore.mockImplementation((id: string) => (
      id === failedSession.id
        ? Promise.reject(new Error('restore failed'))
        : Promise.resolve({ ...archivedSession, status: 'active' })
    ));
    render(<ArchivedDataPanel />);

    await screen.findByText('Failed Session');
    await user.click(screen.getByRole('checkbox', { name: 'archivedData.selectAll' }));
    await user.click(screen.getByRole('button', { name: 'archivedData.restoreSelected' }));

    await waitFor(() => expect(screen.queryByText('Archived Session')).not.toBeInTheDocument());
    expect(screen.getByText('Failed Session')).toBeInTheDocument();
    expect(toast.success).toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalled();
  });

  it('ignores an older response after a newer search completes', async () => {
    const user = userEvent.setup();
    let resolveInitial!: (value: unknown[]) => void;
    let resolveSearch!: (value: unknown[]) => void;
    sessionApi.list
      .mockImplementationOnce(() => new Promise((resolve) => { resolveInitial = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveSearch = resolve; }));
    render(<ArchivedDataPanel />);

    await user.type(screen.getByRole('textbox', { name: 'archivedData.searchPlaceholder' }), 'new');
    await waitFor(() => expect(sessionApi.list).toHaveBeenCalledTimes(2));
    await act(async () => {
      resolveSearch([{ ...archivedSession, id: 'session-new', title: 'New Result' }]);
    });
    expect(await screen.findByText('New Result')).toBeInTheDocument();

    await act(async () => {
      resolveInitial([{ ...archivedSession, id: 'session-old', title: 'Old Result' }]);
    });
    expect(screen.queryByText('Old Result')).not.toBeInTheDocument();
    expect(screen.getByText('New Result')).toBeInTheDocument();
  });

  it('labels legacy ownerless sessions as system tasks', async () => {
    sessionApi.list.mockResolvedValue([{
      ...archivedSession,
      ownerUserID: undefined,
      ownerUsername: undefined,
    }]);

    render(<ArchivedDataPanel />);

    expect(await screen.findByText('archivedData.systemOwner')).toBeInTheDocument();
    expect(screen.queryByText('user')).not.toBeInTheDocument();
  });

  it('localizes the API service owner as a system task', async () => {
    sessionApi.list.mockResolvedValue([{
      ...archivedSession,
      ownerUserID: 'api-token-service',
      ownerUsername: 'api-token-service',
    }]);

    render(<ArchivedDataPanel />);

    expect(await screen.findByText('archivedData.systemOwner')).toBeInTheDocument();
    expect(screen.queryByText('api-token-service')).not.toBeInTheDocument();
  });
});
