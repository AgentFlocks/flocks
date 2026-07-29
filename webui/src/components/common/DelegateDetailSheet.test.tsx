import { render } from '@testing-library/react';
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
