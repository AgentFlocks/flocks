import { describe, expect, it } from 'vitest';
import { createApiProxy, getApiProxyTarget } from './apiProxy';

describe('apiProxy helpers', () => {
  it('uses the configured proxy target when present', () => {
    expect(getApiProxyTarget({ FLOCKS_API_PROXY_TARGET: 'http://127.0.0.1:9000' })).toBe('http://127.0.0.1:9000');
  });

  it('does not rewrite the configured host', () => {
    expect(getApiProxyTarget({ FLOCKS_API_PROXY_TARGET: 'http://10.0.0.8:9000' })).toBe('http://10.0.0.8:9000');
  });

  it('falls back to the default local backend target', () => {
    expect(getApiProxyTarget({})).toBe('http://127.0.0.1:8000');
  });

  it('creates matching API and event proxy targets', () => {
    expect(createApiProxy('http://127.0.0.1:9000')).toEqual({
      '/api': {
        target: 'http://127.0.0.1:9000',
        changeOrigin: true,
        xfwd: true,
      },
      '/event': {
        target: 'http://127.0.0.1:9000',
        changeOrigin: true,
        xfwd: true,
      },
    });
  });
});
