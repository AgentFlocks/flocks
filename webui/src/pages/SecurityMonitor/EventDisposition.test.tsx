import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import EventDisposition from './EventDisposition';
import type { MonitorEvent } from '@/api/securityMonitoring';

const api = vi.hoisted(() => ({ confirmDisposition: vi.fn(), recheckDisposition: vi.fn() }));
vi.mock('@/api/securityMonitoring', () => ({ monitoringApi: api }));
const event: MonitorEvent = { key: 'device:incident:uuid', name: '测试事件', device: 'device', host: '', risk: 'risk', reason: '', sessionID: 'daily', messageID: 'message', closure: 'open' };
beforeEach(() => vi.resetAllMocks());
afterEach(() => vi.unstubAllGlobals());

it('can submit on HTTP deployments without randomUUID', async () => {
  vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) });
  api.confirmDisposition.mockResolvedValue({ data: { status: 'verified' } });
  render(<EventDisposition event={event} enabled refresh={vi.fn()} />);
  fireEvent.click(screen.getByText('确认已处置'));
  fireEvent.change(screen.getByLabelText('处置说明'), { target: { value: '处理完成' } });
  fireEvent.click(screen.getByText('确认写回 XDR'));
  await waitFor(() => expect(api.confirmDisposition).toHaveBeenCalledOnce());
  expect(api.confirmDisposition.mock.calls[0][0].request_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});

it('requires explicit confirmation and a comment, cancel makes no request', () => {
  render(<EventDisposition event={event} enabled refresh={vi.fn()} />);
  fireEvent.click(screen.getByText('确认已处置'));
  expect(screen.getByRole('dialog')).toHaveTextContent('不执行主机隔离或修复');
  expect(screen.getByText('确认写回 XDR')).toBeDisabled();
  fireEvent.change(screen.getByLabelText('处置说明'), { target: { value: '   ' } });
  expect(screen.getByText('确认写回 XDR')).toBeDisabled();
  fireEvent.click(screen.getByText('取消'));
  expect(api.confirmDisposition).not.toHaveBeenCalled();
});

it('pins retries to the same request and prevents double clicks', async () => {
  const refresh = vi.fn().mockResolvedValue(undefined);
  let reject!: (error: Error) => void;
  api.confirmDisposition.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }));
  render(<EventDisposition event={event} enabled refresh={refresh} />);
  fireEvent.click(screen.getByText('确认已处置'));
  fireEvent.change(screen.getByLabelText('处置说明'), { target: { value: '已完成实际处置' } });
  fireEvent.click(screen.getByText('确认写回 XDR'));
  fireEvent.click(screen.getByText('写回并回查中…'));
  expect(api.confirmDisposition).toHaveBeenCalledTimes(1);
  expect(api.confirmDisposition.mock.calls[0][0]).toMatchObject({ event_key: event.key, comment: '已完成实际处置', confirmed: true });
  await act(async () => reject(new Error('timeout')));
  expect(screen.getByRole('alert')).toHaveTextContent('回查状态');
  expect(screen.getByLabelText('处置说明')).toBeDisabled();
  api.confirmDisposition.mockResolvedValue({ data: { status: 'verified' } });
  fireEvent.click(screen.getByText('确认写回 XDR'));
  await waitFor(() => expect(api.confirmDisposition).toHaveBeenCalledTimes(2));
  expect(api.confirmDisposition.mock.calls[0][0]).toEqual(api.confirmDisposition.mock.calls[1][0]);
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2));
});

it('only offers readback for uncertain writes', async () => {
  const record = { id: 'request', status: 'pending' as const, observed_status: null, error: '回查失败', comment: 'done', updated_at: '', session_id: 'manual' };
  api.recheckDisposition.mockResolvedValue({ data: record });
  render(<EventDisposition event={{ ...event, dispositionRecord: record }} enabled refresh={vi.fn()} />);
  expect(screen.queryByText('确认已处置')).not.toBeInTheDocument();
  expect(screen.getByText('未闭环')).toBeInTheDocument();
  fireEvent.click(screen.getByText('回查状态'));
  await waitFor(() => expect(api.recheckDisposition).toHaveBeenCalledWith('request'));
  expect(api.confirmDisposition).not.toHaveBeenCalled();
});

it('closed events cannot be written again and disabled scenes cannot submit', () => {
  const { rerender } = render(<EventDisposition event={{ ...event, closure: 'closed' }} enabled refresh={vi.fn()} />);
  expect(screen.getByText('已闭环（XDR 回查确认）')).toBeInTheDocument();
  expect(screen.queryByText('确认已处置')).not.toBeInTheDocument();
  rerender(<EventDisposition event={event} enabled={false} refresh={vi.fn()} />);
  expect(screen.getByText('确认已处置')).toBeDisabled();
});

it('shows protected status without claiming closure', () => {
  const dispositionRecord = { id: 'request', status: 'mismatch' as const, observed_status: 30, error: null, comment: 'done', updated_at: '', session_id: null };
  render(<EventDisposition event={{ ...event, dispositionRecord }} enabled refresh={vi.fn()} />);
  expect(screen.getByText(/XDR 已防护（30）/)).toBeInTheDocument();
  expect(screen.getByText('未闭环')).toBeInTheDocument();
  expect(screen.queryByText('已闭环（XDR 回查确认）')).not.toBeInTheDocument();
});
