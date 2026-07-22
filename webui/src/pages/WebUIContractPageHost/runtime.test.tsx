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
