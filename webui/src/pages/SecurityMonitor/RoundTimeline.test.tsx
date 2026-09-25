import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it } from 'vitest';
import type { MonitorRun } from '@/api/securityMonitoring';
import RoundTimeline from './RoundTimeline';

const run = (overrides: Partial<MonitorRun> = {}): MonitorRun => ({
  id: 'round-one', execution_id: 'execution-one', session_id: 'daily-session', message_id: 'round-start',
  started_at: '2026-09-25T01:00:00Z', scheduled_for: null, finished_at: '2026-09-25T01:02:00Z',
  status: 'completed', error: null, summary: '查询完成，未发现符合条件的事件。',
  next_step: '等待下一轮定时检查。', result: { events: 0 }, steps: [], ...overrides,
});

function show(runs: MonitorRun[]) {
  return render(<MemoryRouter><RoundTimeline runs={runs} timezone="Asia/Shanghai" /></MemoryRouter>);
}

it('shows a chronological node per round and only expands the selected report', () => {
  show([
    run({ id: 'round-two', message_id: 'second-start', started_at: '2026-09-25T01:10:00Z',
      summary: '已向责任人发送事件 incident-2 的通知，等待回信。',
      result: { events: 1, mail: { notification: { sent: 1 } } } }),
    run(),
  ]);
  const nodes = screen.getAllByTestId('round-timeline-node');
  expect(nodes).toHaveLength(2);
  expect(nodes[0]).toHaveTextContent('09:00:00');
  expect(nodes[0]).toHaveTextContent('零事件 · 无需新通知');
  expect(nodes[1]).toHaveTextContent('09:10:00');
  expect(nodes[1]).toHaveTextContent('已通知 1 封 · 待回信');
  expect(screen.queryByText('查询完成，未发现符合条件的事件。')).not.toBeInTheDocument();
  expect(screen.queryByText(/事件 incident-2 的通知/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '全部收起' })).toBeDisabled();

  fireEvent.click(within(nodes[1]).getByRole('button'));
  expect(screen.getByText(/事件 incident-2 的通知/)).toBeInTheDocument();
  expect(screen.queryByText('查询完成，未发现符合条件的事件。')).not.toBeInTheDocument();
  expect(within(nodes[1]).getByRole('button')).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('link', { name: '查看本轮对话' })).toHaveAttribute('href', '/sessions?session=daily-session&focusMessage=second-start');
  expect(screen.getByText(/任务完成不代表告警已闭环/)).toBeInTheDocument();
  expect(screen.getByText(/等待下一轮定时检查/)).toBeInTheDocument();

  fireEvent.click(within(nodes[0]).getByRole('button'));
  expect(screen.getAllByRole('region', { name: /本轮报告/ })).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: '全部收起' }));
  expect(screen.queryByRole('region', { name: /本轮报告/ })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '全部收起' })).toBeDisabled();
});

it.each([
  [{ result: { mail: { feedback: { verified: 2 } } } }, '2 封反馈回查已确认'],
  [{ result: { mail: { feedback: { verified: 1 }, notification: { sent: 2 } } } }, '1 封反馈回查已确认 · 有新通知待回信'],
  [{ result: { mail: { feedback: { pending: 1 } } } }, '1 封回信待跟进'],
  [{ result: { deferred: 2 } }, '2 条已保存待续查'],
  [{ result: { analyzed: 2, mail: { enabled: false } } }, '调查完成 · 邮件未启用'],
  [{ result: { analyzed: 2, mail: { enabled: true } } }, '调查完成 · 无新增通知'],
  [{ result: {} }, '本轮已结束 · 详见记录'],
  [{ status: 'partial', result: { events: 0, errors: ['旧轮次证据不足'] } }, '调查待续查'],
  [{ status: 'failed', result: { events: 0 }, steps: [{ id: 'query', tool: '查询 XDR 事件', status: 'failed' }] }, '查询失败 · 待重试'],
  [{ status: 'failed', steps: [{ id: 'investigation', tool: '智能体调查结果', status: 'failed' }] }, '调查失败 · 待核对'],
  [{ status: 'failed', result: { mail: { notification: { errors: ['连接失败'] } } } }, '邮件跟进失败'],
  [{ status: 'cancelled' }, '任务中断'],
  [{ status: 'running' }, '调查执行中'],
] as [Partial<MonitorRun>, string][])('uses persisted facts for the concise outcome %#', (overrides, label) => {
  show([run(overrides)]);
  expect(screen.getByRole('button', { name: new RegExp(label) })).toHaveTextContent(label);
  expect(screen.queryByText(/处置完成/)).not.toBeInTheDocument();
});

it('distinguishes notice deduplication from both sending and incident closure', () => {
  show([run({ result: { events: 1 }, steps: [{ id: 'mail', tool: '发送告警通知', status: 'completed',
    output: JSON.stringify({ state: 'sent', duplicate: true }) }] })]);
  expect(screen.getByRole('button', { name: /第 1 轮/ })).toHaveTextContent('通知已去重 · 本轮未重发');
  expect(screen.queryByText(/已通知 1 封/)).not.toBeInTheDocument();
});

it('renders summaries and tool facts as text and escapes exact round links', () => {
  const unsafe = '<img src=x onerror=alert(1)> [外部链接](https://untrusted.invalid)';
  const { container } = show([run({ session_id: 'session&evil=1', message_id: 'anchor#2', summary: unsafe,
    steps: [{ id: 'step', tool: '查询 XDR 事件', status: 'failed', error: '设备查询失败', input: { event: 'incident-123' }, output: unsafe }] })]);
  fireEvent.click(screen.getByRole('button', { name: /第 1 轮/ }));
  const tools = screen.getByRole('list', { name: '本轮工具记录' });
  fireEvent.click(within(tools).getByText('查询 XDR 事件'));
  expect(screen.getAllByText(unsafe)).toHaveLength(2);
  expect(tools).toHaveTextContent('incident-123');
  expect(tools).toHaveTextContent('设备查询失败');
  expect(container.querySelector('img')).toBeNull();
  expect(container.querySelector('a[href="https://untrusted.invalid"]')).toBeNull();
  expect(screen.getByRole('link', { name: '查看本轮对话' })).toHaveAttribute('href', '/sessions?session=session%26evil%3D1&focusMessage=anchor%232');
});

it('retains the selected expansion when refreshed facts append another round', () => {
  const { rerender } = show([run()]);
  fireEvent.click(screen.getByRole('button', { name: /第 1 轮/ }));
  rerender(<MemoryRouter><RoundTimeline runs={[run(), run({ id: 'new-round', status: 'running', started_at: '2026-09-25T01:10:00Z' })]} timezone="Asia/Shanghai" /></MemoryRouter>);
  expect(screen.getByRole('button', { name: /第 1 轮/ })).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('button', { name: /第 2 轮/ })).toHaveAttribute('aria-expanded', 'false');
  expect(screen.getByText('查询完成，未发现符合条件的事件。')).toBeInTheDocument();
});

it('explains an empty date without rendering a report', () => {
  show([]);
  expect(screen.getByText('所选日期暂无监测记录')).toBeInTheDocument();
  expect(screen.queryByRole('list', { name: '执行轮次时间线' })).not.toBeInTheDocument();
});
