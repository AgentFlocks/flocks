import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { PermissionApprovalDialog } from './PermissionApprovalDialog';

const request = {
  id: 'permission-1',
  sessionID: 'session-1',
  messageID: '',
  toolID: 'bash',
  permission: 'bash',
  patterns: ['bash:canonical:abc123'],
  always: [],
  metadata: { command: 'git add .' },
  time: { created: 1 },
};

describe('PermissionApprovalDialog', () => {
  it('shows the policy request details and allows a one-time decision', async () => {
    const user = userEvent.setup();
    const onReply = vi.fn();
    render(<PermissionApprovalDialog request={request} onReply={onReply} />);

    expect(screen.getByRole('alertdialog')).toHaveTextContent('$ git add .');
    await user.click(screen.getByRole('button', { name: '允许一次' }));

    expect(onReply).toHaveBeenCalledWith('allow');
  });

  it('uses the generic always-allow label for non-network requests', async () => {
    const user = userEvent.setup();
    const onReply = vi.fn();
    render(<PermissionApprovalDialog request={request} onReply={onReply} />);

    expect(screen.queryByRole('button', { name: '始终允许此目标' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '始终允许' }));
    await user.click(screen.getByRole('button', { name: '拒绝' }));

    expect(onReply).toHaveBeenNthCalledWith(1, 'always');
    expect(onReply).toHaveBeenNthCalledWith(2, 'deny');
  });

  it('shows network tool trust action when metadata enables it', async () => {
    const user = userEvent.setup();
    const onReply = vi.fn();
    render(
      <PermissionApprovalDialog
        request={{
          ...request,
          metadata: { network_confirm_options: ['allow', 'trust_network_target', 'trust_tool_network', 'deny'] },
        }}
        onReply={onReply}
      />,
    );

    await user.click(screen.getByRole('button', { name: '始终允许此目标' }));
    expect(onReply).toHaveBeenNthCalledWith(1, 'trust_network_target');

    await user.click(screen.getByRole('button', { name: '信任此工具的网络访问' }));
    expect(onReply).toHaveBeenNthCalledWith(2, 'trust_tool_network');
  });

  it('treats Escape as a rejection', async () => {
    const user = userEvent.setup();
    const onReply = vi.fn();
    render(<PermissionApprovalDialog request={request} onReply={onReply} />);

    await user.keyboard('{Escape}');

    expect(onReply).toHaveBeenCalledWith('deny');
  });
});
