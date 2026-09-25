import { useState } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import VoiceInput from './VoiceInput';
import type { VoicePhase } from '@/voice/voiceSession';

const mocks = vi.hoisted(() => ({ status: vi.fn(), setBusy: vi.fn(), start: vi.fn(), stop: vi.fn(), cancel: vi.fn(), options: vi.fn() }));
let callbacks: { onText: (text: string) => void; onPhase: (phase: VoicePhase) => void; onError: (code: string, message?: string) => void };
vi.mock('@/api/voice', () => ({ voiceApi: { status: mocks.status }, VOICE_STATUS_CHANGED: 'voice-changed' }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/voice/voiceSession', () => ({
  VoiceSession: class {
    constructor(value: typeof callbacks, options: unknown) { callbacks = value; mocks.options(options); }
    start() { mocks.start(); callbacks.onPhase('recording'); }
    stop() { mocks.stop(); callbacks.onPhase('stopping'); }
    cancel() { mocks.cancel(); callbacks.onPhase('idle'); }
  },
}));

function Composer({ sessionId = 's1', disabled = false }: { sessionId?: string; disabled?: boolean }) {
  const [input, setInput] = useState('Existing draft');
  return <><output data-testid="draft">{input}</output><button onClick={() => setInput('External edit')}>Replace draft</button><VoiceInput context={{ input, setInput, sessionId, disabled, setBusy: mocks.setBusy }} /></>;
}

describe('voice input composer extension', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.status.mockResolvedValue({ enabled: true, configured: true, available: true });
    Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
      configurable: true,
      value: function (this: HTMLDialogElement) { this.setAttribute('open', ''); },
    });
    Object.defineProperty(HTMLDialogElement.prototype, 'close', {
      configurable: true,
      value: function (this: HTMLDialogElement) { this.removeAttribute('open'); },
    });
  });

  it.each([
    { enabled: false, configured: false, available: false },
    { enabled: false, configured: true, available: false },
    { enabled: true, configured: true, available: false },
  ])('hides the microphone unless the backend reports it available (%j)', async (status) => {
    mocks.status.mockResolvedValue(status);
    render(<Composer />);
    await act(async () => {});
    expect(screen.queryByRole('button', { name: 'start' })).not.toBeInTheDocument();
    expect(mocks.start).not.toHaveBeenCalled();
  });

  it('preserves the draft, replaces whole transcript updates and releases the submit lock on done', async () => {
    render(<Composer />);
    fireEvent.click(await screen.findByRole('button', { name: 'start' }));
    expect(mocks.setBusy).toHaveBeenCalledWith('flocks-voice', true);
    act(() => callbacks.onText('Hello'));
    expect(screen.getByTestId('draft').textContent).toBe('Existing draft\nHello');
    act(() => callbacks.onText('Hello world'));
    expect(screen.getByTestId('draft').textContent).toBe('Existing draft\nHello world');
    fireEvent.click(screen.getByRole('button', { name: 'stop' }));
    expect(mocks.stop).toHaveBeenCalledOnce();
    act(() => callbacks.onText('Hello world.'));
    act(() => callbacks.onPhase('idle'));
    expect(screen.getByTestId('draft').textContent).toBe('Existing draft\nHello world.');
    expect(mocks.setBusy).toHaveBeenLastCalledWith('flocks-voice', false);
    expect(screen.getByRole('button', { name: 'start' })).toBeInTheDocument();
  });

  it('cancels and keeps edits made by another composer tool', async () => {
    render(<Composer />);
    fireEvent.click(await screen.findByRole('button', { name: 'start' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replace draft' }));
    expect(mocks.cancel).toHaveBeenCalledOnce();
    expect(screen.getByTestId('draft').textContent).toBe('External edit');
  });

  it('cancels on session changes and ignores the previous session transcript', async () => {
    const view = render(<Composer />);
    fireEvent.click(await screen.findByRole('button', { name: 'start' }));
    view.rerender(<Composer sessionId="s2" />);
    expect(mocks.cancel).toHaveBeenCalledOnce();
    act(() => callbacks.onText('late'));
    expect(screen.getByTestId('draft').textContent).toBe('Existing draft');
    expect(mocks.setBusy).toHaveBeenLastCalledWith('flocks-voice', false);
  });

  it('cancels on unmount and on a settings disable notification', async () => {
    const view = render(<Composer />);
    fireEvent.click(await screen.findByRole('button', { name: 'start' }));
    mocks.status.mockResolvedValue({ enabled: false, configured: true, available: false });
    act(() => window.dispatchEvent(new Event('voice-changed')));
    await waitFor(() => expect(mocks.cancel).toHaveBeenCalledOnce());
    expect(screen.queryByRole('button', { name: 'start' })).not.toBeInTheDocument();
    view.unmount();
    expect(mocks.setBusy).toHaveBeenLastCalledWith('flocks-voice', false);
  });

  it('shows permission errors in a portal dialog and retains the draft when dismissed', async () => {
    render(<Composer />);
    fireEvent.click(await screen.findByRole('button', { name: 'start' }));
    act(() => { callbacks.onError('permission'); callbacks.onPhase('idle'); });
    expect(screen.getByRole('alertdialog')).toHaveTextContent('errors.permission');
    expect(screen.getByRole('alertdialog').parentElement).toBe(document.body);
    expect(screen.getByTestId('draft').textContent).toBe('Existing draft');
    fireEvent.click(screen.getByRole('button', { name: 'dismiss' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'start' })).toHaveFocus();
    expect(mocks.start).toHaveBeenCalledOnce();
  });

  it.each(['no_speech', 'audio_too_quiet'])('shows actionable local copy for %s', async (code) => {
    render(<Composer />);
    fireEvent.click(await screen.findByRole('button', { name: 'start' }));
    act(() => { callbacks.onError(code); callbacks.onPhase('idle'); });
    expect(screen.getByRole('alertdialog')).toHaveTextContent(`errors.${code}`);
    expect(screen.getByTestId('draft')).toHaveTextContent('Existing draft');
  });

  it('uses the saved microphone capture mode from the core status response', async () => {
    mocks.status.mockResolvedValue({ enabled: true, configured: true, available: true, capture_mode: 'raw' });
    render(<Composer />);
    fireEvent.click(await screen.findByRole('button', { name: 'start' }));
    expect(mocks.options).toHaveBeenCalledWith({ captureMode: 'raw' });
  });

  it('keeps partial transcription after an authentication failure and only retries on a new click', async () => {
    render(<Composer />);
    fireEvent.click(await screen.findByRole('button', { name: 'start' }));
    act(() => callbacks.onText('Recognized words'));
    act(() => { callbacks.onError('upstream_auth', 'untrusted upstream text'); callbacks.onPhase('idle'); });
    const dialog = screen.getByRole('alertdialog');
    expect(dialog).toHaveTextContent('errors.upstream_auth');
    expect(dialog).not.toHaveTextContent('untrusted upstream text');
    fireEvent(dialog, new Event('cancel', { cancelable: true }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByTestId('draft').textContent).toBe('Existing draft\nRecognized words');
    expect(screen.getByRole('button', { name: 'start' })).toHaveFocus();
    expect(mocks.start).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: 'start' }));
    expect(mocks.start).toHaveBeenCalledTimes(2);
    act(() => callbacks.onText('More words'));
    expect(screen.getByTestId('draft').textContent).toBe('Existing draft\nRecognized words\nMore words');
  });

  it('uses a safe server message or fixed fallback for unknown error codes', async () => {
    render(<Composer />);
    fireEvent.click(await screen.findByRole('button', { name: 'start' }));
    act(() => { callbacks.onError('../../unknown.key', 'The administrator disabled voice input.'); callbacks.onPhase('idle'); });
    expect(screen.getByRole('alertdialog')).toHaveTextContent('The administrator disabled voice input.');
    expect(screen.getByRole('alertdialog')).not.toHaveTextContent('../../unknown.key');
    fireEvent.click(screen.getByRole('button', { name: 'dismiss' }));
    fireEvent.click(screen.getByRole('button', { name: 'start' }));
    act(() => { callbacks.onError('__proto__'); callbacks.onPhase('idle'); });
    expect(screen.getByRole('alertdialog')).toHaveTextContent('errors.server');
  });

  it('removes the portal when switching sessions or unmounting with an error open', async () => {
    const view = render(<Composer />);
    fireEvent.click(await screen.findByRole('button', { name: 'start' }));
    act(() => { callbacks.onError('permission'); callbacks.onPhase('idle'); });
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    view.rerender(<Composer sessionId="s2" />);
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'start' }));
    act(() => { callbacks.onError('permission'); callbacks.onPhase('idle'); });
    view.unmount();
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('stops capture and retains the current draft when the Android app enters the background', async () => {
    render(<Composer />);
    fireEvent.click(await screen.findByRole('button', { name: 'start' }));
    act(() => callbacks.onText('Recorded text'));
    act(() => window.dispatchEvent(new Event('flocks-mobile-background')));
    expect(mocks.cancel).toHaveBeenCalledOnce();
    expect(screen.getByTestId('draft')).toHaveTextContent('Existing draft Recorded text');
    expect(mocks.setBusy).toHaveBeenLastCalledWith('flocks-voice', false);
  });

  it('stops capture only when the page becomes hidden and removes listeners on unmount', async () => {
    const visibility = vi.spyOn(document, 'visibilityState', 'get');
    try {
      const view = render(<Composer />);
      fireEvent.click(await screen.findByRole('button', { name: 'start' }));
      visibility.mockReturnValue('visible');
      act(() => document.dispatchEvent(new Event('visibilitychange')));
      expect(mocks.cancel).not.toHaveBeenCalled();
      visibility.mockReturnValue('hidden');
      act(() => document.dispatchEvent(new Event('visibilitychange')));
      expect(mocks.cancel).toHaveBeenCalledOnce();
      view.unmount();
      mocks.cancel.mockClear();
      act(() => {
        document.dispatchEvent(new Event('visibilitychange'));
        window.dispatchEvent(new Event('flocks-mobile-background'));
      });
      expect(mocks.cancel).not.toHaveBeenCalled();
    } finally {
      visibility.mockRestore();
    }
  });
});

/** The entry point people have to notice before any of the rest matters. */
describe('voice entry affordance in the composer toolbar', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.status.mockResolvedValue({ enabled: true, configured: true, available: true });
  });

  it('carries a visible label, not only an icon', async () => {
    // Its neighbours in the toolbar -- execution mode, agent, model -- all show
    // text. An unlabelled glyph among them reads as decoration, which is what
    // made it hard to find.
    render(<Composer />);
    const button = await screen.findByRole('button', { name: 'start' });
    expect(button).toHaveTextContent('button.start');
  });

  it('keeps the descriptive name for assistive technology and the tooltip', async () => {
    render(<Composer />);
    const button = await screen.findByRole('button', { name: 'start' });
    expect(button).toHaveAttribute('title', 'start');
  });

  it('is a chip whose state the stylesheet can address', async () => {
    render(<Composer />);
    const button = await screen.findByRole('button', { name: 'start' });
    expect(button).toHaveClass('flocks-voice-button');
    expect(button).toHaveAttribute('data-state', 'idle');
  });

  it('says it is recording and offers stopping, with one live marker', async () => {
    render(<Composer />);
    fireEvent.click(await screen.findByRole('button', { name: 'start' }));
    const button = screen.getByRole('button', { name: 'stop' });
    expect(button).toHaveAttribute('data-state', 'recording');
    expect(button).toHaveTextContent('button.stop');
    // One marker for "live", not a pulsing dot next to a stop square.
    expect(button.querySelectorAll('.flocks-voice-button__pulse')).toHaveLength(1);
  });

  it('is disabled while the composer itself is', async () => {
    render(<Composer disabled />);
    expect(await screen.findByRole('button', { name: 'start' })).toBeDisabled();
  });
});
