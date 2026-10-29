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

it('shows the title and closes from the header button', () => {
  const onClose = vi.fn();
  render(<ChatDialog open onClose={onClose} title="Rex" subtitle="helper" />);
  expect(screen.getByRole('heading', { name: 'Rex' })).toBeInTheDocument();
  expect(screen.getByText('helper')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('heading', { name: 'Rex' }).closest('div.border-b')!.querySelector('button')!);
  expect(onClose).toHaveBeenCalledTimes(1);
});

it('renders nothing while closed', () => {
  const { container } = render(<ChatDialog open={false} onClose={vi.fn()} title="Rex" />);
  expect(container).toBeEmptyDOMElement();
});
