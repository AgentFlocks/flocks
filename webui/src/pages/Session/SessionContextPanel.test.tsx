import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { SessionContextFile, SessionContextRootPage, SessionContextSnapshot } from '@/api/session';
import SessionContextPanel from './SessionContextPanel';

const sessionApi = vi.hoisted(() => ({
  getContextFile: vi.fn(),
  readContextFile: vi.fn(),
  contextFilePreviewUrl: vi.fn((sessionId: string, resourceId: string) => `/preview/${sessionId}/${resourceId}`),
  contextFileDownloadUrl: vi.fn((sessionId: string, resourceId: string) => `/download/${sessionId}/${resourceId}`),
  addContextFolder: vi.fn(),
  removeContextFolder: vi.fn(),
  listContextRoot: vi.fn(),
  readContextRootFile: vi.fn(),
  contextRootPreviewUrl: vi.fn((sessionId: string, rootId: string, path: string) => `/root-preview/${sessionId}/${rootId}/${path}`),
  contextRootDownloadUrl: vi.fn((sessionId: string, rootId: string, path: string) => `/root-download/${sessionId}/${rootId}/${path}`),
}));

vi.mock('@/api/session', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/session')>();
  return { ...actual, sessionApi };
});

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => ({
      'context.title': 'Context',
      'context.progress': 'Progress',
      'context.outputs': 'Outputs',
      'context.contextFiles': 'Context files',
      'context.skills': 'Skills',
      'context.loaded': 'Loaded',
      'context.loading': 'Loading',
      'context.failed': 'Failed',
      'context.unknown': 'Unknown',
      'context.download': 'Download',
      'chat.tool.todoStatus.inProgress': 'in progress',
    }[key] || key),
  }),
}));

const previewSuspension = vi.hoisted(() => ({ current: null as Promise<void> | null, attempts: 0 }));
vi.mock('@/components/common/FilePreview', () => ({
  FilePreviewRenderer: ({ node, content }: any) => {
    if (previewSuspension.current) {
      previewSuspension.attempts += 1;
      throw previewSuspension.current;
    }
    return <div data-testid="file-preview">{node.name}:{content}</div>;
  },
  PreviewModal: () => <div data-testid="preview-modal" />,
}));

const snapshot: SessionContextSnapshot = {
  sessionID: 'sess-1',
  canManageFolders: true,
  hasMore: false,
  nextBefore: null,
  outputs: [{
    resourceID: 'out-1',
    fileKey: 'file-1',
    displayName: 'report.md',
    mimeType: 'text/markdown',
    size: 12,
    modifiedAt: 10,
    status: 'ready',
    previewStatus: 'text',
    canPreview: true,
    isTextFile: true,
    origin: 'agent_output',
    section: 'outputs',
    sourceMessageID: 'msg-output',
    logicalPath: 'users/admin/outputs/2026-09-14/report.md',
  }],
  contextFiles: [{
    resourceID: 'ctx-1',
    fileKey: 'file-2',
    displayName: 'paper.pdf',
    mimeType: 'application/pdf',
    size: 20,
    modifiedAt: 8,
    status: 'ready',
    previewStatus: 'inline',
    canPreview: true,
    isTextFile: false,
    origin: 'user_upload',
    section: 'context',
    sourceMessageID: 'msg-context',
    logicalPath: 'Uploads/paper.pdf',
  }],
  progress: [{ id: 'todo-1', content: 'Write report', status: 'in_progress' }],
  roots: [{ id: 'project', kind: 'project', displayName: 'Research', status: 'available' }],
  skills: [{ name: 'docx', status: 'loaded' }],
  counts: { total: 4, outputs: 1, contextFiles: 1, roots: 1, progress: 1 },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function renderPanel(overrides: Partial<React.ComponentProps<typeof SessionContextPanel>> = {}) {
  return render(
    <SessionContextPanel
      sessionId="sess-1"
      snapshot={snapshot}
      loading={false}
      onClose={vi.fn()}
      onRefresh={vi.fn()}
      onFocusMessage={vi.fn()}
      {...overrides}
    />,
  );
}

describe('SessionContextPanel', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    previewSuspension.current = null;
    previewSuspension.attempts = 0;
    sessionApi.contextFilePreviewUrl.mockImplementation((sessionId: string, resourceId: string) => `/preview/${sessionId}/${resourceId}`);
    sessionApi.contextFileDownloadUrl.mockImplementation((sessionId: string, resourceId: string) => `/download/${sessionId}/${resourceId}`);
    sessionApi.getContextFile.mockResolvedValue(snapshot.outputs[0]);
    sessionApi.readContextFile.mockResolvedValue({ content: '# Report', truncated: false });
    sessionApi.readContextRootFile.mockResolvedValue({ content: '# Chapter' });
    sessionApi.listContextRoot.mockResolvedValue({
      hasMore: false,
      nextOffset: null,
      items: [{ name: 'chapter.md', path: 'chapter.md', type: 'file', size: 7, isTextFile: true }],
    });
  });

  it('renders Progress, Outputs, Context, and Skills from one snapshot', () => {
    renderPanel();

    expect(screen.getByText('report.md')).toBeInTheDocument();
    expect(screen.getByText('users/admin/outputs/2026-09-14/report.md')).toBeInTheDocument();
    expect(screen.getByText('paper.pdf')).toBeInTheDocument();
    expect(screen.getByText('Write report')).toBeInTheDocument();
    expect(screen.getByText('Research')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Skills'));
    expect(screen.getByText('docx')).toBeInTheDocument();
  });

  it.each([
    ['loaded', 'Loaded'], ['loading', 'Loading'], ['error', 'Failed'], ['unknown', 'Unknown'],
  ] as const)('renders the %s skill status without implying success', (status, label) => {
    renderPanel({ snapshot: { ...snapshot, skills: [{ name: 'docx', description: null, status }] } });
    fireEvent.click(screen.getByRole('button', { name: /Skills/ }));
    const row = screen.getByText('docx').parentElement!;
    expect(within(row).getByText(label)).toBeInTheDocument();
    if (status !== 'loaded') expect(within(row).queryByText('Loaded')).not.toBeInTheDocument();
    // A failed load without a reason remains a compact badge, not an empty disclosure.
    expect(row.querySelector('[aria-expanded]')).toBeNull();
  });

  it('falls back to unknown for an unrecognized runtime skill status', () => {
    renderPanel({ snapshot: {
      ...snapshot,
      skills: [{ name: 'docx', status: 'unexpected' as SessionContextSnapshot['skills'][number]['status'] }],
    } });
    fireEvent.click(screen.getByRole('button', { name: /Skills/ }));
    expect(screen.getByText('Unknown')).toBeInTheDocument();
    expect(screen.queryByText('Loaded')).not.toBeInTheDocument();
  });

  it('discloses the full escaped skill error with both keyboard and mouse', async () => {
    const user = userEvent.setup();
    const reason = `Unable to load <script>alert("unsafe")</script>\n${'long-unbroken-reason'.repeat(50)}\nFinal diagnostic line`;
    renderPanel({ snapshot: { ...snapshot, skills: [{ name: 'docx', status: 'error', error: reason }] } });
    await user.click(screen.getByRole('button', { name: /Skills/ }));
    const disclosure = screen.getByRole('button', { name: 'docx Failed' });
    const error = document.getElementById(disclosure.getAttribute('aria-controls')!)!;
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    expect(error).not.toBeVisible();
    await user.tab();
    expect(disclosure).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(disclosure).toHaveAttribute('aria-expanded', 'true');
    expect(error).toBeVisible();
    expect(error.textContent).toBe(reason);
    expect(error).toHaveClass('whitespace-pre-wrap', '[overflow-wrap:anywhere]');
    expect(error.querySelector('script')).toBeNull();
    await user.keyboard(' ');
    expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    await user.click(disclosure);
    expect(error).toBeVisible();
  });

  it('clears failed skill details through a retry from loading to loaded', () => {
    const props = {
      sessionId: 'sess-1', loading: false, onClose: vi.fn(), onRefresh: vi.fn(), onFocusMessage: vi.fn(),
    };
    const view = renderPanel({ ...props, snapshot: {
      ...snapshot, skills: [{ name: 'docx', status: 'error', error: 'Missing dependency' }],
    } });
    fireEvent.click(screen.getByRole('button', { name: /Skills/ }));
    fireEvent.click(screen.getByRole('button', { name: 'docx Failed' }));
    expect(screen.getByText('Missing dependency')).toBeVisible();
    for (const status of ['loading', 'loaded'] as const) {
      view.rerender(<SessionContextPanel {...props} snapshot={{ ...snapshot, skills: [{ name: 'docx', status }] }} />);
      expect(screen.getByText(status === 'loading' ? 'Loading' : 'Loaded')).toBeInTheDocument();
      expect(screen.queryByText('Failed')).not.toBeInTheDocument();
      expect(screen.queryByText('Missing dependency')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /docx/ })).not.toBeInTheDocument();
    }
  });

  it('opens a text resource in the shared preview and keeps download separate', async () => {
    const onFocusMessage = vi.fn();
    renderPanel({ onFocusMessage });

    fireEvent.click(screen.getByText('report.md'));
    await screen.findByTestId('file-preview');
    expect(screen.getByTestId('file-preview')).toHaveTextContent('report.md:# Report');
    expect(sessionApi.readContextFile).toHaveBeenCalledWith('sess-1', 'out-1', expect.any(AbortSignal));
    expect(screen.getByTitle('Download')).toHaveAttribute('href', '/download/sess-1/out-1');
  });

  it('keeps the latest preview when earlier content resolves late', async () => {
    const first = deferred<{ content: string; truncated: boolean }>();
    const second = deferred<{ content: string; truncated: boolean }>();
    sessionApi.readContextFile.mockImplementation((_sessionId: string, resourceId: string) => (
      resourceId === 'out-1' ? first.promise : second.promise
    ));
    const secondFile = {
      ...snapshot.outputs[0],
      resourceID: 'out-2',
      fileKey: 'file-3',
      displayName: 'latest.md',
      sourceMessageID: 'msg-output-2',
      logicalPath: 'users/admin/outputs/2026-09-14/latest.md',
    };
    renderPanel({ snapshot: { ...snapshot, outputs: [snapshot.outputs[0], secondFile] } });

    fireEvent.click(screen.getByText('report.md'));
    fireEvent.click(screen.getByText('latest.md'));
    second.resolve({ content: '# Latest', truncated: false });
    expect(await screen.findByTestId('file-preview')).toHaveTextContent('latest.md:# Latest');
    first.resolve({ content: '# Old', truncated: false });

    await waitFor(() => {
      expect(screen.getByTestId('file-preview')).toHaveTextContent('latest.md:# Latest');
    });
  });

  it('browses a Project root one level at a time', async () => {
    renderPanel();

    fireEvent.click(screen.getByText('Research'));
    await waitFor(() => expect(sessionApi.listContextRoot).toHaveBeenCalledWith('sess-1', 'project', { path: '', offset: 0 }, expect.any(AbortSignal)));
    expect(await screen.findByText('chapter.md')).toBeInTheDocument();
  });

  it.each([null, snapshot])('resolves an unloaded requested resource without waiting for the snapshot (%j)', async (loadedSnapshot) => {
    const metadata = deferred<SessionContextFile>();
    const file = { ...snapshot.outputs[0], resourceID: 'historical', displayName: 'earlier.md' };
    sessionApi.getContextFile.mockReturnValue(metadata.promise);
    const onRequestedResourceConsumed = vi.fn();
    renderPanel({ snapshot: loadedSnapshot, requestedResourceID: 'historical', onRequestedResourceConsumed });
    expect(onRequestedResourceConsumed).toHaveBeenCalledOnce();
    expect(sessionApi.getContextFile).toHaveBeenCalledWith('sess-1', 'historical', expect.any(AbortSignal));
    await act(async () => metadata.resolve(file));
    expect(await screen.findByTestId('file-preview')).toHaveTextContent('earlier.md:# Report');
    expect(sessionApi.readContextFile).toHaveBeenCalledWith('sess-1', 'historical', expect.any(AbortSignal));
  });

  it.each([null, snapshot])('preserves an opened card when preview translations suspend and reconnect (%j)', async (loadedSnapshot) => {
    const translations = deferred<void>();
    previewSuspension.current = translations.promise;
    function RequestedPreview() {
      const [requested, setRequested] = React.useState<string | null>('out-1');
      return (
        <SessionContextPanel
          sessionId="sess-1" snapshot={loadedSnapshot} loading={false}
          requestedResourceID={requested} onRequestedResourceConsumed={() => setRequested(null)}
          onClose={vi.fn()} onRefresh={vi.fn()} onFocusMessage={vi.fn()}
        />
      );
    }
    render(
      <React.StrictMode>
        <React.Suspense fallback={<div>Loading preview translations</div>}>
          <RequestedPreview />
        </React.Suspense>
      </React.StrictMode>,
    );
    await waitFor(() => expect(previewSuspension.attempts).toBeGreaterThan(0));
    expect(screen.queryByText('Loading preview translations')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'context.back' })).toBeVisible();
    previewSuspension.current = null;
    await act(async () => translations.resolve());
    expect(await screen.findByTestId('file-preview')).toHaveTextContent('report.md:# Report');
    expect(sessionApi.readContextFile.mock.calls.filter((call) => !(call[2] as AbortSignal).aborted)).toHaveLength(1);
  });

  it('uses snapshot metadata when present and opens non-text metadata without a content read', async () => {
    const props = { sessionId: 'sess-1', snapshot, loading: false, onClose: vi.fn(), onRefresh: vi.fn(), onFocusMessage: vi.fn() };
    const view = render(<SessionContextPanel {...props} requestedResourceID="ctx-1" />);
    expect(await screen.findByTestId('file-preview')).toHaveTextContent('paper.pdf:');
    expect(sessionApi.getContextFile).not.toHaveBeenCalled();
    expect(sessionApi.readContextFile).not.toHaveBeenCalled();
    sessionApi.getContextFile.mockResolvedValue({ ...snapshot.contextFiles[0], resourceID: 'historical-pdf', displayName: 'earlier.pdf' });
    view.rerender(<SessionContextPanel {...props} requestedResourceID="historical-pdf" />);
    await waitFor(() => expect(screen.getByTestId('file-preview')).toHaveTextContent('earlier.pdf:'));
    expect(sessionApi.readContextFile).not.toHaveBeenCalled();
  });

  it('cancels stale metadata when another file is requested', async () => {
    const metadata = deferred<SessionContextFile>();
    sessionApi.getContextFile.mockReturnValue(metadata.promise);
    const props = { sessionId: 'sess-1', snapshot, loading: false, onClose: vi.fn(), onRefresh: vi.fn(), onFocusMessage: vi.fn() };
    const view = render(<SessionContextPanel {...props} requestedResourceID="historical" />);
    const signal = sessionApi.getContextFile.mock.calls[0][2] as AbortSignal;
    view.rerender(<SessionContextPanel {...props} requestedResourceID="out-1" />);
    expect(await screen.findByTestId('file-preview')).toHaveTextContent('report.md:# Report');
    expect(signal.aborted).toBe(true);
    await act(async () => metadata.resolve({ ...snapshot.outputs[0], resourceID: 'historical', displayName: 'old.md' }));
    expect(sessionApi.readContextFile).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('file-preview')).toHaveTextContent('report.md:# Report');
  });

  it('keeps loading more available across empty filtered directory pages and appends entries', async () => {
    sessionApi.listContextRoot
      .mockResolvedValueOnce({ items: [], hasMore: true, nextOffset: 100 })
      .mockResolvedValueOnce({ items: [{ name: 'first.md', path: 'first.md', type: 'file' }], hasMore: true, nextOffset: 200 })
      .mockResolvedValueOnce({ items: [], hasMore: true, nextOffset: 300 })
      .mockResolvedValueOnce({ items: [{ name: 'last.md', path: 'last.md', type: 'file' }], hasMore: false, nextOffset: null });
    renderPanel();
    fireEvent.click(screen.getByText('Research'));
    fireEvent.click(await screen.findByRole('button', { name: 'context.loadMore' }));
    expect(await screen.findByText('first.md')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'context.loadMore' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'context.loadMore' })).toBeEnabled());
    expect(screen.getByText('first.md')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'context.loadMore' }));
    expect(await screen.findByText('last.md')).toBeInTheDocument();
    expect(screen.getByText('first.md')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'context.loadMore' })).not.toBeInTheDocument();
    expect(sessionApi.listContextRoot.mock.calls.map((call) => call[2].offset)).toEqual([0, 100, 200, 300]);
  });

  it('does not reopen a directory after returning while its listing is pending', async () => {
    const page = deferred<SessionContextRootPage>();
    sessionApi.listContextRoot.mockReturnValue(page.promise);
    renderPanel();
    fireEvent.click(screen.getByText('Research'));
    const signal = sessionApi.listContextRoot.mock.calls[0][3] as AbortSignal;
    fireEvent.click(screen.getByRole('button', { name: 'context.back' }));
    expect(signal.aborted).toBe(true);
    await act(async () => page.resolve({ items: [{ name: 'late.md', path: 'late.md', type: 'file' }], hasMore: false, nextOffset: null }));
    expect(screen.getByText('report.md')).toBeInTheDocument();
    expect(screen.queryByText('late.md')).not.toBeInTheDocument();
  });

  it('does not replace a parent directory with an earlier child listing', async () => {
    const child = deferred<SessionContextRootPage>();
    sessionApi.listContextRoot
      .mockResolvedValueOnce({ items: [{ name: 'child', path: 'child', type: 'directory' }], hasMore: false, nextOffset: null })
      .mockReturnValueOnce(child.promise)
      .mockResolvedValueOnce({ items: [{ name: 'parent.md', path: 'parent.md', type: 'file' }], hasMore: false, nextOffset: null });
    renderPanel();
    fireEvent.click(screen.getByText('Research'));
    fireEvent.click(await screen.findByText('child'));
    const signal = sessionApi.listContextRoot.mock.calls[1][3] as AbortSignal;
    fireEvent.click(screen.getByRole('button', { name: 'context.parentFolder' }));
    expect(await screen.findByText('parent.md')).toBeInTheDocument();
    expect(signal.aborted).toBe(true);
    await act(async () => child.resolve({ items: [{ name: 'late.md', path: 'child/late.md', type: 'file' }], hasMore: false, nextOffset: null }));
    expect(screen.queryByText('late.md')).not.toBeInTheDocument();
    expect(screen.getByText('parent.md')).toBeInTheDocument();
  });

  it('cancels pending directory pagination when opening a preview', async () => {
    const page = deferred<SessionContextRootPage>();
    sessionApi.listContextRoot
      .mockResolvedValueOnce({ items: [{ name: 'chapter.md', path: 'chapter.md', type: 'file', isTextFile: true }], hasMore: true, nextOffset: 100 })
      .mockReturnValueOnce(page.promise);
    renderPanel();
    fireEvent.click(screen.getByText('Research'));
    fireEvent.click(await screen.findByRole('button', { name: 'context.loadMore' }));
    const signal = sessionApi.listContextRoot.mock.calls[1][3] as AbortSignal;
    fireEvent.click(screen.getByText('chapter.md'));
    expect(await screen.findByTestId('file-preview')).toHaveTextContent('chapter.md:# Chapter');
    expect(signal.aborted).toBe(true);
    await act(async () => page.resolve({ items: [], hasMore: false, nextOffset: null }));
    expect(screen.getByTestId('file-preview')).toHaveTextContent('chapter.md:# Chapter');
  });

  it('cancels root content when navigating back before it resolves', async () => {
    const content = deferred<{ content: string }>();
    sessionApi.readContextRootFile.mockReturnValue(content.promise);
    renderPanel();
    fireEvent.click(screen.getByText('Research'));
    fireEvent.click(await screen.findByText('chapter.md'));
    const signal = sessionApi.readContextRootFile.mock.calls[0][3] as AbortSignal;
    fireEvent.click(screen.getByRole('button', { name: 'context.back' }));
    expect(signal.aborted).toBe(true);
    await act(async () => content.resolve({ content: 'Late' }));
    expect(screen.queryByTestId('file-preview')).not.toBeInTheDocument();
    expect(screen.getByText('report.md')).toBeInTheDocument();
  });

  it.each(['back', 'close', 'unmount'])('cancels a pending preview on %s and ignores late errors', async (action) => {
    const content = deferred<{ content: string }>();
    sessionApi.readContextFile.mockReturnValue(content.promise);
    const onClose = vi.fn();
    const view = renderPanel({ onClose });
    fireEvent.click(screen.getByText('report.md'));
    const signal = sessionApi.readContextFile.mock.calls[0][2] as AbortSignal;
    if (action === 'unmount') view.unmount();
    else fireEvent.click(screen.getByRole('button', { name: `context.${action}` }));
    expect(signal.aborted).toBe(true);
    await act(async () => content.reject({ response: { data: { detail: [{ msg: 'Stale failure' }] } } }));
    expect(screen.queryByText('Stale failure')).not.toBeInTheDocument();
    expect(screen.queryByTestId('file-preview')).not.toBeInTheDocument();
    if (action === 'close') expect(onClose).toHaveBeenCalledOnce();
  });

  it('isolates preview requests across A -> B -> A', async () => {
    const first = deferred<{ content: string }>();
    const second = deferred<{ content: string }>();
    const latest = deferred<{ content: string }>();
    sessionApi.readContextFile.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise).mockReturnValueOnce(latest.promise);
    const props = { snapshot, loading: false, onClose: vi.fn(), onRefresh: vi.fn(), onFocusMessage: vi.fn() };
    const view = render(<SessionContextPanel {...props} sessionId="sess-1" />);
    fireEvent.click(screen.getByText('report.md'));
    view.rerender(<SessionContextPanel {...props} sessionId="sess-2" snapshot={{ ...snapshot, sessionID: 'sess-2' }} />);
    fireEvent.click(screen.getByText('report.md'));
    view.rerender(<SessionContextPanel {...props} sessionId="sess-1" />);
    fireEvent.click(screen.getByText('report.md'));
    expect((sessionApi.readContextFile.mock.calls[0][2] as AbortSignal).aborted).toBe(true);
    expect((sessionApi.readContextFile.mock.calls[1][2] as AbortSignal).aborted).toBe(true);
    await act(async () => latest.resolve({ content: 'Latest A' }));
    await act(async () => { first.resolve({ content: 'Old A' }); second.resolve({ content: 'Old B' }); });
    expect(screen.getByTestId('file-preview')).toHaveTextContent('report.md:Latest A');
  });

  it('does not refresh or show a stale folder mutation error after switching sessions', async () => {
    const mutation = deferred<void>();
    sessionApi.addContextFolder.mockReturnValue(mutation.promise);
    const onRefresh = vi.fn();
    const props = { snapshot, loading: false, onClose: vi.fn(), onRefresh, onFocusMessage: vi.fn() };
    const view = render(<SessionContextPanel {...props} sessionId="sess-1" />);
    fireEvent.click(screen.getByTitle('context.addFolder'));
    fireEvent.change(screen.getByPlaceholderText('context.folderPathPlaceholder'), { target: { value: '/work' } });
    fireEvent.click(screen.getByRole('button', { name: 'context.add' }));
    view.rerender(<SessionContextPanel {...props} sessionId="sess-2" snapshot={{ ...snapshot, sessionID: 'sess-2' }} />);
    await act(async () => mutation.resolve());
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it('renders validation errors as strings and exposes earlier-history pagination', async () => {
    sessionApi.readContextFile.mockRejectedValue({ response: { data: { detail: [{ msg: 'Invalid resource' }] } } });
    const onLoadMore = vi.fn();
    renderPanel({ snapshot: { ...snapshot, hasMore: true, nextBefore: 'msg-older' }, onLoadMore });
    fireEvent.click(screen.getByRole('button', { name: 'context.loadEarlier' }));
    expect(onLoadMore).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByText('report.md'));
    expect(await screen.findByText('Invalid resource')).toBeInTheDocument();
  });
});
