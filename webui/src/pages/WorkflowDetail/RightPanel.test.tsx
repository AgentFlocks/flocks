import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import RightPanel from './RightPanel';

const mockConfirm = vi.fn();

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        'detail.rightPanel.tabOverview': '概览',
        'detail.rightPanel.tabChat': 'AI 编辑',
        'detail.rightPanel.tabRun': '运行',
        'detail.rightPanel.tabIntegration': '集成',
        'detail.rightPanel.deleteWorkflow': '删除工作流',
        'detail.rightPanel.deleting': '删除中...',
        'detail.rightPanel.deleteConfirmTitle': '删除工作流',
        'detail.rightPanel.deleteConfirmDesc': '确认删除',
        'detail.rightPanel.deleteConfirmText': '确认删除',
      };
      return translations[key] ?? key;
    },
  }),
}));

vi.mock('@/components/common/ConfirmDialog', () => ({
  useConfirm: () => mockConfirm,
}));

vi.mock('./tabs/OverviewTab', () => ({
  default: () => <div>overview tab</div>,
}));

vi.mock('./tabs/ChatTab', () => ({
  default: () => <div>chat tab</div>,
}));

vi.mock('./tabs/RunTab', () => ({
  default: () => <div>run tab</div>,
}));

vi.mock('./tabs/IntegrationTab', () => ({
  default: () => <div>integration tab</div>,
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
      callCount: 0,
      successCount: 0,
      errorCount: 0,
      totalRuntime: 0,
      avgRuntime: 0,
      thumbsUp: 0,
      thumbsDown: 0,
    },
    workflowJson: {
      start: 'node-1',
      nodes: [],
      edges: [],
    },
  };
}

describe('RightPanel', () => {
  it('不再渲染前往会话列表按钮', () => {
    render(
      <RightPanel
        workflow={makeWorkflow()}
        open
        onDelete={vi.fn()}
      />,
    );

    expect(screen.queryByRole('button', { name: '前往会话列表查看' })).not.toBeInTheDocument();
  });

  it('支持外部控制当前 Tab', async () => {
    const user = userEvent.setup();
    const onActiveTabChange = vi.fn();

    render(
      <RightPanel
        workflow={makeWorkflow()}
        open
        activeTab="chat"
        onActiveTabChange={onActiveTabChange}
      />,
    );

    expect(screen.getByText('chat tab')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '运行' }));

    expect(onActiveTabChange).toHaveBeenCalledWith('run');
    expect(screen.getByText('chat tab')).toBeInTheDocument();
  });
});
