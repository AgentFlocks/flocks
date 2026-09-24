import { describe, expect, it, vi } from 'vitest';
import apiClient from '@/api/client';
import { installWebUIContractPageRuntime, loadWebUIContractPageBundle } from './runtime';

describe('WebUIContractPage runtime', () => {
  it('exposes page-scoped api helper', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {} } as never);
    installWebUIContractPageRuntime('dash-1');
    const sdk = window.__FLOCKS_WEBUI_CONTRACT_SDK__;
    expect(sdk).toBeTruthy();
    await sdk!.api.page.get('/stats');
    expect(getSpy).toHaveBeenCalledWith('/api/contracts/webui/pages/dash-1/api/stats', undefined);
    getSpy.mockRestore();
  });

  it('exposes contract operation helper', async () => {
    const postSpy = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {} } as never);
    installWebUIContractPageRuntime('dash-1');
    const sdk = window.__FLOCKS_WEBUI_CONTRACT_SDK__;
    await sdk!.api
      .contract('records/list', 'records.operations')
      .operation('list', { params: { limit: 10 } });
    expect(postSpy).toHaveBeenCalledWith(
      '/api/contracts/webui/pages/records/list/access/records.operations/operations/list',
      { params: { limit: 10 } },
      undefined,
    );
    postSpy.mockRestore();
  });

  it('keeps a page-scoped api per installed page instead of sharing one mutable client', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {} } as never);
    installWebUIContractPageRuntime('soc-dashboard');
    const dashboardApi = window.__FLOCKS_WEBUI_CONTRACT_SDK__!.api;
    // A second page (kept alive next to the first) installs after it.
    installWebUIContractPageRuntime('soc-alerts');
    const alertsApi = window.__FLOCKS_WEBUI_CONTRACT_SDK__!.api;

    await dashboardApi.page.get('/task-center');
    await alertsApi.page.get('/task-center');
    expect(getSpy).toHaveBeenNthCalledWith(1, '/api/contracts/webui/pages/soc-dashboard/api/task-center', undefined);
    expect(getSpy).toHaveBeenNthCalledWith(2, '/api/contracts/webui/pages/soc-alerts/api/task-center', undefined);
    // The plain client methods are still the shared, credentialed ones.
    await dashboardApi.get('/api/health');
    expect(getSpy).toHaveBeenLastCalledWith('/api/health');
    getSpy.mockRestore();
  });

  it('installs the runtime for the page whose bundle is evaluating, one bundle at a time', async () => {
    // Like the real SDK shim: `api` is captured when the bundle module evaluates.
    const source = (name: string) => `const api = globalThis.__FLOCKS_WEBUI_CONTRACT_SDK__.api; export default function ${name}(){return api.page;}`;
    const getSpy = vi.spyOn(apiClient, 'get').mockImplementation(async (url: string) => (
      { data: source(url.includes('soc-dashboard') ? 'Dashboard' : 'Alerts') } as never
    ));
    const createObjectURLSpy = vi.spyOn(URL, 'createObjectURL').mockImplementation((blob: Blob) => {
      // The blob content is not readable synchronously here; encode from the request order instead.
      const index = createObjectURLSpy.mock.calls.length;
      return `data:text/javascript,${encodeURIComponent(source(index === 1 ? 'Dashboard' : 'Alerts'))}`;
    });
    const revokeObjectURLSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    const [dashboard, alerts] = await Promise.all([
      loadWebUIContractPageBundle('/api/contracts/webui/pages/soc-dashboard/bundle.js?v=1', 'missing', 'soc-dashboard'),
      loadWebUIContractPageBundle('/api/contracts/webui/pages/soc-alerts/bundle.js?v=1', 'missing', 'soc-alerts'),
    ]);

    getSpy.mockClear();
    getSpy.mockResolvedValue({ data: {} } as never);
    await (dashboard as unknown as () => { get(path: string): Promise<unknown> })().get('/activity');
    await (alerts as unknown as () => { get(path: string): Promise<unknown> })().get('/activity');
    expect(getSpy).toHaveBeenNthCalledWith(1, '/api/contracts/webui/pages/soc-dashboard/api/activity', undefined);
    expect(getSpy).toHaveBeenNthCalledWith(2, '/api/contracts/webui/pages/soc-alerts/api/activity', undefined);

    getSpy.mockRestore();
    createObjectURLSpy.mockRestore();
    revokeObjectURLSpy.mockRestore();
  });

  it('loads page bundles through the credentialed api client', async () => {
    const source = 'export default function Page(){return null;}';
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: source } as never);
    const createObjectURLSpy = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValue(`data:text/javascript,${encodeURIComponent(source)}`);
    const revokeObjectURLSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    const component = await loadWebUIContractPageBundle(
      'https://api.example.test/api/contracts/webui/pages/dash-1/bundle.js?v=abc123',
      'missing default',
    );

    expect(component).toEqual(expect.any(Function));
    expect(getSpy).toHaveBeenCalledWith(
      'https://api.example.test/api/contracts/webui/pages/dash-1/bundle.js?v=abc123',
      { responseType: 'text' },
    );
    expect(createObjectURLSpy).toHaveBeenCalledWith(expect.any(Blob));
    expect(revokeObjectURLSpy).toHaveBeenCalledWith(expect.stringContaining('data:text/javascript'));

    getSpy.mockRestore();
    createObjectURLSpy.mockRestore();
    revokeObjectURLSpy.mockRestore();
  });
});
