import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ChatDialog from './ChatDialog';

const session = vi.hoisted(() => ({
  sessionId: null,
  createAndSend: vi.fn(),
  consumePendingOptimisticMessage: vi.fn(),
  reset: vi.fn(),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/hooks/useSessionChat', () => ({ useSessionChat: () => session }));
vi.mock('@/hooks/useDefaultModelVision', () => ({ useDefaultModelVision: () => false }));
vi.mock('./SessionChat', () => ({ default: () => <><textarea aria-label="Message" /><button>Send</button></> }));

it('contains keyboard navigation and returns focus to the chat trigger', () => {
  const trigger = document.createElement('button');
  document.body.appendChild(trigger);
  trigger.focus();
  const onClose = vi.fn();
  const { rerender } = render(<ChatDialog open onClose={onClose} title="Rex conversation" />);
  expect(screen.getByRole('dialog', { name: 'Rex conversation' })).toHaveAttribute('aria-modal', 'true');
  const close = screen.getByRole('button', { name: 'button.close' });
  const send = screen.getByRole('button', { name: 'Send' });
  expect(close).toHaveFocus();
  fireEvent.keyDown(close, { key: 'Tab', shiftKey: true });
  expect(send).toHaveFocus();
  fireEvent.keyDown(send, { key: 'Tab' });
  expect(close).toHaveFocus();
  fireEvent.keyDown(close, { key: 'Escape' });
  expect(onClose).toHaveBeenCalledTimes(1);
  rerender(<ChatDialog open={false} onClose={onClose} title="Rex conversation" />);
  expect(trigger).toHaveFocus();
  trigger.remove();
});
