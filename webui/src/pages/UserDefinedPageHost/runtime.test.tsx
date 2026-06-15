import { describe, expect, it, vi } from 'vitest';
import apiClient from '@/api/client';
import { installUserDefinedPageRuntime, loadUserDefinedPageBundle } from './runtime';

describe('UserDefinedPage runtime', () => {
  it('exposes page-scoped api helper', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {} } as never);
    installUserDefinedPageRuntime('dash-1');
    const sdk = window.__FLOCKS_USER_DEFINED_PAGE_SDK__;
    expect(sdk).toBeTruthy();
    await sdk!.api.page.get('/stats');
    expect(getSpy).toHaveBeenCalledWith('/api/user-defined-pages/dash-1/api/stats', undefined);
    getSpy.mockRestore();
  });

  it('loads page bundles through the credentialed api client', async () => {
    const source = 'export default function Page(){return null;}';
    const getSpy = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: source } as never);
    const createObjectURLSpy = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValue(`data:text/javascript,${encodeURIComponent(source)}`);
    const revokeObjectURLSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    const component = await loadUserDefinedPageBundle(
      'https://api.example.test/api/user-defined-pages/dash-1/bundle.js?v=abc123',
      'missing default',
    );

    expect(component).toEqual(expect.any(Function));
    expect(getSpy).toHaveBeenCalledWith(
      'https://api.example.test/api/user-defined-pages/dash-1/bundle.js?v=abc123',
      { responseType: 'text' },
    );
    expect(createObjectURLSpy).toHaveBeenCalledWith(expect.any(Blob));
    expect(revokeObjectURLSpy).toHaveBeenCalledWith(expect.stringContaining('data:text/javascript'));

    getSpy.mockRestore();
    createObjectURLSpy.mockRestore();
    revokeObjectURLSpy.mockRestore();
  });
});
