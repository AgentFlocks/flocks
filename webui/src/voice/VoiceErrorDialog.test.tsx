import { useRef, useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import VoiceErrorDialog from './VoiceErrorDialog';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

function Harness({ message }: { message: string }) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  return <div style={{ overflow: 'hidden', height: 60, width: 100 }}>
    <button ref={trigger} onClick={() => setOpen(true)}>Microphone</button>
    {open && <VoiceErrorDialog message={message} onClose={() => setOpen(false)} returnFocusRef={trigger} />}
  </div>;
}

describe('native voice error dialog', () => {
  const showModal = vi.fn(function (this: HTMLDialogElement) { this.setAttribute('open', ''); });
  const close = vi.fn(function (this: HTMLDialogElement) { this.removeAttribute('open'); });
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: showModal });
    Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: close });
  });

  it('opens in the native top layer outside a clipping composer with an accessible name and body', () => {
    const message = `${'LongContinuousError'.repeat(300)}\n<script>not executable</script>\n最后一行`;
    const view = render(<Harness message={message} />);
    fireEvent.click(screen.getByRole('button', { name: 'Microphone' }));
    const dialog = screen.getByRole('alertdialog', { name: 'errorDialog.title' });
    expect(showModal).toHaveBeenCalledOnce();
    expect(dialog.tagName).toBe('DIALOG');
    expect(dialog.parentElement).toBe(document.body);
    expect(view.container).not.toContainElement(dialog);
    expect(dialog).toHaveAttribute('data-flocks-voice-error');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    const body = document.getElementById(dialog.getAttribute('aria-describedby')!);
    expect(body).toHaveClass('flocks-voice-error-body');
    expect(body).toHaveAttribute('tabindex', '0');
    expect(body?.querySelector('p')?.textContent).toBe(message);
    expect(dialog.querySelector('script')).toBeNull();
    expect(screen.getByRole('button', { name: 'errorDialog.close' })).toHaveFocus();
    expect(dialog.querySelector('footer')).toContainElement(screen.getByRole('button', { name: 'dismiss' }));
  });

  it.each(['header', 'footer', 'escape'])('closes by %s and restores microphone focus without retrying', (method) => {
    render(<Harness message="Recognition failed" />);
    const microphone = screen.getByRole('button', { name: 'Microphone' });
    fireEvent.click(microphone);
    if (method === 'escape') fireEvent(screen.getByRole('alertdialog'), new Event('cancel', { cancelable: true }));
    else fireEvent.click(screen.getByRole('button', { name: method === 'header' ? 'errorDialog.close' : 'dismiss' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(close).toHaveBeenCalledOnce();
    expect(microphone).toHaveFocus();
    expect(showModal).toHaveBeenCalledOnce();
  });

  it('closes the native modal when the composer unmounts', () => {
    const view = render(<Harness message="Recognition failed" />);
    fireEvent.click(screen.getByRole('button', { name: 'Microphone' }));
    view.unmount();
    expect(close).toHaveBeenCalledOnce();
    expect(document.querySelector('[data-flocks-voice-error]')).toBeNull();
  });
});
