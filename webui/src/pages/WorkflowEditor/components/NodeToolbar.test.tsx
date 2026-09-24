import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import NodeToolbar from './NodeToolbar';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe('NodeToolbar', () => {
  it('锚定在画布容器内而不是视口（否则会压在侧边栏下面点不到）', () => {
    const { container } = render(<NodeToolbar onAddNode={vi.fn()} />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toContain('absolute');
    expect(root.className).not.toContain('fixed');
  });

  it('点击节点类型按钮回调对应类型', async () => {
    const onAddNode = vi.fn();
    render(<NodeToolbar onAddNode={onAddNode} />);
    await userEvent.setup().click(screen.getByRole('button', { name: /^Python/ }));
    expect(onAddNode).toHaveBeenCalledWith('python');
  });
});
