import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import AgentPage from './index';

const { mockUseAgents } = vi.hoisted(() => ({
  mockUseAgents: vi.fn(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => ({
      pageTitle: 'AI Agents',
      pageDescription: '管理和配置 AI Agent',
      totalCount: '共 2 个 Agent',
      'section.primary.title': '主 Agent',
      'section.primary.subtitle': '主 Agent 描述',
      'section.sub.title': '子 Agent',
      'section.sub.subtitle': '子 Agent 描述',
      'filter.all': '全部',
      'filter.builtin': '内置',
      'filter.custom': '自定义',
      'filter.aria': '按来源筛选',
      'badge.native': '内置',
      'badge.custom': '自定义',
      'badge.delegatable': '可委托',
      'badge.delete': '删除',
      'badge.edit': '编辑',
      'form.enabled': '启用',
      'form.enabledTip': '允许委派',
      'form.disabledTip': '禁止委派',
      createSubAgent: '创建子 Agent',
    }[key] ?? key),
    i18n: { language: 'zh-CN' },
  }),
}));

vi.mock('@/hooks/useAgents', () => ({
  useAgents: () => mockUseAgents(),
}));

vi.mock('@/components/common/PageHeader', () => ({
  default: ({ title, description }: { title: string; description: string }) => (
    <header><h1>{title}</h1><p>{description}</p></header>
  ),
}));

vi.mock('@/components/common/LoadingSpinner', () => ({
  default: () => <div>loading</div>,
}));

vi.mock('@/components/common/EmptyState', () => ({
  default: ({ title, action }: { title: string; action?: ReactNode }) => <div>{title}{action}</div>,
}));

vi.mock('./AgentSheet', () => ({
  default: () => null,
}));

function makeAgent(overrides: Record<string, unknown>) {
  return {
    name: 'agent',
    description: 'Agent description',
    mode: 'subagent',
    native: false,
    permission: [],
    options: {},
    skills: [],
    tools: [],
    ...overrides,
  };
}

describe('AgentPage cards', () => {
  it('使用与工作流卡片一致的纯色扁平样式', () => {
    mockUseAgents.mockReturnValue({
      agents: [
        makeAgent({ name: 'rex', nameCn: 'Rex 主智能体', mode: 'primary', native: true, color: '#06b6d4' }),
        makeAgent({
          name: 'analyst',
          nameCn: '分析智能体',
          delegatable: true,
          color: '#ef4444',
          model: { providerID: 'provider', modelID: 'model-x' },
        }),
      ],
      loading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<AgentPage />);

    const nativeCard = screen.getByText('Rex 主智能体').closest('div.group') as HTMLElement;
    const customCard = screen.getByText('分析智能体').closest('div.group') as HTMLElement;

    expect(nativeCard).toHaveClass('bg-white', 'border-gray-200');
    expect(customCard).toHaveClass('bg-white', 'border-gray-200');
    expect(nativeCard.querySelector('[style]')).not.toBeInTheDocument();
    expect(customCard.querySelector('[style]')).not.toBeInTheDocument();
    expect(nativeCard.querySelector('svg')?.parentElement).toHaveClass('bg-gray-100');
    expect(customCard.querySelector('svg')?.parentElement).toHaveClass('bg-gray-100');
    expect(within(nativeCard).getByText('内置')).not.toHaveClass('border');
    expect(within(customCard).getByText('自定义')).not.toHaveClass('border');
    expect(within(customCard).getByText('可委托')).not.toHaveClass('border');
    expect(within(customCard).getByText('model-x').parentElement).not.toHaveClass('rounded-full', 'border');
  });
});
