export interface ProviderCredentialSnapshot {
  apiKey: string;
  baseUrl?: string | null;
}

function normalizeValue(value?: string | null): string {
  return (value ?? '').trim();
}

export function hasPendingProviderCredentialChanges(
  existing: ProviderCredentialSnapshot,
  current: ProviderCredentialSnapshot,
): boolean {
  return (
    normalizeValue(existing.apiKey) !== normalizeValue(current.apiKey) ||
    normalizeValue(existing.baseUrl) !== normalizeValue(current.baseUrl)
  );
}
