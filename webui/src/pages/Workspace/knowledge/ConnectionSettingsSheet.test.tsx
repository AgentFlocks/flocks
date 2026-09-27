import type { ReactNode } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createInstance } from 'i18next';
import { I18nextProvider } from 'react-i18next';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { knowledgebaseAPI, KnowledgebaseError } from '@/api/knowledgebase';
import { ToastProvider } from '@/components/common/Toast';
import common from '@/locales/en-US/common.json';
import workspace from '@/locales/en-US/workspace.json';
import ConnectionSettingsSheet from './ConnectionSettingsSheet';

vi.mock('@/api/knowledgebase', async importOriginal => {
  const actual = await importOriginal<typeof import('@/api/knowledgebase')>();
  return { ...actual, knowledgebaseAPI: Object.fromEntries(Object.keys(actual.knowledgebaseAPI).map(key => [key, vi.fn()])) };
});
vi.mock('@/hooks/useDefaultModelVision', () => ({ useDefaultModelVision: () => null }));

const api = vi.mocked(knowledgebaseAPI);
const i18n = createInstance();
beforeAll(async () => {
  await i18n.init({ lng: 'en-US', fallbackLng: 'en-US', resources: { 'en-US': { workspace, common } }, interpolation: { escapeValue: false } });
});
beforeEach(() => {
  vi.resetAllMocks();
  api.connection.mockResolvedValue({ provider: 'ragflow', base_url: 'https://ragflow.example', has_api_key: true });
});

function Providers({ children }: { children: ReactNode }) {
  return <I18nextProvider i18n={i18n}><ToastProvider>{children}</ToastProvider></I18nextProvider>;
}

function mount(onClose = vi.fn()) {
  render(<ConnectionSettingsSheet onClose={onClose} />, { wrapper: Providers });
  return onClose;
}

describe('ConnectionSettingsSheet', () => {
  it('shows only RAGFlow and never reads or fills a stored key', async () => {
    mount();
    expect(await screen.findByRole('combobox', { name: workspace.knowledge.connection.provider })).toHaveValue('ragflow');
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(screen.getByRole('option', { name: 'RAGFlow' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: workspace.knowledge.connection.baseUrl })).toHaveValue('https://ragflow.example');
    expect(screen.getByLabelText(workspace.knowledge.connection.apiKey)).toHaveValue('');
    expect(screen.getByText(workspace.knowledge.connection.keyExistingHint)).toBeInTheDocument();
    expect(api.connection).toHaveBeenCalledTimes(1);
  });

  it('keeps the old key for the same URL, blocks dismissal and duplicate saves while pending, and shows restart notice', async () => {
    const user = userEvent.setup();
    let finishSave!: () => void;
    api.saveConnection.mockImplementationOnce(() => new Promise(resolve => {
      finishSave = () => resolve({ provider: 'ragflow', base_url: 'https://ragflow.example', has_api_key: true, applied: false, restart_required: true });
    }));
    const onClose = mount();
    await screen.findByRole('combobox', { name: workspace.knowledge.connection.provider });
    await user.click(screen.getByRole('button', { name: common.entity.defaultSave }));
    expect(api.saveConnection).toHaveBeenCalledExactlyOnceWith({ provider: 'ragflow', base_url: 'https://ragflow.example', api_key: '' });
    expect(screen.getByRole('button', { name: common.entity.defaultSave })).toBeDisabled();
    await user.click(document.querySelector('.fixed.inset-0') as Element);
    await user.click(screen.getByRole('heading', { level: 2 }).parentElement!.querySelector('button')!);
    expect(onClose).not.toHaveBeenCalled();
    await act(async () => finishSave());
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(screen.getByText(workspace.knowledge.connection.restartRequired)).toBeInTheDocument();
    expect(api.status).not.toHaveBeenCalled();
  });

  it('keeps the edited draft and shows a safe code-specific error if the server rejects the probe', async () => {
    const user = userEvent.setup();
    api.saveConnection.mockRejectedValueOnce(new KnowledgebaseError('sensitive endpoint and API key', 'connection_test_failed', 502));
    mount();
    const key = await screen.findByLabelText(workspace.knowledge.connection.apiKey);
    await user.type(key, 'new-secret');
    await user.click(screen.getByRole('button', { name: common.entity.defaultSave }));
    expect(await screen.findByRole('alert')).toHaveTextContent(workspace.knowledge.connection.errors.connection_test_failed);
    expect(screen.queryByText(/sensitive endpoint and API key/)).not.toBeInTheDocument();
    expect(key).toHaveValue('new-secret');
    expect(api.saveConnection).toHaveBeenCalledExactlyOnceWith({ provider: 'ragflow', base_url: 'https://ragflow.example', api_key: 'new-secret' });
  });

  it.each(['https://ragflow.example/api/v1', 'https://new-ragflow.example'])(
    'requires a new key for a changed URL (%s), including paths on the same origin', async changedUrl => {
      const user = userEvent.setup();
      api.saveConnection.mockRejectedValueOnce(new KnowledgebaseError('private detail', 'api_key_required', 400));
      mount();
      const url = await screen.findByRole('textbox', { name: workspace.knowledge.connection.baseUrl });
      await user.clear(url);
      await user.type(url, changedUrl);
      await user.click(screen.getByRole('button', { name: common.entity.defaultSave }));
      await waitFor(() => expect(api.saveConnection).toHaveBeenCalledWith({
        provider: 'ragflow', base_url: changedUrl, api_key: '',
      }));
      expect(await screen.findByRole('alert')).toHaveTextContent(workspace.knowledge.connection.errors.api_key_required);
      expect(screen.queryByText('private detail')).not.toBeInTheDocument();
    },
  );

  it('requires an API key when no key has been stored', async () => {
    const user = userEvent.setup();
    api.connection.mockResolvedValueOnce({ provider: null, base_url: '', has_api_key: false });
    mount();
    const url = await screen.findByRole('textbox', { name: workspace.knowledge.connection.baseUrl });
    await user.type(url, 'https://ragflow.example');
    await user.click(screen.getByRole('button', { name: common.entity.defaultSave }));
    expect(screen.getByRole('alert')).toHaveTextContent(workspace.knowledge.connection.keyRequired);
    expect(api.saveConnection).not.toHaveBeenCalled();
  });
});
