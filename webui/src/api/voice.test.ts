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
    const payload = { capture_mode: 'raw' as const, hotwords: ['Flocks'] };
    await voiceApi.updateSettings(payload);
    expect(get.mock.calls).toEqual([['/api/voice/status'], ['/api/voice/config']]);
    expect(put).toHaveBeenCalledWith('/api/voice/config', payload);
    const stream = new URL(voiceStreamUrl());
    expect(stream.pathname).toBe('/api/voice/stream');
    expect(stream.host).toBe(window.location.host);
    expect(stream.search).toBe('');
  });
});
