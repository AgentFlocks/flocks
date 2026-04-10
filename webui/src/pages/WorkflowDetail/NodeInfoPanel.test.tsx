import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import NodeInfoPanel from './NodeInfoPanel';

const { workflowAPI } = vi.hoisted(() => ({
  workflowAPI: {
    list: vi.fn(),
    update: vi.fn(),
    runNode: vi.fn(),
  },
}));

vi.mock('@/api/workflow', async () => {
  const actual = await vi.importActual<typeof import('@/api/workflow')>('@/api/workflow');
  return {
    ...actual,
    workflowAPI,
  };
});

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const translations: Record<string, string> = {
        'detail.nodeInfo.inputSources': '输入来源',
        'detail.nodeInfo.startNode': '起点节点',
        'detail.nodeInfo.triggerOnly': '仅触发',
        'detail.nodeInfo.outputDests': '输出去向',
        'detail.nodeInfo.outputKeyLabel': '键',
        'detail.nodeInfo.routeByPath': '按路径路由',
        'detail.nodeInfo.endNode': '终点节点',
        'detail.nodeInfo.runtimeSection': '最近一次运行',
        'detail.nodeInfo.runtimeLatest': '显示最后一次',
        'detail.nodeInfo.runtimeCount': `共匹配 ${params?.count ?? 0} 次`,
        'detail.nodeInfo.noRuntimeData': '暂无最近运行数据',
        'detail.nodeInfo.nodeNotExecutedYet': '最近一次执行中，该节点尚未运行',
        'detail.nodeInfo.runtimeStatus': `执行状态：${params?.status ?? ''}`,
        'detail.nodeInfo.runtimeInputs': '真实输入',
        'detail.nodeInfo.runtimeOutputs': '真实输出',
        'detail.nodeInfo.runNodeSection': '单节点执行',
        'detail.nodeInfo.runNodeHint': '隔离执行当前节点',
        'detail.nodeInfo.runNodeUnsupported': '当前节点类型暂不支持',
        'detail.nodeInfo.runNodeUnsupportedDesc': 'Branch 和 Loop 节点暂不支持单节点执行。',
        'detail.nodeInfo.runNodeInputs': '执行输入',
        'detail.nodeInfo.useLatestInputs': '使用最近一次输入',
        'detail.nodeInfo.restoreSuggestedInputs': '恢复建议输入',
        'detail.nodeInfo.runNodeAction': '执行节点',
        'detail.nodeInfo.runningNode': '执行中...',
        'detail.nodeInfo.runNodeSuccess': '执行成功',
        'detail.nodeInfo.runNodeError': '执行错误',
        'detail.nodeInfo.runNodeStdout': '标准输出',
        'detail.nodeInfo.runNodeTraceback': '错误堆栈',
        'detail.nodeInfo.runNodeInputObjectRequired': '执行输入必须是 JSON 对象',
        'detail.nodeInfo.runNodeFailed': '节点执行失败',
        'detail.nodeInfo.description': '描述',
        'detail.nodeInfo.descPlaceholder': 'desc',
        'detail.nodeInfo.code': '代码',
        'detail.nodeInfo.codePlaceholder': 'code',
        'detail.nodeInfo.expandCodeEditor': '放大编辑',
        'detail.nodeInfo.expandedCodeEditorTitle': '大编辑器',
        'detail.nodeInfo.closeExpandedEditor': '收起编辑',
        'detail.nodeInfo.startBadge': '起点',
        'detail.nodeInfo.close': '关闭',
        'detail.nodeInfo.saveNode': '保存节点',
        'detail.nodeInfo.saving': '保存中',
        'detail.nodeInfo.saved': '已保存',
        'detail.nodeInfo.saveFailed': '保存失败',
      };
      return translations[key] ?? key;
    },
  }),
}));

describe('NodeInfoPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    workflowAPI.list.mockResolvedValue({ data: [] });
    workflowAPI.update.mockResolvedValue({ data: {} });
    workflowAPI.runNode.mockResolvedValue({
      data: {
        node_id: 'node-1',
        outputs: { result: 'ok' },
        stdout: 'done',
        success: true,
        duration_ms: 120,
      },
    });
  });

  const workflow = {
    id: 'wf-1',
    name: 'Demo Workflow',
    category: 'default',
    workflowJson: {
      start: 'node-1',
      nodes: [
        {
          id: 'node-1',
          type: 'python' as const,
          code: 'outputs["result"] = inputs.get("host")',
          description: 'demo node',
        },
      ],
      edges: [],
    },
    status: 'draft' as const,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    stats: {
      callCount: 0,
      successCount: 0,
      errorCount: 0,
      totalRuntime: 0,
      avgRuntime: 0,
      thumbsUp: 0,
      thumbsDown: 0,
    },
  };

  it('shows runtime inputs and outputs for the latest matching step', () => {
    render(
      <NodeInfoPanel
        node={workflow.workflowJson.nodes[0]}
        workflow={workflow}
        latestExecution={{
          id: 'exec-1',
          workflowId: 'wf-1',
          inputParams: { host: 'a' },
          outputResults: { result: 'ok' },
          status: 'success',
          startedAt: Date.now(),
          executionLog: [
            {
              node_id: 'node-1',
              inputs: { host: 'demo.local' },
              outputs: { result: 'ok' },
            },
          ],
        }}
        onClose={() => {}}
        onSaved={() => {}}
      />
    );

    expect(screen.getByText('最近一次运行')).toBeInTheDocument();
    expect(screen.getByText('真实输入')).toBeInTheDocument();
    expect(screen.getByText('真实输出')).toBeInTheDocument();
    expect(screen.getByDisplayValue(/demo.local/)).toBeInTheDocument();
    expect(screen.getByText(/"result": "ok"/)).toBeInTheDocument();
  });

  it('shows empty runtime hint when there is no latest execution', () => {
    render(
      <NodeInfoPanel
        node={workflow.workflowJson.nodes[0]}
        workflow={workflow}
        latestExecution={null}
        onClose={() => {}}
        onSaved={() => {}}
      />
    );

    expect(screen.getByText('暂无最近运行数据')).toBeInTheDocument();
  });

  it('toggles the runtime section content', async () => {
    const user = userEvent.setup();

    render(
      <NodeInfoPanel
        node={workflow.workflowJson.nodes[0]}
        workflow={workflow}
        latestExecution={{
          id: 'exec-1',
          workflowId: 'wf-1',
          inputParams: { host: 'a' },
          outputResults: { result: 'ok' },
          status: 'success',
          startedAt: Date.now(),
          executionLog: [
            {
              node_id: 'node-1',
              inputs: { host: 'demo.local' },
              outputs: { result: 'ok' },
            },
          ],
        }}
        onClose={() => {}}
        onSaved={() => {}}
      />
    );

    const runtimeToggle = screen.getByRole('button', { name: /最近一次运行/ });
    expect(screen.getByText('真实输入')).toBeInTheDocument();

    await user.click(runtimeToggle);
    expect(screen.queryByText('真实输入')).not.toBeInTheDocument();
    expect(screen.queryByText('真实输出')).not.toBeInTheDocument();

    await user.click(runtimeToggle);
    expect(screen.getByText('真实输入')).toBeInTheDocument();
    expect(screen.getByText('真实输出')).toBeInTheDocument();
  });

  it('runs a single node with latest runtime inputs', async () => {
    const user = userEvent.setup();

    render(
      <NodeInfoPanel
        node={workflow.workflowJson.nodes[0]}
        workflow={workflow}
        latestExecution={{
          id: 'exec-1',
          workflowId: 'wf-1',
          inputParams: { host: 'a' },
          outputResults: { result: 'ok' },
          status: 'success',
          startedAt: Date.now(),
          executionLog: [
            {
              node_id: 'node-1',
              inputs: { host: 'demo.local' },
              outputs: { result: 'ok' },
            },
          ],
        }}
        onClose={() => {}}
        onSaved={() => {}}
      />
    );

    expect(screen.getByDisplayValue(/demo.local/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '执行节点' }));

    expect(workflowAPI.runNode).toHaveBeenCalledWith('wf-1', {
      nodeId: 'node-1',
      inputs: { host: 'demo.local' },
    });
    expect(await screen.findByText('执行成功')).toBeInTheDocument();
    expect(screen.getByText(/done/)).toBeInTheDocument();
  });

  it('opens the expanded code editor and keeps code in sync', async () => {
    const user = userEvent.setup();

    render(
      <NodeInfoPanel
        node={workflow.workflowJson.nodes[0]}
        workflow={workflow}
        latestExecution={null}
        onClose={() => {}}
        onSaved={() => {}}
      />
    );

    await user.click(screen.getByRole('button', { name: '放大编辑' }));
    expect(screen.getByRole('dialog', { name: '大编辑器' })).toBeInTheDocument();

    const expandedEditor = screen.getByRole('dialog', { name: '大编辑器' }).querySelector('textarea');
    expect(expandedEditor).not.toBeNull();
    await user.clear(expandedEditor!);
    await user.type(expandedEditor!, 'print("expanded")');

    expect(screen.getAllByDisplayValue('print("expanded")').length).toBeGreaterThanOrEqual(1);

    await user.click(screen.getByRole('button', { name: '收起编辑' }));
    expect(screen.queryByRole('dialog', { name: '大编辑器' })).not.toBeInTheDocument();
    expect(screen.getByDisplayValue('print("expanded")')).toBeInTheDocument();
  });

  it('places run node section after the code editor for code nodes', () => {
    render(
      <NodeInfoPanel
        node={workflow.workflowJson.nodes[0]}
        workflow={workflow}
        latestExecution={null}
        onClose={() => {}}
        onSaved={() => {}}
      />
    );

    const codeLabel = screen.getByText('代码');
    const runNodeLabel = screen.getByText('单节点执行');

    expect(
      codeLabel.compareDocumentPosition(runNodeLabel) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('shows unsupported message for branch nodes', () => {
    render(
      <NodeInfoPanel
        node={{
          id: 'branch-1',
          type: 'branch',
          select_key: 'next_step',
        }}
        workflow={{
          ...workflow,
          workflowJson: {
            ...workflow.workflowJson,
            nodes: [
              {
                id: 'branch-1',
                type: 'branch',
                select_key: 'next_step',
              },
            ],
          },
        }}
        latestExecution={null}
        onClose={() => {}}
        onSaved={() => {}}
      />
    );

    expect(screen.getByText('当前节点类型暂不支持')).toBeInTheDocument();
    expect(screen.getByText('Branch 和 Loop 节点暂不支持单节点执行。')).toBeInTheDocument();
  });
});
