import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const sdk = vi.hoisted(() => {
  const operation = vi.fn();
  (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__ = { api: { contract: () => ({ operation }) } };
  return { operation };
});
import SocAlertsPage from '../../../.flocks/flockshub/plugins/webuis/soc_ui/soc_alerts/src/index';

function incident(id: string, source: string, protocol: string) {
  return { id, title: id, observedAt: new Date(Date.now() - 60_000).toISOString(),
    tableCells: { _source_type: { value: source }, net_type: { value: protocol }, threat_name: { value: id } } };
}
const records = [incident('SIP告警', 'sangfor-sip', 'unknown'), incident('TDP告警', 'tdp', 'http')];
function serve(rows = records) {
  sdk.operation.mockImplementation(async (_name, { params }) => ({ data: {
    summary: { availableCount: rows.length },
    filterOptions: { _source_type: ['sangfor-sip', 'tdp'], net_type: ['unknown', 'http'] },
    incidents: rows.filter(row => Object.entries(params.filters || {}).every(([key, values]) => (
      (values as string[]).includes(row.tableCells[key as keyof typeof row.tableCells]?.value)
    ))),
  } }));
}
async function ready() { await waitFor(() => expect(screen.queryByText('正在查询告警…')).not.toBeInTheDocument()); }
function menu(label: string) {
  fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${label}：`) }));
  return within(screen.getByRole('dialog', { name: label }));
}
function select(label: string, choice: string) {
  const dropdown = menu(label);
  fireEvent.click(dropdown.getByRole('checkbox', { name: choice }));
  fireEvent.click(dropdown.getByRole('button', { name: '确定' }));
}

describe('alert investigation filtering', () => {
  beforeEach(() => { sdk.operation.mockReset(); window.localStorage.setItem('flocks-language', 'zh-CN'); });

  it('loads SE-only non-HTTP alerts with no implicit source or protocol filters', async () => {
    serve([records[0]]);
    render(<SocAlertsPage />);
    await ready();
    expect(sdk.operation.mock.calls[0][1].params.filters).toBeUndefined();
    expect(screen.getByText('SIP告警')).toBeVisible();
    expect(screen.getByRole('button', { name: '数据源：全部' })).toBeVisible();
    expect(menu('数据源').getByRole('checkbox', { name: '深信服 SIP' })).toBeVisible();
  });

  it('stages selections, submits only with the main query, and retains facets on zero results', async () => {
    serve(); render(<SocAlertsPage />); await ready();
    select('数据源', '深信服 SIP');
    expect(sdk.operation).toHaveBeenCalledTimes(1);
    expect(screen.getByText('筛选条件已修改，点击查询生效')).toBeVisible();
    const protocolMenu = menu('协议类型');
    expect(protocolMenu.queryByRole('button', { name: '查询' })).toBeNull();
    fireEvent.click(protocolMenu.getByRole('checkbox', { name: 'http' }));
    fireEvent.click(protocolMenu.getByRole('button', { name: '确定' }));
    fireEvent.click(screen.getByRole('button', { name: '查询' })); await ready();
    expect(screen.getByText('当前筛选条件下没有匹配的告警')).toBeVisible();
    expect(menu('数据源').getByRole('checkbox', { name: 'TDP' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    const before = sdk.operation.mock.lastCall![1].params;
    fireEvent.click(screen.getAllByRole('button', { name: '清空筛选' }).at(-1)!); await ready();
    expect(screen.getByText('SIP告警')).toBeVisible();
    expect(screen.getByText('TDP告警')).toBeVisible();
    const after = sdk.operation.mock.lastCall![1].params;
    expect(after.filters).toBeUndefined();
    expect(after.endTime - after.startTime).toBe(before.endTime - before.startTime);
  });

  it('cancels menu edits and keeps missing selected values removable', async () => {
    serve(); render(<SocAlertsPage />); await ready();
    const dropdown = menu('数据源');
    fireEvent.click(dropdown.getByRole('checkbox', { name: '深信服 SIP' }));
    fireEvent.click(dropdown.getByRole('button', { name: '取消' }));
    expect(screen.getByRole('button', { name: '数据源：全部' })).toBeVisible();
    select('数据源', '深信服 SIP');
    sdk.operation.mockResolvedValue({ data: { summary: { availableCount: 0 }, incidents: [], filterOptions: { _source_type: [] } } });
    fireEvent.click(screen.getByRole('button', { name: '查询' })); await ready();
    const selected = menu('数据源').getByRole('checkbox', { name: '深信服 SIP' });
    expect(selected).toHaveAttribute('aria-checked', 'true');
  });

  it('distinguishes an empty time window and supports Enter for keyword queries', async () => {
    serve([]); render(<SocAlertsPage />); await ready();
    expect(screen.getByText('当前时间范围内暂无告警')).toBeVisible();
    const keyword = screen.getByPlaceholderText('请输入源地址、目标地址、HTTP Host、URL、规则 ID 或威胁名称');
    fireEvent.change(keyword, { target: { value: 'missing' } });
    fireEvent.keyDown(keyword, { key: 'Enter' });
    await waitFor(() => expect(sdk.operation).toHaveBeenCalledTimes(2));
  });
  it('preserves a custom time window when clearing business filters', async () => {
    serve(); render(<SocAlertsPage />); await ready();
    fireEvent.click(screen.getByRole('button', { name: /^时间范围/ }));
    fireEvent.click(screen.getByRole('button', { name: '精确时间', exact: true }));
    const start = new Date(Date.now() - 2 * 86400_000);
    const end = new Date(Date.now() + 86400_000);
    const localInput = (date: Date) => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}T00:00`;
    fireEvent.change(screen.getByLabelText('开始时间'), { target: { value: localInput(start) } });
    fireEvent.change(screen.getByLabelText('结束时间'), { target: { value: localInput(end) } });
    fireEvent.click(screen.getByRole('button', { name: '确定', exact: true })); await ready();
    select('数据源', '深信服 SIP');
    fireEvent.click(screen.getByRole('button', { name: '查询' })); await ready();
    const before = sdk.operation.mock.lastCall![1].params;
    fireEvent.click(screen.getByRole('button', { name: '清空筛选' })); await ready();
    const after = sdk.operation.mock.lastCall![1].params;
    expect(after.filters).toBeUndefined();
    expect(after.startTime).toBe(before.startTime);
    expect(after.endTime).toBe(before.endTime);
    expect(screen.getByText('TDP告警')).toBeVisible();
  });

  it('shows a retry action for request failures without claiming there are no alerts', async () => {
    serve();
    const healthy = sdk.operation.getMockImplementation()!;
    sdk.operation.mockRejectedValueOnce(new Error('service unavailable')).mockImplementation(healthy);
    render(<SocAlertsPage />); await ready();
    expect(screen.queryByText('当前时间范围内暂无告警')).toBeNull();
    expect(screen.getByText('service unavailable')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '重试' })); await ready();
    expect(screen.getByText('SIP告警')).toBeVisible();
  });

  it('shows the row limit and keeps source options not present in the returned rows', async () => {
    sdk.operation.mockResolvedValue({ data: { incidents: [records[0]], summary: { availableCount: 2, representativeCount: 2 },
      filterOptions: { _source_type: ['sangfor-sip', 'tdp'] } } });
    render(<SocAlertsPage />); await ready();
    expect(screen.getByText('当前仅展示前 1 条告警，请缩小时间范围或增加筛选条件。')).toBeVisible();
    expect(menu('数据源').getByRole('checkbox', { name: 'TDP' })).toBeVisible();
  });

});
