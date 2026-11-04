import client from './client';

export interface InstallationResult {
  id: string;
  phase: 'failed';
  recovery: 'restored' | 'unchanged' | 'retained' | 'failed';
  updated_at: string;
  title: string;
  message: string;
  retry_hint: string;
  detail: string;
}

export async function getInstallationResult(locale: string): Promise<InstallationResult | null> {
  const response = await client.get<{ result: InstallationResult | null }>('/api/update/installation-result', {
    params: { locale }, timeout: 5000,
  });
  return response.data.result;
}
