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
});
