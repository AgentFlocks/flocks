import client from './client';

// ======================================================================
// Types
// ======================================================================

export type UpdateStage = 'fetching' | 'backing_up' | 'applying' | 'syncing' | 'building' | 'restarting' | 'done' | 'error';

export type DeployMode = 'docker' | 'source';

export interface VersionInfo {
  current_version: string;
  latest_version: string | null;
  has_update: boolean;
  release_notes: string | null;
  release_url: string | null;
  error: string | null;
  deploy_mode?: DeployMode;
  update_allowed?: boolean;
}

export interface UpdateProgress {
  stage: UpdateStage;
  message: string;
  success: boolean | null;
}

// ======================================================================
// API
// ======================================================================

export const checkUpdate = async (locale?: string): Promise<VersionInfo> => {
  const response = await client.get<VersionInfo>('/api/update/check', {
    params: locale ? { locale } : undefined,
  });
  return response.data;
};

/**
 * Apply the upgrade and stream progress events via SSE.
 *
 * @param targetVersion  The version tag to upgrade to (e.g. "2026.03.24").
 *                       Pass it directly from the check result to avoid a
 *                       second version-check round-trip on the server.
 * @param onProgress     Called with each UpdateProgress event as it arrives.
 * @returns              Resolves when the stream closes.
 */
export const applyUpdate = (
  targetVersion: string,
  onProgress: (progress: UpdateProgress) => void,
  locale?: string,
): Promise<void> => {
  return new Promise((resolve, reject) => {
    const params = new URLSearchParams({
      target_version: targetVersion,
    });
    if (locale) {
      params.set('locale', locale);
    }
    const url = `/api/update/apply?${params.toString()}`;
    fetch(url, { method: 'POST' })
      .then((res) => {
        if (!res.ok || !res.body) {
          reject(new Error(`HTTP ${res.status}`));
          return;
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        const pump = (): Promise<void> =>
          reader.read().then(({ done, value }) => {
            if (done) {
              resolve();
              return;
            }

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            // Keep the last (potentially incomplete) line in the buffer
            buffer = lines.pop() ?? '';

            for (const line of lines) {
              if (line.startsWith('data: ')) {
                try {
                  const progress: UpdateProgress = JSON.parse(line.slice(6));
                  onProgress(progress);
                  if (progress.stage === 'error') {
                    reject(new Error(progress.message));
                    return;
                  }
                } catch {
                  // ignore malformed events
                }
              }
            }

            return pump();
          });

        pump().catch(reject);
      })
      .catch(reject);
  });
};
