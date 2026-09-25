import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ConfirmProvider, useConfirm } from './ConfirmDialog';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

function Trigger({ completed }: { completed: (result: boolean) => void }) {
  const confirm = useConfirm();
  return <button onClick={async () => completed(await confirm({ title: 'Confirm change', description: 'Keep existing data.', variant: 'danger' }))}>Open confirmation</button>;
}

describe('confirmation dialog', () => {
  it('exposes a labelled dialog, cancels on Escape, and returns focus', async () => {
    const user = userEvent.setup();
    const completed = vi.fn();
    render(<ConfirmProvider><Trigger completed={completed} /></ConfirmProvider>);
    const trigger = screen.getByRole('button', { name: 'Open confirmation' });
    await user.click(trigger);
    expect(screen.getByRole('dialog', { name: 'Confirm change' })).toHaveAccessibleDescription('Keep existing data.');
    await user.keyboard('{Escape}');
    expect(completed).toHaveBeenCalledWith(false);
    expect(trigger).toHaveFocus();
  });

  it('keeps affirmative confirmation explicit', async () => {
    const user = userEvent.setup();
    const completed = vi.fn();
    render(<ConfirmProvider><Trigger completed={completed} /></ConfirmProvider>);
    await user.click(screen.getByRole('button', { name: 'Open confirmation' }));
    await user.click(screen.getByRole('button', { name: 'button.confirm' }));
    expect(completed).toHaveBeenCalledWith(true);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
