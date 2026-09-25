import { useEffect, useRef, useState } from 'react';
import { IconLoader2, IconMicrophone } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { voiceApi, VOICE_STATUS_CHANGED, type VoiceCaptureMode } from '@/api/voice';
import { VoiceSession, type VoicePhase } from '@/voice/voiceSession';
import VoiceErrorDialog from '@/voice/VoiceErrorDialog';
import './VoiceInput.css';

const BUSY_OWNER = 'flocks-voice';
const LOCAL_ERROR_CODES = new Set([
  'insecure', 'unsupported', 'permission', 'microphone', 'connection', 'timeout', 'backpressure',
  'upstream_auth', 'upstream_forbidden', 'upstream_rate_limited', 'upstream_unavailable',
  'upstream_timeout', 'upstream_closed', 'upstream_rejected',
  'no_speech', 'audio_too_quiet',
]);

interface VoiceInputContext {
  sessionId: string | null | undefined;
  input: string;
  setInput: (value: string) => void;
  disabled: boolean;
  setBusy: (owner: string, busy: boolean) => void;
}

export default function VoiceInput({ context: composer }: { context: VoiceInputContext }) {
  const { t } = useTranslation('voice');
  const current = useRef(composer);
  current.current = composer;
  const [available, setAvailable] = useState(false);
  const [captureMode, setCaptureMode] = useState<VoiceCaptureMode>('voice');
  const [phase, setPhase] = useState<VoicePhase>('idle');
  const [error, setError] = useState('');
  const session = useRef<VoiceSession | null>(null);
  const microphoneRef = useRef<HTMLButtonElement>(null);
  const expectedDraft = useRef(composer.input);
  const active = phase !== 'idle';

  useEffect(() => {
    const stopCapture = () => session.current?.cancel();
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') stopCapture();
    };
    window.addEventListener('flocks-mobile-background', stopCapture);
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      window.removeEventListener('flocks-mobile-background', stopCapture);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, []);

  useEffect(() => {
    let mounted = true;
    let revision = 0;
    const refresh = async () => {
      const request = ++revision;
      try {
        const status = await voiceApi.status();
        if (!mounted || request !== revision) return;
        setAvailable(status.available);
        setCaptureMode(status.capture_mode ?? 'voice');
        if (!status.available) session.current?.cancel();
      } catch {
        if (!mounted || request !== revision) return;
        setAvailable(false);
        session.current?.cancel();
      }
    };
    void refresh();
    window.addEventListener(VOICE_STATUS_CHANGED, refresh);
    window.addEventListener('focus', refresh);
    return () => {
      mounted = false;
      window.removeEventListener(VOICE_STATUS_CHANGED, refresh);
      window.removeEventListener('focus', refresh);
    };
  }, []);

  useEffect(() => {
    setError('');
    return () => {
      session.current?.cancel();
      session.current = null;
      current.current.setBusy(BUSY_OWNER, false);
    };
  }, [composer.sessionId]);

  // A template or another composer control may replace the draft while the
  // textarea is read-only. Preserve that edit and stop writing late transcripts.
  useEffect(() => {
    if (active && (composer.disabled || composer.input !== expectedDraft.current)) {
      session.current?.cancel();
    }
  }, [active, composer.disabled, composer.input]);

  if (!available && !error) return null;

  const start = () => {
    if (composer.disabled || session.current) return;
    const initialDraft = composer.input;
    const sessionId = composer.sessionId;
    expectedDraft.current = initialDraft;
    setError('');
    current.current.setBusy(BUSY_OWNER, true);
    const recording = new VoiceSession({
      onText: (text) => {
        if (current.current.sessionId !== sessionId) return;
        const separator = initialDraft && text && !/\s$/.test(initialDraft) ? '\n' : '';
        const draft = `${initialDraft}${separator}${text}`;
        expectedDraft.current = draft;
        current.current.setInput(draft);
      },
      onPhase: (next) => {
        setPhase(next);
        if (next === 'idle') {
          session.current = null;
          current.current.setBusy(BUSY_OWNER, false);
        }
      },
      onError: (code, message) => {
        setError(LOCAL_ERROR_CODES.has(code)
          ? t(`errors.${code}`)
          : message || t('errors.server'));
      },
    }, { captureMode });
    session.current = recording;
    void recording.start();
  };

  // Two labels: a short one on the button, because the toolbar is crowded,
  // and the descriptive one for assistive technology and the tooltip.
  const label = phase === 'recording' ? t('stop')
    : phase === 'connecting' ? t('cancel')
    : phase === 'stopping' ? t('finishing') : t('start');
  const caption = phase === 'recording' ? t('button.stop')
    : phase === 'connecting' ? t('button.connecting')
    : phase === 'stopping' ? t('button.stopping') : t('button.start');
  const busy = phase === 'connecting' || phase === 'stopping';

  return (
    <div className="relative shrink-0">
      {available && <button
        ref={microphoneRef}
        type="button"
        className="flocks-voice-button"
        data-state={phase}
        aria-label={label}
        title={label}
        aria-pressed={active}
        disabled={phase === 'stopping' || (!active && composer.disabled)}
        onClick={() => { if (active) void session.current?.stop(); else start(); }}
      >
        <span className="flocks-voice-button__icon" aria-hidden="true">
          {busy ? <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
            : phase === 'recording'
              // A pulsing dot says recording is live; the label says what the
              // tap does. A stop square here as well would be two marks for
              // one thing.
              ? <span className="flocks-voice-button__pulse" />
              : <IconMicrophone aria-hidden="true" className="h-4 w-4" />}
        </span>
        <span>{caption}</span>
      </button>}
      <span className="sr-only" role="status">{active ? t(`${phase}`) : ''}</span>
      {error && (
        <VoiceErrorDialog message={error} onClose={() => setError('')} returnFocusRef={microphoneRef} />
      )}
    </div>
  );
}
