import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ChatModelPicker, type ChatModelProviderGroup } from './ChatPromptSelectors';

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
