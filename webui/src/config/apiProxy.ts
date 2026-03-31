export type EnvLike = Record<string, string | undefined>;

export function getApiProxyTarget(env: EnvLike): string {
  return env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';
}

export function createApiProxy(target: string) {
  return {
    '/api': {
      target,
      changeOrigin: true,
    },
    '/event': {
      target,
      changeOrigin: true,
    },
  };
}
