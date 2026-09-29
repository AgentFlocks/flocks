import type { ReactNode } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createInstance } from 'i18next';
import { I18nextProvider } from 'react-i18next';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { knowledgebaseAPI, type Dataset, type KnowledgeDirectory, type KnowledgeEntry, type KnowledgeFolder, type Page } from '@/api/knowledgebase';
import workspace from '@/locales/en-US/workspace.json';
import KnowledgeTab from '../KnowledgeTab';
import { DOCUMENT_REFRESH_INTERVAL_MS } from './useDatasetDetail';

const feedback = vi.hoisted(() => ({ confirm: vi.fn(), success: vi.fn(), error: vi.fn() }));
vi.mock('@/api/knowledgebase', () => ({ knowledgebaseAPI: {
  status: vi.fn(), files: vi.fn(), directory: vi.fn(), datasets: vi.fn(), dataset: vi.fn(), documents: vi.fn(),
  upload: vi.fn(), createFolder: vi.fn(), updateFile: vi.fn(), removeFile: vi.fn(), createDataset: vi.fn(), updateDataset: vi.fn(), removeDataset: vi.fn(),
  linkFiles: vi.fn(), parse: vi.fn(), removeDocuments: vi.fn(), fileText: vi.fn(), previewUrl: vi.fn(), downloadUrl: vi.fn(),
  documentText: vi.fn(), documentContentUrl: vi.fn(),
} }));
vi.mock('@/components/common/Toast', () => ({ useToast: () => feedback }));
vi.mock('@/components/common/ConfirmDialog', () => ({ useConfirm: () => feedback.confirm }));
// Isolate the ordinary form UI from shared chat/model hooks.
vi.mock('@/components/common/EntitySheet', () => ({ default: ({ children, mode, hideRex, hideTest, onClose, onSubmit, submitLabel, submitDisabled, submitLoading }: {
  children: ReactNode; mode: string; hideRex?: boolean; hideTest?: boolean; onClose: () => void; onSubmit: () => void;
  submitLabel: string; submitDisabled?: boolean; submitLoading?: boolean;
}) => <div role="dialog" aria-label="Dataset form" data-mode={mode} data-hide-rex={hideRex} data-hide-test={hideTest}>
  {children}<button onClick={onClose}>Cancel</button><button disabled={submitDisabled || submitLoading} onClick={onSubmit}>{submitLabel}</button>
</div> }));
// Applying this fixture only notifies the parent; no configuration API or filesystem is involved.
vi.mock('./ConnectionSettingsSheet', () => ({ default: ({ onSaved, onClose }: { onSaved: () => void; onClose: () => void }) => (
  <button onClick={() => { onSaved(); onClose(); }}>Apply fixture connection</button>
) }));
vi.mock('@/components/common/FilePreview', () => ({
  getPreviewKind: (node: { name: string }) => node.name.toLowerCase().endsWith('.pdf') ? 'pdf' : 'text',
  FilePreviewRenderer: ({ node, content, fileAccess }: { node: { name: string; path: string; is_text_file: boolean }; content: string | null; fileAccess: { previewUrl: (id: string) => string } }) => (
    <div data-testid="preview" data-file-id={node.path} data-text-file={node.is_text_file} data-preview-url={fileAccess.previewUrl(node.path)}>{node.name}:{content}</div>
  ),
  PreviewModal: ({ node, fileAccess }: { node: { path: string }; fileAccess: { previewUrl: (id: string) => string; downloadUrl: (id: string) => string } }) => (
    <div data-testid="fullscreen" data-preview-url={fileAccess.previewUrl(node.path)} data-download-url={fileAccess.downloadUrl(node.path)} />
  ),
}));

const api = vi.mocked(knowledgebaseAPI);
const i18n = createInstance();
const root: KnowledgeFolder = { id: 'root-id', name: 'Root', parent_id: null, can_write: true };
const file: KnowledgeEntry = { id: 'source-1', name: 'guide.PDF', size: 10, parent_id: root.id, kind: 'file', can_manage: true };
const folder: KnowledgeEntry = { id: 'folder-1', name: 'Reference folder', size: null, parent_id: root.id, kind: 'folder', can_manage: true };
const childFolder: KnowledgeFolder = { id: folder.id, name: folder.name, parent_id: root.id, can_write: true };
const directoryOf = (items: KnowledgeEntry[], total = items.length, page = 1, current = root, breadcrumbs = [root], fileTotal = total): KnowledgeDirectory => ({
  items, total, page, file_total: fileTotal, current_folder: current, breadcrumbs,
});
const dataset: Dataset = { id: 'set-1', name: 'Notes', description: 'Original description', document_count: 0, chunk_count: 0 };
const secondDataset: Dataset = { ...dataset, id: 'set-2', name: 'Other notes' };
const pageOf = <T,>(items: T[], total = items.length, page = 1) => ({ items, total, page });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function Providers({ children }: { children: ReactNode }) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}
async function openDataset(user: ReturnType<typeof userEvent.setup>, name = dataset.name) {
  await user.click(screen.getByRole('tab', { name: /^Knowledge Sets/ }));
  await user.click(await screen.findByRole('button', { name }));
  return screen.findByRole('table', { name: workspace.knowledge.datasets.documents });
}

beforeAll(async () => {
  await i18n.init({ lng: 'en-US', resources: { 'en-US': { workspace } }, interpolation: { escapeValue: false } });
});
beforeEach(() => {
  vi.resetAllMocks();
  api.status.mockResolvedValue({ configured: true, ready: true });
  api.directory.mockResolvedValue(directoryOf([file]));
  api.datasets.mockResolvedValue(pageOf([dataset, secondDataset]));
  api.dataset.mockResolvedValue(dataset);
  api.documents.mockResolvedValue(pageOf([]));
  api.fileText.mockResolvedValue('Source content');
  api.documentText.mockResolvedValue('Document original content');
  api.documentContentUrl.mockImplementation((datasetId, documentId, inline = true) => `/api/knowledgebase/datasets/${encodeURIComponent(datasetId)}/documents/${encodeURIComponent(documentId)}/content?inline=${inline}`);
  api.downloadUrl.mockImplementation(id => `/api/knowledgebase/files/${encodeURIComponent(id)}/content`);
  api.previewUrl.mockImplementation(id => `/api/knowledgebase/files/${encodeURIComponent(id)}/content?inline=1`);
  feedback.confirm.mockResolvedValue(true);
});

describe('Knowledge workspace UI', () => {
  it('keeps source and action controls available when the shared list pane becomes narrow', async () => {
    const measurements: Array<{ target: Element; emit: (width: number) => void }> = [];
    class Observer {
      constructor(private callback: ResizeObserverCallback) {}
      observe(target: Element) { measurements.push({ target, emit: width => this.callback([{ target, contentRect: { width } } as ResizeObserverEntry], this as unknown as ResizeObserver) }); }
      disconnect() {}
      unobserve() {}
    }
    vi.stubGlobal('ResizeObserver', Observer);
    try {
      const user = userEvent.setup();
      api.documents.mockResolvedValue(pageOf([{ id: 'doc-1', dataset_id: dataset.id, name: 'guide.pdf', status: 'ready', progress: 1, chunk_count: 1, created_at: '2026-09-24T05:20:00.000Z' }]));
      render(<KnowledgeTab />, { wrapper: Providers });
      const table = await openDataset(user);
      expect(within(table).getByRole('columnheader', { name: workspace.files.columns.modified })).toBeInTheDocument();
      const pane = table.closest('.kb-document-workbench')!.firstElementChild!;
      const measurement = measurements.find(item => item.target === pane)!;
      expect(measurement).toBeDefined();
      act(() => measurement.emit(500));
      expect(table).toHaveClass('is-compact');
      expect(within(table).queryByRole('columnheader', { name: workspace.files.columns.modified })).not.toBeInTheDocument();
      expect(within(table).queryByRole('columnheader', { name: workspace.knowledge.status })).not.toBeInTheDocument();
      expect(within(table).getByRole('button', { name: 'guide.pdf' })).toBeEnabled();
      expect(within(table).queryByRole('columnheader', { name: workspace.knowledge.datasets.sourceFile })).not.toBeInTheDocument();
      expect(within(table).getByRole('button', { name: workspace.knowledge.datasets.parse })).toBeEnabled();
      expect(within(table).getByRole('button', { name: workspace.knowledge.datasets.unlink })).toBeEnabled();
      act(() => measurement.emit(900));
      expect(table).not.toHaveClass('is-compact');
      expect(within(table).getByRole('columnheader', { name: workspace.files.columns.modified })).toBeInTheDocument();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('defaults to rich cards and switches to horizontal cards without inventing metadata or session usage', async () => {
    const user = userEvent.setup();
    const unknown = { ...dataset, document_count: null, chunk_count: null };
    const known = { ...secondDataset, document_count: 4, chunk_count: 7 };
    api.datasets.mockResolvedValue(pageOf([unknown, known], 12));
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(screen.getByRole('tab', { name: /^Knowledge Sets/ }));
    const gallery = await screen.findByRole('region', { name: workspace.knowledge.datasets.gallery });
    await within(gallery).findByRole('button', { name: unknown.name });
    const list = within(gallery).getByRole('list');
    expect(list).toHaveClass('kb-dataset-grid');
    expect(list).toHaveAttribute('data-layout', 'grid');
    expect(within(gallery).getByRole('button', { name: workspace.knowledge.datasets.gridView })).toHaveAttribute('aria-pressed', 'true');
    const cards = within(list).getAllByRole('listitem');
    expect(cards[0]).toHaveClass('kb-dataset-card');
    expect(within(cards[0]).getByText(unknown.description)).toBeInTheDocument();
    expect(within(cards[0]).queryByText(/Documents:|Chunks:/)).not.toBeInTheDocument();
    expect(within(cards[0]).queryByText(workspace.knowledge.datasets.sessionSelectionHint)).not.toBeInTheDocument();
    expect(within(cards[0]).queryByText(workspace.knowledge.datasets.awaitingFiles)).not.toBeInTheDocument();
    expect(within(cards[1]).getByText('Documents: 4')).toBeInTheDocument();
    expect(within(cards[1]).getByText('Chunks: 7')).toBeInTheDocument();
    const metadataRow = cards[1].querySelector('.kb-dataset-card-footer')!;
    expect(metadataRow).toContainElement(within(cards[1]).getByText('Documents: 4'));
    expect(within(metadataRow).getByRole('button', { name: workspace.knowledge.datasets.open })).toBeInTheDocument();
    expect(within(cards[0]).getByRole('button', { name: workspace.knowledge.datasets.edit })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /session|stop parsing|new folder/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/used in .*session|added to .*session/i)).not.toBeInTheDocument();
    const requests = api.datasets.mock.calls.length;
    await user.click(within(gallery).getByRole('button', { name: workspace.knowledge.datasets.listView }));
    expect(list).toHaveAttribute('data-layout', 'list');
    expect(within(gallery).getByRole('button', { name: workspace.knowledge.datasets.listView })).toHaveAttribute('aria-pressed', 'true');
    expect(within(list).getAllByRole('listitem')).toHaveLength(2);
    expect(cards[0].querySelector('.kb-dataset-card-head')).toContainElement(within(cards[0]).getByRole('button', { name: workspace.knowledge.datasets.edit }));
    expect(cards[0].querySelector('.kb-dataset-card-footer')).not.toContainElement(within(cards[0]).getByRole('button', { name: workspace.knowledge.datasets.edit }));
    expect(api.datasets).toHaveBeenCalledTimes(requests);
    expect(screen.getByRole('tab', { name: 'Knowledge Sets (12)' })).toBeInTheDocument();
  });

  it('keeps counts and the open action on one row without a footer hint', async () => {
    const user = userEvent.setup();
    api.datasets.mockResolvedValue(pageOf([dataset]));
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(screen.getByRole('tab', { name: /^Knowledge Sets/ }));
    const card = (await screen.findByRole('button', { name: dataset.name })).closest('li')!;
    const row = card.querySelector('.kb-dataset-card-footer')!;
    expect(within(card).queryByText(workspace.knowledge.datasets.awaitingFiles)).not.toBeInTheDocument();
    expect(within(card).queryByText(workspace.knowledge.datasets.sessionSelectionHint)).not.toBeInTheDocument();
    expect(row).toContainElement(within(card).getByText('Documents: 0'));
    expect(within(row).getByRole('button', { name: workspace.knowledge.datasets.open })).toBeInTheDocument();
  });

  it('keeps detail editing by the title and uses the shared split for document originals', async () => {
    const user = userEvent.setup();
    api.dataset.mockResolvedValue({ ...dataset, document_count: 1, chunk_count: null });
    api.documents.mockResolvedValue(pageOf([{ id: 'doc-1', dataset_id: dataset.id, name: 'original.PDF', status: 'ready', progress: null, chunk_count: null, created_at: '2026-09-28T10:00:00Z' }]));
    const { container } = render(<KnowledgeTab />, { wrapper: Providers });
    const table = await openDataset(user);
    const back = screen.getByRole('button', { name: workspace.knowledge.datasets.back });
    const refresh = screen.getByRole('button', { name: workspace.knowledge.refresh });
    expect(back.parentElement).toBe(refresh.parentElement);
    expect(back.parentElement).toHaveClass('justify-end');
    expect(screen.queryByText(workspace.knowledge.datasets.refreshingDocuments)).not.toBeInTheDocument();
    expect(screen.queryByText(workspace.knowledge.datasets.refreshStopped)).not.toBeInTheDocument();
    expect(screen.queryByText(workspace.knowledge.datasets.pageSearch)).not.toBeInTheDocument();
    const heading = screen.getByRole('heading', { name: dataset.name });
    expect(heading.parentElement).toHaveClass('kb-dataset-detail-title');
    expect(within(heading.parentElement!).getByRole('button', { name: workspace.knowledge.datasets.edit })).toBeInTheDocument();
    expect(screen.getByText('Documents: 1')).toBeInTheDocument();
    expect(screen.queryByText(/Chunks:/)).not.toBeInTheDocument();
    expect(table).toHaveClass('kb-document-table');
    expect(within(table).getByRole('columnheader', { name: workspace.knowledge.type })).toHaveClass('kb-document-type');
    expect(within(table).getByRole('columnheader', { name: workspace.files.columns.modified })).toHaveClass('kb-document-date');
    expect(within(table).getByText(workspace.knowledge.states.ready)).toHaveAttribute('data-state', 'ready');
    expect(container.querySelector('.kb-document-workbench')).toHaveClass('flex', 'min-h-0');
    expect(screen.queryByRole('button', { name: workspace.files.preview.resize })).not.toBeInTheDocument();
    await user.click(within(table).getAllByRole('row')[1]);
    expect(screen.getByRole('button', { name: workspace.files.preview.resize })).toBeInTheDocument();
    expect(screen.getByTestId('preview')).toHaveAttribute('data-preview-url', '/api/knowledgebase/datasets/set-1/documents/doc-1/content?inline=true');
    expect(screen.queryByRole('button', { name: workspace.knowledge.files.associate })).not.toBeInTheDocument();
    expect(api.documentText).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: workspace.knowledge.files.closePreview }));
    expect(screen.queryByRole('button', { name: workspace.files.preview.resize })).not.toBeInTheDocument();
  });

  it('retains selected files after an add failure and retries without starting parsing', async () => {
    const user = userEvent.setup();
    api.linkFiles.mockRejectedValueOnce(new Error('Add failed')).mockResolvedValue({ dataset_id: dataset.id });
    render(<KnowledgeTab />, { wrapper: Providers });
    await openDataset(user);
    await user.click(screen.getByRole('button', { name: workspace.knowledge.datasets.linkFiles }));
    const dialog = screen.getByRole('dialog', { name: workspace.knowledge.datasets.linkFiles });
    expect(dialog).toHaveClass('kb-modal--knowledge');
    const checkbox = await within(dialog).findByRole('checkbox', { name: file.name });
    await user.click(checkbox);
    await user.click(within(dialog).getByRole('button', { name: workspace.knowledge.datasets.linkFiles }));
    await waitFor(() => expect(feedback.error).toHaveBeenCalledWith(workspace.knowledge.loadFailed, 'Add failed'));
    expect(checkbox).toBeChecked();
    await user.click(within(dialog).getByRole('button', { name: workspace.knowledge.datasets.linkFiles }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(api.linkFiles).toHaveBeenCalledTimes(2);
    expect(api.linkFiles).toHaveBeenLastCalledWith(dataset.id, [file.id]);
    expect(api.parse).not.toHaveBeenCalled();
  });

  it('does not invent zero totals while the directory count is pending or unavailable', async () => {
    const count = deferred<KnowledgeDirectory>();
    api.directory.mockReturnValue(count.promise);
    api.datasets.mockRejectedValue(new Error('unavailable'));
    render(<KnowledgeTab />, { wrapper: Providers });
    await waitFor(() => expect(api.directory).toHaveBeenCalled());
    expect(screen.getByRole('tab', { name: workspace.knowledge.files.title })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: workspace.knowledge.datasets.title })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: workspace.knowledge.files.upload })).not.toBeInTheDocument();
    await act(async () => { count.resolve(directoryOf([])); });
    expect(await screen.findByRole('tab', { name: 'Files (0)' })).toHaveAttribute('title', workspace.knowledge.files.currentFolderCount);
    expect(screen.getByRole('tab', { name: workspace.knowledge.datasets.title })).toBeInTheDocument();
    expect(api.files).not.toHaveBeenCalled();
  });

  it('keeps unfiltered totals on tabs through file and dataset searches and only shows page numbers below lists', async () => {
    const user = userEvent.setup();
    api.directory.mockImplementation(options => Promise.resolve(directoryOf([file], options?.q ? 1 : 41, options?.page ?? 1, root, [root], 41)));
    api.datasets.mockImplementation(options => Promise.resolve(pageOf([dataset], options?.q ? 1 : 12, options?.page ?? 1)));
    render(<KnowledgeTab />, { wrapper: Providers });
    expect(await screen.findByRole('tab', { name: 'Files (41)' })).toBeInTheDocument();
    expect(await screen.findByRole('tab', { name: 'Knowledge Sets (12)' })).toBeInTheDocument();
    expect(screen.getByText('Page 1')).toBeInTheDocument();
    expect(screen.queryByText(/Items:/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: workspace.knowledge.nextPage })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: workspace.knowledge.nextPage }));
    expect(await screen.findByText('Page 2')).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText(workspace.knowledge.files.search), 'guide');
    expect(await screen.findByText('Page 1')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: workspace.knowledge.nextPage })).toBeDisabled();
    expect(screen.getByRole('tab', { name: 'Files (41)' })).toBeInTheDocument();
    expect(api.files).not.toHaveBeenCalled();
    expect(api.directory.mock.calls.some(([options]) => options?.page_size === 1)).toBe(false);
    expect(screen.getByRole('textbox', { name: workspace.knowledge.files.search })).toHaveAttribute('title', workspace.knowledge.files.pageSearch);
    expect(screen.queryByText(workspace.knowledge.files.pageSearch)).not.toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: /^Knowledge Sets/ }));
    await user.type(await screen.findByPlaceholderText(workspace.knowledge.datasets.search), 'Notes');
    await waitFor(() => expect(screen.getByRole('button', { name: workspace.knowledge.nextPage })).toBeDisabled());
    expect(screen.getByRole('tab', { name: 'Knowledge Sets (12)' })).toBeInTheDocument();
    expect(api.datasets.mock.calls.filter(([options]) => options?.page_size === 1)).toHaveLength(1);
  });

  it('clears old counts and previews on connection changes and ignores late count responses', async () => {
    const user = userEvent.setup();
    const oldDatasetCount = deferred<Page<Dataset>>();
    api.datasets.mockImplementation(() => oldDatasetCount.promise);
    api.directory.mockResolvedValue(directoryOf([file], 17));
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: file.name }));
    expect(screen.getByTestId('preview')).toBeInTheDocument();
    expect(await screen.findByRole('tab', { name: 'Files (17)' })).toBeInTheDocument();
    const ready = deferred<{ configured: boolean; ready: boolean }>();
    const newFileCount = deferred<KnowledgeDirectory>();
    api.status.mockImplementation(() => ready.promise);
    api.directory.mockReturnValue(newFileCount.promise);
    api.datasets.mockResolvedValue(pageOf([], 2));
    await user.click(screen.getByRole('button', { name: workspace.knowledge.connection.title }));
    await user.click(screen.getByRole('button', { name: 'Apply fixture connection' }));
    expect(screen.getByRole('tab', { name: workspace.knowledge.files.title })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: workspace.knowledge.datasets.title })).toBeInTheDocument();
    expect(screen.queryByTestId('preview')).not.toBeInTheDocument();
    await act(async () => { oldDatasetCount.resolve(pageOf([], 999)); ready.resolve({ configured: true, ready: true }); });
    expect(await screen.findByRole('tab', { name: 'Knowledge Sets (2)' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /999/ })).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: workspace.knowledge.files.title })).toBeInTheDocument();
    await act(async () => { newFileCount.resolve(directoryOf([])); });
    expect(await screen.findByRole('tab', { name: 'Files (0)' })).toBeInTheDocument();
  });

  it('refreshes unfiltered file counts after upload and delete without parsing automatically', async () => {
    const user = userEvent.setup();
    let files = [file];
    api.directory.mockImplementation(() => Promise.resolve(directoryOf(files)));
    const uploaded = { ...file, id: 'source-2', name: 'extra.txt' };
    api.upload.mockImplementation(async () => { files = [...files, uploaded]; return uploaded; });
    api.removeFile.mockImplementation(async id => { files = files.filter(item => item.id !== id); });
    const { container } = render(<KnowledgeTab />, { wrapper: Providers });
    expect(await screen.findByRole('tab', { name: 'Files (1)' })).toBeInTheDocument();
    await user.upload(container.querySelector<HTMLInputElement>('input[type="file"]')!, new File(['text'], 'extra.txt', { type: 'text/plain' }));
    expect(api.upload).toHaveBeenCalledWith(expect.any(File), root.id);
    expect(await screen.findByRole('tab', { name: 'Files (2)' })).toBeInTheDocument();
    const row = (await screen.findByRole('button', { name: uploaded.name })).closest('tr')!;
    await user.click(within(row).getByRole('button', { name: workspace.knowledge.delete }));
    expect(await screen.findByRole('tab', { name: 'Files (1)' })).toBeInTheDocument();
    expect(api.removeFile).toHaveBeenCalledWith(uploaded.id);
    expect(api.parse).not.toHaveBeenCalled();
  });

  it('refreshes the file badge with the list after an external file change', async () => {
    const user = userEvent.setup();
    let files = [file];
    api.directory.mockImplementation(() => Promise.resolve(directoryOf(files)));
    render(<KnowledgeTab />, { wrapper: Providers });
    expect(await screen.findByRole('tab', { name: 'Files (1)' })).toBeInTheDocument();
    files = [...files, { ...file, id: 'external-file', name: 'external.txt' }];
    await user.click(screen.getByRole('button', { name: workspace.knowledge.refresh }));
    expect(await screen.findByRole('tab', { name: 'Files (2)' })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'external.txt' })).toBeInTheDocument();
    expect(api.parse).not.toHaveBeenCalled();
  });

  it('places a short search field in the root toolbar without the redundant root label', async () => {
    render(<KnowledgeTab />, { wrapper: Providers });
    const search = await screen.findByRole('textbox', { name: workspace.knowledge.files.search });
    await screen.findByRole('button', { name: file.name });
    const refresh = screen.getByRole('button', { name: workspace.knowledge.refresh });
    expect(search).toHaveClass('max-w-xs');
    expect(search.parentElement!.parentElement).toBe(refresh.parentElement!.parentElement);
    expect(screen.queryByText(workspace.knowledge.files.root)).not.toBeInTheDocument();
  });

  it('keeps a root navigation action when a child directory cannot load', async () => {
    const user = userEvent.setup();
    api.directory.mockImplementation(options => options?.parent_id === folder.id
      ? Promise.reject(new Error('Folder unavailable'))
      : Promise.resolve(directoryOf([folder, file], 2, 1, root, [root], 1)));
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: folder.name }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Folder unavailable');
    await user.click(screen.getByRole('button', { name: workspace.knowledge.files.root }));
    expect(await screen.findByRole('button', { name: folder.name })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Files (1)' })).toBeInTheDocument();
  });

  it('keeps the name flexible and fixed-width metadata on the right without inventing a modification time', async () => {
    api.directory.mockResolvedValue(directoryOf([{ ...file, created_at: '2026-09-28T10:00:00Z' }]));
    render(<KnowledgeTab />, { wrapper: Providers });
    const table = await screen.findByRole('table', { name: workspace.knowledge.files.table });
    expect(table).toHaveClass('table-fixed');
    const headers = within(table).getAllByRole('columnheader');
    expect(headers.slice(0, 4).map(item => item.textContent)).toEqual([
      workspace.knowledge.name, workspace.knowledge.type, workspace.files.columns.size, workspace.files.columns.modified,
    ]);
    expect(headers[0].style.width).toBe('');
    expect(headers[1]).toHaveClass('w-20', 'text-right');
    expect(headers[2]).toHaveClass('w-24', 'text-right');
    expect(headers[3]).toHaveClass('w-40', 'text-right');
    expect(headers[4]).toHaveClass('w-28');
    expect(headers).toHaveLength(5);
    expect(headers[3]).toHaveTextContent(workspace.files.columns.modified);
    expect(within(table).queryByRole('columnheader', { name: workspace.knowledge.datasets.createdAt })).not.toBeInTheDocument();
    const cells = within(within(table).getAllByRole('row')[1]).getAllByRole('cell');
    expect(cells[3]).toHaveTextContent(/^—$/);
    expect(cells[3].querySelector('time')).toBeNull();
  });

  it('navigates real folders and resets search, page, preview and count before loading the new directory', async () => {
    const user = userEvent.setup();
    const loaded = deferred<KnowledgeDirectory>();
    const nestedFile = { ...file, id: 'nested-file', name: 'nested.pdf', parent_id: folder.id };
    api.directory.mockImplementation(options => options?.parent_id === folder.id
      ? loaded.promise
      : Promise.resolve(directoryOf([folder, file], 41, options?.page ?? 1, root, [root], 40)));
    render(<KnowledgeTab />, { wrapper: Providers });
    expect(await screen.findByRole('tab', { name: 'Files (40)' })).toBeInTheDocument();
    await user.type(screen.getByRole('textbox', { name: workspace.knowledge.files.search }), 'reference');
    await user.click(await screen.findByRole('button', { name: workspace.knowledge.nextPage }));
    await screen.findByText('Page 2');
    await user.click(await screen.findByRole('button', { name: file.name }));
    expect(screen.getByTestId('preview')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: folder.name }));
    expect(screen.getByRole('textbox', { name: workspace.knowledge.files.search })).toHaveValue('');
    expect(screen.queryByTestId('preview')).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: workspace.knowledge.files.title })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: workspace.knowledge.files.upload })).not.toBeInTheDocument();
    await waitFor(() => expect(api.directory).toHaveBeenLastCalledWith({ parent_id: folder.id, q: '', page: 1, page_size: 20 }, expect.any(AbortSignal)));
    await act(async () => { loaded.resolve(directoryOf([nestedFile], 1, 1, childFolder, [root, childFolder], 1)); });
    expect(await screen.findByRole('tab', { name: 'Files (1)' })).toBeInTheDocument();
    expect(screen.getByText('Page 1')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: workspace.knowledge.files.breadcrumbs })).toHaveTextContent(folder.name);
    await user.click(screen.getByRole('button', { name: workspace.files.back }));
    expect(await screen.findByRole('button', { name: file.name })).toBeInTheDocument();
    expect(await screen.findByRole('tab', { name: 'Files (40)' })).toBeInTheDocument();
    expect(api.files).not.toHaveBeenCalled();
    expect(api.fileText).not.toHaveBeenCalled();
  });

  it('ignores an aborted folder response after returning to root', async () => {
    const user = userEvent.setup();
    const stale = deferred<KnowledgeDirectory>();
    api.directory.mockImplementation(options => options?.parent_id === folder.id
      ? stale.promise
      : Promise.resolve(directoryOf([folder, file], 2, 1, root, [root], 1)));
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: folder.name }));
    await waitFor(() => expect(api.directory).toHaveBeenCalledWith(expect.objectContaining({ parent_id: folder.id }), expect.any(AbortSignal)));
    const signal = api.directory.mock.calls.find(([options]) => options?.parent_id === folder.id)![1]!;
    await user.click(within(screen.getByRole('navigation', { name: workspace.knowledge.files.breadcrumbs })).getByRole('button', { name: workspace.knowledge.files.root }));
    expect(await screen.findByRole('button', { name: file.name })).toBeInTheDocument();
    expect(signal.aborted).toBe(true);
    await act(async () => { stale.resolve(directoryOf([], 0, 1, childFolder, [root, childFolder], 99)); });
    expect(screen.getByRole('tab', { name: 'Files (1)' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Files (99)' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: file.name })).toBeInTheDocument();
  });

  it('allows read-only folder browsing and file preview without exposing write controls', async () => {
    const user = userEvent.setup();
    const readOnly = { ...childFolder, can_write: false };
    const protectedFile = { ...file, parent_id: folder.id, can_manage: false };
    api.directory.mockImplementation(options => Promise.resolve(options?.parent_id === folder.id
      ? directoryOf([protectedFile], 1, 1, readOnly, [root, readOnly], 1)
      : directoryOf([{ ...folder, can_manage: false }], 1, 1, root, [root], 0)));
    render(<KnowledgeTab />, { wrapper: Providers });
    const folderRow = (await screen.findByRole('button', { name: folder.name })).closest('tr')!;
    expect(within(folderRow).queryByRole('button', { name: workspace.knowledge.delete })).not.toBeInTheDocument();
    expect(within(folderRow).queryByRole('button', { name: workspace.knowledge.files.rename })).not.toBeInTheDocument();
    await user.click(within(folderRow).getByRole('button', { name: folder.name }));
    await screen.findByRole('button', { name: file.name });
    expect(screen.getByText(workspace.knowledge.files.readOnlyFolder)).toBeInTheDocument();
    for (const name of [workspace.knowledge.files.newFolder, workspace.knowledge.files.upload, workspace.knowledge.files.rename, workspace.knowledge.files.move, workspace.knowledge.delete]) {
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    }
    await user.click(screen.getByRole('button', { name: file.name }));
    expect(screen.getByTestId('preview')).toBeInTheDocument();
    expect(api.upload).not.toHaveBeenCalled();
    expect(api.updateFile).not.toHaveBeenCalled();
    expect(api.removeFile).not.toHaveBeenCalled();
  });

  it('creates, renames and deletes an empty folder through its parent without reusing its old id', async () => {
    const user = userEvent.setup();
    let entries: KnowledgeEntry[] = [];
    api.directory.mockImplementation(() => Promise.resolve(directoryOf(entries, entries.length, 1, root, [root], 0)));
    api.createFolder.mockImplementation(async () => { entries = [folder]; return folder; });
    api.updateFile.mockImplementation(async (_id, fields) => { entries = [{ ...folder, id: 'renamed-folder', name: fields.name! }]; });
    api.removeFile.mockImplementation(async () => { entries = []; });
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: workspace.knowledge.files.newFolder }));
    await user.type(screen.getByRole('textbox', { name: workspace.files.dirNamePlaceholder }), folder.name);
    await user.click(screen.getByRole('button', { name: workspace.files.create }));
    await screen.findByRole('button', { name: folder.name });
    expect(api.createFolder).toHaveBeenCalledWith({ name: folder.name, parent_id: root.id });
    expect(screen.getByRole('tab', { name: 'Files (0)' })).toBeInTheDocument();
    const oldRow = screen.getByRole('button', { name: folder.name }).closest('tr')!;
    expect(within(oldRow).queryByRole('button', { name: workspace.knowledge.files.move })).not.toBeInTheDocument();
    await user.click(within(oldRow).getByRole('button', { name: workspace.knowledge.files.rename }));
    const name = screen.getByRole('textbox', { name: `Rename ${folder.name}` });
    await user.clear(name);
    await user.type(name, 'Renamed folder');
    await user.click(screen.getByRole('button', { name: workspace.knowledge.files.saveName }));
    const renamedRow = (await screen.findByRole('button', { name: 'Renamed folder' })).closest('tr')!;
    expect(api.updateFile).toHaveBeenCalledWith(folder.id, { name: 'Renamed folder' });
    expect(api.directory.mock.calls.every(([options]) => options?.parent_id === undefined)).toBe(true);
    await user.click(within(renamedRow).getByRole('button', { name: workspace.knowledge.delete }));
    await waitFor(() => expect(api.removeFile).toHaveBeenCalledWith('renamed-folder'));
    expect(feedback.confirm).toHaveBeenCalledWith(expect.objectContaining({ description: expect.stringMatching(/recursive.*concurrent.*not atomic/) }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Renamed folder' })).not.toBeInTheDocument());
    expect(api.parse).not.toHaveBeenCalled();
  });

  it('uploads into the loaded child folder identity without starting parsing', async () => {
    const user = userEvent.setup();
    const uploaded = { ...file, parent_id: folder.id, name: 'inside.txt' };
    let entries: KnowledgeEntry[] = [];
    api.directory.mockImplementation(options => Promise.resolve(options?.parent_id === folder.id
      ? directoryOf(entries, entries.length, 1, childFolder, [root, childFolder])
      : directoryOf([folder], 1, 1, root, [root], 0)));
    api.upload.mockImplementation(async () => { entries = [uploaded]; return uploaded; });
    const { container } = render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: folder.name }));
    await screen.findByRole('button', { name: workspace.knowledge.files.upload });
    const source = new File(['inside'], 'inside.txt', { type: 'text/plain' });
    await user.upload(container.querySelector<HTMLInputElement>('input[type="file"]')!, source);
    expect(await screen.findByRole('button', { name: 'inside.txt' })).toBeInTheDocument();
    expect(api.upload).toHaveBeenCalledWith(source, folder.id);
    expect(await screen.findByRole('tab', { name: 'Files (1)' })).toBeInTheDocument();
    expect(api.parse).not.toHaveBeenCalled();
  });

  it('moves only files to a loaded writable folder and disables stale or read-only targets', async () => {
    const user = userEvent.setup();
    const protectedEntry: KnowledgeEntry = { ...folder, id: 'system-folder', name: 'System folder', can_manage: false };
    const protectedFolder: KnowledgeFolder = { ...childFolder, id: protectedEntry.id, name: protectedEntry.name, can_write: false };
    const target = deferred<KnowledgeDirectory>();
    let moved = false;
    api.directory.mockImplementation(options => {
      if (options?.parent_id === folder.id) return target.promise;
      if (options?.parent_id === protectedEntry.id) return Promise.resolve(directoryOf([], 0, 1, protectedFolder, [root, protectedFolder]));
      return Promise.resolve(directoryOf([folder, protectedEntry, ...(moved ? [] : [file])], moved ? 2 : 3, 1, root, [root], moved ? 0 : 1));
    });
    api.updateFile.mockImplementation(async () => { moved = true; });
    render(<KnowledgeTab />, { wrapper: Providers });
    const row = (await screen.findByRole('button', { name: file.name })).closest('tr')!;
    await user.click(within(row).getByRole('button', { name: workspace.knowledge.files.move }));
    const dialog = screen.getByRole('dialog', { name: `Move ${file.name}` });
    await within(dialog).findByRole('button', { name: folder.name });
    expect(within(dialog).getByRole('button', { name: workspace.knowledge.files.moveHere })).toBeDisabled();
    await user.click(within(dialog).getByRole('button', { name: protectedEntry.name }));
    await within(dialog).findByText(workspace.knowledge.files.readOnlyFolder);
    expect(within(dialog).getByRole('button', { name: workspace.knowledge.files.moveHere })).toBeDisabled();
    await user.click(within(dialog).getByRole('button', { name: workspace.knowledge.files.root }));
    await user.click(await within(dialog).findByRole('button', { name: folder.name }));
    expect(within(dialog).getByRole('button', { name: workspace.knowledge.files.moveHere })).toBeDisabled();
    expect(api.updateFile).not.toHaveBeenCalled();
    await act(async () => { target.resolve(directoryOf([], 0, 1, childFolder, [root, childFolder])); });
    await user.click(within(dialog).getByRole('button', { name: workspace.knowledge.files.moveHere }));
    await waitFor(() => expect(api.updateFile).toHaveBeenCalledWith(file.id, { parent_id: folder.id }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(await screen.findByRole('tab', { name: 'Files (0)' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: file.name })).not.toBeInTheDocument();
    expect(api.linkFiles).not.toHaveBeenCalled();
  });

  it('keeps picker selections across folders and never associates a folder id', async () => {
    const user = userEvent.setup();
    const nested = { ...file, id: 'nested-source', name: 'nested.txt', parent_id: folder.id };
    api.directory.mockImplementation(options => Promise.resolve(options?.parent_id === folder.id
      ? directoryOf([nested], 1, 1, childFolder, [root, childFolder])
      : directoryOf([folder, file], 2, 1, root, [root], 1)));
    api.linkFiles.mockResolvedValue({ dataset_id: dataset.id });
    render(<KnowledgeTab />, { wrapper: Providers });
    await openDataset(user);
    await user.click(screen.getByRole('button', { name: workspace.knowledge.datasets.linkFiles }));
    const dialog = screen.getByRole('dialog', { name: workspace.knowledge.datasets.linkFiles });
    await user.click(await within(dialog).findByRole('checkbox', { name: file.name }));
    expect(within(dialog).queryByRole('checkbox', { name: folder.name })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: folder.name }));
    await user.click(await within(dialog).findByRole('checkbox', { name: nested.name }));
    await user.click(within(dialog).getByRole('button', { name: workspace.knowledge.files.root }));
    expect(await within(dialog).findByRole('checkbox', { name: file.name })).toBeChecked();
    await user.click(within(dialog).getByRole('button', { name: workspace.knowledge.datasets.linkFiles }));
    await waitFor(() => expect(api.linkFiles).toHaveBeenCalledWith(dataset.id, [file.id, nested.id]));
    expect(api.parse).not.toHaveBeenCalled();
    expect(api.files).not.toHaveBeenCalled();
  });

  it('uses text extension columns in a native file table, including a dash for absent extensions', async () => {
    api.directory.mockResolvedValue(directoryOf([file, { ...file, id: 'source-2', name: 'README' }]));
    render(<KnowledgeTab />, { wrapper: Providers });
    const table = await screen.findByRole('table', { name: workspace.knowledge.files.table });
    expect(within(table).getByRole('columnheader', { name: workspace.knowledge.type })).toBeInTheDocument();
    const rows = within(table).getAllByRole('row');
    expect(within(rows[1]).getAllByRole('cell')[1]).toHaveTextContent(/^PDF$/);
    expect(within(rows[2]).getAllByRole('cell')[1]).toHaveTextContent(/^—$/);
  });

  it('edits name and description from a list row using a form-only sheet and reloads after a bodyless success', async () => {
    const user = userEvent.setup();
    let current = { ...dataset };
    api.datasets.mockImplementation(() => Promise.resolve(pageOf([current])));
    api.updateDataset.mockImplementation(async (_id, fields) => { current = { ...current, ...fields }; });
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(screen.getByRole('tab', { name: /^Knowledge Sets/ }));
    await user.click(await screen.findByRole('button', { name: workspace.knowledge.datasets.edit }));
    const sheet = screen.getByRole('dialog', { name: 'Dataset form' });
    expect(sheet).toHaveAttribute('data-mode', 'edit');
    expect(sheet).toHaveAttribute('data-hide-rex', 'true');
    expect(sheet).toHaveAttribute('data-hide-test', 'true');
    const name = within(sheet).getByRole('textbox', { name: workspace.knowledge.name });
    const description = within(sheet).getByRole('textbox', { name: workspace.knowledge.datasets.descriptionLabel });
    expect(name).toHaveValue(dataset.name);
    expect(description).toHaveValue(dataset.description);
    await user.clear(name);
    await user.type(name, '   ');
    expect(within(sheet).getByRole('button', { name: workspace.knowledge.save })).toBeDisabled();
    await user.clear(name);
    await user.type(name, '  Renamed  ');
    await user.clear(description);
    await user.click(within(sheet).getByRole('button', { name: workspace.knowledge.save }));
    expect(api.updateDataset).toHaveBeenCalledWith(dataset.id, { name: 'Renamed', description: '' });
    expect(await screen.findByRole('button', { name: 'Renamed' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.createDataset).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: workspace.knowledge.datasets.edit }));
    await user.click(screen.getByRole('button', { name: workspace.knowledge.cancel }));
    expect(api.updateDataset).toHaveBeenCalledTimes(1);
  });

  it('isolates the A draft from background keyboard/edit actions and resets it when reopening B or create', async () => {
    const user = userEvent.setup();
    api.updateDataset.mockRejectedValueOnce(new Error('Keep A draft')).mockResolvedValue(undefined);
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(screen.getByRole('tab', { name: /^Knowledge Sets/ }));
    await screen.findByRole('button', { name: dataset.name });
    const gallery = screen.getByRole('region', { name: workspace.knowledge.datasets.gallery });
    const [editA, editB] = within(gallery).getAllByRole('button', { name: workspace.knowledge.datasets.edit });
    await user.click(editA);
    const sheet = screen.getByRole('dialog', { name: 'Dataset form' });
    const name = within(sheet).getByRole('textbox', { name: workspace.knowledge.name });
    await user.clear(name);
    await user.type(name, 'A unsaved draft');
    expect(gallery).toHaveAttribute('inert');
    await user.tab({ shift: true });
    expect(within(sheet).getByRole('button', { name: workspace.knowledge.save })).toHaveFocus();
    await user.tab();
    expect(name).toHaveFocus();
    act(() => editB.focus());
    expect(sheet).toContainElement(document.activeElement as HTMLElement);
    fireEvent.keyDown(editB, { key: 'Enter' });
    fireEvent.click(editB);
    expect(name).toHaveValue('A unsaved draft');
    await user.click(within(sheet).getByRole('button', { name: workspace.knowledge.save }));
    await waitFor(() => expect(feedback.error).toHaveBeenCalledWith(workspace.knowledge.loadFailed, 'Keep A draft'));
    expect(api.updateDataset).toHaveBeenLastCalledWith(dataset.id, { name: 'A unsaved draft', description: dataset.description });
    expect(name).toHaveValue('A unsaved draft');
    await user.click(within(sheet).getByRole('button', { name: workspace.knowledge.cancel }));
    expect(gallery).not.toHaveAttribute('inert');
    await user.click(editB);
    expect(screen.getByRole('textbox', { name: workspace.knowledge.name })).toHaveValue(secondDataset.name);
    await user.click(screen.getByRole('button', { name: workspace.knowledge.save }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(api.updateDataset).toHaveBeenLastCalledWith(secondDataset.id, { name: secondDataset.name, description: secondDataset.description });
    await user.click((await within(gallery).findAllByRole('button', { name: workspace.knowledge.datasets.edit }))[0]);
    expect(screen.getByRole('textbox', { name: workspace.knowledge.name })).toHaveValue(dataset.name);
    await user.click(screen.getByRole('button', { name: workspace.knowledge.cancel }));
    await user.click(screen.getByRole('button', { name: workspace.knowledge.datasets.create }));
    expect(screen.getByRole('textbox', { name: workspace.knowledge.name })).toHaveValue('');
    expect(screen.getByRole('textbox', { name: workspace.knowledge.datasets.descriptionLabel })).toHaveValue('');
  });

  it('keeps a failed detail edit and its preview through document polling and refreshes metadata after retry', async () => {
    const user = userEvent.setup();
    let current = { ...dataset };
    api.dataset.mockImplementation(() => Promise.resolve(current));
    api.documents.mockResolvedValue(pageOf([{ id: 'draft-doc', dataset_id: dataset.id, name: 'notes.txt', status: 'ready', progress: 1, chunk_count: 1 }]));
    api.updateDataset.mockRejectedValueOnce(new Error('Update failed')).mockImplementation(async (_id, fields) => { current = { ...current, ...fields }; });
    render(<KnowledgeTab />, { wrapper: Providers });
    await openDataset(user);
    await user.click(within(screen.getByRole('table', { name: workspace.knowledge.datasets.documents })).getAllByRole('row')[1]);
    await waitFor(() => expect(api.documentText).toHaveBeenCalledTimes(1));
    const preview = screen.getByTestId('preview');
    await user.click(screen.getByRole('button', { name: workspace.knowledge.datasets.edit }));
    const name = screen.getByRole('textbox', { name: workspace.knowledge.name });
    await user.clear(name);
    await user.type(name, 'Detail rename');
    const description = screen.getByRole('textbox', { name: workspace.knowledge.datasets.descriptionLabel });
    await user.clear(description);
    await user.type(description, 'New description');
    await user.click(screen.getByRole('button', { name: workspace.knowledge.save }));
    await waitFor(() => expect(feedback.error).toHaveBeenCalledWith(workspace.knowledge.loadFailed, 'Update failed'));
    await waitFor(() => expect(api.documents.mock.calls.length).toBeGreaterThanOrEqual(2), { timeout: DOCUMENT_REFRESH_INTERVAL_MS + 1_500 });
    expect(screen.getByTestId('preview')).toBe(preview);
    expect(api.documentText).toHaveBeenCalledTimes(1);
    expect(name).toHaveValue('Detail rename');
    expect(description).toHaveValue('New description');
    expect(screen.getByText(dataset.name, { selector: 'h2' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: workspace.knowledge.save }));
    expect(await screen.findByRole('heading', { name: 'Detail rename' })).toBeInTheDocument();
    expect(screen.getByText('New description')).toBeInTheDocument();
    expect(api.updateDataset).toHaveBeenCalledTimes(2);
  });

  it('reuses the metadata form to create and refreshes set counts after creation and deletion', async () => {
    const user = userEvent.setup();
    let sets: Dataset[] = [];
    api.datasets.mockImplementation(() => Promise.resolve(pageOf(sets)));
    api.createDataset.mockImplementation(async fields => { const created = { ...dataset, ...fields }; sets = [created]; return created; });
    api.removeDataset.mockImplementation(async () => { sets = []; return null; });
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(screen.getByRole('tab', { name: /^Knowledge Sets/ }));
    expect(await screen.findByRole('tab', { name: 'Knowledge Sets (0)' })).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: workspace.knowledge.datasets.create }));
    expect(screen.getByRole('dialog')).toHaveAttribute('data-mode', 'create');
    await user.type(screen.getByRole('textbox', { name: workspace.knowledge.name }), dataset.name);
    await user.click(screen.getByRole('button', { name: workspace.knowledge.create }));
    expect(await screen.findByRole('tab', { name: 'Knowledge Sets (1)' })).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: workspace.knowledge.datasets.delete }));
    expect(await screen.findByRole('tab', { name: 'Knowledge Sets (0)' })).toBeInTheDocument();
    expect(api.createDataset).toHaveBeenCalledWith({ name: dataset.name, description: '' });
    expect(api.updateDataset).not.toHaveBeenCalled();
  });

  it('offers exactly one preview association entry and submits a single selected target without parsing', async () => {
    const user = userEvent.setup();
    api.linkFiles.mockRejectedValueOnce(new Error('Link failed')).mockResolvedValue({ dataset_id: secondDataset.id });
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: file.name }));
    expect(screen.getAllByRole('button', { name: workspace.knowledge.files.associate })).toHaveLength(1);
    expect(screen.queryByText(workspace.knowledge.files.relationships)).not.toBeInTheDocument();
    expect(screen.queryByText(workspace.knowledge.files.noRelationships)).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: workspace.knowledge.files.download })).toHaveAttribute('href', `/api/knowledgebase/files/${file.id}/content`);
    await user.click(screen.getByRole('button', { name: workspace.knowledge.files.associate }));
    const dialog = await screen.findByRole('dialog', { name: workspace.knowledge.files.associate });
    expect(within(dialog).getByRole('button', { name: workspace.knowledge.files.confirmAssociation })).toBeDisabled();
    await user.click(await within(dialog).findByRole('radio', { name: dataset.name }));
    await user.click(within(dialog).getByRole('radio', { name: secondDataset.name }));
    expect(within(dialog).getByRole('radio', { name: dataset.name })).not.toBeChecked();
    expect(within(dialog).getByRole('radio', { name: secondDataset.name })).toBeChecked();
    await user.click(within(dialog).getByRole('button', { name: workspace.knowledge.files.confirmAssociation }));
    await waitFor(() => expect(feedback.error).toHaveBeenCalledWith(workspace.knowledge.loadFailed, 'Link failed'));
    expect(within(dialog).getByRole('radio', { name: secondDataset.name })).toBeChecked();
    await user.click(within(dialog).getByRole('button', { name: workspace.knowledge.files.confirmAssociation }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(api.linkFiles).toHaveBeenCalledTimes(2);
    expect(api.linkFiles).toHaveBeenLastCalledWith(secondDataset.id, [file.id]);
    expect(feedback.success).toHaveBeenLastCalledWith(workspace.knowledge.files.associated);
    expect(workspace.knowledge.files.associated).toMatch(/request submitted.*processing/i);
    expect(api.parse).not.toHaveBeenCalled();
    expect(screen.getAllByRole('button', { name: workspace.knowledge.files.associate })).toHaveLength(1);
  });

  it('observes a delayed file-page association after opening an initially empty target detail', async () => {
    const user = userEvent.setup();
    api.linkFiles.mockResolvedValue({ dataset_id: dataset.id });
    api.documents.mockResolvedValueOnce(pageOf([])).mockResolvedValue(pageOf([
      { id: 'linked-later', dataset_id: dataset.id, name: 'linked-later.txt', status: 'unparsed', progress: 0, chunk_count: 0 },
    ]));
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: file.name }));
    await user.click(screen.getByRole('button', { name: workspace.knowledge.files.associate }));
    const dialog = await screen.findByRole('dialog', { name: workspace.knowledge.files.associate });
    await user.click(await within(dialog).findByRole('radio', { name: dataset.name }));
    await user.click(within(dialog).getByRole('button', { name: workspace.knowledge.files.confirmAssociation }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(feedback.success).toHaveBeenCalledWith(workspace.knowledge.files.associated);
    const table = await openDataset(user);
    expect(within(table).getByText(workspace.knowledge.datasets.noDocuments)).toBeInTheDocument();
    await waitFor(() => expect(within(table).getByText('linked-later.txt')).toBeInTheDocument(), { timeout: DOCUMENT_REFRESH_INTERVAL_MS + 1_500 });
    expect(api.linkFiles).toHaveBeenCalledTimes(1);
    expect(feedback.success).toHaveBeenCalledTimes(1);
    expect(api.parse).not.toHaveBeenCalled();
  });

  it('adds files from the detail with the new wording and leaves parsing manual', async () => {
    const user = userEvent.setup();
    api.linkFiles.mockResolvedValue({ dataset_id: dataset.id });
    render(<KnowledgeTab />, { wrapper: Providers });
    await openDataset(user);
    await user.click(screen.getByRole('button', { name: 'Add files' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add files' });
    await user.click(await within(dialog).findByRole('checkbox', { name: file.name }));
    await user.click(within(dialog).getByRole('button', { name: 'Add files' }));
    await waitFor(() => expect(api.linkFiles).toHaveBeenCalledWith(dataset.id, [file.id]));
    await waitFor(() => expect(feedback.success).toHaveBeenCalledWith(workspace.knowledge.files.associated));
    expect(api.parse).not.toHaveBeenCalled();
  });

  it('shows document types and valid times, and reads originals without a source file id or same-name lookup', async () => {
    const user = userEvent.setup();
    const base = { dataset_id: dataset.id, name: 'same.txt', status: 'ready', progress: 1, chunk_count: 2 };
    api.documents.mockResolvedValue(pageOf([
      { ...base, id: 'doc-a', source_file_id: 'actual-source', size: 32, created_at: '2026-09-27T10:00:00Z', updated_at: '2026-09-28T10:00:00Z' },
      { ...base, id: 'doc-b', source_file_id: null, size: null, created_at: '2026-09-27T10:00:00Z', updated_at: 'not-a-date' },
      { ...base, id: 'doc-c', name: 'README', created_at: '2026-09-27T10:00:00Z' },
    ]));
    render(<KnowledgeTab />, { wrapper: Providers });
    const table = await openDataset(user);
    const rows = within(table).getAllByRole('row');
    expect(within(table).getByRole('columnheader', { name: workspace.files.columns.modified })).toBeInTheDocument();
    expect(within(table).getAllByRole('columnheader').slice(0, 4).map(item => item.textContent)).toEqual([
      workspace.knowledge.name, workspace.knowledge.type, workspace.files.columns.size, workspace.files.columns.modified,
    ]);
    expect(within(table).queryByRole('columnheader', { name: workspace.knowledge.datasets.sourceFile })).not.toBeInTheDocument();
    expect(within(rows[1]).getAllByRole('cell')[1]).toHaveTextContent(/^TXT$/);
    expect(within(rows[1]).getAllByRole('cell')[2]).toHaveTextContent(/^32 B$/);
    expect(rows[1].querySelector('time')).toHaveAttribute('datetime', '2026-09-28T10:00:00Z');
    expect(within(rows[2]).getAllByRole('cell')[2]).toHaveTextContent(/^—$/);
    expect(within(rows[2]).getAllByRole('cell')[3]).toHaveTextContent(/^—$/);
    expect(within(rows[3]).getAllByRole('cell')[1]).toHaveTextContent(/^—$/);
    expect(within(rows[3]).getAllByRole('cell')[3]).toHaveTextContent(/^—$/);
    await user.click(within(rows[1]).getByRole('button', { name: workspace.knowledge.datasets.parse }));
    await waitFor(() => expect(api.parse).toHaveBeenCalledWith(dataset.id, ['doc-a']));
    expect(screen.queryByTestId('preview')).not.toBeInTheDocument();
    const fileRequests = api.files.mock.calls.length;
    await user.click(rows[2]);
    await waitFor(() => expect(api.documentText).toHaveBeenCalledWith(dataset.id, 'doc-b', expect.any(AbortSignal)));
    const contentPath = '/api/knowledgebase/datasets/set-1/documents/doc-b/content';
    expect(screen.getByTestId('preview')).toHaveAttribute('data-preview-url', `${contentPath}?inline=true`);
    expect(screen.getByRole('link', { name: workspace.knowledge.files.download })).toHaveAttribute('href', `${contentPath}?inline=false`);
    expect(api.files).toHaveBeenCalledTimes(fileRequests);
    expect(api.fileText).not.toHaveBeenCalled();
    expect(api.downloadUrl).not.toHaveBeenCalled();
    expect(api.previewUrl).not.toHaveBeenCalled();
    const preview = screen.getByRole('region', { name: workspace.knowledge.files.preview });
    expect(within(preview).queryByRole('button', { name: workspace.knowledge.datasets.viewSource })).not.toBeInTheDocument();
    expect(within(preview).queryByRole('button', { name: workspace.knowledge.files.associate })).not.toBeInTheDocument();
    await user.click(within(preview).getByRole('button', { name: workspace.files.preview.fullscreen }));
    expect(screen.getByTestId('fullscreen')).toHaveAttribute('data-preview-url', `${contentPath}?inline=true`);
    expect(screen.getByTestId('fullscreen')).toHaveAttribute('data-download-url', `${contentPath}?inline=false`);
    expect(api.linkFiles).not.toHaveBeenCalled();
  });

  it('previews document PDFs by name through document access, without fetching text or inventing a source id', async () => {
    const user = userEvent.setup();
    api.documents.mockResolvedValue(pageOf([{ id: 'pdf-doc', dataset_id: dataset.id, name: 'original.PDF', status: 'ready', progress: 1, chunk_count: 2 }]));
    render(<KnowledgeTab />, { wrapper: Providers });
    await openDataset(user);
    await user.click(within(screen.getByRole('table', { name: workspace.knowledge.datasets.documents })).getAllByRole('row')[1]);
    expect(screen.getByTestId('preview')).toHaveAttribute('data-preview-url', '/api/knowledgebase/datasets/set-1/documents/pdf-doc/content?inline=true');
    expect(api.documentText).not.toHaveBeenCalled();
    expect(api.fileText).not.toHaveBeenCalled();
    expect(api.previewUrl).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: workspace.knowledge.files.associate })).not.toBeInTheDocument();
  });

  it.each(['config.env', '.env', 'README', 'notes.jsonl', 'script.py'])('previews supported knowledge text from in-memory fixtures: %s', async name => {
    const user = userEvent.setup();
    api.directory.mockResolvedValue(directoryOf([{ ...file, name }]));
    api.fileText.mockResolvedValue('IN_MEMORY_EXAMPLE=hello');
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name }));
    await waitFor(() => expect(screen.getByTestId('preview')).toHaveTextContent('IN_MEMORY_EXAMPLE=hello'));
    expect(screen.getByTestId('preview')).toHaveAttribute('data-text-file', 'true');
    expect(api.fileText).toHaveBeenCalledWith(file.id, expect.any(AbortSignal));
    expect(api.documentText).not.toHaveBeenCalled();
  });

  it.each(['config.env', '.env', 'README'])('previews supported document text through the document route: %s', async name => {
    const user = userEvent.setup();
    api.documents.mockResolvedValue(pageOf([{ id: 'text-doc', dataset_id: dataset.id, name, status: 'ready', progress: 1, chunk_count: 1 }]));
    api.documentText.mockResolvedValue('IN_MEMORY_EXAMPLE=hello');
    render(<KnowledgeTab />, { wrapper: Providers });
    await openDataset(user);
    await user.click(within(screen.getByRole('table', { name: workspace.knowledge.datasets.documents })).getAllByRole('row')[1]);
    await waitFor(() => expect(screen.getByTestId('preview')).toHaveTextContent('IN_MEMORY_EXAMPLE=hello'));
    expect(screen.getByTestId('preview')).toHaveAttribute('data-text-file', 'true');
    expect(api.documentText).toHaveBeenCalledWith(dataset.id, 'text-doc', expect.any(AbortSignal));
    expect(api.fileText).not.toHaveBeenCalled();
  });

  it.each(['DOCX', 'xlsx', 'zip', 'unknown'])('does not read binary knowledge files as text and keeps downloads: %s', async extension => {
    const user = userEvent.setup();
    const binary = { ...file, name: `original.${extension}` };
    api.directory.mockResolvedValue(directoryOf([binary]));
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: binary.name }));
    expect(screen.getByTestId('preview')).toHaveAttribute('data-text-file', 'false');
    expect(screen.getByRole('link', { name: workspace.knowledge.files.download })).toHaveAttribute('href', `/api/knowledgebase/files/${file.id}/content`);
    expect(api.fileText).not.toHaveBeenCalled();
    expect(api.documentText).not.toHaveBeenCalled();
  });

  it.each(['DOCX', 'xlsx', 'zip', 'unknown'])('does not read binary document originals as text and keeps document downloads: %s', async extension => {
    const user = userEvent.setup();
    api.documents.mockResolvedValue(pageOf([{ id: 'binary-doc', dataset_id: dataset.id, name: `original.${extension}`, status: 'ready', progress: 1, chunk_count: 2 }]));
    render(<KnowledgeTab />, { wrapper: Providers });
    await openDataset(user);
    await user.click(within(screen.getByRole('table', { name: workspace.knowledge.datasets.documents })).getAllByRole('row')[1]);
    expect(screen.getByTestId('preview')).toHaveAttribute('data-text-file', 'false');
    expect(screen.getByRole('link', { name: workspace.knowledge.files.download })).toHaveAttribute('href', '/api/knowledgebase/datasets/set-1/documents/binary-doc/content?inline=false');
    expect(api.documentText).not.toHaveBeenCalled();
    expect(api.fileText).not.toHaveBeenCalled();
    expect(api.downloadUrl).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: workspace.knowledge.files.associate })).not.toBeInTheDocument();
  });

  it('keeps a failed unlink preview, then clears only that preview before a successful refresh', async () => {
    const user = userEvent.setup();
    const document = { id: 'remove-doc', dataset_id: dataset.id, name: 'original.txt', status: 'ready', progress: 1, chunk_count: 1 };
    const removed = deferred<{ dataset_id: string }>();
    api.documents.mockResolvedValue(pageOf([document]));
    api.removeDocuments.mockRejectedValueOnce(new Error('Unlink failed')).mockImplementation(() => removed.promise);
    render(<KnowledgeTab />, { wrapper: Providers });
    await openDataset(user);
    await user.click(within(screen.getByRole('table', { name: workspace.knowledge.datasets.documents })).getAllByRole('row')[1]);
    await waitFor(() => expect(api.documentText).toHaveBeenCalledTimes(1));
    const textSignal = api.documentText.mock.calls[0][2]!;
    const preview = screen.getByTestId('preview');
    await user.click(screen.getByRole('button', { name: workspace.knowledge.datasets.unlink }));
    await waitFor(() => expect(feedback.error).toHaveBeenCalledWith(workspace.knowledge.loadFailed, 'Unlink failed'));
    expect(screen.getByTestId('preview')).toBe(preview);
    expect(textSignal.aborted).toBe(false);
    await user.click(screen.getByRole('button', { name: workspace.knowledge.datasets.unlink }));
    expect(screen.getByTestId('preview')).toBe(preview);
    api.documents.mockResolvedValue(pageOf([]));
    api.documentContentUrl.mockClear();
    await act(async () => { removed.resolve({ dataset_id: dataset.id }); });
    await screen.findByText(workspace.knowledge.datasets.noDocuments);
    expect(screen.queryByTestId('preview')).not.toBeInTheDocument();
    expect(textSignal.aborted).toBe(true);
    expect(api.documentText).toHaveBeenCalledTimes(1);
    expect(api.documentContentUrl).not.toHaveBeenCalled();
    expect(api.removeDocuments).toHaveBeenLastCalledWith(dataset.id, ['remove-doc']);
  });

  it('preserves an unrelated document preview when another document is unlinked', async () => {
    const user = userEvent.setup();
    const kept = { id: 'kept-doc', dataset_id: dataset.id, name: 'kept.txt', status: 'ready', progress: 1, chunk_count: 1 };
    const removed = { ...kept, id: 'other-doc', name: 'other.txt' };
    api.documents.mockResolvedValue(pageOf([kept, removed]));
    api.removeDocuments.mockImplementation(async () => { api.documents.mockResolvedValue(pageOf([kept])); return { dataset_id: dataset.id }; });
    render(<KnowledgeTab />, { wrapper: Providers });
    const table = await openDataset(user);
    const [keptRow, otherRow] = within(table).getAllByRole('row').slice(1);
    await user.click(keptRow);
    await waitFor(() => expect(api.documentText).toHaveBeenCalledTimes(1));
    const preview = screen.getByTestId('preview');
    await user.click(within(otherRow).getByRole('button', { name: workspace.knowledge.datasets.unlink }));
    await waitFor(() => expect(within(table).queryByText('other.txt')).not.toBeInTheDocument());
    expect(screen.getByTestId('preview')).toBe(preview);
    expect(api.documentText).toHaveBeenCalledTimes(1);
    expect(api.removeDocuments).toHaveBeenCalledWith(dataset.id, ['other-doc']);
  });

  it('reports unavailable document content instead of falling back to file ids', async () => {
    const user = userEvent.setup();
    api.documents.mockResolvedValue(pageOf([{ id: 'missing-doc', dataset_id: dataset.id, name: 'original.txt', status: 'ready', progress: 1, chunk_count: 2 }]));
    api.documentText.mockRejectedValue(new Error('Original unavailable'));
    render(<KnowledgeTab />, { wrapper: Providers });
    await openDataset(user);
    await user.click(within(screen.getByRole('table', { name: workspace.knowledge.datasets.documents })).getAllByRole('row')[1]);
    expect(await screen.findByRole('alert')).toHaveTextContent('Original unavailable');
    expect(api.fileText).not.toHaveBeenCalled();
    expect(api.downloadUrl).not.toHaveBeenCalled();
    expect(api.linkFiles).not.toHaveBeenCalled();
  });
});
