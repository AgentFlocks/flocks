/** Local mutations must refresh navigation even when the SSE connection is down. */
export const SCENE_SUITES_CHANGED_EVENT = 'flocks:scene-suites-changed';

export function sceneSuiteErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'response' in error) {
    const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
      return detail.message;
    }
  }
  return error instanceof Error && error.message ? error.message : fallback;
}
