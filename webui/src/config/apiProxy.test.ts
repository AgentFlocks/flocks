import { createApiProxy, getApiProxyTarget } from './apiProxy';

describe('apiProxy helpers', () => {
  it('uses the configured VITE_API_BASE_URL when present', () => {
    expect(getApiProxyTarget({ VITE_API_BASE_URL: 'http://127.0.0.1:9000' })).toBe('http://127.0.0.1:9000');
  });

  it('falls back to the default local backend target', () => {
    expect(getApiProxyTarget({})).toBe('http://127.0.0.1:8000');
  });

  it('creates matching API and event proxy targets', () => {
    expect(createApiProxy('http://127.0.0.1:9000')).toEqual({
      '/api': {
        target: 'http://127.0.0.1:9000',
        changeOrigin: true,
      },
      '/event': {
        target: 'http://127.0.0.1:9000',
        changeOrigin: true,
      },
    });
  });
});
