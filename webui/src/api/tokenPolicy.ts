import client from './client';

export interface TokenPolicyNotice {
  id: string;
  occurrence_id: string;
  expires_at: string;
}

export interface TokenPolicyStatus {
  state: 'active' | 'finished' | 'disabled' | 'unavailable';
  notice: TokenPolicyNotice | null;
  server_now: string;
  next_check_at: string | null;
  waiting_for_display: boolean;
  lease_expires_at: string | null;
}

export async function confirmTokenPolicyDisplay(
  occurrenceId: string,
  requestId: string,
  signal?: AbortSignal,
): Promise<boolean> {
  const { data } = await client.post<{ confirmed: boolean }>(
    '/api/notifications/token-policy/displayed',
    {
      occurrence_id: occurrenceId,
      request_id: requestId,
    },
    { timeout: 3000, signal },
  );
  return data.confirmed;
}

export async function getTokenPolicyStatus(signal?: AbortSignal): Promise<TokenPolicyStatus> {
  const { data } = await client.get<TokenPolicyStatus>('/api/notifications/token-policy', {
    timeout: 3000,
    signal,
  });
  return data;
}

export async function claimTokenPolicy(
  occurrenceId: string,
  requestId: string,
  signal?: AbortSignal,
): Promise<TokenPolicyStatus> {
  const { data } = await client.post<TokenPolicyStatus>(
    '/api/notifications/token-policy/claim',
    {
      occurrence_id: occurrenceId,
      request_id: requestId,
    },
    { timeout: 3000, signal },
  );
  return data;
}
