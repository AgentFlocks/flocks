import { afterEach, describe, expect, it, vi } from 'vitest';

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPatch = vi.fn();
const mockDelete = vi.fn();
const mockPut = vi.fn();

vi.mock('./client', () => ({
  default: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    patch: (...args: unknown[]) => mockPatch(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
    put: (...args: unknown[]) => mockPut(...args),
  },
}));

function serviceSummary(id: string, name: string) {
  return {
    id,
    name,
    enabled: true,
    status: 'unknown',
    tool_count: 0,
    verify_ssl: false,
  };
}

describe('providerAPI.listApiServices', () => {
  afterEach(() => {
    vi.resetModules();
    vi.useRealTimers();
    mockGet.mockReset();
    mockPost.mockReset();
    mockPatch.mockReset();
    mockDelete.mockReset();
    mockPut.mockReset();
  });

  it('shares concurrent API service list requests', async () => {
    let resolveRequest: (value: { data: ReturnType<typeof serviceSummary>[] }) => void = () => undefined;
    mockGet.mockImplementation(
      () => new Promise((resolve) => {
        resolveRequest = resolve;
      }),
    );

    const { providerAPI } = await import('./provider');
    const first = providerAPI.listApiServices();
    const second = providerAPI.listApiServices();

    expect(mockGet).toHaveBeenCalledTimes(1);
    expect(mockGet).toHaveBeenCalledWith('/api/provider/api-services');

    resolveRequest({ data: [serviceSummary('svc-a', 'Service A')] });
    const [firstResponse, secondResponse] = await Promise.all([first, second]);

    firstResponse.data[0].name = 'Mutated';
    expect(secondResponse.data[0].name).toBe('Service A');
  });

  it('serves a fresh cached response without another request', async () => {
    mockGet.mockResolvedValue({ data: [serviceSummary('svc-a', 'Service A')] });

    const { providerAPI } = await import('./provider');
    const firstResponse = await providerAPI.listApiServices();
    firstResponse.data[0].name = 'Mutated';
    const secondResponse = await providerAPI.listApiServices();

    expect(mockGet).toHaveBeenCalledTimes(1);
    expect(secondResponse.data[0].name).toBe('Service A');
  });

  it('invalidates the cached list after an API service update', async () => {
    mockGet
      .mockResolvedValueOnce({ data: [serviceSummary('svc-a', 'Service A')] })
      .mockResolvedValueOnce({ data: [serviceSummary('svc-a', 'Service A Updated')] });
    mockPatch.mockResolvedValue({ data: serviceSummary('svc-a', 'Service A Updated') });

    const { providerAPI } = await import('./provider');
    await providerAPI.listApiServices();
    await providerAPI.updateApiService('svc-a', { enabled: false });
    const response = await providerAPI.listApiServices();

    expect(mockPatch).toHaveBeenCalledWith('/api/provider/api-services/svc-a', { enabled: false });
    expect(mockGet).toHaveBeenCalledTimes(2);
    expect(response.data[0].name).toBe('Service A Updated');
  });

  it('does not let an older list request repopulate the cache after a mutation', async () => {
    let resolveOldList: (value: { data: ReturnType<typeof serviceSummary>[] }) => void = () => undefined;
    mockGet
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveOldList = resolve;
      }))
      .mockResolvedValueOnce({ data: [serviceSummary('svc-a', 'Fresh Service')] });
    mockPatch.mockResolvedValue({ data: serviceSummary('svc-a', 'Fresh Service') });

    const { providerAPI } = await import('./provider');
    const oldRequest = providerAPI.listApiServices();
    await providerAPI.updateApiService('svc-a', { enabled: false });
    const freshRequest = providerAPI.listApiServices();
    resolveOldList({ data: [serviceSummary('svc-a', 'Stale Service')] });
    await Promise.all([oldRequest, freshRequest]);

    const response = await providerAPI.listApiServices();

    expect(mockGet).toHaveBeenCalledTimes(2);
    expect(response.data[0].name).toBe('Fresh Service');
  });

  it('keeps a forced request authoritative when an older request finishes later', async () => {
    let resolveOldList: (value: { data: ReturnType<typeof serviceSummary>[] }) => void = () => undefined;
    let resolveForcedList: (value: { data: ReturnType<typeof serviceSummary>[] }) => void = () => undefined;
    mockGet
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveOldList = resolve;
      }))
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveForcedList = resolve;
      }));

    const { providerAPI } = await import('./provider');
    const oldRequest = providerAPI.listApiServices();
    const forcedRequest = providerAPI.listApiServices({ force: true });

    resolveForcedList({ data: [serviceSummary('svc-a', 'Forced Service')] });
    await forcedRequest;
    resolveOldList({ data: [serviceSummary('svc-a', 'Stale Service')] });
    await oldRequest;

    const response = await providerAPI.listApiServices();

    expect(mockGet).toHaveBeenCalledTimes(2);
    expect(response.data[0].name).toBe('Forced Service');
  });
});

describe('providerAPI.revealCredentials', () => {
  afterEach(() => {
    vi.resetModules();
    mockPost.mockReset();
  });

  it('uses the explicit credential reveal endpoint', async () => {
    mockPost.mockResolvedValue({ data: { api_key: 'sk-revealed', has_credential: true } });

    const { providerAPI } = await import('./provider');
    await providerAPI.revealCredentials('openai');

    expect(mockPost).toHaveBeenCalledWith('/api/provider/openai/credentials/reveal');
  });

  it('allows bounded authentication probes to finish and keeps credentials in the request body', async () => {
    mockPost.mockResolvedValue({ data: { status: 'valid', code: 'key_valid' } });
    const { providerAPI } = await import('./provider');
    await providerAPI.checkKey('custom-供应商', { api_key: 'draft-key', base_url: 'https://api.example.com/v1' });
    expect(mockPost).toHaveBeenCalledWith(`/api/provider/${encodeURIComponent('custom-供应商')}/check-key`, { api_key: 'draft-key', base_url: 'https://api.example.com/v1' }, { timeout: 60_000 });
  });

  it('uses the explicit API service credential reveal endpoint', async () => {
    mockPost.mockResolvedValue({ data: { api_key: 'service-key', has_credential: true } });

    const { providerAPI } = await import('./provider');
    await providerAPI.revealServiceCredentials('threatbook-cn');

    expect(mockPost).toHaveBeenCalledWith('/api/provider/threatbook-cn/service-credentials/reveal');
  });

  it('validates and saves API service credentials through one request', async () => {
    mockPost.mockResolvedValue({ data: { success: true, message: 'ok' } });

    const { providerAPI } = await import('./provider');
    await providerAPI.configureServiceCredentials('threatbook-cn', {
      api_key: 'new-key',
    });

    expect(mockPost).toHaveBeenCalledWith(
      '/api/provider/threatbook-cn/service-credentials/configure',
      { api_key: 'new-key' },
    );
  });
});

describe('model IDs in provider API paths', () => {
  afterEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    mockDelete.mockReset();
    mockPut.mockReset();
  });

  it('keeps namespaced IDs and reserved URL characters in the model path', async () => {
    const { modelV2API, modelSettingsAPI, customAPI } = await import('./provider');
    const providerId = 'custom-gateway';
    const modelId = 'vendor/model?revision=1#fast%variant';
    const encoded = 'vendor%2Fmodel%3Frevision%3D1%23fast%25variant';
    modelV2API.getDefinition(providerId, modelId);
    modelV2API.deleteDefinition(providerId, modelId);
    modelSettingsAPI.get(providerId, modelId);
    modelSettingsAPI.update(providerId, modelId, { enabled: false });
    customAPI.deleteModel(providerId, modelId);
    expect(mockGet).toHaveBeenCalledWith(`/api/model/v2/definitions/custom-gateway/${encoded}`);
    expect(mockDelete).toHaveBeenCalledWith(`/api/model/v2/definitions/custom-gateway/${encoded}`);
    expect(mockGet).toHaveBeenCalledWith(`/api/model/v2/settings/custom-gateway/${encoded}`);
    expect(mockPut).toHaveBeenCalledWith(`/api/model/v2/settings/custom-gateway/${encoded}`, { enabled: false });
    expect(mockDelete).toHaveBeenCalledWith(`/api/custom/models/custom-gateway/${encoded}`);
  });
});
