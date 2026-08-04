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
      expect(pageGetMock).toHaveBeenCalledWith('/task-center', expect.anything());
    });

    expect(screen.getByText('Flocks AI 智能告警态势中心')).toBeInTheDocument();
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
                eventId: 'workflow-denoise-1',
                stage: 'denoise',
                status: 'running',
                occurredAt,
                triggerSource: 'workflow_execution',
                sessionId: 'session-1',
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
                eventId: 'workflow-triage-1',
                stage: 'triage',
                status: 'running',
                occurredAt,
                triggerSource: 'workflow_execution',
                sessionId: 'session-1',
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
      if (path === '/task-center') {
        return Promise.resolve({
          data: {
            sessionCount: 12,
            scheduledExecutionCount: 20,
            scheduledTodayExecutionCount: 2,
            workflowExecutionCount: 745000,
            workflowTodayExecutionCount: 7,
            scheduledTasks: [
              {
                id: 'scheduled-1',
                name: '定时巡检',
                status: 'active',
                executionCount: 20,
                todayExecutionCount: 2,
                activeCount: 2,
                successRate: 0.9,
                lastStatus: 'completed',
                lastRunAt: '2026-08-04T08:00:00',
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
    expect(screen.getByText('工作流调用')).toBeInTheDocument();
    expect(screen.getByText('今日调用 7')).toBeInTheDocument();

    const summary = container.querySelector('.task-center-summary') as HTMLElement;
    expect(summary).toBeTruthy();
    const cards = Array.from(summary.children) as HTMLElement[];
    const activeCard = cards.find((card) => within(card).queryByText('执行中')) as HTMLElement;
    const workflowCard = cards.find((card) => within(card).queryByText('工作流调用')) as HTMLElement;

    expect(activeCard.querySelector('b.animated-number')).toHaveAttribute('title', '5');
    expect(workflowCard).toHaveAttribute(
      'title',
      '优先来自 workflow_stats.call_count；今日为当天 call_count 增量，缺少快照时回退执行记录数',
    );
  });

  it('reacts to the shared SOC dashboard title change event', async () => {
    render(<Page />);

    window.dispatchEvent(new CustomEvent('soc-dashboard:title-changed', {
      detail: { title: '自定义 SOC 态势中心' },
    }));

    expect(await screen.findByText('自定义 SOC 态势中心')).toBeInTheDocument();
  });
});
