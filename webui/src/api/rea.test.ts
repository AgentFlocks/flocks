import { beforeEach, describe, expect, it, vi } from 'vitest';

const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock('./client', () => ({ default: client }));

import { ReaApiError, reaApi } from './rea';

beforeEach(() => {
  client.get.mockReset();
  client.post.mockReset();
});

describe('reaApi', () => {
  it('reads the status and asks for a fresh environment check only on refresh', async () => {
    client.get.mockResolvedValue({ data: { state: 'not_installed' } });
    await reaApi.status();
    await reaApi.status(true);
    expect(client.get.mock.calls[0]).toEqual(['/api/rea/status', { params: undefined }]);
    expect(client.get.mock.calls[1]).toEqual(['/api/rea/status', { params: { refresh: true } }]);
  });

  it('previews and starts the uninstall', async () => {
    client.get.mockResolvedValue({ data: { removes: [], keeps: [], tasks: [] } });
    client.post.mockResolvedValue({ data: { state: 'uninstalling' } });
    await reaApi.uninstallPreview();
    await reaApi.uninstall(true);
    expect(client.get).toHaveBeenCalledWith('/api/rea/uninstall/preview');
    expect(client.post).toHaveBeenCalledWith('/api/rea/uninstall', { stopTasks: true });
  });

  it('keeps the running tasks of a refused uninstall', async () => {
    const tasks = [{ kind: 'session', id: 'ses_a', title: '分析样本' }];
    client.post.mockRejectedValue({
      response: { status: 409, data: { error: 'HTTPException', message: { code: 'tasks_running', message: 'confirm', tasks } } },
    });
    const failure = reaApi.uninstall(false);
    await expect(failure).rejects.toBeInstanceOf(ReaApiError);
    await expect(failure).rejects.toMatchObject({ code: 'tasks_running', status: 409, details: { tasks } });
  });

  it('keeps plain and network errors readable', async () => {
    client.get.mockRejectedValueOnce({ response: { status: 403, data: { detail: 'Admin required' } } });
    await expect(reaApi.uninstallPreview()).rejects.toMatchObject({ code: 'request_failed', message: 'Admin required' });
    client.post.mockRejectedValueOnce(new Error('Network Error'));
    await expect(reaApi.uninstall(false)).rejects.toMatchObject({ code: 'network', status: 0 });
  });

  it('no longer reserves or records an install conversation: the entry only fills the guide in', () => {
    expect(Object.keys(reaApi).sort()).toEqual(['status', 'uninstall', 'uninstallPreview']);
  });
});
