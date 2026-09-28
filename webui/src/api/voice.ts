import client from '@/api/client';

export interface VoiceStatus {
  enabled: boolean;
  configured: boolean;
  available: boolean;
  capture_mode: VoiceCaptureMode;
}

export type VoiceCaptureMode = 'voice' | 'raw';
export type VoiceRecognitionScene = 'general' | 'security_internet';

export interface VoiceSettings extends VoiceStatus {
  resource_id: string;
  model: string;
  credential_source: 'environment' | 'stored' | 'none'; // secret-guard: allow
  hotwords: string[];
  recognition_scene: VoiceRecognitionScene;
}

export interface VoiceSettingsUpdate {
  api_key?: string;
  enabled?: boolean;
  hotwords?: string[];
  capture_mode?: VoiceCaptureMode;
  recognition_scene?: VoiceRecognitionScene;
}

export const VOICE_STATUS_CHANGED = 'flocks-voice-status-changed';

type VoiceSettingsResponse = Omit<VoiceSettings, 'recognition_scene'> & {
  recognition_scene?: VoiceRecognitionScene;
};

function normalizeSettings(value: VoiceSettingsResponse): VoiceSettings {
  return { ...value, recognition_scene: value.recognition_scene ?? 'general' };
}

export const voiceApi = {
  status: async (): Promise<VoiceStatus> =>
    (await client.get('/api/voice/status')).data,
  settings: async (): Promise<VoiceSettings> =>
    normalizeSettings((await client.get<VoiceSettingsResponse>('/api/voice/config')).data),
  updateSettings: async (payload: VoiceSettingsUpdate): Promise<VoiceSettings> =>
    normalizeSettings((await client.put<VoiceSettingsResponse>('/api/voice/config', payload)).data),
};

/** What the browser granted for recording, reported for server-side diagnostics. */
export interface VoiceCaptureInfo {
  contextRate: number;
  trackRate?: number;
  channels?: number;
  echoCancellation?: boolean;
  noiseSuppression?: boolean;
  autoGainControl?: boolean;
  worklet: boolean;
  mode: VoiceCaptureMode;
}

/** Authentication stays in the same-origin HttpOnly session cookie. */
export function voiceStreamUrl(capture?: VoiceCaptureInfo | null): string {
  const url = new URL('/api/voice/stream', window.location.origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  if (capture) {
    // Capture parameters only, never audio or text. The server records them
    // with a session only while its diagnostics switch is on.
    const flag = (value?: boolean) => (value === undefined ? undefined : value ? '1' : '0');
    const fields: Record<string, string | undefined> = {
      ctx_rate: String(Math.round(capture.contextRate)),
      track_rate: capture.trackRate ? String(Math.round(capture.trackRate)) : undefined,
      channels: capture.channels ? String(capture.channels) : undefined,
      aec: flag(capture.echoCancellation),
      ns: flag(capture.noiseSuppression),
      agc: flag(capture.autoGainControl),
      worklet: flag(capture.worklet),
      mode: capture.mode,
    };
    for (const [key, value] of Object.entries(fields)) {
      if (value !== undefined) url.searchParams.set(key, value);
    }
  }
  return url.toString();
}
