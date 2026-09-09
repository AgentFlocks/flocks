import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import TokenPolicyNotice from './TokenPolicyNotice';
import zhNotification from '@/locales/zh-CN/notification.json';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key === 'tokenPolicyBody' ? zhNotification.tokenPolicyBody : key }),
}));
beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it('reports display after rendering frames, not during mount', async () => {
  const onPresented = vi.fn();
  render(<TokenPolicyNotice onPresented={onPresented} onClose={vi.fn()} />);
  expect(screen.getByRole('dialog', { name: 'tokenPolicyTitle' })).toBeInTheDocument();
  expect(onPresented).not.toHaveBeenCalled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(40);
  });
  expect(onPresented).toHaveBeenCalledTimes(1);
});

it.each(['hidden', 'unmounted'])('does not confirm a card that becomes %s before paint', async (reason) => {
  const onPresented = vi.fn();
  const { unmount } = render(<TokenPolicyNotice onPresented={onPresented} onClose={vi.fn()} />);
  if (reason === 'unmounted') unmount();
  else vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  await act(async () => {
    await vi.advanceTimersByTimeAsync(40);
  });
  expect(onPresented).not.toHaveBeenCalled();
});

it('records presentation before a user immediately closes the card', () => {
  const events: string[] = [];
  render(
    <TokenPolicyNotice onPresented={() => events.push('display')} onClose={() => events.push('close')} />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'gotIt' }));
  expect(events).toEqual(['display', 'close']);
});

it('renders the full announcement as a modal with five policy items and safe links', () => {
  render(<TokenPolicyNotice onPresented={vi.fn()} onClose={vi.fn()} />);
  expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true');
  expect(screen.getByRole('region')).toHaveClass('overflow-y-auto');
  expect(screen.getAllByRole('listitem')).toHaveLength(5);
  expect(screen.getByText(/不统一折算为1000万Token/)).toBeInTheDocument();
  expect(screen.getByText(/2026年9月9日/)).toBeInTheDocument();
  const links = screen.getAllByRole('link');
  expect(links.map((link) => link.getAttribute('href'))).toEqual([
    'https://portal.agentflocks.com', 'https://github.com/AgentFlocks/flocks',
    'https://agentflocks.github.io/flocks-docs/', 'https://portal.agentflocks.com',
  ]);
  for (const link of links) {
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  }
  expect(screen.queryByText(/&#x20;/)).not.toBeInTheDocument();
});

it('traps keyboard focus and restores scrolling and focus on unmount', () => {
  const trigger = document.createElement('button');
  document.body.appendChild(trigger);
  trigger.focus();
  const previousOverflow = document.body.style.overflow;
  const { unmount } = render(<TokenPolicyNotice onPresented={vi.fn()} onClose={vi.fn()} />);
  expect(screen.getByRole('dialog')).toHaveFocus();
  expect(document.body.style.overflow).toBe('hidden');
  const close = screen.getByRole('button', { name: 'close' });
  const confirm = screen.getByRole('button', { name: 'gotIt' });
  close.focus();
  fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
  expect(confirm).toHaveFocus();
  fireEvent.keyDown(window, { key: 'Tab' });
  expect(close).toHaveFocus();
  unmount();
  expect(document.body.style.overflow).toBe(previousOverflow);
  expect(trigger).toHaveFocus();
  trigger.remove();
});
