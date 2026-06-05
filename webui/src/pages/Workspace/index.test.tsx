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
  listMemory: vi.fn(),
  readMemoryFile: vi.fn(),
  confirm: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

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
  'files.downloadFile': 'Download file',
  'files.binaryPreview': 'Binary file cannot be previewed',
  'files.truncatedPreview': 'Preview truncated to first {{limit}}',
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
      return translations[key] ?? key;
    },
    i18n: { language: 'en-US' },
  }),
}));

vi.mock('@/components/common/Toast', () => ({
  useToast: () => ({
    success: mocks.toastSuccess,
    error: mocks.toastError,
  }),
}));

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
      listMemory: mocks.listMemory,
      readMemoryFile: mocks.readMemoryFile,
      downloadUrl: (path: string) => `/api/workspace/download?path=${encodeURIComponent(path)}`,
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

function file(name: string, path: string, isTextFile = true) {
  return {
    name,
    path,
    type: 'file' as const,
    size: 24,
    modified_at: 1710000000,
    is_text_file: isTextFile,
  };
}

describe('WorkspacePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.readFile.mockResolvedValue({ data: { content: '' } });
    mocks.writeFile.mockResolvedValue({ data: { written: true } });
    mocks.deleteFile.mockResolvedValue({ data: { deleted: true } });
    mocks.deleteDir.mockResolvedValue({ data: { deleted: true } });
    mocks.upload.mockResolvedValue({ data: { uploaded: [] } });
    mocks.createDir.mockResolvedValue({ data: { created: true } });
    mocks.listMemory.mockResolvedValue({ data: [] });
    mocks.readMemoryFile.mockResolvedValue({ data: { content: '' } });
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
    expect(screen.getByText('{"id":1}')).toBeInTheDocument();
    expect(screen.queryByTitle('Edit')).not.toBeInTheDocument();
  });
});
