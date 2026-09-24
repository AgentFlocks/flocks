import client from './client';

export type ProxyMode = 'off' | 'system' | 'manual';

export interface NetworkProxyEffective {
  active: boolean;
  http: string | null;
  https: string | null;
  bypass: string[];
  /** Where the effective decision came from: flocks.json, environment variables or the OS proxy settings. */
  source: 'default' | 'config' | 'env' | 'os';
}

export interface NetworkProxyView {
  mode: ProxyMode;
  http: string | null;
  https: string | null;
  all: string | null;
  username: string | null;
  hasPassword: boolean;
  bypass: string[];
  bypassPrivate: boolean;
  source: 'default' | 'config' | 'env';
  effective: NetworkProxyEffective;
  processEnv: Record<string, string>;
}

export interface NetworkProxyUpdate {
  mode?: ProxyMode;
  http?: string;
  https?: string;
  all?: string;
  username?: string;
  /** Empty string removes the stored password; omit to keep it. */
  password?: string;
  bypass?: string[];
  bypassPrivate?: boolean;
}

export interface NetworkProxyTestResult {
  ok: boolean;
  url: string;
  via: string | null;
  status: number | null;
  elapsedMs: number | null;
  error: string | null;
}

export const networkProxyApi = {
  async get(): Promise<NetworkProxyView> {
    const response = await client.get<NetworkProxyView>('/api/config/network/proxy');
    return response.data;
  },

  async update(payload: NetworkProxyUpdate): Promise<NetworkProxyView> {
    const response = await client.patch<NetworkProxyView>('/api/config/network/proxy', payload);
    return response.data;
  },

  async test(url: string, timeout = 10): Promise<NetworkProxyTestResult> {
    const response = await client.post<NetworkProxyTestResult>('/api/config/network/proxy/test', { url, timeout });
    return response.data;
  },
};
