import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import TokenPolicyNotice from './TokenPolicyNotice';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
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
