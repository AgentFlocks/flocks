import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { PermissionApprovalDialog } from './PermissionApprovalDialog';

// Mirror of zh-CN/session.json chat.permission so the assertions keep reading
// like what users see; the component itself is now translated.
const zh: Record<string, string> = {
  'chat.permission.title': '需要确认执行',
  'chat.permission.deny': '拒绝',
  'chat.permission.always': '始终允许',
  'chat.permission.alwaysTarget': '始终允许此目标',
  'chat.permission.trustToolNetwork': '信任此工具的网络访问',
  'chat.permission.allowOnce': '允许一次',
  'chat.permission.replyFailed': '提交确认失败，请重试。',
};
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => zh[key] ?? key, i18n: { language: 'zh-CN' } }),
}));

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

  it('shows the tool and the path for a file edit request', () => {
    render(
      <PermissionApprovalDialog
        request={{
          ...request,
          toolID: 'write',
          permission: 'edit',
          patterns: ['markers/ask.marker'],
          metadata: { tool: 'write', filepath: '/work/markers/ask.marker' },
        }}
        onReply={vi.fn()}
      />,
    );

    expect(screen.getByRole('alertdialog')).toHaveTextContent('$ write markers/ask.marker');
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

  it('takes every visible label from the session namespace (no hardcoded Chinese)', async () => {
    const source = (await import('./PermissionApprovalDialog.tsx?raw')).default as string;
    // The English UI used to show 需要确认执行/拒绝/始终允许/允许一次 regardless of language.
    expect(source).not.toMatch(/[\u4e00-\u9fff]/);
    for (const key of ['title', 'deny', 'always', 'alwaysTarget', 'trustToolNetwork', 'allowOnce']) {
      expect(source).toContain(`t('chat.permission.${key}')`);
    }
  });
});
