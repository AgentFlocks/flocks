import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AuditEventItem, AuditEventQuery } from '@/api/flocksproAudit';
import AuditLogsPage from './index';

const { listEvents, listEventTypes, hasCapability, t } = vi.hoisted(() => ({
  listEvents: vi.fn(),
  listEventTypes: vi.fn(),
  hasCapability: vi.fn(),
  t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? key,
}));

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t }) }));
vi.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ user: { role: 'admin' } }) }));
vi.mock('@/api/flocksproUsers', () => ({ flocksproUsersApi: { hasCapability } }));
vi.mock('@/api/flocksproAudit', () => ({ flocksproAuditApi: { listEvents, listEventTypes } }));
vi.mock('@/components/common/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const event: AuditEventItem = {
  id: 1,
  event_type: 'ingress.stopped',
  category: 'ingress',
  action: 'stop',
  result: 'failed',
  status: 'failed',
  created_at: '2026-09-29 16:30:00',
  reason: '入口未启用，停止接收',
  phase: 'policy',
  entry: 'syslog',
  payload: { alert_id: 'alert-1' },
  metadata: { correlation_id: 'trace-1' },
};

let exportedBlob: Blob | undefined;

async function renderPage() {
  const view = render(<AuditLogsPage />);
  await waitFor(() => expect(listEvents).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getByRole('button', { name: 'audit.actions.search' })).toBeEnabled());
  const [start, end] = Array.from(view.container.querySelectorAll<HTMLInputElement>('input[type="datetime-local"]'));
  return { ...view, start, end };
}

function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = reject;
    reader.readAsText(blob);
  });
}

describe('AuditLogs time filters and denial details', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv('TZ', 'Asia/Shanghai');
    hasCapability.mockResolvedValue(true);
    listEventTypes.mockResolvedValue(['ingress.stopped']);
    // Match the Pro sink's UTC TEXT comparison, including inclusive bounds.
    listEvents.mockImplementation(async (query: AuditEventQuery) => {
      const items = [event].filter((item) => (
        (!query.start_at || item.created_at >= query.start_at)
        && (!query.end_at || item.created_at <= query.end_at)
      ));
      return { items, total: items.length };
    });
    exportedBlob = undefined;
    vi.stubGlobal('URL', Object.assign(class extends URL {}, {
      createObjectURL: vi.fn((blob: Blob) => {
        exportedBlob = blob;
        return 'blob:audit-export';
      }),
      revokeObjectURL: vi.fn(),
    }));
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it('uses identical UTC bounds for the list and export while displaying local time', async () => {
    const { start, end } = await renderPage();
    fireEvent.change(start, { target: { value: '2026-09-30T00:00' } });
    fireEvent.change(end, { target: { value: '2026-09-30T00:30' } });
    fireEvent.click(screen.getByRole('button', { name: 'audit.actions.search' }));
    await waitFor(() => expect(listEvents).toHaveBeenCalledTimes(2));
    const query = {
      start_at: '2026-09-29 16:00:00',
      end_at: '2026-09-29 16:30:00',
      sort_by: 'created_at',
      order: 'desc',
      offset: 0,
    };
    expect(listEvents).toHaveBeenLastCalledWith(expect.objectContaining({ ...query, limit: 20 }));
    expect(await screen.findByText('2026/09/30 00:30:00')).toBeInTheDocument();
    expect(start.value).toBe('2026-09-30T00:00');
    expect(end.value).toBe('2026-09-30T00:30');

    await waitFor(() => expect(screen.getByRole('button', { name: 'audit.actions.exportExcel' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'audit.actions.exportExcel' }));
    await waitFor(() => expect(exportedBlob).toBeDefined());
    expect(listEvents).toHaveBeenLastCalledWith(expect.objectContaining({ ...query, limit: 500 }));
    expect(await readBlob(exportedBlob!)).toContain('2026/09/30 00:30:00');
  });

  it('keeps unfiltered loading, other filters and reset semantics', async () => {
    const { start, end } = await renderPage();
    expect(listEvents).toHaveBeenLastCalledWith({
      event_type: undefined, username: undefined, result: undefined,
      start_at: undefined, end_at: undefined, sort_by: 'created_at', order: 'desc', limit: 20, offset: 0,
    });
    fireEvent.change(screen.getByPlaceholderText('audit.filters.actor'), { target: { value: 'admin' } });
    fireEvent.change(start, { target: { value: '2026-09-30T00:00' } });
    fireEvent.click(screen.getByRole('button', { name: 'audit.actions.search' }));
    await waitFor(() => expect(listEvents).toHaveBeenCalledTimes(2));
    expect(listEvents).toHaveBeenLastCalledWith(expect.objectContaining({
      username: 'admin', start_at: '2026-09-29 16:00:00', end_at: undefined,
    }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'audit.actions.reset' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'audit.actions.reset' }));
    await waitFor(() => expect(listEvents).toHaveBeenCalledTimes(3));
    expect(listEvents).toHaveBeenLastCalledWith(expect.objectContaining({
      username: undefined, start_at: undefined, end_at: undefined,
    }));
    expect(start.value).toBe('');
    expect(end.value).toBe('');
  });

  it('blocks list and export requests for invalid input instead of silently widening the range', async () => {
    const { start } = await renderPage();
    // A restored or programmatically injected bad value must also fail closed.
    start.type = 'text';
    fireEvent.change(start, { target: { value: '2026-02-30T10:00' } });
    fireEvent.click(screen.getByRole('button', { name: 'audit.actions.search' }));
    expect(await screen.findByText('请选择有效的开始和结束时间')).toBeInTheDocument();
    expect(listEvents).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'audit.actions.exportExcel' }));
    expect(await screen.findByText('请选择有效的开始和结束时间')).toBeInTheDocument();
    expect(listEvents).toHaveBeenCalledTimes(1);
    expect(exportedBlob).toBeUndefined();
  });

  it('includes denial reason, phase and entry with the original payload and metadata in details and export', async () => {
    await renderPage();
    fireEvent.click(screen.getByText('ingress.stopped', { selector: 'td' }));
    const detail = screen.getByText(/入口未启用，停止接收/);
    expect(detail).toHaveTextContent('policy');
    expect(detail).toHaveTextContent('syslog');
    expect(detail).toHaveTextContent('alert-1');
    expect(detail).toHaveTextContent('trace-1');
    fireEvent.click(screen.getByRole('button', { name: 'audit.actions.exportExcel' }));
    await waitFor(() => expect(exportedBlob).toBeDefined());
    const html = await readBlob(exportedBlob!);
    for (const value of ['入口未启用，停止接收', 'policy', 'syslog', 'alert-1', 'trace-1']) {
      expect(html).toContain(value);
    }
  });

  it('keeps an empty event detail simple', async () => {
    listEvents.mockResolvedValue({
      items: [{ ...event, reason: null, phase: '', entry: null, payload: {}, metadata: {} }], total: 1,
    });
    const { container } = await renderPage();
    expect(container.querySelector('tbody tr td:last-child')?.textContent).toBe('-');
  });
});
