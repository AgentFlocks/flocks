import axios, { type AxiosRequestConfig } from 'axios';
import client from './client';

// Opt-in console timings only. Never log Axios error/config/response objects:
// those can contain cookies, credentials, filters or customer plugin content.
function enabled(): boolean {
  try { return localStorage.getItem('flocks.hubDiagnostics') === '1'; }
  catch { return false; }
}

export function diagnosticHubGet<T>(path: string, config?: AxiosRequestConfig) {
  const id = crypto.randomUUID?.() ?? Array.from(crypto.getRandomValues(new Uint8Array(16)),
    (byte) => byte.toString(16).padStart(2, '0')).join('');
  const start = performance.now();
  const verbose = enabled();
  const write = (event: string, fields: Record<string, unknown> = {}) => {
    if (verbose) console.info('[flocks-hub-diag]', {
      event, client_id: id, endpoint: path, elapsed_ms: Math.round(performance.now() - start), ...fields,
    });
  };
  write('request.begin');
  return client.get<T>(path, {
    ...config, headers: { ...config?.headers, 'X-Flocks-Hub-Request-Id': id },
  }).then((response) => {
    write('request.end', { status: response.status, trace_id: response.headers?.['x-flocks-hub-trace-id'] });
    return response;
  }, (error: unknown) => {
    write('request.error', axios.isAxiosError(error)
      ? { status: error.response?.status, code: error.code }
      : { code: 'UNKNOWN' });
    throw error;
  });
}
