import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import GuidedCreatePanel from './GuidedCreatePanel';

describe('GuidedCreatePanel', () => {
  it('renders guide actions as workflow-style full-width rows', () => {
    render(
      <GuidedCreatePanel
        title="Rex 辅助修改"
        description="选择一个入口开始编辑"
        groups={[{
          title: '编辑引导',
          actions: [
            { label: '从 Rex 提取配置', description: '提取配置', prompt: 'extract' },
            { label: '检查模型策略', description: '检查模型', prompt: 'model' },
            { label: '调整温度', description: '调整温度', prompt: 'temperature' },
            { label: '验证效果', description: '验证效果', prompt: 'verify' },
          ],
        }]}
        onStartPrompt={vi.fn()}
      />,
    );

    const section = screen.getByTestId('guided-create-section-编辑引导');

    expect(section).toHaveClass('flex');
    expect(section).toHaveClass('flex-col');
    expect(section).not.toHaveClass('sm:grid-cols-2');
    expect(screen.getByRole('button', { name: '从 Rex 提取配置' }).parentElement).toHaveClass('w-full');
    expect(screen.getByRole('button', { name: '从 Rex 提取配置' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '验证效果' })).toBeInTheDocument();
  });
});
