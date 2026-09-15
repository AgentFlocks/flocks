import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { SessionContextSnapshot } from '@/api/session';
import SessionContextPanel from './SessionContextPanel';

const sessionApi = vi.hoisted(() => ({
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
      'context.download': 'Download',
      'chat.tool.todoStatus.inProgress': 'in progress',
    }[key] || key),
  }),
}));

vi.mock('@/components/common/FilePreview', () => ({
  FilePreviewRenderer: ({ node, content }: any) => <div data-testid="file-preview">{node.name}:{content}</div>,
  PreviewModal: () => <div data-testid="preview-modal" />,
}));

const snapshot: SessionContextSnapshot = {
  sessionID: 'sess-1',
  canManageFolders: true,
  historyTruncated: false,
  outputs: [{
    resourceID: 'out-1',
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
    logicalPath: 'Outputs/2026-09-14/report.md',
  }],
  contextFiles: [{
    resourceID: 'ctx-1',
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
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
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
    vi.clearAllMocks();
    sessionApi.readContextFile.mockResolvedValue({ content: '# Report', truncated: false });
    sessionApi.listContextRoot.mockResolvedValue({
      rootID: 'project',
      path: '',
      items: [{ name: 'chapter.md', path: 'chapter.md', type: 'file', size: 7, isTextFile: true }],
    });
  });

  it('renders Progress, Outputs, Context, and Skills from one snapshot', () => {
    renderPanel();

    expect(screen.getByText('report.md')).toBeInTheDocument();
    expect(screen.getByText('paper.pdf')).toBeInTheDocument();
    expect(screen.getByText('Write report')).toBeInTheDocument();
    expect(screen.getByText('Research')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Skills'));
    expect(screen.getByText('docx')).toBeInTheDocument();
  });

  it('opens a text resource in the shared preview and keeps download separate', async () => {
    const onFocusMessage = vi.fn();
    renderPanel({ onFocusMessage });

    fireEvent.click(screen.getByText('report.md'));
    await screen.findByTestId('file-preview');
    expect(screen.getByTestId('file-preview')).toHaveTextContent('report.md:# Report');
    expect(sessionApi.readContextFile).toHaveBeenCalledWith('sess-1', 'out-1');
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
      displayName: 'latest.md',
      sourceMessageID: 'msg-output-2',
      logicalPath: 'Outputs/2026-09-14/latest.md',
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
    await waitFor(() => expect(sessionApi.listContextRoot).toHaveBeenCalledWith('sess-1', 'project', ''));
    expect(await screen.findByText('chapter.md')).toBeInTheDocument();
  });
});
