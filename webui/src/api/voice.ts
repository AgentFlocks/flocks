import client from '@/api/client';

export interface VoiceStatus {
  enabled: boolean;
  configured: boolean;
  available: boolean;
  capture_mode: VoiceCaptureMode;
}

export type VoiceCaptureMode = 'voice' | 'raw';

export interface VoiceSettings extends VoiceStatus {
  resource_id: string;
  model: string;
  credential_source: 'environment' | 'stored' | 'none'; // secret-guard: allow
  hotwords: string[];
}

export interface VoiceSettingsUpdate {
  api_key?: string;
  enabled?: boolean;
  hotwords?: string[];
  capture_mode?: VoiceCaptureMode;
}

export const VOICE_STATUS_CHANGED = 'flocks-voice-status-changed';

export const voiceApi = {
  status: async (): Promise<VoiceStatus> =>
    (await client.get('/api/voice/status')).data,
  settings: async (): Promise<VoiceSettings> =>
    (await client.get('/api/voice/config')).data,
  updateSettings: async (payload: VoiceSettingsUpdate): Promise<VoiceSettings> =>
    (await client.put('/api/voice/config', payload)).data,
};

/** Authentication stays in the same-origin HttpOnly session cookie. */
export function voiceStreamUrl(): string {
  const url = new URL('/api/voice/stream', window.location.origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  return url.toString();
}
