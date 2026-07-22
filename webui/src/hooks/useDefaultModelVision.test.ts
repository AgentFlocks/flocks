import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// ── module under test ──────────────────────────────────────────────────────
// We use dynamic imports so vi.mock() is hoisted correctly.
vi.mock('@/api/provider', () => ({
  defaultModelAPI: { getResolved: vi.fn() },
  modelV2API: { listDefinitions: vi.fn(), getDefinition: vi.fn() },
}));

import { defaultModelAPI, modelV2API } from '@/api/provider';
import { __resetVisionCacheForTesting, MODEL_CHANGED_EVENT, useDefaultModelVision } from './useDefaultModelVision';
import { renderHook, act, waitFor } from '@testing-library/react';

const mockResolved = defaultModelAPI.getResolved as ReturnType<typeof vi.fn>;
const mockDefinitions = modelV2API.listDefinitions as ReturnType<typeof vi.fn>;
const mockDefinition = modelV2API.getDefinition as ReturnType<typeof vi.fn>;

function makeResolvedResp(provider_id = 'openai', model_id = 'gpt-4o') {
  return { data: { provider_id, model_id } };
}

function makeDefResp(caps: Record<string, unknown>, fetchFrom: 'predefined' | 'customizable' = 'customizable') {
  return { data: { fetch_from: fetchFrom, capabilities: caps } };
}

function makeDefinitionsResp(
  caps: Record<string, unknown>,
  fetchFrom: 'predefined' | 'customizable' = 'customizable',
  providerId = 'openai',
  modelId = 'gpt-4o',
) {
  return {
    data: {
      models: [{
        id: modelId,
        name: modelId,
        provider_id: providerId,
        model_type: 'chat',
        status: 'active',
        fetch_from: fetchFrom,
        capabilities: caps,
      }],
    },
  };
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
    mockDefinitions.mockResolvedValue(makeDefinitionsResp({ supports_vision: true }));

    const { result } = renderHook(() => useDefaultModelVision());
    expect(result.current).toBeNull();

    await waitFor(() => expect(result.current).toBe(true));
  });

  it('returns false for a non-vision customizable model', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinitions.mockResolvedValue(makeDefinitionsResp({ supports_vision: false }));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBe(false));
  });

  it('returns false for a predefined model without the built-in allowlist even when it declares vision support', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp('openai', 'gpt-4o'));
    mockDefinitions.mockResolvedValue(makeDefinitionsResp({ supports_vision: true }, 'predefined'));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBe(false));
  });

  it('returns true for an allowlisted predefined vision model', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp('threatbook-cn-llm', 'qwen3.6-plus'));
    mockDefinitions.mockResolvedValue(makeDefinitionsResp(
      { supports_vision: true },
      'predefined',
      'threatbook-cn-llm',
      'qwen3.6-plus',
    ));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBe(true));
  });

  it('returns true for the predefined kimi-k2.7-code model', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp('threatbook-cn-llm', 'kimi-k2.7-code'));
    mockDefinitions.mockResolvedValue(makeDefinitionsResp(
      { supports_vision: true },
      'predefined',
      'threatbook-cn-llm',
      'kimi-k2.7-code',
    ));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBe(true));
  });

  it('returns null when capabilities are absent', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinitions.mockResolvedValue({
      data: {
        models: [{
          id: 'gpt-4o',
          name: 'gpt-4o',
          provider_id: 'openai',
          model_type: 'chat',
          status: 'active',
          fetch_from: 'customizable',
        }],
      },
    });

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBeNull());
  });

  it('module-level cache: API called only once for multiple concurrent hooks', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinitions.mockResolvedValue(makeDefinitionsResp({ supports_vision: true }));

    renderHook(() => useDefaultModelVision());
    renderHook(() => useDefaultModelVision());
    renderHook(() => useDefaultModelVision());

    await waitFor(() => expect(mockResolved).toHaveBeenCalledTimes(1));
    expect(mockDefinitions).toHaveBeenCalledTimes(1);
    expect(mockDefinition).not.toHaveBeenCalled();
  });

  it('MODEL_CHANGED_EVENT invalidates cache and notifies subscribers', async () => {
    // First resolve: non-vision
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinitions.mockResolvedValueOnce(makeDefinitionsResp({ supports_vision: false }));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBe(false));

    // Change to a vision model and dispatch the event
    mockDefinitions.mockResolvedValueOnce(makeDefinitionsResp({ supports_vision: true }));

    act(() => {
      window.dispatchEvent(new CustomEvent(MODEL_CHANGED_EVENT));
    });

    await waitFor(() => expect(result.current).toBe(true));
    // After invalidation, the API was called a second time
    expect(mockDefinitions).toHaveBeenCalledTimes(2);
  });

  it('detects vision via modalities.input', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinitions.mockResolvedValue(makeDefinitionsResp({ modalities: { input: ['text', 'image'] } }));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBe(true));
  });

  it('detects vision via features array', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinitions.mockResolvedValue(makeDefinitionsResp({ features: ['vision', 'tools'] }));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBe(true));
  });

  it('falls back to getDefinition when the default model is missing from shared definitions', async () => {
    mockResolved.mockResolvedValue(makeResolvedResp());
    mockDefinitions.mockResolvedValue({ data: { models: [] } });
    mockDefinition.mockResolvedValue(makeDefResp({ supports_vision: true }));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBe(true));

    expect(mockDefinition).toHaveBeenCalledWith('openai', 'gpt-4o');
  });

  it('returns null on API error', async () => {
    mockResolved.mockRejectedValue(new Error('network error'));

    const { result } = renderHook(() => useDefaultModelVision());
    await waitFor(() => expect(result.current).toBeNull());
  });
});
