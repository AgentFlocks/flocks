import { afterEach, describe, expect, it, vi } from 'vitest';
import client from './client';
import { voiceApi, voiceStreamUrl } from './voice';

describe('open-source voice API', () => {
  afterEach(() => vi.restoreAllMocks());

  it('uses core endpoints for status and configuration', async () => {
    const get = vi.spyOn(client, 'get').mockResolvedValue({ data: {} });
    const put = vi.spyOn(client, 'put').mockResolvedValue({ data: {} });
    await voiceApi.status();
    await voiceApi.settings();
    const payload = { capture_mode: 'raw' as const, hotwords: ['Flocks'], recognition_scene: 'security_internet' as const };
    await voiceApi.updateSettings(payload);
    expect(get.mock.calls).toEqual([['/api/voice/status'], ['/api/voice/config']]);
    expect(put).toHaveBeenCalledWith('/api/voice/config', payload);
    const stream = new URL(voiceStreamUrl());
    expect(stream.pathname).toBe('/api/voice/stream');
    expect(stream.host).toBe(window.location.host);
    expect(stream.search).toBe('');
    const reported = new URL(voiceStreamUrl({
      contextRate: 16000, trackRate: 48000, channels: 1,
      echoCancellation: true, noiseSuppression: false, worklet: true, mode: 'voice',
    }));
    expect(Object.fromEntries(reported.searchParams)).toEqual({
      ctx_rate: '16000', track_rate: '48000', channels: '1', aec: '1', ns: '0', worklet: '1', mode: 'voice',
    });
  });

  it.each([undefined, 'general', 'security_internet'] as const)(
    'normalizes scene %s on load and save without changing vocabulary or capture mode',
    async (scene) => {
      const response = {
        enabled: true,
        configured: true,
        available: true,
        credential_source: 'stored', // secret-guard: allow
        resource_id: 'test',
        model: 'test',
        capture_mode: 'raw',
        hotwords: ['内部产品', 'NDR'],
        ...(scene === undefined ? {} : { recognition_scene: scene }),
      };
      vi.spyOn(client, 'get').mockResolvedValue({ data: response });
      vi.spyOn(client, 'put').mockResolvedValue({ data: response });
      const expected = { ...response, recognition_scene: scene ?? 'general' };

      expect(await voiceApi.settings()).toEqual(expected);
      expect(await voiceApi.updateSettings({ recognition_scene: 'security_internet' })).toEqual(expected);
    },
  );
});
