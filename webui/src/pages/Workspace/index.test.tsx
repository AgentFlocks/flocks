import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WorkspacePage from './index';
import { renderWithRouter } from '@/test/helpers';

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  readFile: vi.fn(),
  writeFile: vi.fn(),
  deleteFile: vi.fn(),
  deleteDir: vi.fn(),
  upload: vi.fn(),
  createDir: vi.fn(),
  reveal: vi.fn(),
  listMemory: vi.fn(),
  listVisibleProjects: vi.fn(),
  readMemoryFile: vi.fn(),
  writeMemoryFile: vi.fn(),
  confirm: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

const pdfMocks = vi.hoisted(() => {
  const renderPage = vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() }));
  const getPage = vi.fn(() => Promise.resolve({
    getViewport: () => ({ width: 600, height: 800 }),
    render: renderPage,
  }));
  const destroyDocument = vi.fn();
  const destroyTask = vi.fn();
  const getDocument = vi.fn(() => ({
    promise: Promise.resolve({
      numPages: 3,
      getPage,
      destroy: destroyDocument,
    }),
    destroy: destroyTask,
  }));
  return {
    getDocument,
    getPage,
    renderPage,
    destroyDocument,
    destroyTask,
  };
});

const translations: Record<string, string> = {
  description: 'Workspace files',
  'tabs.files': 'Files',
  'tabs.memory': 'Memory',
  'files.columns.name': 'Name',
  'files.columns.size': 'Size',
  'files.columns.modified': 'Modified',
  'files.refresh': 'Refresh',
  'files.newDir': 'New directory',
  'files.upload': 'Upload',
  'files.back': 'Back',
  'files.delete': 'Delete',
  'files.download': 'Download',
  'files.reveal': 'Open containing folder',
  'files.downloadFile': 'Download file',
  'files.binaryPreview': 'Binary file cannot be previewed',
  'files.truncatedPreview': 'Preview truncated to first {{limit}}',
  'files.preview.previewMode': 'Preview',
  'files.preview.sourceMode': 'Source',
  'files.preview.fullscreen': 'Fullscreen preview',
  'files.preview.resize': 'Drag to resize preview',
  'files.preview.htmlSandbox': 'HTML sandboxed',
  'files.preview.jsonParseFailed': 'JSON parse failed',
  'files.preview.jsonlParseFailed': '{{count}} JSONL lines failed',
  'files.preview.pdfLoading': 'Loading PDF',
  'files.preview.pdfRendering': 'Rendering page',
  'files.preview.pdfLoadFailed': 'Failed to load PDF preview',
  'files.preview.pdfCanvasUnavailable': 'Canvas unavailable',
  'files.preview.pageIndicator': '{{page}} / {{total}}',
  'files.preview.previousPage': 'Previous page',
  'files.preview.nextPage': 'Next page',
  'files.preview.zoomIn': 'Zoom in',
  'files.preview.zoomOut': 'Zoom out',
  'files.preview.unsupportedTitle': 'This file cannot be previewed',
  'files.preview.unsupportedDesc': 'Download it or open containing folder',
  'files.emptyDir': 'Empty directory',
  'files.dropHere': 'Drop files here',
  'files.uploading': 'Uploading',
  'files.edit': 'Edit',
  'files.save': 'Save',
  'files.cancel': 'Cancel',
  'files.close': 'Close',
  'files.create': 'Create',
  'files.dirNamePlaceholder': 'Folder name',
  'files.confirm.deleteTitle': 'Delete file',
  'files.confirm.deleteBtn': 'Delete',
  'files.toast.deleteSuccess': 'Deleted',
  'files.toast.deleteFailed': 'Delete failed',
  'files.toast.loadDirFailed': 'Load directory failed',
  'files.toast.saveSuccess': 'Saved successfully',
  'files.toast.saveFailed': 'Failed to save',
};

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    // Return a fresh function every render to mimic unstable hook dependencies.
    t: (key: string, params?: Record<string, unknown>) => {
      if (key === 'files.confirm.deleteDesc') {
        return `Delete ${params?.name ?? ''}`;
      }
      if (key === 'files.truncatedPreview') {
        return `Preview truncated to first ${params?.limit ?? ''}`;
      }
      if (key === 'files.preview.jsonlParseFailed') {
        return `${params?.count ?? ''} JSONL lines failed`;
      }
      if (key === 'files.preview.pageIndicator') {
        return `${params?.page ?? ''} / ${params?.total ?? ''}`;
      }
      return translations[key] ?? key;
    },
    i18n: { language: 'en-US' },
  }),
}));

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  getDocument: pdfMocks.getDocument,
}));

vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({
  default: '/pdf.worker.min.mjs',
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({
    success: mocks.toastSuccess,
    error: mocks.toastError,
  }),
}));

Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
  configurable: true,
  value: vi.fn(() => ({})),
});

vi.mock('@/components/common/ConfirmDialog', () => ({
  useConfirm: () => mocks.confirm,
}));

vi.mock('@/components/common/PageHeader', () => ({
  default: ({ title, description }: { title: string; description: string }) => (
    <div>
      <h1>{title}</h1>
      <p>{description}</p>
    </div>
  ),
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>Loading...</div>,
}));

vi.mock('@/api/workspace', async () => {
  const actual = await vi.importActual<typeof import('@/api/workspace')>('@/api/workspace');
  return {
    ...actual,
    workspaceAPI: {
      ...actual.workspaceAPI,
      list: mocks.list,
      readFile: mocks.readFile,
      writeFile: mocks.writeFile,
      deleteFile: mocks.deleteFile,
      deleteDir: mocks.deleteDir,
      upload: mocks.upload,
      createDir: mocks.createDir,
      reveal: mocks.reveal,
      listMemory: mocks.listMemory,
      listVisibleProjects: mocks.listVisibleProjects,
      readMemoryFile: mocks.readMemoryFile,
      writeMemoryFile: mocks.writeMemoryFile,
      downloadUrl: (path: string) => `/api/workspace/download?path=${encodeURIComponent(path)}`,
      previewUrl: (path: string) => `/api/workspace/preview?path=${encodeURIComponent(path)}`,
      memoryDownloadUrl: (path: string) => `/api/workspace/memory/download?path=${encodeURIComponent(path)}`,
      memoryPreviewUrl: (path: string) => `/api/workspace/memory/preview?path=${encodeURIComponent(path)}`,
    },
  };
});

function directory(name: string, path: string) {
  return {
    name,
    path,
    type: 'directory' as const,
    modified_at: 1710000000,
  };
}

function file(name: string, path: string, isTextFile = true, editable = isTextFile) {
  return {
    name,
    path,
    type: 'file' as const,
    size: 24,
    modified_at: 1710000000,
    is_text_file: isTextFile,
    editable,
  };
}

describe('WorkspacePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue({ data: [] });
    mocks.readFile.mockResolvedValue({ data: { content: '' } });
    mocks.writeFile.mockResolvedValue({ data: { written: true } });
    mocks.deleteFile.mockResolvedValue({ data: { deleted: true } });
    mocks.deleteDir.mockResolvedValue({ data: { deleted: true } });
    mocks.upload.mockResolvedValue({ data: { uploaded: [] } });
    mocks.createDir.mockResolvedValue({ data: { created: true } });
    mocks.reveal.mockResolvedValue({ data: { opened: true } });
    mocks.listMemory.mockResolvedValue({ data: [] });
    mocks.listVisibleProjects.mockResolvedValue({ data: [] });
    mocks.readMemoryFile.mockResolvedValue({ data: { content: '' } });
    mocks.writeMemoryFile.mockResolvedValue({ data: { written: true } });
    mocks.confirm.mockResolvedValue(true);
  });

  it('删除子目录文件后保持在当前目录，不会重新加载根目录', async () => {
    let reportsListCount = 0;
    mocks.list.mockImplementation((path = '') => {
      if (path === '') {
        return Promise.resolve({ data: [directory('reports', 'reports')] });
      }
      if (path === 'reports') {
        reportsListCount += 1;
        return Promise.resolve({
          data: reportsListCount === 1
            ? [file('triage_result_001.jsonl', 'reports/triage_result_001.jsonl')]
            : [],
        });
      }
      return Promise.resolve({ data: [] });
    });

    const user = userEvent.setup();
    renderWithRouter(<WorkspacePage />);

    await user.click(await screen.findByText('reports'));
    expect(await screen.findByText('triage_result_001.jsonl')).toBeInTheDocument();

    await user.click(screen.getByTitle('Delete'));

    await waitFor(() => {
      expect(mocks.deleteFile).toHaveBeenCalledWith('reports/triage_result_001.jsonl');
    });

    await waitFor(() => {
      expect(screen.getByText('Empty directory')).toBeInTheDocument();
    });

    expect(mocks.list.mock.calls.filter(([path]) => path === '')).toHaveLength(1);
    expect(mocks.list.mock.calls.filter(([path]) => path === 'reports')).toHaveLength(2);
    expect(mocks.toastSuccess).toHaveBeenCalledWith('Deleted');
  });

  it('大文件预览被截断时显示提示并禁用编辑', async () => {
    mocks.list.mockResolvedValue({
      data: [file('events.jsonl', 'events.jsonl')],
    });
    mocks.readFile.mockResolvedValue({
      data: {
        path: 'events.jsonl',
        content: '{"id":1}\n',
        truncated: true,
        preview_limit_bytes: 16,
        size: 1024,
      },
    });

    const user = userEvent.setup();
    renderWithRouter(<WorkspacePage />);

    await user.click(await screen.findByText('events.jsonl'));

    expect(await screen.findByText('Preview truncated to first 16 B')).toBeInTheDocument();
    expect(screen.getByText(/"id": 1/)).toBeInTheDocument();
    expect(screen.queryByTitle('Edit')).not.toBeInTheDocument();
  });

  it('Markdown 文件默认渲染预览，并可打开全屏预览', async () => {
    mocks.list.mockResolvedValue({
      data: [file('README.md', 'README.md')],
    });
    mocks.readFile.mockResolvedValue({
      data: {
        path: 'README.md',
        content: '# Hello\n\n**World**',
        truncated: false,
      },
    });

    const user = userEvent.setup();
    renderWithRouter(<WorkspacePage />);

    await user.click(await screen.findByText('README.md'));

    expect(await screen.findByRole('heading', { name: 'Hello' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Preview' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Source' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Drag to resize preview' })).toBeInTheDocument();

    await user.click(screen.getByTitle('Fullscreen preview'));
    expect(screen.getAllByRole('heading', { name: 'Hello' })).toHaveLength(2);
  });

  it('JSON 文件默认格式化显示，并可切换源码', async () => {
    mocks.list.mockResolvedValue({
      data: [file('payload.json', 'payload.json')],
    });
    mocks.readFile.mockResolvedValue({
      data: {
        path: 'payload.json',
        content: '{"message":"ok","count":2}',
        truncated: false,
      },
    });

    const user = userEvent.setup();
    renderWithRouter(<WorkspacePage />);

    await user.click(await screen.findByText('payload.json'));

    expect(await screen.findByText(/"message": "ok"/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Source' }));
    expect(screen.getByText('{"message":"ok","count":2}')).toBeInTheDocument();
  });

  it('CSV 文件默认展示表格，并可切换源码', async () => {
    mocks.list.mockResolvedValue({
      data: [file('table.csv', 'table.csv')],
    });
    mocks.readFile.mockResolvedValue({
      data: {
        path: 'table.csv',
        content: 'name,count\nalpha,2\n"beta, inc",5',
        truncated: false,
      },
    });

    const user = userEvent.setup();
    renderWithRouter(<WorkspacePage />);

    await user.click(await screen.findByText('table.csv'));

    expect(await screen.findByRole('columnheader', { name: 'name' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'count' })).toBeInTheDocument();
    expect(screen.getByText('alpha')).toBeInTheDocument();
    expect(screen.getByText('beta, inc')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Source' }));
    expect(screen.getByText(/name,count/)).toBeInTheDocument();
  });

  it('PDF 文件使用 inline preview 地址展示', async () => {
    mocks.list.mockResolvedValue({
      data: [file('report.pdf', 'report.pdf', false)],
    });

    const user = userEvent.setup();
    renderWithRouter(<WorkspacePage />);

    await user.click(await screen.findByText('report.pdf'));

    await waitFor(() => {
      expect(pdfMocks.getDocument).toHaveBeenCalledWith({
        url: '/api/workspace/preview?path=report.pdf',
        withCredentials: true,
      });
    });
    expect(await screen.findByText('1 / 3')).toBeInTheDocument();
    expect(pdfMocks.getPage).toHaveBeenCalledWith(1);
    expect(pdfMocks.renderPage).toHaveBeenCalled();
    expect(screen.getByTitle('Previous page')).toBeDisabled();
    expect(screen.getByTitle('Next page')).toBeEnabled();
  });

  it('Memory Markdown 文件复用预览渲染和全屏预览', async () => {
    mocks.listMemory.mockResolvedValue({
      data: [file('MEMORY.md', 'MEMORY.md')],
    });
    mocks.readMemoryFile.mockResolvedValue({
      data: {
        path: 'MEMORY.md',
        content: '# Memory\n\n**Fact**',
        truncated: false,
      },
    });

    const user = userEvent.setup();
    renderWithRouter(<WorkspacePage />);

    await user.click(screen.getByRole('button', { name: 'Memory' }));
    await user.click(await screen.findByText('MEMORY.md'));

    expect(await screen.findByRole('heading', { name: 'Memory' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Preview' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Source' })).toBeInTheDocument();

    await user.click(screen.getByTitle('Fullscreen preview'));
    expect(screen.getAllByRole('heading', { name: 'Memory' })).toHaveLength(2);
  });

  it('Memory Daily 文件复用编辑器并保存到 Memory API', async () => {
    mocks.listMemory.mockResolvedValue({
      data: [{
        ...directory('daily', 'daily'),
        children: [file('2026-08-03.md', 'daily/2026-08-03.md')],
      }],
    });
    mocks.readMemoryFile.mockResolvedValue({
      data: {
        path: 'daily/2026-08-03.md',
        content: '# Daily\n\nOld content',
        truncated: false,
      },
    });

    const user = userEvent.setup();
    renderWithRouter(<WorkspacePage />);

    await user.click(screen.getByRole('button', { name: 'Memory' }));
    await user.click(await screen.findByText('daily'));
    await user.click(await screen.findByText('2026-08-03.md'));
    await user.click(await screen.findByTitle('Edit'));

    const editor = screen.getAllByRole('textbox').find(
      (element) => element.tagName === 'TEXTAREA',
    );
    expect(editor).toHaveValue('# Daily\n\nOld content');
    if (!editor) throw new Error('Memory editor not found');
    await user.clear(editor);
    await user.type(editor, '# Daily\n\nUpdated content');
    await user.click(screen.getByTitle('Save'));

    await waitFor(() => {
      expect(mocks.writeMemoryFile).toHaveBeenCalledWith(
        'daily/2026-08-03.md',
        '# Daily\n\nUpdated content',
      );
    });
    expect(mocks.toastSuccess).toHaveBeenCalledWith('Saved successfully');
  });

  it('Memory 文件按核心记忆、Project、Daily、其他根文件层级展示', async () => {
    mocks.listVisibleProjects.mockResolvedValue({
      data: [{
        id: 'prj_example',
        name: 'Flocks Raven',
        worktree: '/Users/test/workspace/flocks-raven',
      }],
    });
    mocks.listMemory.mockResolvedValue({
      data: [
        file('USER.md', 'USER.md'),
        file('MEMORY.md', 'MEMORY.md'),
        file('2026-04-07.md', '2026-04-07.md'),
        file('test.md', 'test.md'),
        {
          ...directory('daily', 'daily'),
          children: [file('2026-08-03.md', 'daily/2026-08-03.md')],
        },
        {
          ...directory('projects', 'projects'),
          children: [{
            ...directory('prj_example', 'projects/prj_example'),
            children: [file('MEMORY.md', 'projects/prj_example/MEMORY.md')],
          }, {
            ...directory('prj_stale', 'projects/prj_stale'),
            children: [file('MEMORY.md', 'projects/prj_stale/MEMORY.md')],
          }],
        },
      ],
    });

    const user = userEvent.setup();
    renderWithRouter(<WorkspacePage />);

    await user.click(screen.getByRole('button', { name: 'Memory' }));
    expect(await screen.findByText('USER.md')).toBeInTheDocument();
    expect(screen.getByText('MEMORY.md')).toBeInTheDocument();
    const projectMemory = screen.getByText('Flocks Raven / MEMORY.md');
    const daily = screen.getByText('daily');
    expect(projectMemory).toBeInTheDocument();
    expect(screen.queryByText('/Users/test/workspace/flocks-raven')).not.toBeInTheDocument();
    expect(daily).toBeInTheDocument();
    expect(screen.queryByText('projects')).not.toBeInTheDocument();
    expect(screen.queryByText('prj_stale/MEMORY.md')).not.toBeInTheDocument();
    expect(screen.getByText('2026-04-07.md')).toBeInTheDocument();
    expect(screen.getByText('test.md')).toBeInTheDocument();
    expect(
      projectMemory.compareDocumentPosition(daily) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      daily.compareDocumentPosition(screen.getByText('2026-04-07.md')) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(screen.queryByText('2026-08-03.md')).not.toBeInTheDocument();

    await user.click(projectMemory);
    expect(await screen.findByText('/Users/test/workspace/flocks-raven')).toBeInTheDocument();
  });

  it('Memory 根目录的其他文件和目录会显示但默认只读', async () => {
    mocks.listMemory.mockResolvedValue({
      data: [
        file('SHORT_MEMORY.md', 'SHORT_MEMORY.md', true, false),
        file('bak.txt', 'bak.txt', true, false),
        {
          ...directory('archive', 'archive'),
          children: [file('2026-08-18.md', 'archive/2026-08-18.md', true, false)],
        },
      ],
    });
    mocks.readMemoryFile.mockResolvedValue({
      data: {
        path: 'bak.txt',
        content: 'backup memory',
        truncated: false,
      },
    });

    const user = userEvent.setup();
    renderWithRouter(<WorkspacePage />);

    await user.click(screen.getByRole('button', { name: 'Memory' }));
    await waitFor(() => {
      expect(mocks.listMemory).toHaveBeenCalled();
    });
    expect(screen.getByText('SHORT_MEMORY.md')).toBeInTheDocument();
    expect(screen.getByText('archive')).toBeInTheDocument();
    expect(screen.getByText('bak.txt')).toBeInTheDocument();

    const archiveButton = screen.getByText('archive').closest('button');
    if (!archiveButton) throw new Error('Archive memory row button not found');
    await user.click(archiveButton);
    expect(await screen.findByText('2026-08-18.md')).toBeInTheDocument();

    await user.click(screen.getByText('bak.txt'));
    expect(await screen.findByText('backup memory')).toBeInTheDocument();
    expect(mocks.readMemoryFile).toHaveBeenCalledWith('bak.txt');
    expect(screen.queryByTitle('Edit')).not.toBeInTheDocument();
    expect(pdfMocks.getDocument).not.toHaveBeenCalled();
  });

  it('Memory 文本文件快速切换时忽略过期读取结果', async () => {
    let resolveFirst!: (value: { data: { path: string; content: string; truncated: boolean } }) => void;
    const firstRead = new Promise<{ data: { path: string; content: string; truncated: boolean } }>((resolve) => {
      resolveFirst = resolve;
    });

    mocks.listMemory.mockResolvedValue({
      data: [
        file('USER.md', 'USER.md'),
        file('MEMORY.md', 'MEMORY.md'),
      ],
    });
    mocks.readMemoryFile.mockImplementation((path: string) => {
      if (path === 'USER.md') {
        return firstRead;
      }
      return Promise.resolve({
        data: {
          path: 'MEMORY.md',
          content: '# Second',
          truncated: false,
        },
      });
    });

    const user = userEvent.setup();
    renderWithRouter(<WorkspacePage />);

    await user.click(screen.getByRole('button', { name: 'Memory' }));
    await user.click(await screen.findByText('USER.md'));
    await user.click(await screen.findByText('MEMORY.md'));

    expect(await screen.findByRole('heading', { name: 'Second' })).toBeInTheDocument();

    resolveFirst({
      data: {
        path: 'USER.md',
        content: '# First',
        truncated: false,
      },
    });

    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'First' })).not.toBeInTheDocument();
    });
    expect(screen.getByRole('heading', { name: 'Second' })).toBeInTheDocument();
  });

  it('不支持预览的文件显示下载和打开目录入口', async () => {
    mocks.list.mockResolvedValue({
      data: [file('archive.zip', 'archive.zip', false)],
    });

    const user = userEvent.setup();
    renderWithRouter(<WorkspacePage />);

    await user.click(await screen.findByText('archive.zip'));

    expect(screen.getByText('This file cannot be previewed')).toBeInTheDocument();
    expect(screen.getByText('Download file')).toBeInTheDocument();
    const revealButtons = screen.getAllByRole('button', { name: 'Open containing folder' });
    await user.click(revealButtons[revealButtons.length - 1]);
    expect(mocks.reveal).toHaveBeenCalledWith('archive.zip');
  });

  it('目录内容默认按名称升序，并支持按名称、大小和修改时间切换排序', async () => {
    mocks.list.mockResolvedValue({
      data: [
        { ...directory('beta', 'beta'), modified_at: 300 },
        { ...directory('alpha', 'alpha'), modified_at: 100 },
        { ...file('gamma.txt', 'gamma.txt'), size: 200, modified_at: 200 },
        { ...file('delta.txt', 'delta.txt'), size: 40, modified_at: 400 },
      ],
    });

    const user = userEvent.setup();
    renderWithRouter(<WorkspacePage />);

    const alpha = await screen.findByText('alpha');
    const beta = screen.getByText('beta');
    const delta = screen.getByText('delta.txt');
    const gamma = screen.getByText('gamma.txt');

    expect(alpha.compareDocumentPosition(beta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(beta.compareDocumentPosition(delta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(delta.compareDocumentPosition(gamma) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Name' }));
    expect(gamma.compareDocumentPosition(delta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(delta.compareDocumentPosition(beta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(beta.compareDocumentPosition(alpha) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Size' }));
    expect(alpha.compareDocumentPosition(beta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(beta.compareDocumentPosition(delta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(delta.compareDocumentPosition(gamma) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Size' }));
    expect(gamma.compareDocumentPosition(delta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(delta.compareDocumentPosition(beta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(beta.compareDocumentPosition(alpha) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Modified' }));
    expect(alpha.compareDocumentPosition(gamma) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(gamma.compareDocumentPosition(beta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(beta.compareDocumentPosition(delta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await user.click(screen.getByRole('button', { name: 'Modified' }));
    expect(delta.compareDocumentPosition(beta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(beta.compareDocumentPosition(gamma) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(gamma.compareDocumentPosition(alpha) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
