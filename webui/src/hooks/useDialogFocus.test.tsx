import { StrictMode, useRef, useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { useDialogFocus } from './useDialogFocus';
import { DialogFocusProvider } from '@/contexts/DialogFocusContext';

function Example({ busy = false, nested = false }: { busy?: boolean; nested?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useDialogFocus(ref, open, busy ? undefined : () => setOpen(false));
  return <>
    <button onClick={() => setOpen(true)}>{nested ? 'Open child' : 'Open dialog'}</button>
    {open && <div ref={ref} tabIndex={-1} role="dialog" aria-label={nested ? 'Child' : 'Parent'}>
      <div style={{ display: 'none' }}><button>Hidden</button></div>
      <button disabled>Disabled</button>
      <button onClick={() => setOpen(false)}>Cancel</button>
      {!nested && <Example nested />}
      <button>Last action</button>
    </div>}
  </>;
}

describe('modal keyboard contract', () => {
  it('restores the pointer trigger when WebKit leaves focus on a different control', async () => {
    const user = userEvent.setup();
    render(<DialogFocusProvider><button>Other control</button><Example /></DialogFocusProvider>);
    const other = screen.getByRole('button', { name: 'Other control' });
    const trigger = screen.getByRole('button', { name: 'Open dialog' });
    other.focus();
    fireEvent(trigger, new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
    fireEvent.click(trigger);
    await user.keyboard('{Escape}');
    expect(trigger).toHaveFocus();
  });

  it('restores a nested pointer trigger without closing or unlocking the parent dialog', async () => {
    const user = userEvent.setup();
    render(<DialogFocusProvider><Example /></DialogFocusProvider>);
    await user.click(screen.getByRole('button', { name: 'Open dialog' }));
    const childTrigger = screen.getByRole('button', { name: 'Open child' });
    fireEvent(childTrigger, new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
    fireEvent.click(childTrigger);
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog', { name: 'Parent' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: 'Child' })).not.toBeInTheDocument();
    expect(childTrigger).toHaveFocus();
    expect(document.body.style.overflow).toBe('hidden');
  });

  it('does not reuse an old BODY pointer activation for a later automatic dialog', () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000);
    function AutomaticDialog({ open }: { open: boolean }) {
      const ref = useRef<HTMLDivElement>(null);
      useDialogFocus(ref, open);
      return open ? <div ref={ref} tabIndex={-1} role="dialog"><button>Automatic action</button></div> : null;
    }
    const content = (open: boolean) => <DialogFocusProvider>
      <button>Earlier pointer target</button><AutomaticDialog open={open} />
    </DialogFocusProvider>;
    try {
      const { rerender } = render(content(false));
      const oldTrigger = screen.getByRole('button', { name: 'Earlier pointer target' });
      fireEvent(oldTrigger, new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
      expect(document.body).toHaveFocus();
      now.mockReturnValue(61_000);
      rerender(content(true));
      expect(screen.getByRole('button', { name: 'Automatic action' })).toHaveFocus();
      rerender(content(false));
      expect(oldTrigger).not.toHaveFocus();
      expect(document.body).toHaveFocus();
    } finally {
      now.mockRestore();
    }
  });

  it('uses the current focus when it has moved since a previous pointer activation', async () => {
    const user = userEvent.setup();
    render(<DialogFocusProvider><button>Earlier pointer target</button><Example /></DialogFocusProvider>);
    const oldTrigger = screen.getByRole('button', { name: 'Earlier pointer target' });
    const trigger = screen.getByRole('button', { name: 'Open dialog' });
    fireEvent(oldTrigger, new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
    trigger.focus();
    fireEvent.click(trigger);
    await user.keyboard('{Escape}');
    expect(trigger).toHaveFocus();
  });

  it('cleans up pointer capture listeners across StrictMode remounts', () => {
    const add = vi.spyOn(document, 'addEventListener');
    const remove = vi.spyOn(document, 'removeEventListener');
    const { unmount } = render(<StrictMode><DialogFocusProvider><Example /></DialogFocusProvider></StrictMode>);
    unmount();
    const registrations = add.mock.calls.filter(([type, , capture]) => (
      capture === true && (type === 'pointerdown' || type === 'keydown')
    ));
    expect(registrations.length).toBeGreaterThan(0);
    for (const [type, listener, capture] of registrations) {
      expect(remove).toHaveBeenCalledWith(type, listener, capture);
    }
    add.mockRestore();
    remove.mockRestore();
  });

  it('focuses a visible control, loops Tab, and restores trigger focus after Escape', async () => {
    const user = userEvent.setup();
    render(<Example />);
    const trigger = screen.getByRole('button', { name: 'Open dialog' });
    await user.click(trigger);
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByRole('button', { name: 'Last action' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(document.body.style.overflow).not.toBe('hidden');
  });

  it('closes only the topmost dialog and keeps the background locked until the parent closes', async () => {
    const user = userEvent.setup();
    render(<Example />);
    await user.click(screen.getByRole('button', { name: 'Open dialog' }));
    const childTrigger = screen.getByRole('button', { name: 'Open child' });
    await user.click(childTrigger);
    expect(within(screen.getByRole('dialog', { name: 'Child' })).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Child' })).not.toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'Parent' })).toBeInTheDocument();
    expect(childTrigger).toHaveFocus();
    expect(document.body.style.overflow).toBe('hidden');
  });

  it('keeps a busy dialog open on Escape and redirects background focus into the dialog', async () => {
    const user = userEvent.setup();
    render(<Example busy />);
    const trigger = screen.getByRole('button', { name: 'Open dialog' });
    await user.click(trigger);
    await user.keyboard('{Escape}');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    trigger.focus();
    fireEvent.focusIn(trigger);
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus();
  });
});
