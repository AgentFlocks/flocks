import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WorkspacePage from './index';

const { listMock, readFileMock, tMock, toastMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  readFileMock: vi.fn(),
  tMock: vi.fn((key: string) => key),
  toastMock: { error: vi.fn(), success: vi.fn() },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: tMock }),
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => toastMock,
}));

vi.mock('@/components/common/ConfirmDialog', () => ({
  useConfirm: () => vi.fn().mockResolvedValue(false),
}));

vi.mock('@/api/workspace', async () => {
  const actual = await vi.importActual<typeof import('@/api/workspace')>('@/api/workspace');
  return {
    ...actual,
    workspaceAPI: {
      ...actual.workspaceAPI,
      list: listMock,
      readFile: readFileMock,
      createDir: vi.fn(),
      deleteFile: vi.fn(),
      deleteDir: vi.fn(),
      upload: vi.fn(),
      writeFile: vi.fn(),
    },
  };
});

vi.mock('@/components/common/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div role="status">loading</div>,
}));

const mdNode = {
  name: 'report.md',
  path: 'outputs/report.md',
  type: 'file' as const,
  size: 1024,
  modified_at: 1_700_000_000,
  is_text_file: true,
};

const jsonNode = {
  name: 'result.json',
  path: 'outputs/result.json',
  type: 'file' as const,
  size: 512,
  modified_at: 1_700_000_000,
  is_text_file: true,
};

const textNode = {
  name: 'notes.txt',
  path: 'outputs/notes.txt',
  type: 'file' as const,
  size: 128,
  modified_at: 1_700_000_000,
  is_text_file: true,
};

const pdfNode = {
  name: 'report.pdf',
  path: 'outputs/report.pdf',
  type: 'file' as const,
  size: 2048,
  modified_at: 1_700_000_000,
  is_text_file: false,
};

describe('WorkspacePage files preview', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listMock.mockResolvedValue({ data: [mdNode, jsonNode, textNode, pdfNode] });
    readFileMock.mockResolvedValue({ data: { path: mdNode.path, content: '# Hello Report\n\nBody text.' } });
  });

  function clickFileRow(filename: string) {
    const cell = screen.getByText(filename, { selector: 'span' });
    const row = cell.closest('tr');
    if (!row) throw new Error(`row not found for ${filename}`);
    return userEvent.setup().click(row);
  }

  it('opens markdown drawer with Review tab and renders heading by default', async () => {
    render(<WorkspacePage />);

    await waitFor(() => {
      expect(screen.getByText('report.md', { selector: 'span' })).toBeInTheDocument();
    });

    await clickFileRow('report.md');

    await waitFor(() => {
      expect(readFileMock).toHaveBeenCalledWith(mdNode.path);
    });

    await waitFor(() => {
      expect(screen.getByTestId('workspace-preview-drawer')).toBeInTheDocument();
    });

    expect(screen.getByRole('heading', { level: 1, name: 'Hello Report' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'files.previewTabs.review' })).toHaveClass('border-slate-700');
  });

  it('switches to Raw tab and shows source markdown', async () => {
    const user = userEvent.setup();
    render(<WorkspacePage />);

    await waitFor(() => expect(screen.getByText('report.md', { selector: 'span' })).toBeInTheDocument());
    await clickFileRow('report.md');

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Hello Report' })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'files.previewTabs.raw' }));

    expect(screen.getByText((content) => content.includes('# Hello Report'))).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Hello Report' })).not.toBeInTheDocument();
  });

  it('opens plain text files in the drawer as raw source', async () => {
    render(<WorkspacePage />);

    await waitFor(() => expect(screen.getByText('notes.txt', { selector: 'span' })).toBeInTheDocument());
    readFileMock.mockResolvedValueOnce({ data: { path: textNode.path, content: 'line 1\nline 2' } });

    await clickFileRow('notes.txt');

    await waitFor(() => {
      expect(readFileMock).toHaveBeenCalledWith(textNode.path);
    });
    expect(screen.getByTestId('workspace-preview-drawer')).toBeInTheDocument();
    expect(screen.getByText((content) => content.includes('line 1'))).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'files.previewTabs.review' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'files.previewTabs.raw' })).not.toBeInTheDocument();
  });

  it('does not read file content for non-text files', async () => {
    render(<WorkspacePage />);

    await waitFor(() => expect(screen.getByText('report.pdf', { selector: 'span' })).toBeInTheDocument());
    await clickFileRow('report.pdf');

    await waitFor(() => {
      expect(screen.getByTestId('workspace-download-panel')).toBeInTheDocument();
    });

    expect(readFileMock).not.toHaveBeenCalled();
    expect(screen.queryByTestId('workspace-preview-drawer')).not.toBeInTheDocument();
  });

  it('opens drawer for json and renders json code block in review tab', async () => {
    render(<WorkspacePage />);

    await waitFor(() => expect(screen.getByText('result.json', { selector: 'span' })).toBeInTheDocument());
    readFileMock.mockResolvedValueOnce({ data: { path: jsonNode.path, content: '{\n  \"status\": \"ok\"\n}' } });

    await clickFileRow('result.json');

    await waitFor(() => {
      expect(readFileMock).toHaveBeenCalledWith(jsonNode.path);
    });

    await waitFor(() => {
      expect(screen.getByTestId('workspace-preview-drawer')).toBeInTheDocument();
    });

    const codeEl = document.querySelector('pre code');
    expect(codeEl).toBeTruthy();
    expect(codeEl?.textContent).toContain('"status": "ok"');
    expect(screen.queryByRole('button', { name: 'files.previewTabs.review' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'files.previewTabs.raw' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('workspace-download-panel')).not.toBeInTheDocument();
  });

  it('shows an error state when text file content fails to load', async () => {
    render(<WorkspacePage />);

    await waitFor(() => expect(screen.getByText('notes.txt', { selector: 'span' })).toBeInTheDocument());
    readFileMock.mockRejectedValueOnce(new Error('network down'));

    await clickFileRow('notes.txt');

    await waitFor(() => {
      expect(screen.getByText('files.readFailed')).toBeInTheDocument();
    });
    expect(screen.getByText('network down')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'files.retry' })).toBeInTheDocument();
  });

  it('ignores stale file content responses after switching files', async () => {
    let resolveMarkdown!: (value: { data: { path: string; content: string } }) => void;
    let resolveText!: (value: { data: { path: string; content: string } }) => void;

    readFileMock.mockImplementation((path: string) => new Promise<{ data: { path: string; content: string } }>((resolve) => {
      if (path === mdNode.path) {
        resolveMarkdown = resolve;
        return;
      }
      resolveText = resolve;
    }));

    render(<WorkspacePage />);

    await waitFor(() => expect(screen.getByText('report.md', { selector: 'span' })).toBeInTheDocument());
    await clickFileRow('report.md');
    await waitFor(() => expect(readFileMock).toHaveBeenCalledWith(mdNode.path));

    await clickFileRow('notes.txt');
    await waitFor(() => expect(readFileMock).toHaveBeenCalledWith(textNode.path));

    resolveText({ data: { path: textNode.path, content: 'current file content' } });
    await waitFor(() => expect(screen.getByText('current file content')).toBeInTheDocument());

    resolveMarkdown({ data: { path: mdNode.path, content: '# stale markdown content' } });
    await waitFor(() => expect(screen.getByText('current file content')).toBeInTheDocument());
    expect(screen.queryByText((content) => content.includes('stale markdown content'))).not.toBeInTheDocument();
  });
});
