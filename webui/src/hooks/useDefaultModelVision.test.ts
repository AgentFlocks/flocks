import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// ── module under test ──────────────────────────────────────────────────────
// We use dynamic imports so vi.mock() is hoisted correctly.
vi.mock('@/api/provider', () => ({
  defaultModelAPI: { getResolved: vi.fn() },
  modelV2API: { getDefinition: vi.fn() },
}));

import { defaultModelAPI, modelV2API } from '@/api/provider';
import { __resetVisionCacheForTesting, MODEL_CHANGED_EVENT, useDefaultModelVision } from './useDefaultModelVision';
import { renderHook, act, waitFor } from '@testing-library/react';

const mockResolved = defaultModelAPI.getResolved as ReturnType<typeof vi.fn>;
const mockDefinition = modelV2API.getDefinition as ReturnType<typeof vi.fn>;

function makeResolvedResp(provider_id = 'openai', model_id = 'gpt-4o') {
  return { data: { provider_id, model_id } };
}

function makeDefResp(caps: Record<string, unknown>, fetchFrom: 'predefined' | 'customizable' = 'customizable') {
  return { data: { fetch_from: fetchFrom, capabilities: caps } };
}

describe('useDefaultModelVision', () => {
  beforeEach(() => {
    __resetVisionCacheForTesting();
    vi.clearAllMocks();
  });

  afterEach(() => {
    __resetVisionCacheForTesting();
  });

  it('returns null initially then true for a vision model', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinition.mockResolvedValue(makeDefResp({ supports_vision: true }));

    const { result } = renderHook(() => useDefaultModelVision());
    expect(result.current).toBeNull();

    await waitFor(() => expect(result.current).toBe(true));
  });

  it('returns false for a non-vision customizable model', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinition.mockResolvedValue(makeDefResp({ supports_vision: false }));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBe(false));
  });

  it('returns false for a predefined (built-in) model even when it declares vision support', async () => {
    // Built-in models must explicitly reject image uploads (not return
    // null / unknown) so SessionChat shows the "model does not support
    // images" toast instead of silently letting them through.
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinition.mockResolvedValue(makeDefResp({ supports_vision: true }, 'predefined'));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBe(false));
  });

  it('returns null when capabilities are absent', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinition.mockResolvedValue({ data: {} });

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBeNull());
  });

  it('module-level cache: API called only once for multiple concurrent hooks', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinition.mockResolvedValue(makeDefResp({ supports_vision: true }));

    renderHook(() => useDefaultModelVision());
    renderHook(() => useDefaultModelVision());
    renderHook(() => useDefaultModelVision());

    await waitFor(() => expect(mockResolved).toHaveBeenCalledTimes(1));
    expect(mockDefinition).toHaveBeenCalledTimes(1);
  });

  it('MODEL_CHANGED_EVENT invalidates cache and notifies subscribers', async () => {
    // First resolve: non-vision
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinition.mockResolvedValue(makeDefResp({ supports_vision: false }));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBe(false));

    // Change to a vision model and dispatch the event
    mockDefinition.mockResolvedValue(makeDefResp({ supports_vision: true }));

    act(() => {
      window.dispatchEvent(new CustomEvent(MODEL_CHANGED_EVENT));
    });

    await waitFor(() => expect(result.current).toBe(true));
    // After invalidation, the API was called a second time
    expect(mockDefinition).toHaveBeenCalledTimes(2);
  });

  it('detects vision via modalities.input', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinition.mockResolvedValue(makeDefResp({ modalities: { input: ['text', 'image'] } }));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBe(true));
  });

  it('detects vision via features array', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinition.mockResolvedValue(makeDefResp({ features: ['vision', 'tools'] }));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBe(true));
  });

  it('returns null on API error', async () => {
    mockResolved.mockRejectedValue(new Error('network error'));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBeNull());
  });
});
