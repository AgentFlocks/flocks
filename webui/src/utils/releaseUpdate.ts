import type { UpdateProgress, VersionInfo } from '@/api/update';

/** What the build's own page knows of the release this install could go to. */
export interface ReleaseUpdateStatus {
  release_install: boolean;
  latest_version?: string | null;
  has_update?: boolean;
  error?: string | null;
}

/** Where an update handed over at the restarting stage stands. */
export interface ReleaseUpdateOutcome {
  state: string;
  detail?: string | null;
  /** Once it has failed: the step it stopped at, how the machine was left, the log. */
  failed_step?: string | null;
  recovery?: string | null;
  log?: string | null;
  log_tail?: string[] | null;
}

/**
 * A build whose releases its own core installs: where the update check and the update dialog get
 * and apply one, since the built-in updater leaves a managed install alone.
 */
export interface ReleaseUpdateSource {
  status: () => Promise<ReleaseUpdateStatus>;
  start: (onProgress: (progress: UpdateProgress) => void) => Promise<void>;
  /**
   * The release is installed apart from this service, which stays up until the install restarts it:
   * that it answers says nothing of how the update went. This does, by the restarting stage's token.
   */
  outcome: (progressToken: string) => Promise<ReleaseUpdateOutcome>;
}

/**
 * The version check of a managed install, in the terms of what can be done about it: a release
 * install takes its release from the source. Another kind of managed install stays as it was told.
 */
export async function withReleaseUpdate(info: VersionInfo, source?: ReleaseUpdateSource): Promise<VersionInfo> {
  if (!source || info.deploy_mode !== 'managed') return info;
  const status = await source.status().catch(() => null);
  if (!status?.release_install) return info;
  const latest = status.latest_version ?? info.latest_version;
  return {
    ...info,
    latest_version: latest,
    has_update: Boolean(status.has_update),
    update_allowed: true,
    error: status.error ?? null,
    // The check's notes are of the version it found, which may not be this one.
    ...(latest === info.latest_version ? {} : { release_notes: null, release_url: null }),
  };
}
