import client from './client';

// ======================================================================
// Types
// ======================================================================

export type UpdateStage =
  | 'checking'
  | 'reporting'
  | 'fetching'
  | 'backing_up'
  | 'applying'
  | 'syncing'
  | 'downgrading'
  | 'restarting'
  | 'done'
  | 'error';

export type DeployMode = 'docker' | 'source' | 'managed' | 'offline';

/** Update channel the application asks the server for; unset means the server default. */
export const DEFAULT_UPDATE_CHANNEL = 'default';

export interface VersionInfo {
  current_version: string;
  latest_version: string | null;
  /** Channel the answer applies to (`default` unless the server reports another). */
  channel?: string;
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
  /** Archive being fetched/applied, shown next to the stage label when present. */
  bundle_filename?: string | null;
  downloaded_bytes?: number | null;
  total_bytes?: number | null;
  percent?: number | null;
}

// ======================================================================
// API
// ======================================================================

export const checkUpdate = async (
  locale?: string,
  channel?: string,
  force?: boolean,
): Promise<VersionInfo> => {
  const response = await client.get<VersionInfo>('/api/update/check', {
    params: {
      ...(locale ? { locale } : {}),
      ...(channel && channel !== DEFAULT_UPDATE_CHANNEL ? { edition: channel } : {}),
      ...(force ? { force: true } : {}),
    },
  });
  return response.data;
};

export class IncompleteUpdateError extends Error {
  constructor() {
    super('Update stream ended before completion.');
    this.name = 'IncompleteUpdateError';
  }
}

export class UpdateStreamTimeoutError extends Error {
  constructor() {
    super('Update stream stopped responding.');
    this.name = 'UpdateStreamTimeoutError';
  }
}

/**
 * Apply the upgrade and stream progress events via SSE.
 *
 * @param targetVersion  The version tag to upgrade to (e.g. "2026.03.24").
 *                       Pass it directly from the check result to avoid a
 *                       second version-check round-trip on the server.
 * @param onProgress     Called with each UpdateProgress event as it arrives.
 * @returns              Resolves after a completed update or restart handoff.
 */
export const applyUpdate = async (
  targetVersion: string,
  onProgress: (progress: UpdateProgress) => void,
  locale?: string,
  channel?: string,
): Promise<void> => {
  const params = new URLSearchParams({ target_version: targetVersion });
  if (locale) params.set('locale', locale);
  if (channel && channel !== DEFAULT_UPDATE_CHANNEL) params.set('edition', channel);
  const url = `/api/update/apply?${params.toString()}`;
  const controller = new AbortController();
  // Long-running updates may take many minutes, but each silent wait is bounded.
  // This is an idle deadline, not a limit on the duration of a progressing update.
  let timeout = window.setTimeout(() => controller.abort(), 300_000);
  try {
    const res = await fetch(url, { method: 'POST', signal: controller.signal });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let terminalStage = false;
    const handleLine = (line: string) => {
      if (!line.startsWith('data:')) return;
      let progress: UpdateProgress;
      try {
        progress = JSON.parse(line.slice(5).trim());
      } catch {
        return;
      }
      onProgress(progress);
      if (progress.stage === 'error' || (progress.stage === 'done' && progress.success === false)) {
        throw new Error(progress.message);
      }
      if (progress.stage === 'done' || progress.stage === 'restarting') terminalStage = true;
    };
    try {
      while (true) {
        const { done, value } = await reader.read();
        window.clearTimeout(timeout);
        timeout = window.setTimeout(() => controller.abort(), 300_000);
        buffer += decoder.decode(value, { stream: !done });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) handleLine(line);
        if (done) {
          if (buffer.trim()) handleLine(buffer);
          if (!terminalStage) throw new IncompleteUpdateError();
          return;
        }
      }
    } finally {
      // Do not close on the restarting event: the server still has to
      // launch its detached handoff after yielding that event.
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  } catch (error) {
    if (controller.signal.aborted) throw new UpdateStreamTimeoutError();
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
};
