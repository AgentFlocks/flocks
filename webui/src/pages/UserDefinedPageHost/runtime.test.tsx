import { describe, expect, it, vi } from 'vitest';
import apiClient from '@/api/client';
import { installUserDefinedPageRuntime } from './runtime';

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
});
