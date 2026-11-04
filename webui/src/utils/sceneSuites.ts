/** Local mutations must refresh navigation even when the SSE connection is down. */
export const SCENE_SUITES_CHANGED_EVENT = 'flocks:scene-suites-changed';

export function sceneSuiteErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'response' in error) {
    const data = (error as { response?: { data?: { detail?: unknown; message?: unknown } } }).response?.data;
    const detail = data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
      return detail.message;
    }
    // The server's own error shape: { "error": ..., "message": ... }. It is
    // what tells the operator why an install was refused (a suite that needs
    // its licence renewed, say); the transport's "status code 422" does not.
    if (typeof data?.message === 'string' && data.message.trim()) return data.message;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}
