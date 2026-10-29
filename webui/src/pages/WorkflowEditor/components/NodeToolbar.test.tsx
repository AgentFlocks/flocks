import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import NodeToolbar from './NodeToolbar';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe('NodeToolbar', () => {

  it('点击节点类型按钮回调对应类型', async () => {
    const onAddNode = vi.fn();
    render(<NodeToolbar onAddNode={onAddNode} />);
    await userEvent.setup().click(screen.getByRole('button', { name: /^Python/ }));
    expect(onAddNode).toHaveBeenCalledWith('python');
  });
});
