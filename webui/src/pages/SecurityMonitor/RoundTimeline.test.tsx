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

it('shows newest rounds first and only expands the selected report', () => {
  show([
    run({ id: 'round-two', message_id: 'second-start', started_at: '2026-09-25T01:10:00Z',
      summary: '已向责任人发送事件 incident-2 的通知，等待回信。',
      result: { events: 1, mail: { notification: { sent: 1 } } } }),
    run(),
  ]);
  const nodes = screen.getAllByTestId('round-timeline-node');
  expect(nodes).toHaveLength(2);
  expect(nodes[1]).toHaveTextContent('09:00:00');
  expect(nodes[1]).toHaveTextContent('零事件 · 无需新通知');
  expect(nodes[0]).toHaveTextContent('09:10:00');
  expect(nodes[0]).toHaveTextContent('已通知 1 封 · 待回信');
  expect(screen.queryByText('查询完成，未发现符合条件的事件。')).not.toBeInTheDocument();
  expect(screen.queryByText(/事件 incident-2 的通知/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '全部收起' })).toBeDisabled();

  fireEvent.click(within(nodes[0]).getByRole('button'));
  expect(screen.getByText(/事件 incident-2 的通知/)).toBeInTheDocument();
  expect(screen.queryByText('查询完成，未发现符合条件的事件。')).not.toBeInTheDocument();
  expect(within(nodes[0]).getByRole('button')).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('link', { name: '查看本轮对话' })).toHaveAttribute('href', '/sessions?session=daily-session&focusMessage=second-start');
  expect(screen.getByText(/任务完成不代表告警已闭环/)).toBeInTheDocument();
  expect(screen.getByText(/等待下一轮定时检查/)).toBeInTheDocument();

  fireEvent.click(within(nodes[1]).getByRole('button'));
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
  [{ status: 'failed', result: { mail: { notification: { errors: ['连接失败'] } } } }, '本轮执行失败 · 展开查看原因'],
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

it('separates successful current work from historical recovery and exposes query overlap without counting new incidents', () => {
  show([run({ result: { events: 11, query_events: 3, resumed_events: 10, query_resumed_overlap: 2, analyzed: 2, deferred: 9,
    investigation_backlog: { system_wait: 1, earliest_retry_at: '2026-09-25T01:49:49Z' } } })]);
  const node = screen.getByTestId('round-timeline-node');
  expect(node).toHaveTextContent('9 条已保存待续查');
  expect(node).toHaveTextContent('本轮查询 3 条 · 历史续查 10 条 · 其中 2 条重叠 · 已有调查结论 2 条');
  expect(node).toHaveTextContent('历史待恢复 1 条');
  expect(node).toHaveTextContent('最早重试 2026/9/25 09:49:49');
  expect(within(node).getByText('完成')).toBeInTheDocument();
  expect(within(node).queryByText('失败')).not.toBeInTheDocument();
  expect(node).not.toHaveTextContent('新事件');
});

it('keeps an old failed result as recorded instead of silently recoloring its history', () => {
  show([run({ status: 'failed', error: '仍有 1 条调查因系统故障未恢复；具体恢复安排见本轮结束说明',
    result: { events: 11, investigation_backlog: { system_wait: 1 } } })]);
  const node = screen.getByTestId('round-timeline-node');
  expect(node).toHaveTextContent('历史故障尚待恢复 · 查看原记录');
  expect(node).toHaveTextContent('本轮处理 11 条（来源未记录）');
  expect(node).toHaveTextContent('待恢复记录 1 条（含本轮及历史）');
  expect(within(node).getByText('失败')).toBeInTheDocument();
  expect(node).not.toHaveTextContent('本轮查询 0 条');
});

it('identifies model failure without treating mailbox readiness as a task failure', () => {
  show([run({ status: 'failed', summary: '事件长篇调查正文仅用于展开查看', result: {
    events: 14, query_events: 0, resumed_events: 14, analyzed: 2,
    errors: ['智能体调查未完成：调查模型请求失败，请检查模型服务、配置和访问权限'],
    mail: { health: { enabled: true, errors: ['收信连接暂不可用'], receive: { state: 'unavailable' }, send: { state: 'unknown' } } },
  } })]);
  const node = screen.getByTestId('round-timeline-node');
  expect(node).toHaveTextContent('调查模型请求失败');
  expect(node).not.toHaveTextContent('邮件连接未就绪');
  expect(node).toHaveTextContent('本轮查询 0 条 · 历史续查 14 条 · 已有调查结论 2 条');
  expect(screen.queryByText('事件长篇调查正文仅用于展开查看')).not.toBeInTheDocument();
});

it('does not turn an incomplete zero-count query into a no-alert conclusion', () => {
  show([run({ status: 'failed', result: { events: 0, query_events: 0, query_complete: false, resumed_events: 0 },
    steps: [{ id: 'query', tool: '查询 XDR 事件', status: 'failed' }] })]);
  const node = screen.getByTestId('round-timeline-node');
  expect(node).toHaveTextContent('查询失败 · 待重试');
  expect(node).toHaveTextContent('查询未完成，已保存 0 条');
  expect(node).not.toHaveTextContent('本轮查询 0 条');
  expect(node).not.toHaveTextContent('零事件');
});

it.each(['调查模型请求超时', '调查模型认证失败', '调查模型访问被拒绝', '调查模型请求受到限流', '调查模型服务暂时不可用', '调查模型连接失败'])(
  'preserves the recorded model failure type: %s', failure => {
    show([run({ status: 'failed', result: { errors: [`智能体调查未完成：${failure}；已有证据保留`] },
      steps: [{ id: 'model', tool: '智能体调查结果', status: 'failed' }] })]);
    expect(screen.getByRole('button', { name: /第 1 轮/ })).toHaveTextContent(failure);
  },
);

it('identifies a TDP time-validation failure and waits for a valid source window without claiming retries were exhausted', () => {
  show([run({ status: 'failed', error: '智能体调查未完成：通用错误', result: {
    events: 18, analyzed: 2, investigation_incomplete: 16,
    investigation_failures: [{ event_id: 'incident-time', event_name: '可疑外联事件', device_name: '四季青 XDR',
      failure_stage: 'query_time', failure_source: 'TDP', error_kind: 'query_time',
      reason: 'TDP 关联查询未执行：原事件调查时间窗口缺少结束时间', retry_exhausted: true }],
    mail: { health: { enabled: true, errors: ['收信未配置'], receive: { state: 'unavailable' }, send: { state: 'unknown' } } },
  }, steps: [{ id: 'summary', tool: '关联分析', status: 'completed' }] })]);
  const node = screen.getByTestId('round-timeline-node');
  expect(node).toHaveTextContent('关联查询时间参数异常');
  expect(node).toHaveTextContent('调查未完成 16 条');
  expect(node).not.toHaveTextContent('邮件连接未就绪');
  fireEvent.click(within(node).getByRole('button'));
  const failure = screen.getByRole('region', { name: '本轮调查异常' });
  expect(failure).toHaveTextContent('四季青 XDR · 可疑外联事件');
  expect(failure).toHaveTextContent('incident-time');
  expect(failure).toHaveTextContent('发生环节：关联查询时间参数校验 · TDP');
  expect(failure).toHaveTextContent('原事件调查时间窗口缺少结束时间');
  expect(failure).toHaveTextContent('等待后续正式 XDR 查询取得合法调查时间窗口后重新调查');
  expect(failure).toHaveTextContent('事件与已有证据保留，不判定为无风险');
  expect(failure).not.toHaveTextContent('已停止自动重试');
  expect(failure).not.toHaveTextContent('暂停并重新启动');
  expect(failure).not.toHaveTextContent('检查模型');
  expect(node).toHaveTextContent('结果汇总完成 · 不代表调查全部成功');
});

it('uses structured model 504 and query failure categories instead of guessing from the narrative', () => {
  show([run({ status: 'failed', result: { investigation_failures: [
    { failure_stage: 'model', http_status: 504, model_failure: 'timeout', reason: '网关未及时返回', next_retry_at: '2026-10-13T10:22:25Z' },
    { failure_stage: 'query', error_type: 'TypeError', reason: '关联查询发生程序错误（TypeError）', retry_exhausted: true },
  ] } })]);
  const node = screen.getByTestId('round-timeline-node');
  expect(node).toHaveTextContent('调查模型网关超时 · 关联取证查询失败');
  fireEvent.click(within(node).getByRole('button'));
  expect(screen.getByRole('region', { name: '本轮调查异常' })).toHaveTextContent('已停止自动重试');
  expect(screen.getByRole('region', { name: '本轮调查异常' })).toHaveTextContent('2026/10/13 18:22:25');
  expect(screen.getByRole('region', { name: '本轮调查异常' })).toHaveTextContent('监测暂停时不会执行');
});

it('reads safe failure metadata from persisted failed step output when a round projection lacks it', () => {
  show([run({ status: 'failed', steps: [{ id: 'fault', tool: '智能体调查结果', status: 'failed',
    input: { event: 'incident-program' }, output: JSON.stringify({ state: 'system_wait', failure_stage: 'internal',
      reason: '调查程序发生内部异常', error_type: 'TypeError' }) }] })]);
  const node = screen.getByTestId('round-timeline-node');
  expect(node).toHaveTextContent('调查程序异常');
  fireEvent.click(within(node).getByRole('button'));
  const failure = screen.getByRole('region', { name: '本轮调查异常' });
  expect(failure).toHaveTextContent('事件编号：incident-program');
  expect(failure).toHaveTextContent('发生环节：调查程序执行');
  expect(failure).not.toHaveTextContent('调查模型');
});

it.each([
  { title: 'authoritative empty failure array', result: { investigation_failures: [] },
    tool: '智能体调查结果', output: { state: 'system_wait', attempted_this_round: true } },
  { title: 'model cooldown deferred work', result: { deferred: 1 },
    tool: '智能体调查结果', output: { state: 'deferred', attempted_this_round: false } },
  { title: 'historical fault not attempted this round', result: {},
    tool: '智能体调查结果', output: { state: 'system_wait', attempted_this_round: false } },
  { title: 'intermediate query failure later recovered', result: {},
    tool: '调查取证：TDP', output: { state: 'system_wait', attempted_this_round: true } },
])('does not mislabel $title as a new investigation failure', ({ result, tool, output }) => {
  show([run({ result: { events: 2, analyzed: 1, investigation_incomplete: 1, ...result },
    steps: [{ id: 'saved-step', tool, status: 'failed', output: { failure_stage: 'model',
      reason: '历史模型失败或等待模型冷却', ...output } }] })]);
  const node = screen.getByTestId('round-timeline-node');
  expect(node).toHaveTextContent('调查未完成 1 条');
  expect(node).not.toHaveTextContent('调查完成 ·');
  expect(within(node).getByText('完成')).toBeInTheDocument();
  fireEvent.click(within(node).getByRole('button'));
  expect(screen.queryByRole('region', { name: '本轮调查异常' })).not.toBeInTheDocument();
});


it('keeps chronological round numbers while showing newest first without mutating input', () => {
 const runs = [run(), run({ id: 'new', started_at: '2026-09-25T02:00:00Z' })];
 show(runs);
 const nodes = screen.getAllByTestId('round-timeline-node');
 expect(within(nodes[0]).getByRole('button')).toHaveAccessibleName(/第 2 轮/);
 expect(within(nodes[1]).getByRole('button')).toHaveAccessibleName(/第 1 轮/);
 expect(runs[0].id).toBe('round-one');
});
