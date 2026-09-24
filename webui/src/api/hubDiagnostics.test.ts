import { afterEach, describe, expect, it, vi } from 'vitest';
import { AxiosError } from 'axios';
import client from './client';
import { diagnosticHubGet } from './hubDiagnostics';

afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

describe('Hub diagnostic request timings', () => {
  it('preserves result and parameters and logs nothing by default', async () => {
    const result = { data: ['private'], status: 200, headers: {} };
    const get = vi.spyOn(client, 'get').mockResolvedValue(result);
    const info = vi.spyOn(console, 'info').mockImplementation(() => {});
    expect(await diagnosticHubGet('/api/hub/catalog', { params: { q: 'private' } })).toBe(result);
    expect(get).toHaveBeenCalledWith('/api/hub/catalog', expect.objectContaining({
      params: { q: 'private' }, headers: { 'X-Flocks-Hub-Request-Id': expect.any(String) },
    }));
    expect(info).not.toHaveBeenCalled();
  });

  it('correlates start and response without logging payloads or filters', async () => {
    localStorage.setItem('flocks.hubDiagnostics', '1');
    vi.spyOn(client, 'get').mockResolvedValue({ status: 200, headers: { 'x-flocks-hub-trace-id': 'server-trace' }, data: 'secret-result' });
    const info = vi.spyOn(console, 'info').mockImplementation(() => {});
    await diagnosticHubGet('/api/hub/catalog', { params: { q: 'secret-query' } });
    expect(info).toHaveBeenCalledTimes(2);
    expect(info.mock.calls[0][1].client_id).toBe(info.mock.calls[1][1].client_id);
    expect(info.mock.calls[1][1].trace_id).toBe('server-trace');
    expect(JSON.stringify(info.mock.calls)).not.toContain('secret');
  });

  it('records timeout code and rethrows the original error', async () => {
    localStorage.setItem('flocks.hubDiagnostics', '1');
    const error = new AxiosError('secret-url and token', 'ECONNABORTED');
    vi.spyOn(client, 'get').mockRejectedValue(error);
    const info = vi.spyOn(console, 'info').mockImplementation(() => {});
    await expect(diagnosticHubGet('/api/hub/scene-suites')).rejects.toBe(error);
    expect(info.mock.calls[1][1].code).toBe('ECONNABORTED');
    expect(JSON.stringify(info.mock.calls)).not.toContain('secret');
  });

  it('continues when localStorage is unavailable', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('disabled'); });
    vi.spyOn(client, 'get').mockResolvedValue({ status: 200, headers: {} });
    await expect(diagnosticHubGet('/api/hub/categories')).resolves.toMatchObject({ status: 200 });
  });
});
