import type { ReactNode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createInstance } from 'i18next';
import { I18nextProvider } from 'react-i18next';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { knowledgebaseAPI } from '@/api/knowledgebase';
import { ToastProvider } from '@/components/common/Toast';
import session from '@/locales/en-US/session.json';
import common from '@/locales/en-US/common.json';
import SessionDatasetSection from './SessionDatasetSection';

vi.mock('@/api/knowledgebase', async importOriginal => {
  const actual = await importOriginal<typeof import('@/api/knowledgebase')>();
  return {
    ...actual,
    knowledgebaseAPI: {
      status: vi.fn(),
      sessionDatasets: vi.fn(),
      datasets: vi.fn(),
      setSessionDatasets: vi.fn(),
    },
  };
});

const api = vi.mocked(knowledgebaseAPI);
const i18n = createInstance();
beforeAll(async () => {
  await i18n.init({ lng: 'en-US', fallbackLng: 'en-US', resources: { 'en-US': { session, common } }, interpolation: { escapeValue: false } });
});

function Providers({ children }: { children: ReactNode }) {
  return <I18nextProvider i18n={i18n}><ToastProvider>{children}</ToastProvider></I18nextProvider>;
}

describe('SessionDatasetSection', () => {
  beforeEach(() => { vi.resetAllMocks(); });

  it('keeps a load failure inside the section', async () => {
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockRejectedValue(new Error('Dataset GET failed'));
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    expect(await screen.findByRole('alert')).toHaveTextContent('Dataset GET failed');
    expect(screen.getByRole('region', { name: session.dataset.title })).toBeInTheDocument();
  });

  it('saves the current session selection', async () => {
    const user = userEvent.setup();
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: [] });
    api.datasets.mockResolvedValue({ items: [{ id: 'alpha', name: 'Alpha', description: '', document_count: 0, chunk_count: 0 }], total: 1, page: 1 });
    api.setSessionDatasets.mockResolvedValue({ session_id: 'sess-1', dataset_ids: ['alpha'] });
    render(<SessionDatasetSection sessionId="sess-1" />, { wrapper: Providers });
    await user.click(await screen.findByRole('button', { name: session.dataset.select }));
    await user.click(screen.getByRole('checkbox', { name: 'Alpha' }));
    await user.click(screen.getByRole('button', { name: session.dataset.apply }));
    await waitFor(() => expect(api.setSessionDatasets).toHaveBeenCalledWith('sess-1', ['alpha']));
  });

  it('keeps the current session when an older load returns later', async () => {
    const pending: Array<(value: { session_id: string; dataset_ids: string[]; datasets: { id: string; name: string; description: string; document_count: number; chunk_count: number }[] }) => void> = [];
    const beta = { id: 'beta', name: 'Beta', description: '', document_count: 0, chunk_count: 0 };
    api.status.mockResolvedValue({ configured: true, ready: true });
    api.sessionDatasets.mockImplementation(id => {
      if (id === 'sess-a') return new Promise(resolve => { pending.push(resolve); });
      return Promise.resolve({ session_id: 'sess-b', dataset_ids: ['beta'], datasets: [beta] });
    });
    const view = render(<SessionDatasetSection sessionId="sess-a" />, { wrapper: Providers });
    view.rerender(<SessionDatasetSection sessionId="sess-b" />);
    expect(await screen.findByText('Beta')).toBeInTheDocument();
    pending.forEach(resolve => resolve({ session_id: 'sess-a', dataset_ids: ['alpha'], datasets: [{ id: 'alpha', name: 'Alpha', description: '', document_count: 0, chunk_count: 0 }] }));
    await waitFor(() => expect(screen.queryByText('Alpha')).not.toBeInTheDocument());
    expect(screen.getByText('Beta')).toBeInTheDocument();
  });
});
