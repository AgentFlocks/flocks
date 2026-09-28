import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import SecurityMonitor from './index';
const mocks = vi.hoisted(() => ({ overview: vi.fn(), report: vi.fn(), start: vi.fn(), pause: vi.fn(), diagnostics: vi.fn(), setInvestigationEngine: vi.fn(), subscription: null as any }));
vi.mock('@/api/securityMonitoring', async importOriginal => ({ ...(await importOriginal<any>()), monitoringApi: mocks }));
vi.mock('@/hooks/useSSE', () => ({ useSSE: (options: any) => { mocks.subscription = options; return {}; } }));
vi.mock('@/components/common/SessionChat', () => ({ default: ({ sessionId, hideInput, live }: any) => <div data-testid="native-chat">{sessionId} {hideInput && 'readonly'} {live && 'live'}</div> }));
const data = {
 businessDate: '2026-09-23', timezone: 'Asia/Shanghai', sessionID: 'real-session', nextRun: null, scheduledNextRun: '2026-09-23T02:10:00Z',
 installation: { installed: true, ready: true, status: 'active', reason: null },
 metrics: { definitions: 1, started: 2, attempts: 3, events: 2, risk: 1, unknown: 1, ignored: 0 },
 runs: [{ id: 'attempt', execution_id: 'execution', session_id: 'real-session', message_id: 'anchor', status: 'partial', started_at: '2026-09-23T01:00:00Z', steps: [] }],
 events: [{ key: 'one', name: '风险记录', risk: 'risk', device: 'fixture', sessionID: 'real-session', messageID: 'anchor' }, { key: 'two', name: '未知记录', risk: 'unknown', device: 'fixture', sessionID: 'real-session', messageID: 'anchor' }],
 queued: [], report: { status: 'updated', version: 1 },
};
beforeEach(() => { vi.resetAllMocks(); mocks.overview.mockResolvedValue({ data }); mocks.report.mockResolvedValue({ data: '# 安全运营监测\n[查看本轮对话](/sessions?session=real-session&focusMessage=anchor)' }); });
it('shows only agent investigation and removes development instructions, including legacy snapshots', async () => {
 mocks.overview.mockResolvedValue({ data: { ...data, developmentSample: true, investigationEngine: 'rules' } });
 render(<MemoryRouter><SecurityMonitor /></MemoryRouter>);
 const controls = await screen.findByRole('region', { name: '监测控制' });
 fireEvent.click(screen.getByLabelText('更多监测功能'));
 fireEvent.click(screen.getByRole('button', { name: '运行与邮件健康' }));
 expect(screen.getByRole('dialog')).toHaveTextContent('智能体调查');
 expect(screen.queryByLabelText('调查方式')).not.toBeInTheDocument();
 expect(controls).not.toHaveTextContent('开发联调');
 expect(controls).not.toHaveTextContent('每轮随机');
 expect(controls).not.toHaveTextContent('固定规则调查');
 expect(mocks.setInvestigationEngine).not.toHaveBeenCalled();
});
it('uses a primary readonly live session with workspace links behind More', async () => {
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/session']}><SecurityMonitor /></MemoryRouter>);
 expect(await screen.findByTestId('native-chat')).toHaveTextContent('real-session readonly live');
 expect(screen.getByLabelText('更多监测功能').parentElement).not.toHaveAttribute('open');
 fireEvent.click(screen.getByLabelText('更多监测功能'));
 expect(within(screen.getByRole('navigation')).getAllByRole('link')).toHaveLength(4);
 expect(screen.getByText('在工作台打开')).toHaveAttribute('href', '/sessions?session=real-session&focusMessage=anchor');
});
it('uses fact metrics, filters risks and never claims disposition', async () => {
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/dashboard']}><SecurityMonitor /></MemoryRouter>);
 await screen.findByText('风险记录'); expect(screen.getByText(/尝试 3 次/)).toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('风险筛选'), { target: { value: 'risk' } });
 expect(screen.queryByText('未知记录')).not.toBeInTheDocument(); expect(screen.queryByText('已处置事件')).not.toBeInTheDocument();
 expect(screen.getByText('未闭环')).toBeInTheDocument();
});
it('report links point to original message', async () => {
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/report']}><SecurityMonitor /></MemoryRouter>);
 fireEvent.click(await screen.findByRole('button', { name: /第 1 轮/ }));
 const link = await screen.findByText('查看本轮对话'); expect(link.closest('a')).toHaveAttribute('href', '/sessions?session=real-session&focusMessage=anchor');
});
it('marks fetch errors stale on the dashboard', async () => {
 mocks.overview.mockRejectedValueOnce(new Error('unavailable')); render(<MemoryRouter initialEntries={['/suites/host-security-monitor/dashboard']}><SecurityMonitor /></MemoryRouter>);
 expect(await screen.findByRole('alert')).toHaveTextContent('数据更新失败');
});

it('rechecks an unready installation on Start and displays the next execution', async () => {
 const unready = { ...data, installation: { ...data.installation, ready: false, status: 'disabled', reason: '未配置 XDR' }, scheduledNextRun: null };
 mocks.overview.mockResolvedValue({ data: unready });
 let finish!: (result: unknown) => void;
 mocks.start.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
 render(<MemoryRouter><SecurityMonitor /></MemoryRouter>);
 const button = await screen.findByRole('button', { name: '启动监测' });
 expect(screen.getByRole('region', { name: '监测控制' })).toHaveTextContent('未就绪');
 fireEvent.click(button); fireEvent.click(button);
 expect(mocks.start).toHaveBeenCalledTimes(1);
 expect(screen.getByRole('button', { name: '正在处理…' })).toBeDisabled();
 expect(screen.queryByLabelText('业务日期')).not.toBeInTheDocument();
 mocks.overview.mockResolvedValue({ data });
 await act(async () => finish({ data }));
 expect(await screen.findByRole('button', { name: '暂停监测' })).toBeEnabled();
 expect(screen.queryByRole('status')).not.toBeInTheDocument();
 expect(screen.queryByText(/监测已启动，首轮/)).not.toBeInTheDocument();
 expect(screen.getByRole('region', { name: '监测控制' })).toHaveTextContent('10:10:00');
});

it.each(['message', 'detail'])('shows the actual preflight reason from %s and leaves Start available for retry', async (key) => {
 mocks.overview.mockResolvedValue({ data: { ...data, installation: { ...data.installation, ready: false, status: 'disabled' } } });
 mocks.start.mockRejectedValue({ response: { data: { [key]: '未找到已启用的 XDR 接入' } } });
 render(<MemoryRouter><SecurityMonitor /></MemoryRouter>);
 fireEvent.click(await screen.findByRole('button', { name: '启动监测' }));
 await waitFor(() => expect(mocks.overview).toHaveBeenCalledTimes(2));
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();
 fireEvent.click(screen.getByLabelText('更多监测功能'));
 fireEvent.click(screen.getByRole('link', { name: '总结看板' }));
 expect(await screen.findByRole('alert')).toHaveTextContent('未找到已启用的 XDR 接入');
 await waitFor(() => expect(screen.getByRole('button', { name: '启动监测' })).toBeEnabled());
 expect(screen.queryByRole('status')).not.toBeInTheDocument();
});

it('pauses monitoring and clears the next execution time', async () => {
 const paused = { ...data, installation: { ...data.installation, status: 'disabled' }, scheduledNextRun: null };
 mocks.pause.mockImplementation(async () => { mocks.overview.mockResolvedValue({ data: paused }); return { data: paused }; });
 render(<MemoryRouter><SecurityMonitor /></MemoryRouter>);
 fireEvent.click(await screen.findByRole('button', { name: '暂停监测' }));
 expect(await screen.findByRole('button', { name: '启动监测' })).toBeEnabled();
 expect(mocks.pause).toHaveBeenCalledTimes(1);
 expect(screen.getByRole('region', { name: '监测控制' })).toHaveTextContent('监测：已暂停');
 expect(screen.getByRole('region', { name: '监测控制' })).toHaveTextContent('下次监测 —');
});

it('downloads one diagnostic file without starting another monitoring round', async () => {
 const blob = new Blob(['{"schema":1,"records":[]}'], { type: 'application/json' });
 const createUrl = vi.fn(() => 'blob:diagnostics');
 Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createUrl });
 Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
 const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
 mocks.diagnostics.mockResolvedValue({ data: blob });
 render(<MemoryRouter><SecurityMonitor /></MemoryRouter>);
 fireEvent.click(screen.getByLabelText('更多监测功能'));
 fireEvent.click(screen.getByRole('button', { name: '导出诊断日志' }));
 await waitFor(() => expect(createUrl).toHaveBeenCalledWith(blob));
 expect(click).toHaveBeenCalledTimes(1);
 expect(mocks.start).not.toHaveBeenCalled();
 expect(mocks.pause).not.toHaveBeenCalled();
 click.mockRestore();
});

it('deduplicates diagnostic downloads and keeps retry available after failure', async () => {
 let reject!: (reason: unknown) => void;
 mocks.diagnostics.mockImplementation(() => new Promise((_, fail) => { reject = fail; }));
 render(<MemoryRouter><SecurityMonitor /></MemoryRouter>);
 fireEvent.click(screen.getByLabelText('更多监测功能'));
 const button = screen.getByRole('button', { name: '导出诊断日志' });
 fireEvent.click(button); fireEvent.click(button);
 expect(mocks.diagnostics).toHaveBeenCalledTimes(1);
 expect(screen.getByRole('button', { name: '正在导出…' })).toBeDisabled();
 await act(async () => reject(new Error('private path')));
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('link', { name: '总结看板' }));
 expect(await screen.findByRole('alert')).toHaveTextContent('诊断日志导出失败，请稍后重试。');
 fireEvent.click(screen.getByLabelText('更多监测功能'));
 expect(screen.getByRole('button', { name: '导出诊断日志' })).toBeEnabled();
 expect(screen.queryByText('private path')).not.toBeInTheDocument();
});

it('switches between two reports for the selected business day', async () => {
 mocks.report.mockImplementation(async (_day, kind) => ({ data: kind === 'summary' ? '# 当日汇总内容' : '# 分轮次内容' }));
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/report']}><SecurityMonitor /></MemoryRouter>);
 expect(await screen.findByRole('list', { name: '执行轮次时间线' })).toBeInTheDocument();
 expect(screen.queryByText('分轮次内容')).not.toBeInTheDocument();
 await waitFor(() => expect(mocks.report).toHaveBeenCalledWith('2026-09-23', 'timeline'));
 fireEvent.click(screen.getByRole('button', { name: '当日告警总结' }));
 expect(await screen.findByText('当日汇总内容')).toBeInTheDocument();
 expect(mocks.report).toHaveBeenLastCalledWith('2026-09-23', 'summary');
});

it('normalizes legacy round endings and retains explanatory results without claiming event closure', async () => {
 mocks.overview.mockResolvedValue({ data: { ...data, runs: [
   { ...data.runs[0], summary: '查询完成，未发现符合条件的事件。' },
   { ...data.runs[0], id: 'interrupted', status: 'interrupted', error: '设备连接失败' },
   { ...data.runs[0], id: 'cancelled', status: 'cancelled', error: '用户暂停了监测' },
 ] } });
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/dashboard']}><SecurityMonitor /></MemoryRouter>);
 expect(await screen.findByText('查询完成，未发现符合条件的事件。')).toBeInTheDocument();
 expect(screen.getByText('完成')).toBeInTheDocument();
 expect(screen.getAllByText('失败')).toHaveLength(2);
 expect(screen.queryByText('部分完成')).not.toBeInTheDocument();
 expect(screen.getByText('设备连接失败')).toBeInTheDocument();
 expect(screen.getByText(/轮次完成不代表告警已闭环/)).toBeInTheDocument();
});

it('opens the exact original round message instead of just the daily session', async () => {
 function RouteObserver() { const location = useLocation(); return <output data-testid="location">{location.pathname}{location.search}</output>; }
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/dashboard']}><SecurityMonitor /><RouteObserver /></MemoryRouter>);
 fireEvent.click(await screen.findByRole('button', { name: '查看本轮' }));
 expect(screen.getByTestId('location')).toHaveTextContent('/sessions?session=real-session&focusMessage=anchor');
});

it('loads and downloads both reports for the selected historical day', async () => {
 mocks.overview.mockImplementation(async (day) => ({ data: { ...data, businessDate: day || data.businessDate } }));
 mocks.report.mockImplementation(async (day, kind) => ({ data: `# ${day} ${kind}` }));
 Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:report') });
 Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
 let filename = '';
 const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { filename = this.download; });
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/report']}><SecurityMonitor /></MemoryRouter>);
 fireEvent.change(await screen.findByLabelText('报告日期'), { target: { value: '2026-09-21' } });
 await waitFor(() => expect(mocks.report).toHaveBeenLastCalledWith('2026-09-21', 'timeline'));
 await waitFor(() => expect(screen.getByRole('button', { name: '下载 Markdown' })).toBeEnabled());
 expect(screen.getByRole('list', { name: '执行轮次时间线' })).toBeInTheDocument();
 expect(mocks.overview).toHaveBeenLastCalledWith('2026-09-21');
 fireEvent.click(screen.getByRole('button', { name: '下载 Markdown' }));
 expect(filename).toBe('安全运营监测-执行时间线-2026-09-21.md');
 fireEvent.click(screen.getByRole('button', { name: '当日告警总结' }));
 expect(await screen.findByText('2026-09-21 summary')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button', { name: '下载 Markdown' }));
 expect(mocks.report).toHaveBeenLastCalledWith('2026-09-21', 'summary');
 expect(filename).toBe('安全运营监测-当日总结-2026-09-21.md');
 click.mockRestore();
});

it('hides the old report and disables downloading until the newly selected date is loaded', async () => {
 mocks.report.mockResolvedValue({ data: '# 旧日期报告' });
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/report']}><SecurityMonitor /></MemoryRouter>);
 fireEvent.click(await screen.findByRole('button', { name: '当日告警总结' }));
 expect(await screen.findByText('旧日期报告')).toBeInTheDocument();
 let finish!: (value: unknown) => void;
 mocks.overview.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
 fireEvent.change(screen.getByLabelText('报告日期'), { target: { value: '2026-09-20' } });
 expect(screen.queryByText('旧日期报告')).not.toBeInTheDocument();
 expect(screen.getByRole('button', { name: '下载 Markdown' })).toBeDisabled();
 expect(screen.getByText('正在读取所选日期的报告…')).toBeInTheDocument();
 await act(async () => finish({ data: { ...data, businessDate: '2026-09-20', runs: [], report: { status: 'pending', version: 0 } } }));
 expect(await screen.findByText('所选日期暂无监测记录')).toBeInTheDocument();
 expect(screen.queryByText('旧日期报告')).not.toBeInTheDocument();
 expect(screen.getByRole('button', { name: '下载 Markdown' })).toBeDisabled();
});

it('provides today and yesterday shortcuts in the monitoring timezone', async () => {
 vi.useFakeTimers({ toFake: ['Date'] });
 vi.setSystemTime(new Date('2026-09-24T18:00:00Z'));
 mocks.overview.mockImplementation(async (day) => ({ data: { ...data, businessDate: day || '2026-09-25' } }));
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/report']}><SecurityMonitor /></MemoryRouter>);
 fireEvent.click(await screen.findByRole('button', { name: '昨天' }));
 await waitFor(() => expect(mocks.overview).toHaveBeenLastCalledWith('2026-09-24'));
 expect(screen.getByLabelText('报告日期')).toHaveValue('2026-09-24');
 fireEvent.click(screen.getByRole('button', { name: '今天' }));
 await waitFor(() => expect(mocks.overview).toHaveBeenLastCalledWith('2026-09-25'));
 expect(screen.getByLabelText('报告日期')).toHaveValue('2026-09-25');
 vi.useRealTimers();
});

it('keeps saved timeline nodes readable when the Markdown export cannot load', async () => {
 mocks.report.mockRejectedValue(new Error('download unavailable'));
 mocks.overview.mockResolvedValue({ data: { ...data, runs: [{ ...data.runs[0], status: 'completed', result: { events: 0 }, summary: '本轮查询完成，零条新事件。' }] } });
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/report']}><SecurityMonitor /></MemoryRouter>);
 expect(await screen.findByRole('alert')).toHaveTextContent('当前仍可查看已保存的执行记录');
 fireEvent.click(screen.getByRole('button', { name: /零事件 · 无需新通知/ }));
 expect(screen.getByText('本轮查询完成，零条新事件。')).toBeInTheDocument();
 expect(screen.getByRole('button', { name: '下载 Markdown' })).toBeDisabled();
});

it('shows live execution facts even before the report is generated', async () => {
 mocks.overview.mockResolvedValue({ data: { ...data, report: { status: 'pending', version: 0 }, runs: [{ ...data.runs[0], status: 'running' }] } });
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/report']}><SecurityMonitor /></MemoryRouter>);
 expect(await screen.findByRole('button', { name: /第 1 轮：调查执行中/ })).toBeInTheDocument();
 expect(screen.getByRole('button', { name: '下载 Markdown' })).toBeDisabled();
 expect(mocks.report).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button', { name: '当日告警总结' }));
 expect(screen.getByText('所选日期的报告尚未生成')).toBeInTheDocument();
});

it('clears previous timeline details while loading another date and starts its nodes collapsed', async () => {
 mocks.overview.mockResolvedValue({ data: { ...data, runs: [{ ...data.runs[0], summary: '此前日期的详细报告' }] } });
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/report']}><SecurityMonitor /></MemoryRouter>);
 fireEvent.click(await screen.findByRole('button', { name: /第 1 轮/ }));
 expect(screen.getByText('此前日期的详细报告')).toBeInTheDocument();
 let finish!: (value: unknown) => void;
 mocks.overview.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
 fireEvent.change(screen.getByLabelText('报告日期'), { target: { value: '2026-09-20' } });
 expect(screen.queryByText('此前日期的详细报告')).not.toBeInTheDocument();
 expect(screen.queryByRole('list', { name: '执行轮次时间线' })).not.toBeInTheDocument();
 expect(screen.getByRole('button', { name: '下载 Markdown' })).toBeDisabled();
 await act(async () => finish({ data: { ...data, businessDate: '2026-09-20', runs: [{ ...data.runs[0], summary: '当前日期的详细报告' }] } }));
 expect(await screen.findByRole('button', { name: /第 1 轮/ })).toHaveAttribute('aria-expanded', 'false');
 expect(screen.queryByText('当前日期的详细报告')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button', { name: /第 1 轮/ }));
 expect(screen.getByText('当前日期的详细报告')).toBeInTheDocument();
});


it('keeps the primary route on Start and navigates only through the manual workbench link', async () => {
 function RouteObserver() { const location = useLocation(); return <output data-testid="location">{location.pathname}{location.search}</output>; }
 mocks.overview.mockResolvedValue({ data: { ...data, installation: { ...data.installation, status: 'disabled' } } });
 mocks.start.mockImplementation(async () => { mocks.overview.mockResolvedValue({ data }); return { data }; });
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/session']}><SecurityMonitor /><RouteObserver /></MemoryRouter>);
 fireEvent.click(await screen.findByRole('button', { name: '启动监测' }));
 await screen.findByRole('button', { name: '暂停监测' });
 expect(screen.getByTestId('location')).toHaveTextContent('/suites/host-security-monitor/session');
 await act(async () => mocks.subscription.onEvent({ type: 'monitor.execution.started' }));
 await act(async () => mocks.subscription.onReconnect());
 expect(screen.getByTestId('location')).toHaveTextContent('/suites/host-security-monitor/session');
 fireEvent.click(screen.getByLabelText('更多监测功能'));
 fireEvent.click(screen.getByRole('link', { name: '在工作台打开' }));
 expect(screen.getByTestId('location')).toHaveTextContent('/sessions?session=real-session&focusMessage=anchor');
});

it('keeps a historical conversation while showing live global starts and finishes', async () => {
 let running = false;
 mocks.overview.mockImplementation(async (day) => ({ data: { ...data, businessDate: day || data.businessDate, sessionID: day ? 'historical-session' : 'real-session', currentRun: running ? { id: 'current', session_id: 'today-session', message_id: 'today-message', started_at: '2026-09-29T01:00:00Z' } : null } }));
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/session']}><SecurityMonitor /></MemoryRouter>);
 await screen.findByTestId('native-chat');
 fireEvent.click(screen.getByLabelText('更多监测功能'));
 fireEvent.click(screen.getByRole('button', { name: '查看历史日期' }));
 fireEvent.change(screen.getByLabelText('业务日期'), { target: { value: '2026-09-20' } });
 await waitFor(() => expect(screen.getByTestId('native-chat')).toHaveTextContent('historical-session'));
 fireEvent.keyDown(document, { key: 'Escape' });
 running = true;
 await act(async () => mocks.subscription.onEvent({ type: 'monitor.execution.started' }));
 expect(screen.getByRole('region', { name: '监测控制' })).toHaveTextContent('本轮执行中');
 expect(screen.getByRole('region', { name: '监测控制' })).toHaveTextContent('下次：本轮结束后等待调度');
 expect(screen.getByTestId('native-chat')).toHaveTextContent('historical-session');
 expect(mocks.overview).toHaveBeenLastCalledWith('2026-09-20');
 running = false;
 await act(async () => mocks.subscription.onEvent({ type: 'monitor.execution.finished' }));
 expect(screen.getByRole('region', { name: '监测控制' })).toHaveTextContent('等待下一轮');
 expect(screen.getByTestId('native-chat')).toHaveTextContent('historical-session');
});

it('keeps mail warnings off the conversation and displays them only on the dashboard', async () => {
 mocks.overview.mockResolvedValue({ data: { ...data, mail: { enabled: true, pending: 2, needsReview: 1, health: { errors: ['收信连接中断'], receive: { state: 'unavailable', error_type: 'TimeoutError', stage: 'poll', last_error_stage: 'fetch', last_error_at: '2026-09-29T01:00:00Z', consecutive_failures: 3 }, send: { state: 'healthy', last_success_at: '2026-09-29T00:50:00Z' } } }, investigation: { pending: 2, deferred: 1, system_wait: 1, needs_review: 0 }, metrics: { ...data.metrics, investigatedEvents: 2, investigationCompletedEvents: 1, investigationCompletionRate: .5 } } });
 render(<MemoryRouter><SecurityMonitor /></MemoryRouter>);
 await screen.findByTestId('native-chat');
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();
 expect(screen.queryByText(/邮件通道异常/)).not.toBeInTheDocument();
 fireEvent.click(screen.getByLabelText('更多监测功能'));
 fireEvent.click(screen.getByRole('link', { name: '总结看板' }));
 expect(await screen.findByRole('alert')).toHaveTextContent('邮件通道异常：收信连接中断');
 fireEvent.click(screen.getByRole('button', { name: '查看原因' }));
 const drawer = screen.getByRole('dialog');
 expect(drawer).toHaveTextContent('收信连接 · 不可用');
 expect(drawer).toHaveTextContent('TimeoutError');
 expect(drawer).toHaveTextContent('fetch');
 expect(drawer).not.toHaveTextContent('poll');
 expect(drawer).toHaveTextContent('连续失败：3 次');
 expect(drawer).toHaveTextContent('发信连接 · 正常');
 expect(drawer).toHaveTextContent('1 / 2 条 · 50%');
 expect(drawer).toHaveTextContent('等待系统恢复 1');
});

it('keeps dashboard rows concise and preserves the full report and exact conversation anchor on demand', async () => {
 function RouteObserver() { const location = useLocation(); return <output data-testid="location">{location.pathname}{location.search}</output>; }
 mocks.overview.mockResolvedValue({ data: { ...data, runs: [{ ...data.runs[0], status: 'failed', summary: '长篇事件证据和调查过程。'.repeat(100),
  error: '智能体调查未完成：调查模型请求失败，请检查模型服务、配置和访问权限',
  next_step: '已保存进度，稍后继续调查。', result: { query_events: 0, resumed_events: 14, analyzed: 2,
   investigation_backlog: { system_wait: 1 }, errors: ['智能体调查未完成：调查模型请求失败'] } }] } });
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/dashboard']}><SecurityMonitor /><RouteObserver /></MemoryRouter>);
 const headline = await screen.findByText('调查模型请求失败');
 expect(headline).toBeVisible();
 expect(screen.getByText('本轮查询 0 条 · 历史续查 14 条 · 已有调查结论 2 条')).toBeVisible();
 const full = screen.getByText('长篇事件证据和调查过程。'.repeat(100));
 expect(full).not.toBeVisible();
 const disclosure = screen.getByText('展开本轮详情').closest('details');
 expect(disclosure).not.toHaveAttribute('open');
 fireEvent.click(screen.getByText('展开本轮详情'));
 expect(disclosure).toHaveAttribute('open');
 expect(full).toBeVisible();
 expect(screen.getByText('已保存进度，稍后继续调查。')).toBeVisible();
 fireEvent.click(screen.getByRole('button', { name: '查看本轮' }));
 expect(screen.getByTestId('location')).toHaveTextContent('/sessions?session=real-session&focusMessage=anchor');
});

it('displays a completed round and outstanding historical recovery independently', async () => {
 mocks.overview.mockResolvedValue({ data: { ...data, runs: [{ ...data.runs[0], status: 'completed', result: {
  events: 11, query_events: 0, resumed_events: 11, analyzed: 2, deferred: 9,
  investigation_backlog: { system_wait: 1, earliest_retry_at: '2026-09-29T01:49:49Z' },
 } }] } });
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/dashboard']}><SecurityMonitor /></MemoryRouter>);
 const line = await screen.findByText(/历史待恢复 1 条/);
 expect(line).toHaveClass('text-sky-700');
 const row = line.closest('tr')!;
 expect(within(row).getByText('完成')).toBeVisible();
 expect(within(row).queryByText('失败')).not.toBeInTheDocument();
 expect(row).toHaveTextContent('本轮查询 0 条 · 历史续查 11 条 · 已有调查结论 2 条');
});

it('labels a recovered mail connection without presenting old errors as a current outage or probes as delivery', async () => {
 mocks.overview.mockResolvedValue({ data: { ...data, mail: { enabled: true, health: { errors: [],
  receive: { state: 'healthy', last_error_at: '2026-09-29T01:43:11Z', last_error_stage: 'connect', error_type: 'TimeoutError', last_success_at: '2026-09-29T01:46:44Z', last_success_stage: 'poll', consecutive_failures: 0 },
  send: { state: 'healthy', last_success_at: '2026-09-29T01:42:11Z', last_success_stage: 'probe' },
 } } } });
 render(<MemoryRouter><SecurityMonitor /></MemoryRouter>);
 await screen.findByTestId('native-chat');
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();
 fireEvent.click(screen.getByLabelText('更多监测功能'));
 fireEvent.click(screen.getByRole('button', { name: '运行与邮件健康' }));
 const drawer = screen.getByRole('dialog');
 expect(drawer).toHaveTextContent('收信连接 · 正常（已恢复）');
 expect(drawer).toHaveTextContent('上次异常（已恢复）');
 expect(drawer).toHaveTextContent('TimeoutError');
 expect(drawer).toHaveTextContent('连接检查成功：');
 expect(drawer).not.toHaveTextContent('邮件已发送');
 expect(drawer).not.toHaveTextContent('连续失败：');
});


it('keeps refresh and installation notices off the conversation without navigating automatically', async () => {
 function RouteObserver() { return <output data-testid="route">{useLocation().pathname}</output>; }
 mocks.overview.mockResolvedValueOnce({ data: { ...data, installation: { installed: true, ready: false, status: 'disabled', reason: '配置仍需检查' } } });
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/session']}><SecurityMonitor /><RouteObserver /></MemoryRouter>);
 await screen.findByTestId('native-chat');
 expect(screen.queryByText(/配置仍需检查/)).not.toBeInTheDocument();
 mocks.overview.mockRejectedValue(new Error('unavailable'));
 await act(async () => mocks.subscription.onReconnect());
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();
 expect(screen.getByTestId('route')).toHaveTextContent('/session');
 fireEvent.click(screen.getByLabelText('更多监测功能'));
 fireEvent.click(screen.getByRole('link', { name: '总结看板' }));
 expect(await screen.findByRole('alert')).toHaveTextContent('数据更新失败');
 expect(screen.getByText(/配置仍需检查/)).toBeVisible();
});


it('orders dashboard round rows and trend markers newest first even for ascending API data', async () => {
 mocks.overview.mockResolvedValue({ data: { ...data, runs: [
  { ...data.runs[0], id: 'older', started_at: '2026-09-23T01:00:00Z', error: '旧记录' },
  { ...data.runs[0], id: 'newer', started_at: '2026-09-23T02:00:00Z', error: '新记录' },
 ] } });
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/dashboard']}><SecurityMonitor /></MemoryRouter>);
 await screen.findByText('轮次明细');
 const table = screen.getByText('轮次明细').parentElement!;
 const rows = within(table).getAllByRole('row');
 expect(rows[1]).toHaveTextContent('新记录');
 expect(rows[2]).toHaveTextContent('旧记录');
 const trend = screen.getByLabelText('实际轮次状态');
 expect(within(trend).getAllByRole('button')[0].title).toContain('10:00:00');
});
