import { useEffect, useRef } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ConfirmProvider, requestConfirm } from './ConfirmDialog';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

let showModal: ReturnType<typeof vi.fn>;
const originalShowModal = HTMLDialogElement.prototype.showModal;

beforeEach(() => {
  // jsdom has no top layer; record the call and open the element like a browser would.
  showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.showModal = showModal as unknown as () => void;
});

afterEach(() => {
  HTMLDialogElement.prototype.showModal = originalShowModal;
});

/** A page editor that is itself a native modal dialog, like the group editor. */
function NativeEditor({ onAsk }: { onAsk: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog ref={ref} aria-label="editor">
      <button type="button" onClick={onAsk}>save</button>
    </dialog>
  );
}

describe('ConfirmProvider', () => {
  it('opens as a native modal dialog above another modal dialog and answers once', async () => {
    let answer: Promise<boolean> | undefined;
    render(
      <ConfirmProvider>
        <NativeEditor onAsk={() => { answer = requestConfirm({ title: 'Move', description: 'Move 2 plugins?' }); }} />
      </ConfirmProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'save' }));
    const prompt = await screen.findByRole('alertdialog', { name: 'Move' });
    expect(prompt.tagName).toBe('DIALOG');
    // The editor and then the prompt entered the top layer, so the prompt stacks on top.
    expect(showModal.mock.contexts.at(-1)).toBe(prompt);
    expect(prompt).toHaveAccessibleDescription('Move 2 plugins?');
    expect(screen.getByRole('button', { name: 'button.confirm' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'button.confirm' }));
    await expect(answer).resolves.toBe(true);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('treats Escape, the backdrop and a newer request as cancel', async () => {
    render(<ConfirmProvider><span /></ConfirmProvider>);
    let first: Promise<boolean> | undefined;
    act(() => { first = requestConfirm({ description: 'first' }); });
    let second: Promise<boolean> | undefined;
    act(() => { second = requestConfirm({ description: 'second' }); });
    await expect(first).resolves.toBe(false);
    fireEvent.keyDown(document, { key: 'Escape' });
    await expect(second).resolves.toBe(false);

    let third: Promise<boolean> | undefined;
    act(() => { third = requestConfirm({ description: 'third' }); });
    const prompt = await screen.findByRole('alertdialog');
    fireEvent.mouseDown(prompt);
    await expect(third).resolves.toBe(false);
  });

  it('shows only the confirm button for information prompts', async () => {
    render(<ConfirmProvider><span /></ConfirmProvider>);
    act(() => { void requestConfirm({ description: 'Shortcuts', confirmText: 'Got it', hideCancel: true }); });
    await screen.findByRole('alertdialog');
    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual(['Got it']);
  });
});

it('falls back to the browser prompt without a mounted provider', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  await waitFor(() => expect(requestConfirm({ title: 'T', description: 'D' })).resolves.toBe(true));
  expect(confirm).toHaveBeenCalledWith('T\nD');
  confirm.mockRestore();
});
