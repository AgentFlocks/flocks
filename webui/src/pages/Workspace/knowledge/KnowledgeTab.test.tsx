import type { ReactNode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createInstance } from 'i18next';
import { I18nextProvider } from 'react-i18next';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { knowledgebaseAPI } from '@/api/knowledgebase';
import { ConfirmProvider } from '@/components/common/ConfirmDialog';
import { ToastProvider } from '@/components/common/Toast';
import workspace from '@/locales/en-US/workspace.json';
import common from '@/locales/en-US/common.json';
import KnowledgeTab from '../KnowledgeTab';

vi.mock('@/api/knowledgebase', async importOriginal => {
  const actual = await importOriginal<typeof import('@/api/knowledgebase')>();
  return {
    ...actual,
    knowledgebaseAPI: Object.fromEntries(Object.keys(actual.knowledgebaseAPI).map(key => [key, vi.fn()])),
  };
});
vi.mock('@/hooks/useDefaultModelVision', () => ({ useDefaultModelVision: () => null }));
vi.mock('@/components/common/FilePreview', () => ({
  getPreviewKind: (node: { name: string }) => node.name.endsWith('.pdf') ? 'pdf' : 'text',
  FilePreviewRenderer: ({ node, content }: { node: { name: string }; content: string | null }) => (
    <div data-testid="preview">{node.name}:{content === null ? 'binary' : content}</div>
  ),
  PreviewModal: () => null,
}));

const api = vi.mocked(knowledgebaseAPI);
const i18n = createInstance();
beforeAll(async () => {
  await i18n.init({ lng: 'en-US', fallbackLng: 'en-US', resources: { 'en-US': { workspace, common } }, interpolation: { escapeValue: false } });
});

function Providers({ children }: { children: ReactNode }) {
  return <I18nextProvider i18n={i18n}><ToastProvider><ConfirmProvider>{children}</ConfirmProvider></ToastProvider></I18nextProvider>;
}

describe('KnowledgeTab', () => {
  beforeEach(() => { vi.resetAllMocks(); });

  it('shows that the service is not configured and does not load files', async () => {
    api.status.mockResolvedValue({ configured: false, ready: false });
    render(<KnowledgeTab />, { wrapper: Providers });
    expect(await screen.findByText(workspace.knowledge.unavailable.knowledgebase_not_configured)).toBeInTheDocument();
    expect(api.files).not.toHaveBeenCalled();
  });

  it('opens the same connection settings from the header and not-configured message', async () => {
    const user = userEvent.setup();
    api.status.mockResolvedValue({ configured: false, ready: false });
    api.connection.mockResolvedValue({ provider: null, base_url: '', has_api_key: false });
    render(<KnowledgeTab />, { wrapper: Providers });
    await screen.findByText(workspace.knowledge.unavailable.knowledgebase_not_configured);
    expect(screen.getAllByRole('button', { name: workspace.knowledge.connection.title })).toHaveLength(2);
    await user.click(screen.getAllByRole('button', { name: workspace.knowledge.connection.title })[1]);
    expect(await screen.findByRole('combobox', { name: workspace.knowledge.connection.provider })).toHaveValue('ragflow');
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(api.files).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: common.entity.cancelButton }));
    await user.click(screen.getAllByRole('button', { name: workspace.knowledge.connection.title })[0]);
    expect(await screen.findByRole('textbox', { name: workspace.knowledge.connection.baseUrl })).toHaveValue('');
    expect(api.connection).toHaveBeenCalledTimes(2);
  });

  it('shows connection settings to a ready knowledge tab without changing readiness', async () => {
    const user = userEvent.setup();
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.files.mockResolvedValue({ items: [], total: 0, page: 1 });
    api.connection.mockResolvedValue({ provider: 'ragflow', base_url: 'https://ragflow.example', has_api_key: true });
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(screen.getByRole('button', { name: workspace.knowledge.connection.title }));
    expect(await screen.findByRole('textbox', { name: workspace.knowledge.connection.baseUrl })).toHaveValue('https://ragflow.example');
    expect(api.status).toHaveBeenCalledTimes(1);
  });

  it('applies a new connection, reloads readiness and the list, removes the old preview, and reopens with metadata only', async () => {
    const user = userEvent.setup();
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.files.mockResolvedValueOnce({ items: [{ id: 'old', name: 'old.pdf', size: 10, parent_id: null }], total: 1, page: 1 })
      .mockResolvedValue({ items: [{ id: 'new', name: 'new.pdf', size: 20, parent_id: null }], total: 1, page: 1 });
    api.connection.mockResolvedValueOnce({ provider: 'ragflow', base_url: 'https://old.example', has_api_key: true })
      .mockResolvedValue({ provider: 'ragflow', base_url: 'https://new.example', has_api_key: true });
    api.saveConnection.mockResolvedValue({ provider: 'ragflow', base_url: 'https://new.example', has_api_key: true, applied: true, restart_required: false });
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: 'old.pdf' }));
    expect(await screen.findByTestId('preview')).toHaveTextContent('old.pdf');
    await user.click(screen.getByRole('button', { name: workspace.knowledge.connection.title }));
    const url = await screen.findByLabelText(workspace.knowledge.connection.baseUrl);
    await user.clear(url);
    await user.type(url, 'https://new.example');
    await user.type(screen.getByLabelText(workspace.knowledge.connection.apiKey), 'replacement-key');
    await user.click(screen.getByRole('button', { name: common.entity.defaultSave }));
    expect(await screen.findByText(workspace.knowledge.connection.saved)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'new.pdf' })).toBeInTheDocument();
    expect(screen.queryByTestId('preview')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'old.pdf' })).not.toBeInTheDocument();
    expect(api.status).toHaveBeenCalledTimes(2);
    expect(api.files).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole('button', { name: workspace.knowledge.connection.title }));
    expect(await screen.findByLabelText(workspace.knowledge.connection.baseUrl)).toHaveValue('https://new.example');
    expect(screen.getByLabelText(workspace.knowledge.connection.apiKey)).toHaveValue('');
    expect(screen.getByLabelText(workspace.knowledge.connection.apiKey)).toHaveAttribute('placeholder', workspace.knowledge.connection.keyConfiguredPlaceholder);
    expect(api.connection).toHaveBeenCalledTimes(2);
  });

  it('keeps the current preview and draft without refreshing when saving fails', async () => {
    const user = userEvent.setup();
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.files.mockResolvedValue({ items: [{ id: 'old', name: 'old.pdf', size: 10, parent_id: null }], total: 1, page: 1 });
    api.connection.mockResolvedValue({ provider: 'ragflow', base_url: 'https://old.example', has_api_key: true });
    api.saveConnection.mockRejectedValue(new Error('private connection details'));
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: 'old.pdf' }));
    expect(await screen.findByTestId('preview')).toHaveTextContent('old.pdf');
    await user.click(screen.getByRole('button', { name: workspace.knowledge.connection.title }));
    const url = await screen.findByLabelText(workspace.knowledge.connection.baseUrl);
    await user.clear(url);
    await user.type(url, 'https://draft.example');
    const key = screen.getByLabelText(workspace.knowledge.connection.apiKey);
    await user.type(key, 'draft-key');
    await user.click(screen.getByRole('button', { name: common.entity.defaultSave }));
    expect(await screen.findByRole('alert')).toHaveTextContent(workspace.knowledge.connection.saveFailed);
    expect(url).toHaveValue('https://draft.example');
    expect(key).toHaveValue('draft-key');
    expect(screen.getByTestId('preview')).toHaveTextContent('old.pdf');
    expect(api.status).toHaveBeenCalledTimes(1);
    expect(api.files).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(workspace.knowledge.connection.saved)).not.toBeInTheDocument();
  });

  it('lists files and previews a PDF through the existing renderer without a text fetch', async () => {
    const user = userEvent.setup();
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.files.mockResolvedValue({ items: [{ id: 'file-1', name: 'guide.pdf', size: 10, parent_id: null }], total: 1, page: 1 });
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: 'guide.pdf' }));
    expect(await screen.findByTestId('preview')).toHaveTextContent('guide.pdf:binary');
    expect(api.fileText).not.toHaveBeenCalled();
  });

  it('creates a dataset without choosing an embedding model', async () => {
    const user = userEvent.setup();
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.files.mockResolvedValue({ items: [], total: 0, page: 1 });
    api.datasets.mockResolvedValue({ items: [], total: 0, page: 1 });
    api.createDataset.mockResolvedValue({ id: 'ds-1', name: 'Notes', description: '', document_count: 0, chunk_count: 0 });
    api.dataset.mockResolvedValue({ id: 'ds-1', name: 'Notes', description: '', document_count: 0, chunk_count: 0 });
    api.documents.mockResolvedValue({ items: [], total: 0, page: 1 });
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(await screen.findByRole('tablist').then(() => screen.getByRole('button', { name: workspace.knowledge.datasets.title })));
    await user.click(await screen.findByRole('button', { name: workspace.knowledge.datasets.create }));
    await user.type(screen.getByRole('textbox', { name: workspace.knowledge.name }), 'Notes');
    await user.click(screen.getByRole('button', { name: workspace.knowledge.create }));
    await waitFor(() => expect(api.createDataset).toHaveBeenCalledWith({ name: 'Notes', description: '' }));
  });

  it('stays on the dataset when deletion fails', async () => {
    const user = userEvent.setup();
    const dataset = { id: 'ds-1', name: 'Notes', description: '', document_count: 0, chunk_count: 0 };
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.files.mockResolvedValue({ items: [], total: 0, page: 1 });
    api.datasets.mockResolvedValue({ items: [dataset], total: 1, page: 1 });
    api.dataset.mockResolvedValue(dataset);
    api.documents.mockResolvedValue({ items: [], total: 0, page: 1 });
    api.removeDataset.mockRejectedValue(new Error('delete failed'));
    render(<KnowledgeTab />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: workspace.knowledge.datasets.title }));
    await user.click(await screen.findByRole('button', { name: 'Notes' }));
    await user.click(await screen.findByRole('button', { name: workspace.knowledge.datasets.delete }));
    await user.click(await screen.findByRole('button', { name: common.button.confirm }));
    expect(await screen.findByRole('alert')).toHaveTextContent('delete failed');
    expect(screen.getByRole('button', { name: workspace.knowledge.datasets.back })).toBeInTheDocument();
  });
});
