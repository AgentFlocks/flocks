import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  __resetChatModelOptionsResourcesForTesting,
  ChatModelPicker,
  useChatModelOptions,
  type ChatModelProviderGroup,
} from './ChatPromptSelectors';

const { listDefinitionsMock, getResolvedMock, useProvidersMock } = vi.hoisted(() => ({
  listDefinitionsMock: vi.fn(),
  getResolvedMock: vi.fn(),
  useProvidersMock: vi.fn(),
}));

vi.mock('@/api/provider', () => ({
  modelV2API: {
    listDefinitions: listDefinitionsMock,
  },
  defaultModelAPI: {
    getResolved: getResolvedMock,
  },
}));

vi.mock('@/hooks/useProviders', () => ({
  useProviders: useProvidersMock,
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const translations: Record<string, string> = {
        'modelPicker.title': '选择模型',
        'modelPicker.hint': '作为本次对话发送时的模型覆盖',
        'modelPicker.empty': '暂无模型',
        'modelPicker.count': `${params?.count ?? 0}`,
        'modelPicker.vision': '视觉',
        loading: '加载中',
      };
      return translations[key] ?? key;
    },
  }),
}));

const groupedOptions: ChatModelProviderGroup[] = [
  {
    providerID: 'minimax',
    providerName: 'Minimax',
    models: [
      {
        key: 'minimax::minimax-m3',
        providerID: 'minimax',
        providerName: 'Minimax',
        modelID: 'minimax-m3',
        label: 'minimax-m3',
        pricingLabel: 'free',
        contextLabel: '128k',
        contextWindowTokens: 128000,
        supportsVision: false,
      },
    ],
  },
];

function makeProvider(id: string) {
  return {
    id,
    name: id,
    source: 'builtin',
    env: [],
    key: null,
    options: {},
    models: {},
    configured: true,
    modelCount: 1,
    category: 'connected',
  };
}

function makeModelDefinition(providerId = 'provider-1', modelId = 'model-1') {
  return {
    id: modelId,
    name: modelId,
    provider_id: providerId,
    model_type: 'chat',
    status: 'active',
    capabilities: { supports_vision: false },
    limits: { context_window: 128000 },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  __resetChatModelOptionsResourcesForTesting();
  useProvidersMock.mockReturnValue({
    providers: [makeProvider('provider-1')],
    loading: false,
    error: null,
    connectedIds: ['provider-1'],
    refetch: vi.fn(),
  });
});

describe('ChatModelPicker', () => {
  it('opens the model menu toward the left edge of the trigger', async () => {
    const user = userEvent.setup();

    render(
      <ChatModelPicker
        groupedOptions={groupedOptions}
        loading={false}
        selectedModelOption={groupedOptions[0].models[0]}
        onSelectModel={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: /minimax-m3/i }));

    const menu = screen.getByText('选择模型').closest('.absolute');
    expect(menu).not.toBeNull();
    expect(menu).toHaveClass('right-0');
    expect(menu).toHaveClass('bottom-full');
    expect(menu).not.toHaveClass('left-0');
  });
});

describe('useChatModelOptions', () => {
  it('shares enabled model and default model requests across concurrent hook instances', async () => {
    let resolveDefinitions: (value: { data: { models: any[] } }) => void = () => {};
    listDefinitionsMock.mockReturnValue(new Promise((resolve) => {
      resolveDefinitions = resolve;
    }));
    getResolvedMock.mockResolvedValue({
      data: { provider_id: 'provider-1', model_id: 'model-1' },
    });

    const first = renderHook(() => useChatModelOptions());
    const second = renderHook(() => useChatModelOptions());

    expect(listDefinitionsMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveDefinitions({
        data: { models: [makeModelDefinition()] },
      });
    });

    await waitFor(() => {
      expect(first.result.current.loading).toBe(false);
      expect(second.result.current.loading).toBe(false);
      expect(first.result.current.options).toHaveLength(1);
      expect(second.result.current.options).toHaveLength(1);
    });

    await waitFor(() => {
      expect(first.result.current.selectedModelKey).toBe('provider-1::model-1');
      expect(second.result.current.selectedModelKey).toBe('provider-1::model-1');
    });
    expect(getResolvedMock).toHaveBeenCalledTimes(1);
  });
});
