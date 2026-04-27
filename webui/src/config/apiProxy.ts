export type EnvLike = Record<string, string | undefined>;

export function getApiProxyTarget(env: EnvLike): string {
  return env.FLOCKS_API_PROXY_TARGET || 'http://127.0.0.1:8000';
}

export function createApiProxy(target: string) {
  return {
    '/api': {
      target,
      changeOrigin: true,
      xfwd: true,
    },
    '/event': {
      target,
      changeOrigin: true,
      xfwd: true,
    },
  };
}
