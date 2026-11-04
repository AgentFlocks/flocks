import type { UpgradeHandoffProgress } from '@/api/update';

const UPGRADE_PAGE_MARKER = 'flocks-upgrade-in-progress';
const RESTART_READINESS_REQUEST_TIMEOUT_MS = 3_000;
export const UPGRADE_PROGRESS_TOKEN_HEADER = 'X-Flocks-Upgrade-Token';

export interface RestartReadiness {
  ready: boolean;
  reason?: string;
  /** Restart-stage progress reported by the upgrade handoff while the service is offline. */
  upgrade?: UpgradeHandoffProgress;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string' && error) return error;
  return 'request failed';
}

function abortable<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(new DOMException('The operation was aborted.', 'AbortError'));
    signal.addEventListener('abort', onAbort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', onAbort));
  });
}

/** Fetch and read the response within one timeout, so a stalled body cannot stop the polling. */
async function fetchWithTimeout<T>(
  url: string,
  read: (response: Response) => Promise<T>,
  headers?: Record<string, string>,
): Promise<T> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => {
    controller.abort();
  }, RESTART_READINESS_REQUEST_TIMEOUT_MS);

  try {
    const request = fetch(url, {
      cache: 'no-store',
      signal: controller.signal,
      ...(headers ? { headers } : {}),
    }).then(read);
    return await abortable(request, controller.signal);
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function isHandoffProgress(value: unknown): value is UpgradeHandoffProgress {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<UpgradeHandoffProgress>;
  return typeof candidate.phase === 'string' && Array.isArray(candidate.steps);
}

/** The upgrade handoff answers 503 with its progress while the service is offline. */
async function readHandoffProgress(response: Response): Promise<UpgradeHandoffProgress | null> {
  if (response.status !== 503) return null;
  if (!(response.headers.get('content-type') || '').includes('application/json')) return null;
  try {
    const payload: unknown = await response.json();
    if (!payload || typeof payload !== 'object') return null;
    const { status, upgrade } = payload as { status?: unknown; upgrade?: unknown };
    return status === 'upgrading' && isHandoffProgress(upgrade) ? upgrade : null;
  } catch {
    return null;
  }
}

async function readUpgradePageState(): Promise<string | null> {
  try {
    return await fetchWithTimeout('/', async (rootResponse) => {
      if (!rootResponse.ok) {
        return `root page returned HTTP ${rootResponse.status}`;
      }

      const rootHtml = await rootResponse.text();
      return rootHtml.includes(UPGRADE_PAGE_MARKER) ? 'upgrade handover page is still active' : null;
    });
  } catch (error) {
    return `root page check failed: ${errorMessage(error)}`;
  }
}

interface HealthCheck {
  ok: boolean;
  status: number;
  upgrade: UpgradeHandoffProgress | null;
}

async function checkHealth(url: string, progressToken?: string | null): Promise<HealthCheck | null> {
  try {
    return await fetchWithTimeout(
      url,
      async (response) => ({
        ok: response.ok,
        status: response.status,
        upgrade: response.ok ? null : await readHandoffProgress(response),
      }),
      progressToken ? { [UPGRADE_PROGRESS_TOKEN_HEADER]: progressToken } : undefined,
    );
  } catch {
    return null;
  }
}

/**
 * Poll target while the service restarts after an upgrade. `progressToken` comes with the
 * `restarting` event and lets the handoff include the error text of a failed step.
 */
export async function checkRestartReadiness(progressToken?: string | null): Promise<RestartReadiness> {
  const health = await checkHealth('/api/health', progressToken);
  if (health?.ok) {
    return { ready: true };
  }
  const upgrade = health?.upgrade;
  if (upgrade) {
    return { ready: false, reason: `upgrade handoff ${upgrade.phase}: ${upgrade.step ?? 'starting'}`, upgrade };
  }

  const pageReason = await readUpgradePageState();
  return {
    ready: false,
    reason: [
      health ? `health check returned HTTP ${health.status}` : 'health check failed',
      pageReason,
    ].filter(Boolean).join('; '),
  };
}
