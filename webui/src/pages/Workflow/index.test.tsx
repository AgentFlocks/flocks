import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import WorkflowPage from './index';

const { mockNavigate, mockUseWorkflows, mockLanguage } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockUseWorkflows: vi.fn(),
  mockLanguage: { current: 'zh-CN' },
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        pageTitle: '工作流',
        pageDescription: '管理和执行工作流',
        createWorkflow: '创建工作流',
        'section.custom': '自定义工作流',
        'section.builtin': '内置工作流',
        'status.draft': '草稿',
        'stats.nodes': '节点',
        noDescription: '无描述',
      };
      return translations[key] ?? key;
    },
    i18n: { language: mockLanguage.current },
  }),
}));

vi.mock('@/hooks/useWorkflow', () => ({
  useWorkflows: () => mockUseWorkflows(),
}));

vi.mock('@/components/common/PageHeader', () => ({
  default: ({ title, description, action }: { title: string; description: string; action?: ReactNode }) => (
    <div>
      <h1>{title}</h1>
      <p>{description}</p>
      {action}
    </div>
  ),
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading</div>,
}));

vi.mock('@/components/common/EmptyState', () => ({
  default: ({ title, description, action }: { title: string; description: string; action?: ReactNode }) => (
    <div>
      <div>{title}</div>
      <div>{description}</div>
      {action}
    </div>
  ),
}));

function makeWorkflow(overrides: Partial<any> = {}) {
  return {
    id: 'wf-1',
    name: '默认工作流',
    category: 'default',
    workflowJson: { start: 'node-1', nodes: [{ id: 'node-1' }], edges: [] },
    status: 'draft' as const,
    source: 'project' as const,
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
    ...overrides,
  };
}

describe('WorkflowPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockLanguage.current = 'zh-CN';
    mockUseWorkflows.mockReturnValue({
      workflows: [],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });
  });

  it('按 source 将工作流分到自定义和内置分组', () => {
    mockUseWorkflows.mockReturnValue({
      workflows: [
        makeWorkflow({ id: 'wf-global', name: 'Global Workflow', source: 'global' }),
        makeWorkflow({ id: 'wf-project', name: 'Project Workflow', source: 'project' }),
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<WorkflowPage />);

    const customRegion = screen.getByRole('region', { name: '自定义工作流' });
    const builtinRegion = screen.getByRole('region', { name: '内置工作流' });

    expect(within(customRegion).getByText('Global Workflow')).toBeInTheDocument();
    expect(within(customRegion).queryByText('Project Workflow')).not.toBeInTheDocument();
    expect(within(builtinRegion).getByText('Project Workflow')).toBeInTheDocument();
    expect(within(builtinRegion).queryByText('Global Workflow')).not.toBeInTheDocument();
  });

  it('按当前语言展示本地化工作流名称', () => {
    mockUseWorkflows.mockReturnValue({
      workflows: [
        makeWorkflow({
          id: 'wf-localized',
          name: 'localized_workflow',
          source: 'global',
          nameI18n: {
            'zh-CN': '中文工作流',
            'en-US': 'English Workflow',
          },
        }),
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<WorkflowPage />);

    expect(screen.getByText('中文工作流')).toBeInTheDocument();
    expect(screen.queryByText('localized_workflow')).not.toBeInTheDocument();
  });

  it('从创建入口进入时显式开启新建草稿', async () => {
    const user = userEvent.setup();
    mockUseWorkflows.mockReturnValue({
      workflows: [],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<WorkflowPage />);

    const createButtons = screen.getAllByRole('button', { name: /创建工作流/ });
    await user.click(createButtons[0]);
    await user.click(createButtons[1]);

    expect(mockNavigate).toHaveBeenCalledTimes(2);
    expect(mockNavigate).toHaveBeenNthCalledWith(
      1,
      '/workflows/new',
      expect.objectContaining({
        state: expect.objectContaining({
          freshCreate: true,
          ts: expect.any(Number),
        }),
      }),
    );
    expect(mockNavigate).toHaveBeenNthCalledWith(
      2,
      '/workflows/new',
      expect.objectContaining({
        state: expect.objectContaining({
          freshCreate: true,
          ts: expect.any(Number),
        }),
      }),
    );
  });

  it('没有自定义工作流时不渲染空分组', () => {
    mockUseWorkflows.mockReturnValue({
      workflows: [makeWorkflow({ id: 'wf-project-only', name: 'Project Only', source: 'project' })],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<WorkflowPage />);

    expect(screen.queryByRole('region', { name: '自定义工作流' })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: '内置工作流' })).toBeInTheDocument();
  });
});
