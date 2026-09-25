import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import DelegateDetailSheet from './DelegateDetailSheet';

const { sessionChatPropsMock } = vi.hoisted(() => ({
  sessionChatPropsMock: vi.fn(),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

vi.mock('./SessionChat', () => ({
  default: (props: Record<string, unknown>) => {
    sessionChatPropsMock(props);
    return null;
  },
}));

describe('DelegateDetailSheet', () => {
  it('uses the default drawer width and restores focus after one Escape close', () => {
    localStorage.removeItem('delegate-sheet-width');
    const trigger = document.createElement('button');
    document.body.appendChild(trigger);
    trigger.focus();
    const onClose = vi.fn();
    const props = { onClose, sessionId: 'child', agentName: 'Agent', description: 'Task', status: 'running' };
    const { rerender } = render(<DelegateDetailSheet {...props} open />);
    expect(screen.getByRole('dialog', { name: 'Agent' })).toHaveStyle({ width: '480px' });
    const close = screen.getByRole('button', { name: 'button.close' });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    rerender(<DelegateDetailSheet {...props} open={false} />);
    expect(trigger).toHaveFocus();
    trigger.remove();
  });

  it('uses the session-management process timeline inside the child conversation', () => {
    render(
      <DelegateDetailSheet
        open
        onClose={vi.fn()}
        sessionId="ses-child"
        agentName="Librarian"
        description="调研 OpenClaw 最新版本"
        status="running"
      />,
    );

    expect(sessionChatPropsMock).toHaveBeenCalledWith(expect.objectContaining({
      sessionId: 'ses-child',
      live: true,
      hideInput: true,
      agentName: 'Librarian',
      display: {
        compact: false,
        pageCanvas: true,
        showTimestamp: true,
        collapseIntermediateSteps: true,
        processGroupsDefaultOpen: false,
        processGroupsOpenWhileActive: true,
      },
    }));
  });
});
