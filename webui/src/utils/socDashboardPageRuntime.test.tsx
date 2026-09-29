import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Page from '../../../.flocks/flockshub/plugins/webuis/soc_ui/soc_dashboard/src/Page';

const pageGetMock = vi.fn();
const originalDocumentHidden = Object.getOwnPropertyDescriptor(Document.prototype, 'hidden');

function installContractSdk() {
  (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__ = {
    React,
    api: {
      page: {
        get: pageGetMock,
      },
    },
  };
}

function setDocumentHidden(value: boolean) {
  Object.defineProperty(document, 'hidden', {
    configurable: true,
    value,
  });
}

function workflowEvent(id: string, status = 'running', extra: Record<string, any> = {}) {
  return {
    eventId: `workflow-execution:${id}`, triggerSource: 'workflow_execution',
    workflowId: 'stream_alert_denoise', stage: 'denoise', status,
    occurredAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
    alert: { id: 'same-alert', threatName: '边界扫描', sourceType: 'ndr',
      srcIp: '192.0.2.1', dstIp: '198.51.100.2' },
    result: { rawCount: 5, metricsAvailable: true }, ...extra,
  };
}

function mockActivity(read: () => any) {
  const fallback = pageGetMock.getMockImplementation()!;
  pageGetMock.mockImplementation((path: string, ...args: any[]) => path === '/activity'
    ? Promise.resolve().then(() => ({ data: { cursor: 'test-cursor', events: [], recentEvents: [],
      workflowSnapshotComplete: true, ...read() } }))
    : fallback(path, ...args));
}

async function pollActivity() {
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
}

describe('SOC dashboard contract page runtime', () => {
  beforeEach(() => {
    installContractSdk();
    setDocumentHidden(false);
    window.localStorage.clear();
    window.history.replaceState(null, '', '/');
    window.sessionStorage.clear();
    pageGetMock.mockImplementation((path: string) => {
      if (path === '/stats') {
        return Promise.resolve({ data: {} });
      }
      if (path === '/activity') {
        return Promise.resolve({
          data: {
            cursor: 'eyJsYXN0Um93SWQiOjAsImxhc3RBY3Rpdml0eUlkIjowfQ',
            events: [],
            recentEvents: [],
            workflowEvents: [],
            batch: {},
            workflowStats: { callCount: 0, latestStartedAt: 0 },
            tokenUsage: { totalTokens: 0, todayTokens: 0, todayRequests: 0, dailySeries: [] },
          },
        });
      }
      if (path === '/task-center') {
        return Promise.resolve({ data: { scheduledTasks: [], workflows: [] } });
      }
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    delete (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__;
    if (originalDocumentHidden) {
      Object.defineProperty(document, 'hidden', originalDocumentHidden);
    } else {
      delete (document as any).hidden;
    }
    pageGetMock.mockReset();
  });

  it('loads SOC stats and activity without polling the general task center', async () => {
    render(<Page />);

    await waitFor(() => {
      expect(pageGetMock).toHaveBeenCalledWith('/stats', expect.anything());
      expect(pageGetMock).toHaveBeenCalledWith('/activity', expect.anything());
      expect(pageGetMock).not.toHaveBeenCalledWith('/task-center', expect.anything());
    });

    expect(screen.getByText('Flocks AI 智能告警态势中心')).toBeInTheDocument();
  });

  it('pauses SOC activity polling while the page is hidden', async () => {
    setDocumentHidden(true);

    render(<Page />);

    await waitFor(() => {
      expect(pageGetMock).toHaveBeenCalledWith('/stats', expect.anything());
    });

    expect(pageGetMock).not.toHaveBeenCalledWith('/task-center', expect.anything());
  });

  it('labels denoise sources, triage context, and linked lane events', async () => {
    const occurredAt = new Date().toISOString();
    pageGetMock.mockImplementation((path: string) => {
      if (path === '/stats') {
        return Promise.resolve({
          data: {
            triage: {
              totalRecords: 12,
              newTriaged: 5,
              cacheHit: 4,
              followersReused: 2,
            },
          },
        });
      }
      if (path === '/activity') {
        return Promise.resolve({
          data: {
            cursor: 'eyJsYXN0Um93SWQiOjAsImxhc3RBY3Rpdml0eUlkIjowfQ',
            events: [],
            recentEvents: [],
            workflowEvents: [
              {
                eventId: 'workflow-execution:exec-denoise-1',
                stage: 'denoise',
                status: 'running',
                occurredAt,
                triggerSource: 'workflow_execution',
                workflowId: 'stream_alert_denoise',
                sessionId: '',
                messageId: '',
                alert: {
                  id: 'alert-1',
                  sourceType: 'workflow.db',
                  threatName: '远程命令执行',
                  srcIp: '10.0.0.1',
                  dstIp: '10.0.0.2',
                },
                result: {
                  dedupKey: 'dedup-1',
                  isDuplicate: false,
                },
              },
              {
                eventId: 'workflow-execution:exec-triage-1',
                stage: 'triage',
                status: 'running',
                occurredAt,
                triggerSource: 'workflow_execution',
                workflowId: 'stream_alert_triage',
                sessionId: '',
                messageId: '',
                alert: {
                  id: 'alert-1',
                  sourceType: 'workflow.db',
                  threatName: '远程命令执行',
                  srcIp: '10.0.0.1',
                  dstIp: '10.0.0.2',
                },
                result: {
                  triageSource: 'triaged',
                  riskLevel: 'high',
                  verdictLabel: '攻击行为',
                },
              },
            ],
            batch: {},
            workflowStats: { callCount: 0, latestStartedAt: 0 },
            tokenUsage: { totalTokens: 0, todayTokens: 0, todayRequests: 0, dailySeries: [] },
          },
        });
      }
      if (path === '/task-center') {
        return Promise.resolve({ data: { scheduledTasks: [], workflows: [] } });
      }
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    render(<Page />);

    expect(await screen.findByText('工作流执行')).toBeInTheDocument();
    expect(await screen.findByText('窗口研判 12 条 · AI新研判 5 条 · 复用 6 条')).toBeInTheDocument();
    expect(await screen.findByText('已流转至研判')).toBeInTheDocument();
  });

  it('shows only the two SOC workflows and omits the general task center entry', async () => {
    mockActivity(() => ({ workflowEvents: [workflowEvent('soc'), workflowEvent('other', 'running', {
      workflowId: 'unrelated-agent-task', alert: { threatName: '不应展示的其他任务' },
    })] }));
    const { container } = render(<Page />);
    await waitFor(() => expect(container.querySelectorAll('.event-rail-item')).toHaveLength(1));
    expect(screen.queryByRole('tab', { name: '任务中心' })).not.toBeInTheDocument();
    const rail = container.querySelector('.command-event-rail') as HTMLElement;
    expect(within(rail).queryByText('不应展示的其他任务')).not.toBeInTheDocument();
    expect(pageGetMock).not.toHaveBeenCalledWith('/task-center', expect.anything());
    expect(within(rail).getByText('SOC 工作套件')).toBeInTheDocument();
  });

  it('shows only actual running/queued tasks, not completed playback or empty batches', async () => {
    mockActivity(() => ({ workflowEvents: [
      workflowEvent('done', 'completed'), workflowEvent('failed', 'failed'),
      workflowEvent('empty', 'running', { alert: { threatName: '降噪批次 · 原始 0 条' }, result: { rawCount: 0 } }),
      workflowEvent('active'), workflowEvent('queued', 'queued'),
    ] }));
    const { container } = render(<Page />);
    await waitFor(() => expect(container.querySelectorAll('.event-rail-item')).toHaveLength(2));
    const rail = container.querySelector('.event-rail-list') as HTMLElement;
    expect(within(rail).getByText('处理中')).toBeInTheDocument();
    expect(within(rail).getByText('排队中')).toBeInTheDocument();
    expect(within(rail).queryByText(/原始 0 条/)).not.toBeInTheDocument();
    expect(within(rail).queryByText(/降噪处理完成/)).not.toBeInTheDocument();
    expect(within(rail).queryByText(/s \/ .*s/)).not.toBeInTheDocument();
  });

  it('keeps four identity cards stable when metrics and partial fields arrive', async () => {
    vi.useFakeTimers();
    let event = workflowEvent('active');
    mockActivity(() => ({ workflowEvents: [event] }));
    const { container } = render(<Page />);
    await act(async () => {});
    const cards = container.querySelector('.ai-evidence-field') as HTMLElement;
    expect(within(cards).getByText('告警名称')).toBeInTheDocument();
    expect(within(cards).getByText('来源类型')).toBeInTheDocument();
    const original = cards.textContent;
    event = { ...event, alert: { id: 'same-alert', threatName: '降噪批次', sourceType: 'unknown', srcIp: '', dstIp: '' },
      result: { rawCount: 5, metricsAvailable: false } };
    await pollActivity();
    expect(cards.textContent).toBe(original);
    expect(cards.querySelectorAll('.ai-evidence-card')).toHaveLength(4);
    expect(within(cards).queryByText('原始告警')).not.toBeInTheDocument();
  });

  it('refreshes right-hand task metadata even when ID and status are unchanged', async () => {
    vi.useFakeTimers();
    let event = workflowEvent('active', 'running', { alert: { threatName: '降噪批次 · 数量未提供' } });
    mockActivity(() => ({ workflowEvents: [event] }));
    const { container } = render(<Page />);
    await act(async () => {});
    event = workflowEvent('active');
    await pollActivity();
    const rail = container.querySelector('.event-rail-list') as HTMLElement;
    expect(within(rail).getByText('边界扫描')).toBeInTheDocument();
    expect(rail.querySelector('.ai-record-title')).toHaveTextContent('边界扫描');
  });

  it('keeps execution records without placeholder cards until a valid step arrives', async () => {
    vi.useFakeTimers();
    let event = workflowEvent('awaiting-node', 'running', { live: { nodeId: 'not-a-soc-step' } });
    mockActivity(() => ({ workflowEvents: [event] }));
    const { container } = render(<Page />);
    await act(async () => {});
    expect(container.querySelectorAll('.ai-step-card')).toHaveLength(0);
    expect(container.querySelectorAll('.ai-card-slot')).toHaveLength(0);
    expect(container.querySelector('.ai-execution-record')).toHaveTextContent('边界扫描');
    expect(container.querySelector('.ai-record-state')).toHaveTextContent('处理中');
    expect(container).not.toHaveTextContent('获取当前步骤');
    event = { ...event, live: { nodeId: 'normalize' } };
    await pollActivity();
    expect(container.querySelectorAll('.ai-step-card')).toHaveLength(1);
    expect(container.querySelector('.ai-step-card')).toHaveAttribute('data-step', 'normalize');
    expect(container.querySelectorAll('.ai-execution-record')).toHaveLength(1);
  });

  it('does not replay a terminal workflow or synthesize tasks from counter increases', async () => {
    vi.useFakeTimers();
    const running = workflowEvent('active');
    let event = running;
    let callCount = 1;
    mockActivity(() => ({ workflowEvents: [event], workflowStats: { callCount } }));
    const { container } = render(<Page />);
    await act(async () => {});
    event = { ...running, status: 'completed' };
    callCount += 20;
    await pollActivity();
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(container.querySelectorAll('.event-rail-item')).toHaveLength(0);
    expect(screen.getByText('最近告警记录')).toBeInTheDocument();
    event = running; // out-of-order running response must not resurrect it
    await pollActivity();
    expect(container.querySelectorAll('.event-rail-item')).toHaveLength(0);
    expect(container.querySelectorAll('.ai-core.core-processing')).toHaveLength(0);
  });

  it('preserves data on failed/incomplete polls and removes absent tasks only with a complete snapshot', async () => {
    vi.useFakeTimers();
    let data: any = { workflowEvents: [workflowEvent('active')] };
    mockActivity(() => data);
    const { container } = render(<Page />);
    await act(async () => {});
    data = { workflowEvents: [], workflowSnapshotComplete: false };
    await pollActivity();
    expect(container.querySelectorAll('.event-rail-item')).toHaveLength(1);
    data = { error: 'offline' };
    await pollActivity();
    expect(container.querySelectorAll('.event-rail-item')).toHaveLength(1);
    expect(screen.getAllByText('连接恢复中')[0]).toBeInTheDocument();
    data = { workflowEvents: [], workflowSnapshotComplete: true };
    await act(async () => { await vi.advanceTimersByTimeAsync(6500); });
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(container.querySelectorAll('.event-rail-item')).toHaveLength(0);
  });

  it('labels a historical alert fallback without assigning it to an unrelated active batch', async () => {
    mockActivity(() => ({ workflowEvents: [workflowEvent('batch', 'running', {
      alert: { threatName: '降噪批次 · 数量未提供' }, result: { rawCount: null },
    })], recentEvents: [{ ...workflowEvent('history', 'completed'), triggerSource: 'soc_record' }] }));
    const { container } = render(<Page />);
    await waitFor(() => expect(screen.getByText('最近告警记录')).toBeInTheDocument());
    const rail = container.querySelector('.event-rail-list') as HTMLElement;
    expect(within(rail).getByText('降噪批次 · 数量未提供')).toBeInTheDocument();
    expect(within(rail).queryByText('边界扫描')).not.toBeInTheDocument();
    const cards = container.querySelector('.ai-evidence-field') as HTMLElement;
    expect(within(cards).getByText('边界扫描')).toBeInTheDocument();
  });

  it('keeps the visible queue bounded under repeated polling and clears poll timers on unmount', async () => {
    vi.useFakeTimers();
    let batch = 0;
    mockActivity(() => ({ workflowEvents: Array.from({ length: 20 }, (_, index) =>
      workflowEvent(`${batch}-${index}`)) }));
    const { container, unmount } = render(<Page />);
    await act(async () => {});
    for (batch = 1; batch <= 20; batch += 1) await pollActivity();
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(container.querySelectorAll('.event-rail-item').length).toBeLessThanOrEqual(10);
    unmount();
    const calls = pageGetMock.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(pageGetMock.mock.calls.length).toBe(calls);
  });

  it('does not mix two different alerts from the same batch execution', async () => {
    vi.useFakeTimers();
    let event = workflowEvent('batch');
    mockActivity(() => ({ workflowEvents: [event] }));
    const { container } = render(<Page />);
    await act(async () => {});
    event = workflowEvent('batch', 'running', { alert: {
      id: 'different-alert', threatName: '另一条告警', srcIp: '203.0.113.9',
    } });
    await pollActivity();
    const cards = container.querySelector('.ai-evidence-field') as HTMLElement;
    expect(within(cards).getByText('另一条告警')).toBeInTheDocument();
    expect(within(cards).queryByText('198.51.100.2')).not.toBeInTheDocument();
    expect(within(cards).getAllByText('未提供')).toHaveLength(2);
  });

  it('promotes real running work ahead of an earlier queued task', async () => {
    vi.useFakeTimers();
    let events = [workflowEvent('queued', 'queued', { alert: { threatName: '等待中的批次' } })];
    mockActivity(() => ({ workflowEvents: events }));
    const { container } = render(<Page />);
    await act(async () => {});
    events = [...events, workflowEvent('running')];
    await pollActivity();
    const cards = container.querySelector('.ai-evidence-field') as HTMLElement;
    expect(within(cards).getByText('边界扫描')).toBeInTheDocument();
    expect(container.querySelectorAll('.event-rail-item')).toHaveLength(2);
    expect(container.querySelector('.ai-core')).toHaveClass('core-processing');
  });

  it.each(['', 'batch'])('replaces a whole preview when the alert ID is missing or a legacy execution fallback (%s)', async (id) => {
    vi.useFakeTimers();
    let event = workflowEvent('batch', 'running', { alert: {
      id, threatName: '第一条告警', srcIp: '192.0.2.1', dstIp: '198.51.100.2', sourceType: 'ndr',
    } });
    mockActivity(() => ({ workflowEvents: [event] }));
    const { container } = render(<Page />);
    await act(async () => {});
    event = { ...event, alert: { id, threatName: '第二条告警', srcIp: '203.0.113.9' } };
    await pollActivity();
    const cards = container.querySelector('.ai-evidence-field') as HTMLElement;
    expect(within(cards).getByText('第二条告警')).toBeInTheDocument();
    expect(within(cards).getAllByText('未提供')).toHaveLength(2);
    expect(within(cards).queryByText('198.51.100.2')).not.toBeInTheDocument();
    const rail = container.querySelector('.event-rail-list') as HTMLElement;
    expect(within(rail).queryByText(/198\.51\.100\.2/)).not.toBeInTheDocument();
  });

  it('releases an unconfirmed current task when busy snapshots keep omitting its final state', async () => {
    vi.useFakeTimers();
    let events = [workflowEvent('old', 'running', { alert: { id: 'old-alert', threatName: '旧任务' } })];
    mockActivity(() => ({ workflowEvents: events, workflowSnapshotComplete: false }));
    const { container } = render(<Page />);
    await act(async () => {});
    events = Array.from({ length: 10 }, (_, index) => workflowEvent(`new-${index}`, 'running', {
      alert: { id: `new-alert-${index}`, threatName: `新任务-${index}` },
    }));
    for (let i = 0; i < 12; i += 1) await pollActivity();
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    const cards = container.querySelector('.ai-evidence-field') as HTMLElement;
    expect(within(cards).queryByText('旧任务')).not.toBeInTheDocument();
    expect(within(cards).getByText(/新任务-/)).toBeInTheDocument();
    expect(container.querySelectorAll('.event-rail-item.state-processing')).toHaveLength(10);
  });

  it('does not expire a long-running task whose unchanged status is still confirmed by polls', async () => {
    vi.useFakeTimers();
    const event = workflowEvent('long-running');
    mockActivity(() => ({ workflowEvents: [event], workflowSnapshotComplete: false }));
    const { container } = render(<Page />);
    await act(async () => {});
    for (let i = 0; i < 25; i += 1) await pollActivity();
    expect(container.querySelector('.event-rail-item')).toHaveClass('state-processing');
    expect(container.querySelector('.ai-core')).toHaveClass('core-processing');
    expect(screen.queryByText('状态待确认')).not.toBeInTheDocument();
  });

  it('marks tasks unconfirmed even during a hung request and restores them on a fresh confirmation', async () => {
    vi.useFakeTimers();
    const event = workflowEvent('active');
    mockActivity(() => ({ workflowEvents: [event] }));
    const { container } = render(<Page />);
    await act(async () => {});
    const fallback = pageGetMock.getMockImplementation()!;
    let resolveActivity: (value: any) => void = () => {};
    pageGetMock.mockImplementation((path: string, ...args: any[]) => path === '/activity'
      ? new Promise((resolve) => { resolveActivity = resolve; }) : fallback(path, ...args));
    await act(async () => { await vi.advanceTimersByTimeAsync(33000); });
    expect(container.querySelector('.event-rail-item')).toHaveClass('state-unconfirmed');
    expect(container.querySelector('.ai-core')).not.toHaveClass('core-processing');
    expect(within(container.querySelector('.event-rail-list') as HTMLElement).getByText('状态待确认')).toBeInTheDocument();
    await act(async () => { resolveActivity({ data: { workflowEvents: [event], workflowSnapshotComplete: true } }); });
    expect(container.querySelector('.event-rail-item')).toHaveClass('state-processing');
    expect(container.querySelector('.ai-core')).toHaveClass('core-processing');
  });

  it('removes unconfirmed tasks when an authoritative snapshot confirms their absence', async () => {
    vi.useFakeTimers();
    let data: any = { workflowEvents: [workflowEvent('old')] };
    mockActivity(() => data);
    const { container } = render(<Page />);
    await act(async () => {});
    data = { workflowEvents: [], workflowSnapshotComplete: false };
    await act(async () => { await vi.advanceTimersByTimeAsync(33000); });
    expect(container.querySelector('.event-rail-item')).toHaveClass('state-unconfirmed');
    data = { workflowEvents: [], workflowSnapshotComplete: true };
    await pollActivity();
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(container.querySelectorAll('.event-rail-item')).toHaveLength(0);
  });

  it('hides unfinished tasks older than 30 minutes, without deleting completed observations', async () => {
    const old = new Date(Date.now() - 31 * 60 * 1000).toISOString();
    mockActivity(() => ({ workflowEvents: [
      ...['running', 'queued', 'pending'].map((status) => workflowEvent(`old-${status}`, status, { occurredAt: old })),
      workflowEvent('current', 'running', { live: { nodeId: 'normalize' } }),
      workflowEvent('finished', 'completed', { occurredAt: old, live: { metrics: { rawCount: 100, duplicateCount: 60, uniqueCount: 40 } } }),
    ] }));
    const { container } = render(<Page />);
    await waitFor(() => expect(container.querySelectorAll('.event-rail-item')).toHaveLength(1));
    await waitFor(() => expect(container.querySelector('.ai-step-count b')).toHaveTextContent('5')); // One batch, not summed snapshots.
    expect(container.querySelectorAll('.ai-recent-record')).toHaveLength(1);
    expect(container.querySelector('.ai-recent-record')).toHaveTextContent('已完成');
  });

  it('does not renew the 30-minute display deadline on poll confirmation', async () => {
    vi.useFakeTimers();
    const started = new Date(Date.now() - 30 * 60 * 1000 + 2000).toISOString();
    mockActivity(() => ({ workflowEvents: [workflowEvent('deadline', 'running', { occurredAt: started })] }));
    const { container } = render(<Page />);
    await act(async () => {});
    expect(container.querySelectorAll('.event-rail-item')).toHaveLength(1);
    await pollActivity();
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(container.querySelectorAll('.event-rail-item')).toHaveLength(0);
    expect(container.querySelector('.event-rail-head b')).toBeNull();
    expect(container.querySelector('.ai-board-ready')).toHaveTextContent('实时');
  });

  it('removes tasks after 30 minutes even when the next poll never returns', async () => {
    vi.useFakeTimers();
    const started = new Date(Date.now() - 30 * 60 * 1000 + 4000).toISOString();
    mockActivity(() => ({ workflowEvents: [workflowEvent('deadline', 'running', { occurredAt: started })] }));
    const { container } = render(<Page />);
    await act(async () => {});
    const fallback = pageGetMock.getMockImplementation()!;
    pageGetMock.mockImplementation((path: string, ...args: any[]) => path === '/activity'
      ? new Promise(() => {}) : fallback(path, ...args));
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(container.querySelectorAll('.event-rail-item')).toHaveLength(0);
  });

  it('shows parallel cards for different batches at the same step plus triage', async () => {
    mockActivity(() => ({ workflowEvents: [
      workflowEvent('denoise-a', 'running', { live: { nodeId: 'normalize', metrics: { rawCount: 20 } } }),
      workflowEvent('denoise-b', 'running', { live: { nodeId: 'normalize', metrics: { rawCount: 50 } } }),
      workflowEvent('triage-a', 'running', { stage: 'triage', workflowId: 'stream_alert_triage', live: {
        nodeId: 'concurrent_triage', metrics: { inputCount: 100, workUnitCount: 8, completedCount: 5 } } }),
    ] }));
    const { container } = render(<Page />);
    await waitFor(() => expect(container.querySelectorAll('.ai-step-card')).toHaveLength(3));
    expect(container.querySelectorAll('.ai-step-card[data-step="normalize"]')).toHaveLength(2);
    expect(container.querySelector('.ai-step-card.kind-triage')).toHaveTextContent('并发研判');
    expect(container.querySelector('.ai-step-card.kind-triage')).toHaveTextContent('100');
    expect(container.querySelector('.ai-step-card.kind-triage')).not.toHaveTextContent('5%');
    expect(container.querySelectorAll('.ai-pipeline-track')).toHaveLength(0);
    expect(container.querySelector('details.ai-execution-details')).not.toHaveAttribute('open');
  });

  it('updates the current card without duplicating the batch and transitions to the next step', async () => {
    vi.useFakeTimers();
    const original = workflowEvent('live-batch', 'running', { live: {
      nodeId: 'normalize', metrics: { rawCount: 20 },
    } });
    let event = original;
    mockActivity(() => ({ workflowEvents: [event], recentEvents: [event] }));
    const { container } = render(<Page />);
    await act(async () => {});
    expect(container.querySelector('.ai-step-card')).toHaveTextContent('标准化');
    event = { ...original, live: { nodeId: 'dedup_and_write', metrics: {
      rawCount: 20, afterFilterCount: 18, duplicateCount: 12, uniqueCount: 6,
    } } };
    await pollActivity();
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(container.querySelectorAll('.ai-step-card')).toHaveLength(1);
    const card = container.querySelector('.ai-step-card')!;
    expect(card).toHaveTextContent('去重入库');
    expect(card).toHaveTextContent('去重率 66.7%');
    expect(card.querySelector('.ai-step-count b')).toHaveTextContent('20');
  });

  it('renders measured step durations, sweeps each card, then removes it before the next', async () => {
    vi.useFakeTimers();
    const event = workflowEvent('quick-done', 'completed', { result: { rawCount: 120, durationMs: 230 }, live: {
      nodeId: 'normalize', phase: 'success', metrics: { rawCount: 120 },
      stepDurationsMs: { receive_alert: 20, normalize: 50 },
    } });
    mockActivity(() => ({ workflowEvents: [event] }));
    const { container } = render(<Page />);
    await act(async () => {});
    expect(container.querySelector('.ai-step-card')).toHaveTextContent('接收');
    expect(container.querySelector('.ai-step-card')).toHaveTextContent('本步 0.02 秒');
    expect(container.querySelector('.ai-step-card')).toHaveTextContent('已完成步骤回放');
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(container.querySelectorAll('.ai-step-card')).toHaveLength(1);
    expect(container.querySelector('.ai-step-card')).toHaveTextContent('标准化');
    expect(container.querySelector('.ai-step-card')).toHaveTextContent('本步 0.05 秒');
    await act(async () => { await vi.advanceTimersByTimeAsync(2400); });
    expect(container.querySelectorAll('.ai-step-card')).toHaveLength(0);
    expect(container.querySelector('.ai-stream-resting')).toHaveTextContent('最近一批已完成 · 0.23 秒');
    await pollActivity();
    expect(container.querySelectorAll('.ai-step-card')).toHaveLength(0);
  });

  it('never replaces missing step durations with total runtime, or invents unobserved steps', async () => {
    mockActivity(() => ({ workflowEvents: [workflowEvent('done-no-steps', 'completed', {
      result: { rawCount: 12, durationMs: 450 }, live: { nodeId: 'dedup_and_write', metrics: { rawCount: 12 } },
    })] }));
    const { container } = render(<Page />);
    await waitFor(() => expect(container.querySelector('.ai-stream-resting')).toHaveTextContent('最近一批已完成'));
    expect(container.querySelectorAll('.ai-step-card')).toHaveLength(0);
    expect(container.querySelector('.ai-task-board-scroll')).not.toHaveTextContent('本步 0.45 秒');
  });

  it.each(['success', 'canceled', 'timeout'])('stops cards when offline or execution is saving a %s result', async (phase) => {
    vi.useFakeTimers();
    let data: any = { workflowEvents: [workflowEvent('live', 'running', {
      live: { nodeId: 'normalize', metrics: {} }, result: { rawCount: null },
    })] };
    mockActivity(() => data);
    const { container } = render(<Page />);
    await act(async () => {});
    expect(container.querySelector('.ai-step-card')).toHaveClass('is-moving');
    expect(container.querySelector('.ai-step-card')).toHaveTextContent('批次数量尚未返回');
    expect(container.querySelector('.ai-step-duration')).toBeNull();
    data = { error: 'offline' }; await pollActivity();
    expect(container.querySelector('.ai-step-card')).toBeNull();
    data = { workflowEvents: [workflowEvent('saving', 'running', {
      live: { nodeId: 'dedup_and_write', phase, metrics: { rawCount: 5 } },
    })] };
    await act(async () => { await vi.advanceTimersByTimeAsync(6500); });
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(container.querySelector('.ai-step-card')).toBeNull();
    expect(container.querySelector('.event-rail-list')).toHaveTextContent('结果保存中');
    expect(container.querySelector('.ai-stream-resting')).toHaveTextContent('本批步骤已结束，正在保存执行结果');
  });

  it('never labels failed or unknown terminal work as successful processing', async () => {
    mockActivity(() => ({ workflowEvents: [workflowEvent('failed', 'failed', {
      result: { rawCount: null }, live: { nodeId: 'normalize', metrics: {} },
    })] }));
    const { container } = render(<Page />);
    await waitFor(() => expect(container.querySelector('.ai-stream-resting')).toHaveTextContent('执行异常'));
    expect(container.querySelectorAll('.ai-step-card')).toHaveLength(0);
    expect(container.querySelector('.ai-task-board-scroll')).not.toHaveTextContent('本批完成降噪');
    expect(container.querySelector('.ai-task-board-scroll')).not.toHaveTextContent('数据处理中');
  });

  it('ignores an in-flight activity response after unmount', async () => {
    vi.useFakeTimers();
    const fallback = pageGetMock.getMockImplementation()!;
    let resolveActivity: (value: any) => void = () => {};
    pageGetMock.mockImplementation((path: string, ...args: any[]) => path === '/activity'
      ? new Promise((resolve) => { resolveActivity = resolve; }) : fallback(path, ...args));
    const { unmount } = render(<Page />);
    await act(async () => {});
    unmount();
    const callCount = pageGetMock.mock.calls.length;
    await act(async () => { resolveActivity({ data: { cursor: 'late', workflowEvents: [workflowEvent('late')] } }); });
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(pageGetMock.mock.calls.length).toBe(callCount);
  });

  it('reacts to the shared SOC dashboard title change event', async () => {
    render(<Page />);

    window.dispatchEvent(new CustomEvent('soc-dashboard:title-changed', {
      detail: { title: '自定义 SOC 态势中心' },
    }));

    expect(await screen.findByText('自定义 SOC 态势中心')).toBeInTheDocument();
  });
});

// The posture page owns its title setting (it used to be a sidebar entry in the host).
describe('SOC dashboard page settings', () => {
  const DEFAULT_TITLE = 'Flocks AI 智能告警态势中心';
  const TITLE_KEY = 'soc-dashboard-custom-title-v1';
  const TITLE_EVENT = 'soc-dashboard:title-changed';
  let titleEvents: Array<{ title: string | null }> = [];
  const recordTitleEvent = (event: Event) => titleEvents.push((event as CustomEvent).detail);

  beforeEach(() => {
    installContractSdk();
    window.localStorage.clear();
    titleEvents = [];
    window.addEventListener(TITLE_EVENT, recordTitleEvent);
    pageGetMock.mockImplementation((path: string) => {
      if (path === '/stats') return Promise.resolve({ data: {} });
      if (path === '/activity') {
        return Promise.resolve({ data: { cursor: 'c', events: [], recentEvents: [], workflowEvents: [], batch: {},
          workflowStats: { callCount: 0, latestStartedAt: 0 } } });
      }
      if (path === '/task-center') return Promise.resolve({ data: { scheduledTasks: [], workflows: [] } });
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });
  });

  afterEach(() => {
    window.removeEventListener(TITLE_EVENT, recordTitleEvent);
    delete (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__;
    pageGetMock.mockReset();
    vi.restoreAllMocks();
  });

  async function renderPage() {
    const utils = render(<Page />);
    await waitFor(() => expect(pageGetMock).toHaveBeenCalledWith('/stats', expect.anything()));
    return utils;
  }
  const headline = (container: HTMLElement) => container.querySelector('.command-brand strong')!.textContent;
  const gear = () => screen.getByRole('button', { name: '页面设置' });
  const panel = () => screen.queryByRole('dialog', { name: '页面设置' });

  it('puts the settings gear between refresh and the clock', async () => {
    const { container } = await renderPage();
    const order = Array.from(container.querySelector('.command-tools')!.children).map((el) => el.className);
    expect(order).toEqual(['command-time-filter', 'command-refresh', 'command-settings', 'command-clock']);
    expect(gear()).toHaveAttribute('aria-haspopup', 'dialog');
    expect(gear()).toHaveAttribute('aria-expanded', 'false');
    expect(panel()).toBeNull();
  });

  it('opens with the current title, focused and capped at 64 characters', async () => {
    window.localStorage.setItem(TITLE_KEY, '自定义标题A');
    const user = userEvent.setup();
    const { container } = await renderPage();
    expect(headline(container)).toBe('自定义标题A');
    await user.click(gear());
    const input = within(panel()!).getByRole('textbox') as HTMLInputElement;
    expect(input.value).toBe('自定义标题A');
    expect(input).toHaveFocus();
    expect(input).toHaveAttribute('placeholder', DEFAULT_TITLE);
    expect(input.maxLength).toBe(64);
    expect(within(panel()!).getByText('只修改告警态势页的大标题，保存在当前浏览器。')).toBeInTheDocument();
    expect(within(panel()!).getAllByRole('button').map((el) => el.textContent)).toEqual(['恢复默认', '取消', '保存']);
  });

  it('saves the trimmed title on Enter, updates the heading in place and announces it', async () => {
    const user = userEvent.setup();
    const { container } = await renderPage();
    const header = container.querySelector('.command-header');
    await user.click(gear());
    await user.type(within(panel()!).getByRole('textbox'), '  新的态势大屏  {Enter}');
    expect(panel()).toBeNull();
    expect(headline(container)).toBe('新的态势大屏');
    expect(window.localStorage.getItem(TITLE_KEY)).toBe('新的态势大屏');
    expect(titleEvents).toEqual([{ title: '新的态势大屏' }]);
    // No reload: the same header stays mounted.
    expect(container.querySelector('.command-header')).toBe(header);
    expect(gear()).toHaveFocus();
  });

  it('treats a blank title and 恢复默认 as the default title', async () => {
    window.localStorage.setItem(TITLE_KEY, '旧标题');
    const user = userEvent.setup();
    const { container } = await renderPage();
    await user.click(gear());
    const input = within(panel()!).getByRole('textbox');
    await user.clear(input);
    await user.type(input, '   ');
    await user.click(within(panel()!).getByRole('button', { name: '保存' }));
    expect(window.localStorage.getItem(TITLE_KEY)).toBeNull();
    expect(headline(container)).toBe(DEFAULT_TITLE);

    window.localStorage.setItem(TITLE_KEY, '又一个标题');
    await user.click(gear());
    await user.click(within(panel()!).getByRole('button', { name: '恢复默认' }));
    expect(panel()).toBeNull();
    expect(window.localStorage.getItem(TITLE_KEY)).toBeNull();
    expect(headline(container)).toBe(DEFAULT_TITLE);
    expect(titleEvents).toEqual([{ title: null }, { title: null }]);
  });

  it('leaves the title alone on 取消, Escape and a press outside', async () => {
    window.localStorage.setItem(TITLE_KEY, '旧标题');
    const user = userEvent.setup();
    const { container } = await renderPage();
    await user.click(gear());
    await user.type(within(panel()!).getByRole('textbox'), '改了一半');
    await user.click(within(panel()!).getByRole('button', { name: '取消' }));
    expect(panel()).toBeNull();

    await user.click(gear());
    expect((within(panel()!).getByRole('textbox') as HTMLInputElement).value).toBe('旧标题');
    await user.type(within(panel()!).getByRole('textbox'), 'x');
    await user.keyboard('{Escape}');
    expect(panel()).toBeNull();
    expect(gear()).toHaveFocus();

    await user.click(gear());
    // An Escape that only ends an IME composition keeps the panel open.
    fireEvent.keyDown(within(panel()!).getByRole('textbox'), { key: 'Escape', isComposing: true });
    expect(panel()).not.toBeNull();
    await user.click(within(panel()!).getByText('页面标题'));
    expect(panel()).not.toBeNull();
    await user.click(container.querySelector('.command-brand')!);
    expect(panel()).toBeNull();

    expect(headline(container)).toBe('旧标题');
    expect(window.localStorage.getItem(TITLE_KEY)).toBe('旧标题');
    expect(titleEvents).toEqual([]);
  });

  it('never keeps the time filter and page settings open together', async () => {
    const user = userEvent.setup();
    const { container } = await renderPage();
    const timeTrigger = container.querySelector('.command-time-trigger') as HTMLElement;
    await user.click(timeTrigger);
    expect(container.querySelector('.command-time-panel')).not.toBeNull();
    await user.click(gear());
    expect(container.querySelector('.command-time-panel')).toBeNull();
    expect(panel()).not.toBeNull();
    await user.click(timeTrigger);
    expect(panel()).toBeNull();
    expect(container.querySelector('.command-time-panel')).not.toBeNull();
  });

  it('still applies the title when storage is unavailable', async () => {
    const storage = (window.localStorage instanceof Storage ? Storage.prototype : window.localStorage) as any;
    vi.spyOn(storage, 'setItem').mockImplementation(() => { throw new Error('QuotaExceededError'); });
    const user = userEvent.setup();
    const { container } = await renderPage();
    await user.click(gear());
    await user.type(within(panel()!).getByRole('textbox'), '离线标题{Enter}');
    expect(panel()).toBeNull();
    expect(headline(container)).toBe('离线标题');
    expect(titleEvents).toEqual([{ title: '离线标题' }]);
  });
});
