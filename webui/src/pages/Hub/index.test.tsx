import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import HubPage from './index';

const { hubAPI, toastError } = vi.hoisted(() => ({
  hubAPI: {
    catalog: vi.fn(),
    catalogPage: vi.fn(),
    categories: vi.fn(),
    refresh: vi.fn(),
    install: vi.fn(),
    installStream: vi.fn(),
    update: vi.fn(),
    uninstall: vi.fn(),
    get: vi.fn(),
    files: vi.fn(),
    fileContent: vi.fn(),
  },
  toastError: vi.fn(),
}));

vi.mock('@/api/hub', () => ({ hubAPI }));
vi.mock('@/hooks/useDebouncedValue', () => ({
  useDebouncedValue: <T,>(value: T) => value,
}));
vi.mock('@/contexts/ProductNameContext', () => ({
  useProductName: () => ({ productName: 'Flocks' }),
}));
vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'admin-1', role: 'admin' } }),
}));
vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({ error: toastError }),
}));
vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading</div>,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'en-US' },
  }),
}));

const emptyFacets = {
  type: {},
  category: {},
  tags: {},
  useCases: {},
  state: {},
  trust: {},
  riskLevel: {},
};

function catalogEntry(id: string, name: string, manifestPath = `${id}/manifest.json`) {
  return {
    id,
    type: 'tool' as const,
    name,
    description: `${name} description`,
    version: '1.0.0',
    category: 'security',
    tags: ['security'],
    useCases: ['investigation'],
    domains: [],
    capabilities: [],
    trust: 'verified',
    riskLevel: 'low',
    state: 'available' as const,
    source: 'bundled',
    manifestPath,
    native: false,
  };
}

function catalogPage(items: ReturnType<typeof catalogEntry>[], total = items.length) {
  return {
    data: {
      items,
      total,
      offset: 0,
      limit: 25,
      facets: emptyFacets,
    },
  };
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

function renderHub() {
  return render(
    <MemoryRouter>
      <HubPage />
    </MemoryRouter>,
  );
}

describe('HubPage catalog loading', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    hubAPI.categories.mockResolvedValue({
      data: { categories: [], tags: [], useCases: [] },
    });
    hubAPI.catalog.mockResolvedValue({ data: [] });
    hubAPI.files.mockResolvedValue({
      data: { name: 'root', path: '', type: 'directory', size: 0, previewable: false, children: [] },
    });
  });

  it('ignores an older search response that finishes after the latest query', async () => {
    const oldSearch = deferred<ReturnType<typeof catalogPage>>();
    const latestSearch = deferred<ReturnType<typeof catalogPage>>();
    hubAPI.catalogPage
      .mockResolvedValueOnce(catalogPage([catalogEntry('initial', 'Initial result')]))
      .mockImplementationOnce(() => oldSearch.promise)
      .mockImplementationOnce(() => latestSearch.promise);

    renderHub();
    expect(await screen.findByText('Initial result')).toBeInTheDocument();

    const search = screen.getByPlaceholderText('Search plugin name, description, tag, use case');
    fireEvent.change(search, { target: { value: 'old' } });
    await waitFor(() => expect(hubAPI.catalogPage).toHaveBeenCalledTimes(2));
    fireEvent.change(search, { target: { value: 'latest' } });
    await waitFor(() => expect(hubAPI.catalogPage).toHaveBeenCalledTimes(3));

    await act(async () => {
      latestSearch.resolve(catalogPage([catalogEntry('latest', 'Latest result')]));
    });
    expect(await screen.findByText('Latest result')).toBeInTheDocument();

    await act(async () => {
      oldSearch.resolve(catalogPage([catalogEntry('old', 'Old result')]));
    });

    expect(screen.getByText('Latest result')).toBeInTheDocument();
    expect(screen.queryByText('Old result')).not.toBeInTheDocument();
    expect(hubAPI.catalogPage).toHaveBeenNthCalledWith(
      3,
      expect.objectContaining({ q: 'latest' }),
    );
  });

  it('loads the complete unpaged catalog for directory view', async () => {
    const user = userEvent.setup();
    const pagedEntry = catalogEntry('paged', 'Paged Entry', 'paged-tree/manifest.json');
    const beyondPage = catalogEntry('complete', 'Complete Entry', 'complete-tree/manifest.json');
    hubAPI.catalogPage.mockResolvedValue(catalogPage([pagedEntry], 854));
    hubAPI.catalog.mockResolvedValue({ data: [pagedEntry, beyondPage] });

    renderHub();
    expect(await screen.findByText('Paged Entry')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Directory View' }));

    expect(await screen.findByText('complete-tree')).toBeInTheDocument();
    expect(screen.getByText('paged-tree')).toBeInTheDocument();
    expect(hubAPI.catalogPage).toHaveBeenCalledTimes(1);
    expect(hubAPI.catalogPage).toHaveBeenCalledWith(
      expect.objectContaining({ offset: 0, limit: 25 }),
    );
    expect(hubAPI.catalog).toHaveBeenCalledWith({
      q: undefined,
      type: undefined,
      useCases: undefined,
      tags: undefined,
      state: undefined,
    });
  });

  it('refreshes the selected entity after an action even when it leaves the filtered page', async () => {
    const user = userEvent.setup();
    const available = catalogEntry('action-entry', 'Action Entry');
    const installed = { ...available, state: 'installed' as const, installedVersion: '1.0.0' };
    hubAPI.catalogPage
      .mockResolvedValueOnce(catalogPage([available]))
      .mockResolvedValueOnce(catalogPage([]));
    hubAPI.catalog.mockResolvedValue({ data: [installed] });
    // The manifest endpoint intentionally has no dynamic catalog state.
    hubAPI.get.mockResolvedValueOnce({ data: available });
    hubAPI.install.mockResolvedValue({ data: installed });

    renderHub();
    await user.click(await screen.findByText('Action Entry'));
    await waitFor(() => expect(hubAPI.get).toHaveBeenCalledTimes(1));

    const installButtons = screen.getAllByRole('button', { name: 'Install' });
    await user.click(installButtons[installButtons.length - 1]);

    await waitFor(() => expect(hubAPI.install).toHaveBeenCalledWith('tool', 'action-entry'));
    expect(hubAPI.catalog).toHaveBeenCalledWith({ q: 'action-entry', type: 'tool' });
    expect(hubAPI.get).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole('button', { name: 'Uninstall' })).toBeInTheDocument();
    expect(screen.getByText('Installed')).toBeInTheDocument();
  });

  it('does not let a table action started under an old query overwrite the latest query', async () => {
    const user = userEvent.setup();
    const actionEntry = catalogEntry('action-entry', 'Action Entry');
    const latestEntry = catalogEntry('latest-entry', 'Latest Entry');
    const installedEntry = { ...actionEntry, state: 'installed' as const, installedVersion: '1.0.0' };
    const install = deferred<void>();
    hubAPI.catalogPage.mockImplementation(({ q }: { q?: string }) => (
      Promise.resolve(q === 'latest' ? catalogPage([latestEntry]) : catalogPage([actionEntry]))
    ));
    hubAPI.install.mockReturnValue(install.promise);
    hubAPI.catalog.mockResolvedValue({ data: [installedEntry] });

    renderHub();
    await user.click(await screen.findByRole('button', { name: 'Install' }));
    await waitFor(() => expect(hubAPI.install).toHaveBeenCalledWith('tool', 'action-entry'));

    fireEvent.change(
      screen.getByPlaceholderText('Search plugin name, description, tag, use case'),
      { target: { value: 'latest' } },
    );
    expect(await screen.findByText('Latest Entry')).toBeInTheDocument();

    await act(async () => {
      install.resolve();
      await install.promise;
    });

    await waitFor(() => {
      expect(hubAPI.catalog).toHaveBeenCalledWith({ q: 'action-entry', type: 'tool' });
    });
    expect(screen.getByText('Latest Entry')).toBeInTheDocument();
    expect(screen.queryByText('Action Entry')).not.toBeInTheDocument();
    expect(hubAPI.catalogPage).toHaveBeenCalledTimes(2);
    expect(hubAPI.catalogPage).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'latest' }));
  });

  it('does not let a refresh started under an old query overwrite the latest query', async () => {
    const user = userEvent.setup();
    const initialEntry = catalogEntry('initial-entry', 'Initial Entry');
    const latestEntry = catalogEntry('latest-entry', 'Latest Entry');
    const refresh = deferred<void>();
    hubAPI.catalogPage.mockImplementation(({ q }: { q?: string }) => (
      Promise.resolve(q === 'latest' ? catalogPage([latestEntry]) : catalogPage([initialEntry]))
    ));
    hubAPI.refresh.mockReturnValue(refresh.promise);

    renderHub();
    expect(await screen.findByText('Initial Entry')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Refresh' }));

    fireEvent.change(
      screen.getByPlaceholderText('Search plugin name, description, tag, use case'),
      { target: { value: 'latest' } },
    );
    expect(await screen.findByText('Latest Entry')).toBeInTheDocument();

    await act(async () => {
      refresh.resolve();
      await refresh.promise;
    });

    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled());
    expect(screen.getByText('Latest Entry')).toBeInTheDocument();
    expect(screen.queryByText('Initial Entry')).not.toBeInTheDocument();
    expect(hubAPI.catalogPage).toHaveBeenCalledTimes(2);
    expect(hubAPI.catalogPage).toHaveBeenLastCalledWith(expect.objectContaining({ q: 'latest' }));
  });

  it('shows a user-visible error when a manual refresh fails', async () => {
    const user = userEvent.setup();
    hubAPI.catalogPage.mockResolvedValue(catalogPage([catalogEntry('initial-entry', 'Initial Entry')]));
    hubAPI.refresh.mockRejectedValue(new Error('refresh unavailable'));

    renderHub();
    expect(await screen.findByText('Initial Entry')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Refresh' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith('Hub refresh failed', 'refresh unavailable');
    });
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled();
    expect(hubAPI.catalogPage).toHaveBeenCalledTimes(1);
  });

  it('distinguishes a completed manual refresh from a catalog reload failure', async () => {
    const user = userEvent.setup();
    hubAPI.catalogPage
      .mockResolvedValueOnce(catalogPage([catalogEntry('initial-entry', 'Initial Entry')]))
      .mockRejectedValueOnce({
        response: { data: { detail: 'catalog temporarily unavailable' } },
        message: 'Request failed with status code 503',
      });
    hubAPI.refresh.mockResolvedValue({ data: { status: 'success' } });

    renderHub();
    expect(await screen.findByText('Initial Entry')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Refresh' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        'Hub refresh completed, but catalog reload failed',
        'catalog temporarily unavailable',
      );
    });
    expect(toastError).not.toHaveBeenCalledWith('Hub refresh failed', expect.anything());
    expect(hubAPI.refresh).toHaveBeenCalledTimes(1);
    expect(hubAPI.catalogPage).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled();
  });

  it('does not report a stale manual reload failure after the query changes', async () => {
    const user = userEvent.setup();
    const staleReload = deferred<ReturnType<typeof catalogPage>>();
    hubAPI.catalogPage
      .mockResolvedValueOnce(catalogPage([catalogEntry('initial-entry', 'Initial Entry')]))
      .mockImplementationOnce(() => staleReload.promise)
      .mockResolvedValueOnce(catalogPage([catalogEntry('latest-entry', 'Latest Entry')]));
    hubAPI.refresh.mockResolvedValue({ data: { status: 'success' } });

    renderHub();
    expect(await screen.findByText('Initial Entry')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(hubAPI.catalogPage).toHaveBeenCalledTimes(2));

    fireEvent.change(
      screen.getByPlaceholderText('Search plugin name, description, tag, use case'),
      { target: { value: 'latest' } },
    );
    expect(await screen.findByText('Latest Entry')).toBeInTheDocument();

    await act(async () => {
      staleReload.reject(new Error('obsolete catalog failure'));
      try {
        await staleReload.promise;
      } catch {
        // The component owns this rejected request.
      }
    });

    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled());
    expect(toastError).not.toHaveBeenCalledWith(
      'Hub refresh completed, but catalog reload failed',
      expect.anything(),
    );
    expect(screen.getByText('Latest Entry')).toBeInTheDocument();
  });

  it.each([
    ['install', 'available', 'Install', 'Install failed'],
    ['update', 'updateAvailable', 'Update', 'Update failed'],
    ['uninstall', 'installed', 'Uninstall', 'Uninstall failed'],
  ] as const)('shows a user-visible error when %s fails', async (action, state, buttonLabel, errorTitle) => {
    const user = userEvent.setup();
    const entry = { ...catalogEntry('action-entry', 'Action Entry'), state };
    hubAPI.catalogPage.mockResolvedValue(catalogPage([entry]));
    hubAPI[action].mockRejectedValue({
      response: { data: { detail: `${action} permission denied` } },
      message: `Request failed with status code 422`,
    });

    renderHub();
    await user.click(await screen.findByRole('button', { name: buttonLabel }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        `${errorTitle}: Action Entry`,
        `${action} permission denied`,
      );
    });
    expect(screen.getByRole('button', { name: buttonLabel })).toBeEnabled();
    expect(hubAPI.catalogPage).toHaveBeenCalledTimes(1);
  });

  it('does not report a completed action as failed when the follow-up reload fails', async () => {
    const user = userEvent.setup();
    const entry = catalogEntry('action-entry', 'Action Entry');
    hubAPI.catalogPage.mockResolvedValue(catalogPage([entry]));
    hubAPI.install.mockResolvedValue({ data: { ...entry, state: 'installed' } });
    hubAPI.catalog.mockRejectedValue({
      response: { data: { detail: 'catalog temporarily unavailable' } },
      message: 'Request failed with status code 503',
    });

    renderHub();
    await user.click(await screen.findByRole('button', { name: 'Install' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        'Action completed, but Hub reload failed: Action Entry',
        'catalog temporarily unavailable',
      );
    });
    expect(toastError).not.toHaveBeenCalledWith(
      'Install failed: Action Entry',
      expect.anything(),
    );
    expect(hubAPI.install).toHaveBeenCalledTimes(1);
  });

  it('does not let a tree action started under old filters overwrite the latest tree', async () => {
    const user = userEvent.setup();
    const actionEntry = catalogEntry('tree-action', 'Tree Action');
    const latestEntry = catalogEntry('tree-latest', 'Tree Latest');
    const installedEntry = { ...actionEntry, state: 'installed' as const, installedVersion: '1.0.0' };
    const install = deferred<void>();
    hubAPI.catalogPage.mockImplementation(({ q }: { q?: string }) => (
      Promise.resolve(q === 'latest' ? catalogPage([latestEntry]) : catalogPage([actionEntry]))
    ));
    hubAPI.catalog.mockImplementation(({ q, type }: { q?: string; type?: string }) => {
      if (q === 'tree-action' && type === 'tool') return Promise.resolve({ data: [installedEntry] });
      return Promise.resolve({ data: q === 'latest' ? [latestEntry] : [actionEntry] });
    });
    hubAPI.get.mockResolvedValue({ data: actionEntry });
    hubAPI.install.mockReturnValue(install.promise);

    renderHub();
    expect(await screen.findByText('Tree Action')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Directory View' }));
    await user.click(await screen.findByRole('button', { name: 'tree-action' }));
    await user.click(await screen.findByRole('button', { name: 'Install' }));
    await waitFor(() => expect(hubAPI.install).toHaveBeenCalledWith('tool', 'tree-action'));

    fireEvent.change(
      screen.getByPlaceholderText('Search plugin name, description, tag, use case'),
      { target: { value: 'latest' } },
    );
    expect(await screen.findByRole('button', { name: 'tree-latest' })).toBeInTheDocument();

    await act(async () => {
      install.resolve();
      await install.promise;
    });

    await waitFor(() => {
      expect(hubAPI.catalog).toHaveBeenCalledWith({ q: 'tree-action', type: 'tool' });
    });
    expect(screen.getByRole('button', { name: 'tree-latest' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'tree-action' })).not.toBeInTheDocument();
    expect(hubAPI.catalogPage).toHaveBeenCalledTimes(2);
    expect(hubAPI.catalog.mock.calls.filter(([params]) => params?.q === undefined)).toHaveLength(1);
  });
});
