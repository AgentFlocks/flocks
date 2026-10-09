import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Page from '../../../.flocks/flockshub/plugins/webuis/soc_ui/soc_cases/src/index';
import type {
  CaseDetail,
  CaseRequest,
} from '../../../.flocks/flockshub/plugins/webuis/soc_ui/soc_cases/src/api';

const fetchMock = vi.fn();
const mutation = vi.fn();
let detail: CaseDetail;
let empty = false;
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
function response(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}
function success(output: unknown) {
  return response({ success: true, output: JSON.stringify(output), metadata: {} });
}
function action(extra: Record<string, unknown> = {}) {
  return {
    id: 'action-1',
    title: '隔离测试终端',
    target: '测试终端',
    reason: '已确认恶意连接',
    tool_name: 'test_isolate',
    parameters: { host: 'test-host' },
    evidence_ids: ['e1'],
    rollback_plan: '解除隔离',
    verification_plan: '新采集网络连接',
    approval_status: 'approved',
    execution_status: 'not_started',
    verification_status: 'unverified',
    ...extra,
  };
}
async function openCase() {
  render(<Page />);
  await screen.findByRole('heading', { name: '可疑终端连接' });
}
function dialog() {
  return within(screen.getByRole('dialog'));
}
beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem('flocks-language', 'zh-CN');
  window.history.replaceState(null, '', '/');
  empty = false;
  mutation.mockReset();
  fetchMock.mockReset();
  detail = {
    case: {
      id: 'case-1',
      title: '可疑终端连接',
      summary: '调查异常外联',
      severity: 'high',
      status: 'investigating',
      version: 7,
      updated_at: '2026-10-01T02:00:00Z',
    },
    evidence: [
      { id: 'e1', title: '初始网络日志', source: '测试传感器', content: '已采集原始记录' },
      { id: 'e2', title: '处置后连接日志', source: '测试传感器', content: '测试连接已停止' },
    ],
    hypotheses: [],
    actions: [],
    reviews: [],
    improvements: [],
    timeline: [],
    counts: { evidence: 2, records: 2, timeline: 0 },
    limit: 100,
    offset: 0,
  };
  mutation.mockImplementation((request: CaseRequest) => {
    detail.case.version += 1;
    if (request.params.operation === 'create') {
      empty = false;
      detail.case.title = String(request.params.data.title);
    }
    return success({ case: clone(detail.case), record: {} });
  });
  fetchMock.mockImplementation(async (url: string, options?: RequestInit) => {
    if (url === '/api/tools?source=device')
      return response([
        {
          name: 'test_isolate',
          source: 'device',
          enabled: true,
          parameters: [{ name: 'host', type: 'string' }],
        },
      ]);
    if (url.startsWith('/api/session/'))
      return response({
        items: [
          { info: { id: 'message-real', role: 'user' }, parts: [{ type: 'text', text: '调查本次告警' }] },
        ],
      });
    if (url === '/api/workflow/soc_case_investigation/run') return response({ id: 'execution-1' });
    if (url !== '/api/tools/soc_case_manage/execute') throw new Error(`Unexpected URL ${url}`);
    const request = JSON.parse(String(options?.body)) as CaseRequest;
    if (request.params.operation === 'list')
      return success({ cases: empty ? [] : [clone(detail.case)], total: empty ? 0 : 1 });
    if (request.params.operation === 'get') return success(clone(detail));
    return mutation(request);
  });
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SOC incident response page', () => {
  it('keeps tab selection, keyboard focus and the labelled panel in sync', async () => {
    const user = userEvent.setup();
    await openCase();
    const investigation = screen.getByRole('tab', { name: '调查', exact: true });
    await user.click(investigation);
    await user.keyboard('{ArrowRight}');
    const responseTab = screen.getByRole('tab', { name: '处置', exact: true });
    expect(responseTab).toHaveFocus();
    expect(responseTab).toHaveAttribute('aria-selected', 'true');
    expect(investigation).toHaveAttribute('tabindex', '-1');
    expect(screen.getByRole('tabpanel', { name: '处置' })).toBeInTheDocument();
    await user.tab();
    expect(screen.getByRole('tabpanel', { name: '处置' })).toHaveFocus();
    await user.tab({ shift: true });
    expect(responseTab).toHaveFocus();
    await user.keyboard('{End}{ArrowRight}');
    expect(investigation).toHaveFocus();
    await user.keyboard('{ArrowLeft}');
    expect(screen.getByRole('tab', { name: '事件时间线' })).toHaveFocus();
    expect(screen.getByRole('tabpanel', { name: '事件时间线' })).toBeInTheDocument();
    await user.keyboard('{Home}');
    expect(investigation).toHaveFocus();
    expect(screen.getByRole('tab', { selected: true })).toBe(investigation);
  });

  it('creates an incident from empty state without sending invented execution identity', async () => {
    empty = true;
    const user = userEvent.setup();
    render(<Page />);
    expect(await screen.findByText('暂无事件，可从告警详情升级或直接新建。')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '新建事件' }));
    await user.type(dialog().getByLabelText('事件标题 *'), '新的安全事件');
    await user.click(dialog().getByRole('button', { name: '创建事件' }));
    await screen.findByRole('heading', { name: '新的安全事件' });
    const request = mutation.mock.calls[0][0];
    expect(request.params).toMatchObject({
      operation: 'create',
      data: { title: '新的安全事件', severity: 'medium' },
    });
    expect(request.params.idempotency_key).toBeTruthy();
    expect(request.params.expected_version).toBeUndefined();
    expect(request.session_id).toBeUndefined();
    expect(request.message_id).toBeUndefined();
  });
  it('refreshes a conflicting version while preserving the draft for resubmit', async () => {
    const user = userEvent.setup();
    await openCase();
    mutation.mockImplementationOnce(() => {
      detail.case.version = 8;
      return response({
        success: false,
        error: 'version mismatch',
        metadata: { code: 'version_conflict', status_code: 409 },
      });
    });
    await user.click(screen.getByRole('button', { name: '添加证据' }));
    await user.type(dialog().getByLabelText('证据名称 *'), '持续追查记录');
    await user.type(dialog().getByLabelText('证据来源 *'), '审计日志');
    await user.type(dialog().getByLabelText('证据内容 *'), '不能丢失的调查发现');
    await user.click(dialog().getByRole('button', { name: '添加证据' }));
    expect(await dialog().findByRole('alert')).toHaveTextContent('你的输入仍保留');
    expect(dialog().getByLabelText('证据内容 *')).toHaveValue('不能丢失的调查发现');
    await waitFor(() => expect(screen.getByText('版本 8')).toBeInTheDocument());
    await user.click(dialog().getByRole('button', { name: '添加证据' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mutation.mock.calls[0][0].params.expected_version).toBe(7);
    expect(mutation.mock.calls[1][0].params.expected_version).toBe(8);
    expect(mutation.mock.calls[1][0].params.idempotency_key).not.toBe(
      mutation.mock.calls[0][0].params.idempotency_key,
    );
  });
  it('re-reads the edited record after a conflict instead of resubmitting a stage it has passed', async () => {
    detail.case.status = 'open';
    const user = userEvent.setup();
    await openCase();
    mutation.mockImplementationOnce(() => {
      detail.case.version = 8;
      detail.case.status = 'review';
      return response({
        success: false,
        error: 'version mismatch',
        metadata: { code: 'version_conflict', status_code: 409 },
      });
    });
    await user.click(screen.getByRole('button', { name: '更新事件' }));
    await user.selectOptions(dialog().getByLabelText('推进阶段 *'), 'investigating');
    await user.type(dialog().getByLabelText('负责人'), '值班分析师');
    await user.click(dialog().getByRole('button', { name: '更新事件' }));
    expect(await dialog().findByRole('alert')).toHaveTextContent('你的输入仍保留');
    await waitFor(() => expect(dialog().queryByLabelText('推进阶段 *')).not.toBeInTheDocument());
    expect(dialog().getByLabelText('负责人')).toHaveValue('值班分析师');
    await user.click(dialog().getByRole('button', { name: '更新事件' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mutation.mock.calls[0][0].params.data.status).toBe('investigating');
    expect(mutation.mock.calls[1][0].params.expected_version).toBe(8);
    expect(mutation.mock.calls[1][0].params.data.status).toBeUndefined();
    expect(mutation.mock.calls[1][0].params.data.assignee).toBe('值班分析师');
  });
  it('asks for the approval decision again when a conflict shows the action was already decided', async () => {
    detail.actions = [action({ approval_status: 'pending' })];
    const user = userEvent.setup();
    await openCase();
    mutation.mockImplementationOnce(() => {
      detail.case.version = 8;
      detail.actions = [action({ approval_status: 'approved' })];
      return response({
        success: false,
        error: 'version mismatch',
        metadata: { code: 'version_conflict', status_code: 409 },
      });
    });
    await user.click(screen.getByRole('tab', { name: '处置' }));
    await user.click(screen.getByRole('button', { name: '人工审批' }));
    await user.selectOptions(dialog().getByLabelText('审批决定 *'), 'approve');
    await user.type(dialog().getByLabelText('审批意见 *'), '影响范围已确认，批准隔离');
    await user.click(dialog().getByRole('button', { name: '人工审批' }));
    expect(await dialog().findByRole('alert')).toHaveTextContent('你的输入仍保留');
    await waitFor(() => expect(dialog().getByLabelText('审批决定 *')).toHaveValue(''));
    expect(dialog().getByLabelText('审批意见 *')).toHaveValue('影响范围已确认，批准隔离');
    await user.click(dialog().getByRole('button', { name: '人工审批' }));
    expect(mutation).toHaveBeenCalledTimes(1);
    await user.selectOptions(dialog().getByLabelText('审批决定 *'), 'reject');
    await user.click(dialog().getByRole('button', { name: '人工审批' }));
    await waitFor(() => expect(mutation).toHaveBeenCalledTimes(2));
    expect(mutation.mock.calls[1][0].params.data.decision).toBe('reject');
    expect(mutation.mock.calls[1][0].params.expected_version).toBe(8);
  });
  it('retries an unconfirmed write using exactly the same key, version and payload', async () => {
    const user = userEvent.setup();
    await openCase();
    mutation.mockRejectedValueOnce(new TypeError('network interrupted'));
    await user.click(screen.getByRole('button', { name: '添加调查假设' }));
    await user.type(dialog().getByLabelText('调查假设 *'), '主机可能遭到入侵');
    await user.click(dialog().getByRole('button', { name: '添加调查假设' }));
    await dialog().findByRole('button', { name: '重试同一请求' });
    expect(dialog().getByRole('button', { name: '添加调查假设' })).toBeDisabled();
    await user.click(dialog().getByRole('button', { name: '刷新当前记录' }));
    await user.click(dialog().getByRole('button', { name: '重试同一请求' }));
    await waitFor(() => expect(mutation).toHaveBeenCalledTimes(2));
    expect(mutation.mock.calls[1][0]).toEqual(mutation.mock.calls[0][0]);
  });
  it('links a hypothesis to named evidence without copying record IDs', async () => {
    const user = userEvent.setup();
    await openCase();
    await user.click(screen.getByRole('button', { name: '添加调查假设' }));
    await user.type(dialog().getByLabelText('调查假设 *'), '外联来自被利用的服务');
    await user.click(dialog().getByRole('checkbox', { name: /初始网络日志/ }));
    await user.type(dialog().getByLabelText('待补证据与问题'), '确认进程来源\n核查部署时间');
    await user.click(dialog().getByRole('button', { name: '添加调查假设' }));
    await waitFor(() => expect(mutation).toHaveBeenCalled());
    expect(mutation.mock.calls[0][0].params.data).toMatchObject({
      evidence_ids: ['e1'],
      gaps: ['确认进程来源', '核查部署时间'],
    });
  });
  it('requires a loaded real session message before executing the approved immutable plan', async () => {
    detail.actions = [action()];
    const user = userEvent.setup();
    await openCase();
    await user.click(screen.getByRole('tab', { name: '处置' }));
    const execute = screen.getByRole('button', { name: '执行已批准动作' });
    expect(execute).toBeDisabled();
    await user.type(screen.getByLabelText('执行关联会话'), 'session-real');
    expect(execute).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '加载会话消息' }));
    await user.selectOptions(await screen.findByLabelText('关联消息'), 'message-real');
    await user.click(execute);
    await waitFor(() => expect(mutation).toHaveBeenCalled());
    expect(mutation.mock.calls[0][0]).toMatchObject({
      session_id: 'session-real',
      message_id: 'message-real',
      params: { operation: 'execute_action', expected_version: 7, data: { action_id: 'action-1' } },
    });
    expect(Object.keys(mutation.mock.calls[0][0].params.data)).toEqual(['action_id']);
    await user.clear(screen.getByLabelText('执行关联会话'));
    await user.type(screen.getByLabelText('执行关联会话'), 'other-session');
    expect(screen.getByRole('button', { name: '执行已批准动作' })).toBeDisabled();
  });
  it('distinguishes successful execution from verified effect and links fresh evidence', async () => {
    detail.actions = [action({ execution_status: 'succeeded' })];
    const user = userEvent.setup();
    await openCase();
    await user.click(screen.getByRole('tab', { name: '处置' }));
    expect(screen.getByText('执行成功')).toBeInTheDocument();
    expect(screen.getByText('尚未核验')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '执行已批准动作' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '核验处置效果' }));
    await user.selectOptions(dialog().getByLabelText('核验结论 *'), 'verified');
    await user.type(dialog().getByLabelText('核验记录 *'), '新记录证实连接已被阻断');
    await user.click(dialog().getByRole('checkbox', { name: /处置后连接日志/ }));
    await user.click(dialog().getByRole('button', { name: '核验处置效果' }));
    await waitFor(() => expect(mutation).toHaveBeenCalled());
    expect(mutation.mock.calls[0][0].params.data).toMatchObject({
      action_id: 'action-1',
      verdict: 'verified',
      evidence_ids: ['e2'],
    });
  });
  it('prevents blind re-execution of unknown outcomes and permits evidence-based reconciliation', async () => {
    detail.actions = [action({ execution_status: 'unknown' })];
    const user = userEvent.setup();
    await openCase();
    await user.click(screen.getByRole('tab', { name: '处置' }));
    expect(screen.queryByRole('button', { name: '执行已批准动作' })).not.toBeInTheDocument();
    expect(screen.getByText(/系统不会再次执行该动作/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '核验处置效果' }));
    await user.selectOptions(dialog().getByLabelText('核验结论 *'), 'not_executed');
    await user.type(dialog().getByLabelText('核验记录 *'), '设备审计证实指令没有到达');
    await user.click(dialog().getByRole('checkbox', { name: /处置后连接日志/ }));
    await user.click(dialog().getByRole('button', { name: '核验处置效果' }));
    await waitFor(() => expect(mutation).toHaveBeenCalled());
    expect(mutation.mock.calls[0][0].params.data).toMatchObject({
      verdict: 'failed',
      execution_occurred: false,
      evidence_ids: ['e2'],
    });
  });
  it('starts investigation through the existing workflow and session', async () => {
    const user = userEvent.setup();
    await openCase();
    expect(screen.getByRole('button', { name: '交给 Agent 调查' })).toBeDisabled();
    await user.type(screen.getByLabelText('执行关联会话'), 'session-real');
    await user.click(screen.getByRole('button', { name: '交给 Agent 调查' }));
    await user.type(dialog().getByLabelText('本轮调查重点 *'), '核对主机来源和攻击链');
    await user.click(dialog().getByRole('button', { name: '交给 Agent 调查' }));
    expect(await screen.findByRole('link', { name: '查看调查执行' })).toHaveAttribute(
      'href',
      '/workflows/soc_case_investigation?tab=run&execId=execution-1',
    );
    const call = fetchMock.mock.calls.find(([url]) => url === '/api/workflow/soc_case_investigation/run');
    expect(JSON.parse(call![1].body)).toEqual({
      inputs: { case_id: 'case-1', question: '核对主机来源和攻击链' },
      sessionId: 'session-real',
    });
  });
  it.each(['failed', 'inconclusive'])(
    'records explicit confirmed execution with %s effect without claiming verification passed',
    async (verdict) => {
      detail.actions = [action({ execution_status: 'unknown' })];
      mutation.mockImplementation((request: CaseRequest) => {
        detail.case.version += 1;
        detail.actions[0] = {
          ...detail.actions[0],
          execution_status: 'succeeded',
          verification_status: request.params.data.verdict,
          verification_summary: request.params.data.summary,
          verification_evidence_ids: request.params.data.evidence_ids,
        };
        return success({ case: clone(detail.case), record: clone(detail.actions[0]) });
      });
      const user = userEvent.setup();
      await openCase();
      await user.click(screen.getByRole('tab', { name: '处置' }));
      await user.click(screen.getByRole('button', { name: '核验处置效果' }));
      expect(dialog().getByLabelText('核验结论 *')).toHaveValue('inconclusive');
      await user.selectOptions(dialog().getByLabelText('核验结论 *'), `${verdict}_executed`);
      await user.type(
        dialog().getByLabelText('核验记录 *'),
        '新设备审计确认动作已执行，处置效果尚未达到验收标准',
      );
      await user.click(dialog().getByRole('checkbox', { name: /处置后连接日志/ }));
      await user.click(dialog().getByRole('button', { name: '核验处置效果' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      expect(mutation.mock.calls[0][0].params.data).toMatchObject({
        action_id: 'action-1',
        verdict,
        execution_occurred: true,
        evidence_ids: ['e2'],
      });
      expect(screen.getByText('执行成功')).toBeInTheDocument();
      expect(screen.getByText(verdict === 'failed' ? '失败' : '尚无定论')).toBeInTheDocument();
      expect(screen.queryByText('核验通过')).not.toBeInTheDocument();
    },
  );
  it('does not infer execution from an inconclusive verification or from a changed selection', async () => {
    detail.actions = [action({ execution_status: 'running' })];
    const user = userEvent.setup();
    await openCase();
    await user.click(screen.getByRole('tab', { name: '处置' }));
    await user.click(screen.getByRole('button', { name: '核验处置效果' }));
    await user.selectOptions(dialog().getByLabelText('核验结论 *'), 'failed_executed');
    await user.selectOptions(dialog().getByLabelText('核验结论 *'), 'inconclusive');
    await user.type(dialog().getByLabelText('核验记录 *'), '执行事实仍无法确认');
    await user.click(dialog().getByRole('checkbox', { name: /处置后连接日志/ }));
    await user.click(dialog().getByRole('button', { name: '核验处置效果' }));
    await waitFor(() => expect(mutation).toHaveBeenCalled());
    expect(mutation.mock.calls[0][0].params.data.verdict).toBe('inconclusive');
    expect(mutation.mock.calls[0][0].params.data.execution_occurred).toBeUndefined();
  });
  it('keeps closed cases read-only and supports explicit reopening with a reason', async () => {
    detail.case.status = 'closed';
    const user = userEvent.setup();
    await openCase();
    expect(screen.getByRole('button', { name: '添加证据' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: '结案' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重新调查' }));
    await user.type(dialog().getByLabelText('重新调查的原因 *'), '发现新的相关证据');
    await user.click(dialog().getByRole('button', { name: '重新调查' }));
    await waitFor(() => expect(mutation).toHaveBeenCalled());
    expect(mutation.mock.calls[0][0].params).toMatchObject({
      operation: 'reopen',
      data: { reason: '发现新的相关证据' },
    });
  });
  it('edits review-stage metadata without sending an invalid or unchanged status transition', async () => {
    detail.case.status = 'review';
    const user = userEvent.setup();
    await openCase();
    await user.click(screen.getByRole('button', { name: '更新事件' }));
    expect(dialog().queryByLabelText('推进阶段 *')).not.toBeInTheDocument();
    await user.type(dialog().getByLabelText('负责人'), '调查负责人');
    await user.click(dialog().getByRole('button', { name: '更新事件' }));
    await waitFor(() => expect(mutation).toHaveBeenCalled());
    expect(mutation.mock.calls[0][0].params.data.assignee).toBe('调查负责人');
    expect(mutation.mock.calls[0][0].params.data.status).toBeUndefined();
  });
  it('continues improvement follow-up after closure while keeping investigation evidence isolated', async () => {
    detail.case.status = 'closed';
    detail.improvements = [
      {
        id: 'improvement-1',
        title: '修正规则',
        owner: '值班负责人',
        status: 'open',
        acceptance_criteria: '样本验证通过',
      },
    ];
    detail.evidence.push(
      {
        id: 'acceptance-1',
        title: '本项验收证据',
        source: '回放测试',
        content: '本改善项验证记录',
        purpose: 'improvement',
        improvement_id: 'improvement-1',
      },
      {
        id: 'acceptance-2',
        title: '其他项验收证据',
        source: '回放测试',
        content: '另一改善项记录',
        purpose: 'improvement',
        improvement_id: 'improvement-2',
      },
    );
    const user = userEvent.setup();
    await openCase();
    expect(screen.queryByText('本项验收证据')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '添加证据' })).toBeDisabled();
    await user.click(screen.getByRole('tab', { name: '复盘与改善' }));
    await user.click(screen.getByRole('button', { name: '补充验收证据' }));
    await user.type(dialog().getByLabelText('证据名称 *'), '补充回放结果');
    await user.type(dialog().getByLabelText('证据来源 *'), '测试回放');
    await user.type(dialog().getByLabelText('证据内容 *'), '验证边界样本');
    await user.click(dialog().getByRole('button', { name: '补充验收证据' }));
    await waitFor(() => expect(mutation).toHaveBeenCalled());
    expect(mutation.mock.calls[0][0].params).toMatchObject({
      operation: 'add_improvement_evidence',
      data: { improvement_id: 'improvement-1' },
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: '更新改善项' }));
    expect(dialog().getByRole('checkbox', { name: /本项验收证据/ })).toBeInTheDocument();
    expect(dialog().queryByRole('checkbox', { name: /其他项验收证据/ })).not.toBeInTheDocument();
  });
  it('advances independent byte-bounded record and timeline cursors without skipping or duplicating records', async () => {
    detail.next_offset = 2;
    detail.timeline_next_offset = 1;
    detail.counts = { evidence: 3, records: 3, timeline: 2 };
    detail.timeline = [{ id: 'event-new', operation: 'add_evidence', version: 2 }];
    const originalFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      const params = options?.body ? JSON.parse(String(options.body)).params : null;
      if (params?.operation === 'get' && params.data.offset === 2) {
        expect(params.data.timeline_offset).toBe(1);
        return Promise.resolve(
          success({
            ...clone(detail),
            evidence: [{ id: 'e3', title: '历史日志', source: '归档', content: '完整保留的历史证据' }],
            timeline: [{ id: 'event-old', operation: 'create', version: 1 }],
            next_offset: null,
            timeline_next_offset: null,
            offset: 2,
          }),
        );
      }
      return originalFetch(url, options);
    });
    const user = userEvent.setup();
    await openCase();
    await user.click(screen.getByRole('button', { name: '加载更多历史记录' }));
    expect(await screen.findByText('历史日志')).toBeInTheDocument();
    expect(screen.getByText('初始网络日志')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '加载更多历史记录' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: '事件时间线' }));
    expect(screen.getByText('创建事件')).toBeInTheDocument();
    expect(screen.getByText('添加证据')).toBeInTheDocument();
  });
  it('retains the existing improvement result and evidence when changing its status', async () => {
    detail.case.status = 'closed';
    detail.improvements = [
      {
        id: 'improvement-1',
        title: '补齐资产记录',
        owner: '值班负责人',
        status: 'open',
        acceptance_criteria: '记录齐全',
        result: '已有第一批核对结果',
        evidence_ids: ['e2', 'earlier-proof'],
      },
    ];
    const user = userEvent.setup();
    await openCase();
    await user.click(screen.getByRole('tab', { name: '复盘与改善' }));
    await user.click(screen.getByRole('button', { name: '更新改善项' }));
    expect(dialog().getByLabelText('完成结果或延期原因')).toHaveValue('已有第一批核对结果');
    expect(dialog().getByRole('checkbox', { name: /处置后连接日志/ })).toBeChecked();
    await user.selectOptions(dialog().getByLabelText('进度 *'), 'in_progress');
    await user.click(dialog().getByRole('button', { name: '更新改善项' }));
    await waitFor(() => expect(mutation).toHaveBeenCalled());
    expect(mutation.mock.calls[0][0].params.data).toEqual({
      improvement_id: 'improvement-1',
      status: 'in_progress',
      result: '已有第一批核对结果',
      evidence_ids: ['e2', 'earlier-proof'],
    });
  });
  it('shows approval rationale, separate verification evidence and redacted historical audit details', async () => {
    detail.actions = [
      action({
        execution_status: 'succeeded',
        verification_status: 'verified',
        approval_reason: '已核对资产归属与恢复方案',
        decided_by: 'reviewer-1',
        decided_at: '2026-10-01T02:00:00Z',
        verification_summary: '处置后日志确认连接已停止',
        verification_evidence_ids: ['e2'],
        verified_by: 'reviewer-2',
        verified_at: '2026-10-01T03:00:00Z',
      }),
    ];
    detail.timeline = [
      {
        id: 'event-1',
        operation: 'decide_action',
        version: 5,
        actor_user_id: 'reviewer-1',
        actor_kind: 'human',
        details: {
          record: { approval_reason: '历史审批意见', evidence_ids: ['e1'] },
          claim_token: 'test-claim-do-not-display',
          receipt: { authorization: 'test-auth-do-not-display' },
        },
      },
    ];
    const user = userEvent.setup();
    await openCase();
    await user.click(screen.getByRole('tab', { name: '处置' }));
    const approval = within(screen.getByRole('region', { name: '审批记录' }));
    expect(approval.getByText('已核对资产归属与恢复方案')).toBeInTheDocument();
    expect(approval.getByText(/reviewer-1/)).toBeInTheDocument();
    const verification = within(screen.getByRole('region', { name: '效果核验记录' }));
    expect(verification.getByText('处置后日志确认连接已停止')).toBeInTheDocument();
    expect(verification.getByText('关联证据：处置后连接日志')).toBeInTheDocument();
    expect(verification.queryByText(/初始网络日志/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: '事件时间线' }));
    await user.click(screen.getByText('查看审计详情'));
    const audit = screen.getByText(/历史审批意见/);
    expect(audit).toBeVisible();
    expect(audit).toHaveTextContent('[REDACTED]');
    expect(audit).not.toHaveTextContent('test-claim-do-not-display');
    expect(audit).not.toHaveTextContent('test-auth-do-not-display');
  });
  it('uses independent opaque cursors past the legacy offset limit and skips a completed stream', async () => {
    detail.next_cursor = 'records-page-2';
    detail.timeline_next_cursor = 'timeline-page-2';
    detail.next_offset = null;
    detail.timeline_next_offset = null;
    detail.counts = { records: 10001, timeline: 10002 };
    const reads: Record<string, unknown>[] = [];
    const originalFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      const params = options?.body ? JSON.parse(String(options.body)).params : null;
      if (params?.operation === 'get') {
        reads.push(params.data);
        if (params.data.cursor === 'records-page-2')
          return Promise.resolve(
            success({
              ...clone(detail),
              evidence: [{ id: 'e3', title: '游标历史证据', source: '归档', content: '完整记录' }],
              timeline: [{ id: 'event-middle', operation: 'add_evidence', version: 2 }],
              next_cursor: null,
              timeline_next_cursor: 'timeline-page-3',
            }),
          );
        if (params.data.cursor === 'end')
          return Promise.resolve(
            success({
              ...clone(detail),
              evidence: [],
              timeline: [{ id: 'event-last', operation: 'create', version: 1 }],
              next_cursor: null,
              timeline_next_cursor: null,
            }),
          );
      }
      return originalFetch(url, options);
    });
    const user = userEvent.setup();
    await openCase();
    await user.click(screen.getByRole('button', { name: '加载更多历史记录' }));
    expect(await screen.findByText('游标历史证据')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '加载更多历史记录' }));
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: '加载更多历史记录' })).not.toBeInTheDocument(),
    );
    expect(reads).toEqual([
      { limit: 100, offset: 0, timeline_offset: 0 },
      { limit: 100, cursor: 'records-page-2', timeline_cursor: 'timeline-page-2' },
      { limit: 100, cursor: 'end', timeline_cursor: 'timeline-page-3' },
    ]);
    expect(screen.getByText('初始网络日志')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: '事件时间线' }));
    expect(screen.getByText('创建事件')).toBeInTheDocument();
    expect(screen.getByText('添加证据')).toBeInTheDocument();
  });
  it('refreshes the first page once after a stale cursor without mixing case versions', async () => {
    detail.next_cursor = 'stale-records';
    detail.timeline_next_cursor = null;
    const reads: Record<string, unknown>[] = [];
    const originalFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      const params = options?.body ? JSON.parse(String(options.body)).params : null;
      if (params?.operation === 'get') {
        reads.push(params.data);
        if (params.data.cursor) {
          detail.case.version = 8;
          detail.evidence = [{ id: 'fresh', title: '最新版本证据', source: '日志', content: '新记录' }];
          detail.next_cursor = null;
          return Promise.resolve(
            response({
              success: false,
              error: 'cursor version changed',
              metadata: { code: 'cursor_stale', status_code: 409 },
            }),
          );
        }
      }
      return originalFetch(url, options);
    });
    const user = userEvent.setup();
    await openCase();
    await user.click(screen.getByRole('button', { name: '加载更多历史记录' }));
    expect(await screen.findByText('最新版本证据')).toBeInTheDocument();
    expect(screen.getByText('版本 8')).toBeInTheDocument();
    expect(screen.queryByText('初始网络日志')).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('事件已更新');
    expect(reads).toEqual([
      { limit: 100, offset: 0, timeline_offset: 0 },
      { limit: 100, cursor: 'stale-records', timeline_cursor: 'end' },
      { limit: 100, offset: 0, timeline_offset: 0 },
    ]);
  });
  it('uses list cursor history for forward and back navigation before restarting a search', async () => {
    const listReads: Record<string, unknown>[] = [];
    const originalFetch = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url: string, options?: RequestInit) => {
      const params = options?.body ? JSON.parse(String(options.body)).params : null;
      if (params?.operation === 'list') {
        listReads.push(params.data);
        return Promise.resolve(
          success({
            cases: [clone(detail.case)],
            total: 10001,
            next_offset: null,
            next_cursor: params.data.cursor ? null : 'list-page-2',
          }),
        );
      }
      return originalFetch(url, options);
    });
    const user = userEvent.setup();
    await openCase();
    await user.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled());
    await user.click(screen.getByRole('button', { name: '上一页' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled());
    await user.type(screen.getByLabelText('搜索事件'), '终端{Enter}');
    await waitFor(() => expect(listReads).toHaveLength(4));
    expect(listReads).toEqual([
      { limit: 30, offset: 0, query: '' },
      { limit: 30, cursor: 'list-page-2', query: '' },
      { limit: 30, offset: 0, query: '' },
      { limit: 30, offset: 0, query: '终端' },
    ]);
  });
  it('plans an available device action with linked evidence and immutable JSON parameters', async () => {
    const user = userEvent.setup();
    await openCase();
    await user.click(screen.getByRole('tab', { name: '处置' }));
    await user.click(screen.getByRole('button', { name: '制定处置计划' }));
    await waitFor(() => expect(dialog().getByLabelText('设备动作 *')).toHaveValue('test_isolate'));
    await user.type(dialog().getByLabelText('处置动作 *'), '隔离测试主机');
    await user.type(dialog().getByLabelText('处置对象 *'), 'test-host');
    await user.type(dialog().getByLabelText('处置依据 *'), '证据证实恶意连接');
    await user.click(dialog().getByRole('checkbox', { name: /初始网络日志/ }));
    await user.clear(dialog().getByLabelText('动作参数（JSON） *'));
    await user.paste('{"host":"test-host"}');
    await user.type(dialog().getByLabelText('恢复方案 *'), '人工确认后解除隔离');
    await user.type(dialog().getByLabelText('效果核验方法 *'), '重新采集连接日志');
    await user.click(dialog().getByRole('button', { name: '制定处置计划' }));
    await waitFor(() => expect(mutation).toHaveBeenCalled());
    expect(mutation.mock.calls[0][0].params.data).toMatchObject({
      tool_name: 'test_isolate',
      parameters: { host: 'test-host' },
      evidence_ids: ['e1'],
    });
    expect(mutation.mock.calls[0][0].session_id).toBeUndefined();
  });
  it('explains unavailable component access without showing invented incidents', async () => {
    fetchMock.mockResolvedValue(response({ detail: 'Tool not found: soc_case_manage' }, 404));
    render(<Page />);
    expect(await screen.findByRole('alert')).toHaveTextContent('事件或事件响应组件不可用');
    expect(screen.queryByRole('heading', { name: '可疑终端连接' })).not.toBeInTheDocument();
  });
});
