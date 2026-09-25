import { useState } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import PageDialog from './PageDialog';

describe('PageDialog', () => {
  it('names the dialog from its heading, contains keyboard focus and returns it to the opener', async () => {
    const user = userEvent.setup();
    function Example() {
      const [open, setOpen] = useState(false);
      return <>
        <button onClick={() => setOpen(true)}>Open</button>
        {open && <PageDialog onClose={() => setOpen(false)}>
          <section><h2>Credentials</h2><input aria-label="Key" /><button>Save</button></section>
        </PageDialog>}
      </>;
    }
    render(<Example />);
    const opener = screen.getByRole('button', { name: 'Open' });
    await user.click(opener);
    const dialog = screen.getByRole('dialog', { name: 'Credentials' });
    const input = within(dialog).getByRole('textbox', { name: 'Key' });
    expect(input).toHaveFocus();
    await user.tab({ shift: true });
    expect(within(dialog).getByRole('button', { name: 'Save' })).toHaveFocus();
    await user.tab();
    expect(input).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it('keeps controls clickable and dismisses only the backdrop; busy dialogs cannot dismiss', () => {
    const close = vi.fn();
    const save = vi.fn();
    const { rerender } = render(<PageDialog aria-label="Edit" onClose={close}>
      <section><button onClick={save}>Save</button></section>
    </PageDialog>);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(save).toHaveBeenCalledOnce();
    expect(close).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('dialog'));
    expect(close).toHaveBeenCalledOnce();
    close.mockClear();
    rerender(<PageDialog aria-label="Edit"><section><button disabled>Saving</button></section></PageDialog>);
    fireEvent.keyDown(document, { key: 'Escape' });
    fireEvent.click(screen.getByRole('dialog'));
    expect(close).not.toHaveBeenCalled();
  });

  it('contains both panels of a tool drawer without blocking its sibling detail controls', async () => {
    const user = userEvent.setup();
    render(<PageDialog variant="panels" className="contents">
      <section><h2>Connection</h2><button>Reconnect</button></section>
      <section><h3>Tool detail</h3><button>Run test</button></section>
    </PageDialog>);
    expect(screen.getByRole('button', { name: 'Reconnect' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: 'Run test' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: 'Reconnect' })).toHaveFocus();
  });
});
