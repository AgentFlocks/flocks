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
        'modelPicker.auto': 'Auto',
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
  it('opens the model menu from the left edge of the trigger', async () => {
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
    expect(menu).toHaveClass('left-0');
    expect(menu).toHaveClass('bottom-full');
    expect(menu).not.toHaveClass('right-0');
    expect(menu).toHaveStyle({ transform: 'translateX(0px)' });
  });

  it('shifts the left-anchored menu only when the right edge would overflow', async () => {
    const user = userEvent.setup();

    render(
      <ChatModelPicker
        groupedOptions={groupedOptions}
        loading={false}
        selectedModelOption={groupedOptions[0].models[0]}
        onSelectModel={vi.fn()}
      />,
    );

    const trigger = screen.getByRole('button', { name: /minimax-m3/i });
    const selector = trigger.closest('[data-model-selector]');
    vi.spyOn(selector!, 'getBoundingClientRect').mockReturnValue({
      bottom: 0,
      height: 0,
      left: 700,
      right: 700,
      top: 0,
      width: 0,
      x: 700,
      y: 0,
      toJSON: () => ({}),
    });
    const originalViewportWidth = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 });

    await user.click(trigger);

    const menu = screen.getByText('选择模型').closest('.absolute');
    expect(menu).toHaveStyle({ transform: 'translateX(-36px)' });
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalViewportWidth });
  });

  it('renders Auto as an opt-in single-line item with its hint in the info tooltip', async () => {
    const user = userEvent.setup();
    const onSelectAuto = vi.fn();

    render(
      <ChatModelPicker
        groupedOptions={groupedOptions}
        loading={false}
        selectedModelOption={groupedOptions[0].models[0]}
        onSelectModel={vi.fn()}
        autoOption={{
          selected: false,
          disabled: false,
          statusLabel: 'Primary then fallback',
          onSelect: onSelectAuto,
        }}
      />,
    );

    await user.click(screen.getByRole('button', { name: /minimax-m3/i }));
    const autoButton = screen.getByRole('button', { name: 'Auto' });
    expect(screen.queryByText('Primary then fallback')).not.toBeInTheDocument();

    const info = autoButton.querySelector('.lucide-info')?.parentElement;
    expect(info).toBeInTheDocument();
    await user.hover(info!);
    expect(await screen.findByText('Primary then fallback')).toBeInTheDocument();
    await user.click(autoButton);
    expect(onSelectAuto).toHaveBeenCalledOnce();
  });

  it('shows only Auto as selected when Auto mode is active', async () => {
    const user = userEvent.setup();

    render(
      <ChatModelPicker
        groupedOptions={groupedOptions}
        loading={false}
        selectedModelOption={groupedOptions[0].models[0]}
        onSelectModel={vi.fn()}
        autoOption={{
          selected: true,
          disabled: false,
          statusLabel: 'Primary then fallback',
          onSelect: vi.fn(),
        }}
      />,
    );

    const trigger = screen.getByRole('button', { name: /^Auto/i });
    expect(trigger).toHaveAttribute('title', 'Auto: Primary then fallback');
    await user.click(trigger);

    expect(screen.getAllByRole('button', { name: 'Auto' })[1]).toHaveClass('shadow-[inset_2px_0_0_#a1a1aa]');
    expect(screen.getByRole('button', { name: /minimax-m3/i })).not.toHaveClass('shadow-[inset_2px_0_0_#a1a1aa]');
  });

  it('does not show Auto unless the caller opts in', async () => {
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
    expect(screen.queryByRole('button', { name: 'Auto' })).not.toBeInTheDocument();
  });
});

describe('useChatModelOptions', () => {
  it('keeps Auto opt-in and clears it when a concrete model is selected', async () => {
    listDefinitionsMock.mockResolvedValue({
      data: { models: [makeModelDefinition()] },
    });
    getResolvedMock.mockResolvedValue({
      data: { provider_id: 'provider-1', model_id: 'model-1' },
    });

    const { result } = renderHook(() => useChatModelOptions({ enableAuto: true }));

    await waitFor(() => {
      expect(result.current.canSelectAuto).toBe(true);
      expect(result.current.selectedModelKey).toBe('provider-1::model-1');
    });
    expect(result.current.selectedModelAuto).toBe(false);
    expect(result.current.selectedPromptModel).toEqual({
      providerID: 'provider-1',
      modelID: 'model-1',
    });

    act(() => result.current.selectAuto());

    expect(result.current.selectedModelAuto).toBe(true);
    expect(result.current.selectedPromptModel).toBeNull();
    expect(result.current.effectiveModelOption).toEqual(result.current.primaryModelOption);

    act(() => result.current.selectModelKey('provider-1::model-1'));

    expect(result.current.selectedModelAuto).toBe(false);
    expect(result.current.selectedPromptModel).toEqual({
      providerID: 'provider-1',
      modelID: 'model-1',
    });
  });

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
