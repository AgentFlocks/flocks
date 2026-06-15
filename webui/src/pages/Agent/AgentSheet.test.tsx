import { render } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import AgentSheet from './AgentSheet';
import type { Agent } from '@/api/agent';

const {
  capturedEntitySheetProps,
  mockUseRexComposerControls,
  mockProviderList,
  mockDefaultModelGetResolved,
  mockModelListDefinitions,
  mockToolList,
  mockSkillList,
} = vi.hoisted(() => ({
  capturedEntitySheetProps: [] as any[],
  mockUseRexComposerControls: vi.fn(),
  mockProviderList: vi.fn(),
  mockDefaultModelGetResolved: vi.fn(),
  mockModelListDefinitions: vi.fn(),
  mockToolList: vi.fn(),
  mockSkillList: vi.fn(),
}));

vi.mock('@/components/common/EntitySheet', () => ({
  default: (props: any) => {
    capturedEntitySheetProps.push(props);
    return <div data-testid="entity-sheet">{props.rexGuidePanelTitle}</div>;
  },
  useEntitySheet: () => ({
    openRex: vi.fn(),
    openTest: vi.fn(),
  }),
}));

vi.mock('@/components/common/useRexComposerControls', () => ({
  useRexComposerControls: mockUseRexComposerControls,
}));

vi.mock('@/api/provider', () => ({
  providerAPI: { list: mockProviderList },
  defaultModelAPI: { getResolved: mockDefaultModelGetResolved },
  modelV2API: { listDefinitions: mockModelListDefinitions },
}));

vi.mock('@/api/tool', () => ({
  toolAPI: { list: mockToolList },
}));

vi.mock('@/api/skill', () => ({
  skillAPI: { list: mockSkillList },
}));

vi.mock('@/api/agent', () => ({
  agentAPI: {
    update: vi.fn(),
    updateModel: vi.fn(),
    test: vi.fn(),
  },
}));

vi.mock('@/api/session', () => ({
  sessionApi: {
    getMessages: vi.fn(),
  },
}));

vi.mock('@/api/client', () => ({
  default: {
    post: vi.fn(),
  },
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const translations: Record<string, unknown> = {
        'create.guideSectionTitle': '创建引导',
        'create.caseSectionTitle': '创建案例',
        'create.guideActions': [
          {
            label: '如何创建子 Agent',
            description: '创建说明',
            prompt: '创建子 Agent',
          },
        ],
        'create.caseActions': [],
        'create.guidePanelTitle': 'Rex 辅助创建',
        'create.guidePanelDesc': '创建描述',
        'create.emptyStateTitle': '暂无创建对话',
        'edit.guideSectionTitle': '编辑引导',
        'edit.caseSectionTitle': '编辑案例',
        'edit.guideActions': [
          {
            label: '优化当前 Agent',
            description: '审视当前配置',
            prompt: `编辑 ${String(params?.name ?? '')}`,
          },
          {
            label: '验证效果',
            description: '设计测试输入',
            prompt: `验证 ${String(params?.name ?? '')}`,
          },
        ],
        'edit.caseActions': [
          {
            label: '变得更保守',
            description: '降低风险',
            prompt: `保守 ${String(params?.name ?? '')}`,
          },
        ],
        'edit.guidePanelTitle': 'Rex 辅助修改',
        'edit.guidePanelDesc': '编辑描述',
        'edit.nativeGuidePanelDesc': '内置编辑描述',
        'edit.nativeGuideActions': [
          {
            label: '检查模型策略',
            description: '检查模型',
            prompt: `模型 ${String(params?.name ?? '')}`,
          },
          {
            label: '调整温度',
            description: '调整温度',
            prompt: `温度 ${String(params?.name ?? '')}`,
          },
        ],
        'edit.nativeCaseActions': [
          {
            label: '提升响应效率',
            description: '提升效率',
            prompt: `效率 ${String(params?.name ?? '')}`,
          },
        ],
        'edit.emptyStateTitle': '暂无编辑对话',
        'sheet.done': '完成',
      };
      return translations[key] ?? key;
    },
  }),
}));

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    name: 'audit-agent',
    nameCn: '审计 Agent',
    description: 'Reviews code changes',
    descriptionCn: '审计代码变更',
    mode: 'subagent',
    native: false,
    permission: [],
    prompt: 'Review code safely.',
    temperature: 0.3,
    options: {},
    tools: ['query_ioc'],
    skills: ['agent-builder'],
    ...overrides,
  };
}

describe('AgentSheet', () => {
  beforeEach(() => {
    capturedEntitySheetProps.length = 0;
    mockUseRexComposerControls.mockReturnValue({
      rexAgentName: 'rex',
      rexMentionAgents: [{ name: 'rex' }],
      rexModel: { providerID: 'minimax', modelID: 'minimax-m3' },
      rexSupportsVision: false,
      rexContextWindowTokens: 8192,
    });
    mockProviderList.mockResolvedValue({ data: { connected: [], all: [] } });
    mockDefaultModelGetResolved.mockResolvedValue({ data: {} });
    mockModelListDefinitions.mockResolvedValue({ data: { models: [] } });
    mockToolList.mockResolvedValue({ data: [] });
    mockSkillList.mockResolvedValue({ data: [] });
  });

  it('uses an edit guide experience when editing an agent', () => {
    render(
      <AgentSheet
        agent={makeAgent()}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );

    const props = capturedEntitySheetProps.at(-1);
    expect(props.mode).toBe('edit');
    expect(props.initialTab).toBe('rex');
    expect(props.hideForm).toBe(false);
    expect(props.rexGuidePanelTitle).toBe('Rex 辅助修改');
    expect(props.rexGuidePanelDesc).toBe('编辑描述');
    expect(props.rexGuideEmptyTitle).toBe('暂无编辑对话');
    expect(props.rexGuideGroups).toEqual([
      {
        title: '编辑引导',
        actions: [
          {
            label: '优化当前 Agent',
            description: '审视当前配置',
            prompt: '编辑 audit-agent',
            group: '编辑引导',
          },
          {
            label: '验证效果',
            description: '设计测试输入',
            prompt: '验证 audit-agent',
            group: '编辑引导',
          },
        ],
      },
      {
        title: '编辑案例',
        actions: [
          {
            label: '变得更保守',
            description: '降低风险',
            prompt: '保守 audit-agent',
            group: '编辑案例',
          },
        ],
      },
    ]);
    expect(props.rexAgentName).toBe('rex');
    expect(props.onExtractFromRex).toEqual(expect.any(Function));
    expect(props.onRunTest).toBeUndefined();
    expect(props.rexSystemContext).toContain('Agent 编辑引导助手');
    expect(props.rexSystemContext).toContain('Tools：query_ioc');
    expect(props.rexSystemContext).toContain('Skills：agent-builder');
  });

  it('uses a model and temperature guide when editing a native agent', () => {
    render(
      <AgentSheet
        agent={makeAgent({
          name: 'device-inspector',
          native: true,
          model: { providerID: 'minimax', modelID: 'minimax-m3' },
          tools: ['device_query'],
          skills: ['agent-builder'],
        })}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );

    const props = capturedEntitySheetProps.at(-1);
    expect(props.mode).toBe('edit');
    expect(props.initialTab).toBe('rex');
    expect(props.rexGuidePanelDesc).toBe('内置编辑描述');
    expect(props.rexGuideGroups).toEqual([
      {
        title: '编辑引导',
        actions: [
          {
            label: '检查模型策略',
            description: '检查模型',
            prompt: '模型 device-inspector',
            group: '编辑引导',
          },
          {
            label: '调整温度',
            description: '调整温度',
            prompt: '温度 device-inspector',
            group: '编辑引导',
          },
        ],
      },
      {
        title: '编辑案例',
        actions: [
          {
            label: '提升响应效率',
            description: '提升效率',
            prompt: '效率 device-inspector',
            group: '编辑案例',
          },
        ],
      },
    ]);
    expect(props.rexSystemContext).toContain('只能保存模型和温度');
    expect(props.rexSystemContext).toContain('不要建议提取或覆盖这些字段');
    expect(props.rexWelcomeMessage).toContain('当前只支持保存模型和温度');
  });
});
