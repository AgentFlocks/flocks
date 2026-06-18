import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import OverviewTab from './OverviewTab';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'zh-CN' },
    t: (key: string, params?: Record<string, unknown>) => {
      const translations: Record<string, string> = {
        'detail.overview.configInfo': '信息',
        'detail.overview.nodeCount': '节点数',
        'detail.overview.nodesAndEdges': `${params?.nodes ?? 0} 个节点 / ${params?.edges ?? 0} 条边`,
        'detail.overview.category': '分类',
        'detail.overview.version': '版本',
        'detail.overview.createdBy': '创建人',
        'detail.overview.createdAt': '创建时间',
        'detail.overview.updatedAt': '更新时间',
        'detail.overview.workflowFiles': '工作流文件',
        'detail.overview.run': '运行',
        'detail.overview.runStats': '运行统计',
        'detail.overview.totalCalls': '总调用次数',
        'detail.overview.successRate': '成功率',
        'detail.overview.avgRuntime': '平均耗时',
        'detail.overview.errorCount': '失败次数',
        'detail.overview.successTimes': `成功 ${params?.count ?? 0} 次`,
        'detail.overview.errorTimes': `失败 ${params?.count ?? 0} 次`,
        'detail.run.testSection': '测试运行',
        'detail.run.historySection': '执行历史',
      };
      return translations[key] ?? key;
    },
  }),
}));

vi.mock('./RunTab', () => ({
  default: ({
    embedded,
    embeddedTabs,
    hideSectionHeaders,
  }: {
    embedded?: boolean;
    embeddedTabs?: boolean;
    hideSectionHeaders?: boolean;
  }) => (
    <div
      data-testid="embedded-run-tab"
      data-embedded={String(Boolean(embedded))}
      data-embedded-tabs={String(Boolean(embeddedTabs))}
      data-hide-section-headers={String(Boolean(hideSectionHeaders))}
    >
      run tab
    </div>
  ),
}));

function makeWorkflow() {
  return {
    id: 'wf-1',
    name: '测试工作流',
    category: 'default',
    status: 'draft' as const,
    createdAt: 0,
    updatedAt: 0,
    stats: {
      callCount: 3,
      successCount: 2,
      errorCount: 1,
      totalRuntime: 6,
      avgRuntime: 2,
      thumbsUp: 0,
      thumbsDown: 0,
    },
    workflowJson: {
      start: 'node-1',
      version: '1.0',
      nodes: [{ id: 'node-1', type: 'start' }],
      edges: [],
    },
  };
}

describe('OverviewTab', () => {
  it('在概览内以可折叠区块承载信息和运行', async () => {
    const user = userEvent.setup();

    render(<OverviewTab workflow={makeWorkflow()} />);

    expect(screen.getByRole('button', { name: '信息 1 个节点 / 0 条边' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '运行 总调用次数 3 / 成功率 66.7% / 平均耗时 2.00s' })).toBeInTheDocument();
    expect(screen.getByText('分类')).toBeInTheDocument();
    expect(screen.getByText('.flocks/plugins/workflows/wf-1/workflow.json')).toBeInTheDocument();
    expect(screen.getByText('.flocks/plugins/workflows/wf-1/workflow.md')).toBeInTheDocument();
    expect(screen.getByText('运行统计')).toBeInTheDocument();

    const runTab = screen.getByTestId('embedded-run-tab');
    expect(runTab).toHaveAttribute('data-embedded', 'true');
    expect(runTab).toHaveAttribute('data-embedded-tabs', 'true');
    expect(runTab).toHaveAttribute('data-hide-section-headers', 'true');

    await user.click(screen.getByRole('button', { name: '运行 总调用次数 3 / 成功率 66.7% / 平均耗时 2.00s' }));
    expect(screen.queryByTestId('embedded-run-tab')).not.toBeInTheDocument();
    expect(screen.queryByText('运行统计')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '信息 1 个节点 / 0 条边' }));
    expect(screen.queryByText('分类')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '运行 总调用次数 3 / 成功率 66.7% / 平均耗时 2.00s' }));
    expect(screen.getByText('运行统计')).toBeInTheDocument();
  });
});
