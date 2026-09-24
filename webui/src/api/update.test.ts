import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import client from './client';
import { applyUpdate, checkUpdate, IncompleteUpdateError, UpdateStreamTimeoutError } from './update';

vi.mock('./client', () => ({ default: { get: vi.fn() } }));
const event = (stage: string, message = stage, success: boolean | null = null) =>
  `data: ${JSON.stringify({ stage, message, success })}\n\n`;

function stream(chunks: string[], status = 200) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(new TextEncoder().encode(chunk)));
      controller.close();
    },
  }), { status })));
}

describe('update transport contract', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it.each([undefined, 'default'])('keeps the server default edition for %s', async (channel) => {
    vi.mocked(client.get).mockResolvedValue({ data: {} });
    await checkUpdate('zh-CN', channel, true);
    expect(client.get).toHaveBeenCalledWith('/api/update/check', { params: { locale: 'zh-CN', force: true } });
    stream([event('done', 'Complete', true)]);
    await applyUpdate('2026.09.20', vi.fn(), 'zh-CN', channel);
    expect(fetch).toHaveBeenCalledWith('/api/update/apply?target_version=2026.09.20&locale=zh-CN', expect.objectContaining({ method: 'POST', signal: expect.anything() }));
  });

  it('sends an extension channel through the edition parameter accepted by the server', async () => {
    vi.mocked(client.get).mockResolvedValue({ data: {} });
    await checkUpdate('en-US', 'extension-edition');
    expect(client.get).toHaveBeenCalledWith('/api/update/check', { params: { locale: 'en-US', edition: 'extension-edition' } });
    stream([event('done', 'Complete', true)]);
    await applyUpdate('2026.09.20', vi.fn(), undefined, 'extension-edition');
    expect(fetch).toHaveBeenCalledWith('/api/update/apply?target_version=2026.09.20&edition=extension-edition', expect.objectContaining({ method: 'POST', signal: expect.anything() }));
  });

  it('reads split events and a final done event without a trailing newline', async () => {
    const progress = vi.fn();
    const payload = event('fetching') + event('done', 'Complete', true).trimEnd();
    stream([payload.slice(0, 12), payload.slice(12)]);
    await expect(applyUpdate('2026.09.20', progress)).resolves.toBeUndefined();
    expect(progress.mock.calls.map(([value]) => value.stage)).toEqual(['fetching', 'done']);
  });

  it.each([{ chunks: [] }, { chunks: [event('fetching')] }, { chunks: ['data: invalid\n\n'] }])('rejects EOF without completion: %j', async ({ chunks }) => {
    stream(chunks);
    await expect(applyUpdate('2026.09.20', vi.fn())).rejects.toBeInstanceOf(IncompleteUpdateError);
  });

  it.each(['error', 'done'])('rejects failed %s events', async (stage) => {
    stream([event(stage, 'Update refused', false)]);
    await expect(applyUpdate('2026.09.20', vi.fn())).rejects.toThrow('Update refused');
  });

  it('preserves restarting as a handoff even when no done event follows', async () => {
    stream([event('restarting')]);
    await expect(applyUpdate('2026.09.20', vi.fn())).resolves.toBeUndefined();
  });

  it('does not cancel the stream before the server can launch its restart handoff', async () => {
    let controller!: ReadableStreamDefaultController;
    const cancel = vi.fn();
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new ReadableStream({
      start(value) { controller = value; }, cancel,
    }))));
    const progress = vi.fn();
    const pending = applyUpdate('2026.09.20', progress);
    controller.enqueue(new TextEncoder().encode(event('restarting')));
    await vi.waitFor(() => expect(progress).toHaveBeenCalledTimes(1));
    expect(cancel).not.toHaveBeenCalled();
    controller.close();
    await pending;
  });

  it('surfaces managed deployment HTTP refusal', async () => {
    stream([], 409);
    await expect(applyUpdate('2026.09.20', vi.fn())).rejects.toThrow('HTTP 409');
  });

  it('aborts a silent connection after five minutes', async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn((_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    })));
    const result = expect(applyUpdate('2026.09.20', vi.fn())).rejects.toBeInstanceOf(UpdateStreamTimeoutError);
    await vi.advanceTimersByTimeAsync(300_000);
    await result;
    expect(vi.getTimerCount()).toBe(0);
  });
});
