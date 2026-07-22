const UPGRADE_PAGE_MARKER = 'flocks-upgrade-in-progress';

export interface RestartReadiness {
  ready: boolean;
  reason?: string;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string' && error) return error;
  return 'request failed';
}

async function readUpgradePageState(): Promise<string | null> {
  try {
    const rootResponse = await fetch('/', { cache: 'no-store' });
    if (!rootResponse.ok) {
      return `root page returned HTTP ${rootResponse.status}`;
    }

    const rootHtml = await rootResponse.text();
    if (rootHtml.includes(UPGRADE_PAGE_MARKER)) {
      return 'upgrade handover page is still active';
    }
  } catch (error) {
    return `root page check failed: ${errorMessage(error)}`;
  }

  return null;
}

async function checkHealth(url: string): Promise<Response | null> {
  try {
    return await fetch(url, { cache: 'no-store' });
  } catch {
    return null;
  }
}

export async function checkRestartReadiness(): Promise<RestartReadiness> {
  const healthResponse = await checkHealth('/api/health');
  if (healthResponse?.ok) {
    return { ready: true };
  }

  const pageReason = await readUpgradePageState();
  return {
    ready: false,
    reason: [
      healthResponse ? `health check returned HTTP ${healthResponse.status}` : 'health check failed',
      pageReason,
    ].filter(Boolean).join('; '),
  };
}
