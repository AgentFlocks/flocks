import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
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
      if (path === '/ai-tasks') {
        return Promise.resolve({
          data: {
            connection: 'online',
            summary: { active: 0, running: 0, waiting: 0, stale: 0 },
            tasks: [],
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
    delete (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__;
    if (originalDocumentHidden) {
      Object.defineProperty(document, 'hidden', originalDocumentHidden);
    } else {
      delete (document as any).hidden;
    }
    pageGetMock.mockReset();
  });

  it('loads stats, activity, and task center data through the page SDK', async () => {
    render(<Page />);

    await waitFor(() => {
      expect(pageGetMock).toHaveBeenCalledWith('/stats', expect.anything());
      expect(pageGetMock).toHaveBeenCalledWith('/activity', expect.anything());
      expect(pageGetMock).toHaveBeenCalledWith('/ai-tasks', expect.anything());
      expect(pageGetMock).toHaveBeenCalledWith('/task-center', expect.anything());
    });

    expect(screen.getByText('Flocks AI 智能告警态势中心')).toBeInTheDocument();
  });

  it('shows unavailable denoise metrics as dashes instead of false zeros', async () => {
    pageGetMock.mockImplementation((path: string) => {
      if (path === '/stats') {
        return Promise.resolve({
          data: {
            generatedAt: new Date().toISOString(),
            denoise: { totalRaw: 0, totalUnique: 0, duplicateRate: 0, duplicates: 0 },
            pipeline: { raw: 0, unique: 0 },
            sources: [{ key: 'ndr', label: 'NDR', value: 0, rate: 0, active: false }],
            sourceStatus: {
              metricQuality: {
                status: 'unavailable',
                dataAvailable: false,
                metricsAvailable: false,
                unavailableReason: 'workflow_db_missing',
              },
            },
          },
        });
      }
      if (path === '/activity') {
        return Promise.resolve({
          data: {
            cursor: 'cursor', events: [], recentEvents: [], workflowEvents: [], batch: {},
            workflowStats: { callCount: null, latestStartedAt: null },
          },
        });
      }
      if (path === '/ai-tasks') {
        return Promise.resolve({ data: { connection: 'online', summary: {}, tasks: [] } });
      }
      if (path === '/task-center') return Promise.resolve({ data: { scheduledTasks: [], workflows: [] } });
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    const { container } = render(<Page />);

    expect(await screen.findByText('降噪统计数据源不可用，相关数字已隐藏；系统正在重试')).toBeInTheDocument();
    const rawMetric = screen.getByText('原始告警量').closest('.command-metric') as HTMLElement;
    expect(within(rawMetric).getByText('--')).toHaveAttribute('title', '数据不可用');
    const ndrSource = screen.getByText('NDR').closest('.command-source') as HTMLElement;
    expect(within(ndrSource).getByText('--')).toBeInTheDocument();
    expect(container.querySelector('.command-source b')).toHaveAttribute('title', '数据不可用');
  });

  it('shows verified partial-window metrics without a yellow coverage warning', async () => {
    pageGetMock.mockImplementation((path: string) => {
      if (path === '/stats') {
        return Promise.resolve({
          data: {
            generatedAt: new Date().toISOString(),
            denoise: { totalRaw: 10, totalUnique: 4, duplicateRate: 0.6, duplicates: 6 },
            timeline: { denoiseRaw: [10], denoiseUnique: [4] },
            sourceStatus: {
              metricQuality: {
                status: 'partial',
                dataAvailable: true,
                metricsAvailable: true,
                sourceMetricsAvailable: false,
                coverageComplete: false,
                coverageStartedAt: Date.now() - 60_000,
                invalidExecutionCount: 0,
                errorExecutionCount: 0,
                unprocessedInputCount: 0,
              },
            },
          },
        });
      }
      if (path === '/activity') {
        return Promise.resolve({
          data: {
            cursor: 'cursor', events: [], recentEvents: [], workflowEvents: [], batch: {},
            workflowStats: { callCount: 10, latestStartedAt: Date.now() },
          },
        });
      }
      if (path === '/ai-tasks') return Promise.resolve({ data: { connection: 'online', summary: {}, tasks: [] } });
      if (path === '/task-center') return Promise.resolve({ data: { scheduledTasks: [], workflows: [] } });
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    render(<Page />);

    const rawMetric = screen.getByText('原始告警量').closest('.command-metric') as HTMLElement;
    await waitFor(() => {
      expect(rawMetric.querySelector('.command-metric-value')).toHaveAttribute('title', '10');
    });
    expect(within(rawMetric).getByText(/精确采集自 .* · 4 条进入研判/)).toBeInTheDocument();
    expect(screen.queryByText(/精确降噪指标尚未覆盖当前时间范围/)).not.toBeInTheDocument();
    expect(screen.queryByText(/降噪指标部分可用/)).not.toBeInTheDocument();
  });

  it('shows unavailable SOC triage metrics as dashes instead of false zeros', async () => {
    pageGetMock.mockImplementation((path: string) => {
      if (path === '/stats') {
        return Promise.resolve({
          data: {
            generatedAt: new Date().toISOString(),
            denoise: { totalRaw: 10, totalUnique: 4, duplicateRate: 0.6, duplicates: 6 },
            triage: {
              totalRecords: 0,
              attackTotal: 0,
              attackSuccess: 0,
              benign: 0,
              unknown: 0,
            },
            pipeline: { attackRate: 0, successRate: 0 },
            closedLoop: { autoClosed: 0, manualDecision: 0, pending: 0, resolutionRate: 0 },
            severityLevels: [],
            sourceStatus: {
              metricQuality: { status: 'complete', metricsAvailable: true },
              triageQuality: {
                status: 'unavailable',
                dataAvailable: false,
                metricsAvailable: false,
                unavailableReason: 'soc_db_missing',
              },
            },
          },
        });
      }
      if (path === '/activity') {
        return Promise.resolve({
          data: {
            cursor: 'cursor', events: [], recentEvents: [], workflowEvents: [], batch: {},
            workflowStats: { callCount: 1, latestStartedAt: Date.now() },
          },
        });
      }
      if (path === '/ai-tasks') {
        return Promise.resolve({ data: { connection: 'online', summary: {}, tasks: [] } });
      }
      if (path === '/task-center') return Promise.resolve({ data: { scheduledTasks: [], workflows: [] } });
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    render(<Page />);

    expect(await screen.findByText('SOC 事件数据库不可用，研判、事件与闭环数字已隐藏；系统正在重试')).toBeInTheDocument();
    const eventMetric = screen.getByText('安全事件量').closest('.command-metric') as HTMLElement;
    expect(within(eventMetric).getByText('--')).toHaveAttribute('title', '数据不可用');
    const criticalSeverity = screen.getByText('严重').closest('.severity-node') as HTMLElement;
    expect(within(criticalSeverity).getByText('--')).toHaveAttribute('title', '数据不可用');
    expect(screen.queryByText('SOC 事件数据库不可用，研判、事件与闭环数字已隐藏；系统正在重试')).toBeInTheDocument();
  });

  it('renders unknown metrics as dashes on the loading frame', () => {
    pageGetMock.mockImplementation(() => new Promise(() => {}));

    render(<Page />);

    const rawMetric = screen.getByText('原始告警量').closest('.command-metric') as HTMLElement;
    const eventMetric = screen.getByText('安全事件量').closest('.command-metric') as HTMLElement;
    const aiTaskTab = screen.getByRole('tab', { name: 'AI处理任务' });
    const aiTaskHeader = aiTaskTab.closest('.event-rail-head') as HTMLElement;
    expect(within(rawMetric).getByText('--')).toHaveAttribute('title', '数据不可用');
    expect(within(eventMetric).getByText('--')).toHaveAttribute('title', '数据不可用');
    expect(within(aiTaskHeader).getByText('--')).toHaveAttribute('title', '数据不可用');
    expect(screen.getByText('AI 处理任务数据加载中')).toBeInTheDocument();
    expect(screen.queryByText('当前没有运行或等待中的 AI 任务')).not.toBeInTheDocument();
  });

  it('pauses task-center polling while the page is hidden', async () => {
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
      if (path === '/ai-tasks') {
        return Promise.resolve({
          data: {
            connection: 'online',
            summary: { active: 0, running: 0, waiting: 0, stale: 0 },
            tasks: [],
          },
        });
      }
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    render(<Page />);

    expect(await screen.findByText('工作流执行')).toBeInTheDocument();
    expect(await screen.findByText('窗口研判 12 条 · AI新研判 5 条 · 复用 6 条')).toBeInTheDocument();
    expect(await screen.findByText('已流转至研判')).toBeInTheDocument();
  });

  it('renders task center overview with corrected metric semantics', async () => {
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
      if (path === '/ai-tasks') {
        return Promise.resolve({
          data: {
            connection: 'online',
            summary: { active: 0, running: 0, waiting: 0, stale: 0 },
            tasks: [],
          },
        });
      }
      if (path === '/task-center') {
        return Promise.resolve({
          data: {
            sessionCount: 12,
            activeExecutionCount: 9,
            scheduledExecutionCount: 20,
            scheduledTodayExecutionCount: 2,
            workflowExecutionCount: 745000,
            workflowTodayExecutionCount: 7,
            scheduledTasks: [
              {
                id: 'scheduled-1',
                name: '定时巡检',
                status: 'disabled',
                executionCount: 20,
                todayExecutionCount: 2,
                activeCount: 0,
                successRate: 0.9,
                lastStatus: 'completed',
                lastRunAt: '2026-08-04T08:00:00',
                nextRunAt: '2026-08-05T01:00:00Z',
              },
            ],
            workflows: [
              {
                id: 'workflow-1',
                name: '告警研判',
                executionCount: 745000,
                todayExecutionCount: 7,
                activeCount: 3,
                successRate: 0.98,
                lastStatus: 'running',
                lastRunAt: Date.now(),
                latestExecutionHash: 'workflow-run-1',
                latestAlertName: '远程命令执行',
                sessionId: '',
                messageId: '',
                progressPercent: 0.5,
                progressLabel: '运行中',
              },
            ],
          },
        });
      }
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    const user = userEvent.setup();
    const { container } = render(<Page />);

    await user.click(await screen.findByRole('tab', { name: '任务中心' }));

    expect(await screen.findByText('关联会话')).toBeInTheDocument();
    expect(screen.getByText('今日启动 2')).toBeInTheDocument();
    expect(screen.getAllByText('工作流调用').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('今日调用 7')).toBeInTheDocument();
    expect(screen.getByText('1 个任务')).toBeInTheDocument();
    expect(screen.getByText('1 个工作流')).toBeInTheDocument();
    expect(screen.getByText('已关闭')).toBeInTheDocument();
    expect(screen.getByText(/上次执行/)).toBeInTheDocument();
    expect(screen.queryByText(/下次/)).not.toBeInTheDocument();
    expect(screen.getByText('关联告警')).toBeInTheDocument();
    expect(screen.getByText('远程命令执行')).toBeInTheDocument();
    expect(screen.getByText('执行ID')).toBeInTheDocument();
    expect(screen.getByText('workflow-run-1')).toBeInTheDocument();
    expect(screen.getByText('执行详情')).toBeInTheDocument();
    expect(screen.getByText('查看执行')).toBeInTheDocument();
    expect(screen.getByText(/最近调用/)).toBeInTheDocument();

    const summary = container.querySelector('.task-center-summary') as HTMLElement;
    expect(summary).toBeTruthy();
    const cards = Array.from(summary.children) as HTMLElement[];
    const activeCard = cards.find((card) => within(card).queryByText('执行中')) as HTMLElement;
    const workflowCard = cards.find((card) => within(card).queryByText('工作流调用')) as HTMLElement;

    expect(activeCard.querySelector('b.animated-number')).toHaveAttribute('title', '9');
    expect(workflowCard).toHaveAttribute(
      'title',
      '优先来自 workflow_stats.call_count；今日为当天 call_count 增量，缺少快照时回退执行记录数',
    );

    const workflowStats = container.querySelector('.task-center-stats.workflow-stats') as HTMLElement;
    expect(within(workflowStats).getByText('调用')).toBeInTheDocument();
    expect(within(workflowStats).getByText('今日调用')).toBeInTheDocument();
  });

  it('uses authoritative workflow task status instead of activity playback state', async () => {
    const now = Date.now();
    pageGetMock.mockImplementation((path: string) => {
      if (path === '/stats') return Promise.resolve({ data: {} });
      if (path === '/activity') {
        return Promise.resolve({
          data: {
            cursor: 'cursor',
            events: [],
            recentEvents: [],
            workflowEvents: [
              {
                eventId: 'workflow-execution:completed-history',
                stage: 'denoise',
                status: 'completed',
                occurredAt: new Date(now).toISOString(),
                triggerSource: 'workflow_execution',
                workflowId: 'stream_alert_denoise',
                alert: { id: 'history', threatName: '不应进入任务栏' },
                result: { isDuplicate: false, rawCount: 0 },
              },
            ],
            batch: {},
            workflowStats: { callCount: 0, latestStartedAt: 0 },
            tokenUsage: { totalTokens: 0, todayTokens: 0, todayRequests: 0, dailySeries: [] },
          },
        });
      }
      if (path === '/ai-tasks') {
        return Promise.resolve({
          data: {
            connection: 'online',
            summary: { active: 2, running: 1, waiting: 1, stale: 0 },
            tasks: [
              {
                taskId: 'workflow-execution:running-1',
                workflowId: 'stream_alert_triage',
                executionId: 'running-1',
                stage: 'triage',
                status: 'running',
                startedAt: now,
                title: 'SSRF盲打探测攻击结果未知',
                counts: { raw: null },
                dataQuality: 'pending',
                progress: { mode: 'steps', current: 2, total: 3, percent: 0.6667, label: '第 2/3 步' },
              },
              {
                taskId: 'workflow-execution:queued-1',
                workflowId: 'stream_alert_denoise',
                executionId: 'queued-1',
                stage: 'denoise',
                status: 'queued',
                startedAt: now - 1000,
                title: '降噪批次',
                counts: { raw: 0 },
                dataQuality: 'complete',
                rawCountSource: 'workflow_output',
                emptyInput: false,
                progress: { mode: 'waiting', percent: null, label: '等待调度' },
              },
            ],
          },
        });
      }
      if (path === '/task-center') return Promise.resolve({ data: { scheduledTasks: [], workflows: [] } });
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    render(<Page />);

    expect(await screen.findByText('正在处理 1 个，等待 1 个')).toBeInTheDocument();
    expect(screen.getByText('SSRF盲打探测攻击结果未知')).toBeInTheDocument();
    expect(screen.getByText('降噪批次')).toBeInTheDocument();
    expect(screen.getByText(/原始条数待校验/)).toBeInTheDocument();
    expect(screen.queryByText(/原始 0 条/)).not.toBeInTheDocument();
    expect(screen.getByText('第 2/3 步')).toBeInTheDocument();
    expect(screen.queryByText('不应进入任务栏')).not.toBeInTheDocument();
    expect(screen.queryByText('降噪处理完成')).not.toBeInTheDocument();
  });

  it('stops activity playback when a workflow execution becomes stale', async () => {
    const now = Date.now();
    let activityPolls = 0;
    pageGetMock.mockImplementation((path: string) => {
      if (path === '/stats') return Promise.resolve({ data: {} });
      if (path === '/activity') {
        activityPolls += 1;
        const status = activityPolls === 1 ? 'running' : 'stale';
        return Promise.resolve({
          data: {
            cursor: 'cursor',
            events: [],
            recentEvents: [],
            workflowEvents: [
              {
                eventId: 'workflow-execution:stale-denoise',
                stage: 'denoise',
                status,
                occurredAt: new Date(now).toISOString(),
                triggerSource: 'workflow_execution',
                workflowId: 'stream_alert_denoise',
                alert: { id: 'stale-alert', threatName: '降噪批次 · 原始 1 条' },
                result: { metricsAvailable: true, rawCount: 1, uniqueCount: 1 },
              },
            ],
            batch: {},
            workflowStats: { callCount: 1, latestStartedAt: now },
          },
        });
      }
      if (path === '/ai-tasks') {
        return Promise.resolve({
          data: {
            connection: 'online',
            summary: { active: 0, running: 0, waiting: 0, stale: 1 },
            tasks: [],
          },
        });
      }
      if (path === '/task-center') {
        return Promise.resolve({ data: { scheduledTasks: [], workflows: [] } });
      }
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    render(<Page />);

    expect((await screen.findAllByText('告警降噪中')).length).toBeGreaterThan(0);
    await waitFor(() => expect(activityPolls).toBeGreaterThan(1), { timeout: 5000 });
    await waitFor(() => {
      expect(screen.queryAllByText('告警降噪中')).toHaveLength(0);
    });
    expect(screen.getByText('智能研判核心')).toBeInTheDocument();
    expect(screen.getByText('发现 1 个失联任务，已移出活跃列表')).toBeInTheDocument();
  }, 7000);

  it('uses dashboard mock rows with the same workflow execution field shape as real task-center data', async () => {
    window.localStorage.setItem('soc-dashboard-mock-v1', '1');

    render(<Page />);

    const user = userEvent.setup();
    await user.click(await screen.findByRole('tab', { name: '任务中心' }));

    expect(await screen.findByText('告警研判工作流（Mock）')).toBeInTheDocument();
    expect(screen.getByText('mock-triage-run-002')).toBeInTheDocument();
    expect(screen.getAllByText('执行详情').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('查看执行').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('查看对话')).not.toBeInTheDocument();
  });

  it('reacts to the shared SOC dashboard title change event', async () => {
    render(<Page />);

    window.dispatchEvent(new CustomEvent('soc-dashboard:title-changed', {
      detail: { title: '自定义 SOC 态势中心' },
    }));

    expect(await screen.findByText('自定义 SOC 态势中心')).toBeInTheDocument();
  });
});
