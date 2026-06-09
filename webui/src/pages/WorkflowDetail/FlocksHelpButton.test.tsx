import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import WorkflowDetail from './index';

const { mockGetWorkflow } = vi.hoisted(() => ({
  mockGetWorkflow: vi.fn(),
}));

vi.mock('@/api/workflow', () => ({
  workflowAPI: {
    get: mockGetWorkflow,
    delete: vi.fn(),
    export: vi.fn(),
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        'detail.flocksHelp': 'Flocks 帮助',
        'detail.flocksHelpTitle': '打开右侧 AI 编辑',
        'detail.resetLayout': '重置布局',
        'detail.canvasTabs.flow': '流程图',
        'detail.canvasTabs.md': 'MD 描述',
        'detail.canvasTabs.json': 'JSON',
      };
      return translations[key] ?? key;
    },
  }),
}));

vi.mock('./TopBar', () => ({
  default: ({ onTogglePanel }: { onTogglePanel: () => void }) => (
    <button type="button" onClick={onTogglePanel}>toggle panel</button>
  ),
}));

vi.mock('./FlowCanvas', () => ({
  default: () => <div data-testid="flow-canvas">flow canvas</div>,
}));

vi.mock('./RightPanel', () => ({
  default: ({ open, activeTab }: { open: boolean; activeTab?: string }) => (
    <div
      data-testid="right-panel"
      data-open={open ? 'open' : 'closed'}
      data-active-tab={activeTab}
    >
      right panel
    </div>
  ),
}));

vi.mock('./NodeInfoPanel', () => ({
  default: () => <div>node info</div>,
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
      nodes: [{ id: 'node-1', type: 'python' as const }],
      edges: [],
    },
  };
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={['/workflows/wf-1']}>
      <Routes>
        <Route path="/workflows/:id" element={<WorkflowDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('Flocks help button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetWorkflow.mockResolvedValue({ data: makeWorkflow() });
  });

  it('opens the right panel on the AI edit tab', async () => {
    const user = userEvent.setup();
    renderDetail();

    await screen.findByTestId('flow-canvas');
    await user.click(screen.getByRole('button', { name: 'toggle panel' }));

    await waitFor(() => {
      expect(screen.getByTestId('right-panel')).toHaveAttribute('data-open', 'closed');
    });

    await user.click(screen.getByRole('button', { name: 'Flocks 帮助' }));

    expect(screen.getByTestId('right-panel')).toHaveAttribute('data-open', 'open');
    expect(screen.getByTestId('right-panel')).toHaveAttribute('data-active-tab', 'chat');
  });
});
