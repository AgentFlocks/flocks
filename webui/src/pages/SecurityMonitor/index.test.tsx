import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import SecurityMonitor from './index';
const mocks = vi.hoisted(() => ({ overview: vi.fn(), report: vi.fn(), start: vi.fn(), pause: vi.fn(), diagnostics: vi.fn(), setInvestigationEngine: vi.fn() }));
vi.mock('@/api/securityMonitoring', async importOriginal => ({ ...(await importOriginal<any>()), monitoringApi: mocks }));
vi.mock('@/hooks/useSSE', () => ({ useSSE: () => ({}) }));
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
 expect(controls).toHaveTextContent('智能体调查');
 expect(screen.queryByLabelText('调查方式')).not.toBeInTheDocument();
 expect(controls).not.toHaveTextContent('开发联调');
 expect(controls).not.toHaveTextContent('每轮随机');
 expect(controls).not.toHaveTextContent('固定规则调查');
 expect(mocks.setInvestigationEngine).not.toHaveBeenCalled();
});
it('uses one native readonly live session and four workspace tabs', async () => {
 render(<MemoryRouter initialEntries={['/suites/host-security-monitor/session']}><SecurityMonitor /></MemoryRouter>);
 expect(await screen.findByTestId('native-chat')).toHaveTextContent('real-session readonly live');
 expect(within(screen.getByRole('navigation')).getAllByRole('link')).toHaveLength(4);
 expect(screen.getByText('在工作台打开')).toHaveAttribute('href', '/sessions?session=real-session');
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
it('marks fetch errors stale', async () => {
 mocks.overview.mockRejectedValueOnce(new Error('unavailable')); render(<MemoryRouter><SecurityMonitor /></MemoryRouter>);
 expect(await screen.findByRole('alert')).toHaveTextContent('数据更新失败');
});

it('rechecks an unready installation on Start and displays the next execution', async () => {
 const unready = { ...data, installation: { ...data.installation, ready: false, status: 'disabled', reason: '未配置 XDR' }, scheduledNextRun: null };
 mocks.overview.mockResolvedValue({ data: unready });
 let finish!: (result: unknown) => void;
 mocks.start.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
 render(<MemoryRouter><SecurityMonitor /></MemoryRouter>);
 const button = await screen.findByRole('button', { name: '检查接入并启动' });
 expect(screen.getByText(/监测未启动/)).toBeInTheDocument();
 fireEvent.click(button); fireEvent.click(button);
 expect(mocks.start).toHaveBeenCalledTimes(1);
 expect(screen.getByRole('button', { name: '正在处理…' })).toBeDisabled();
 expect(screen.getByLabelText('业务日期')).toBeDisabled();
 mocks.overview.mockResolvedValue({ data });
 await act(async () => finish({ data }));
 expect(await screen.findByRole('button', { name: '暂停监测' })).toBeEnabled();
 expect(screen.getByRole('status')).toHaveTextContent('监测已启动');
 expect(screen.getByRole('status')).toHaveTextContent('首轮立即进入执行队列');
 expect(screen.getByRole('region', { name: '监测控制' })).toHaveTextContent('10:10:00');
});

it.each(['message', 'detail'])('shows the actual preflight reason from %s and leaves Start available for retry', async (key) => {
 mocks.overview.mockResolvedValue({ data: { ...data, installation: { ...data.installation, ready: false, status: 'disabled' } } });
 mocks.start.mockRejectedValue({ response: { data: { [key]: '未找到已启用的 XDR 接入' } } });
 render(<MemoryRouter><SecurityMonitor /></MemoryRouter>);
 fireEvent.click(await screen.findByRole('button', { name: '检查接入并启动' }));
 expect(await screen.findByRole('alert')).toHaveTextContent('未找到已启用的 XDR 接入');
 await waitFor(() => expect(screen.getByRole('button', { name: '检查接入并启动' })).toBeEnabled());
 expect(screen.queryByRole('status')).not.toBeInTheDocument();
});

it('pauses monitoring and clears the next execution time', async () => {
 const paused = { ...data, installation: { ...data.installation, status: 'disabled' }, scheduledNextRun: null };
 mocks.pause.mockImplementation(async () => { mocks.overview.mockResolvedValue({ data: paused }); return { data: paused }; });
 render(<MemoryRouter><SecurityMonitor /></MemoryRouter>);
 fireEvent.click(await screen.findByRole('button', { name: '暂停监测' }));
 expect(await screen.findByRole('button', { name: '检查接入并启动' })).toBeEnabled();
 expect(mocks.pause).toHaveBeenCalledTimes(1);
 expect(screen.getByRole('region', { name: '监测控制' })).toHaveTextContent('监测：已暂停');
 expect(screen.getByRole('region', { name: '监测控制' })).toHaveTextContent('下次检查 —');
});

it('downloads one diagnostic file without starting another monitoring round', async () => {
 const blob = new Blob(['{"schema":1,"records":[]}'], { type: 'application/json' });
 const createUrl = vi.fn(() => 'blob:diagnostics');
 Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createUrl });
 Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
 const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
 mocks.diagnostics.mockResolvedValue({ data: blob });
 render(<MemoryRouter><SecurityMonitor /></MemoryRouter>);
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
 const button = screen.getByRole('button', { name: '导出诊断日志' });
 fireEvent.click(button); fireEvent.click(button);
 expect(mocks.diagnostics).toHaveBeenCalledTimes(1);
 expect(screen.getByRole('button', { name: '正在导出…' })).toBeDisabled();
 await act(async () => reject(new Error('private path')));
 expect(await screen.findByRole('alert')).toHaveTextContent('诊断日志导出失败，请稍后重试。');
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
