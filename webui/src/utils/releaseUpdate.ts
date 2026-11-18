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
  /** The request the update was handed over as: what a confirmed retry of it names. */
  id?: string | null;
  state: string;
  detail?: string | null;
  /** Once it has failed: the step it stopped at, how the machine was left, the log. */
  failed_step?: string | null;
  recovery?: string | null;
  log?: string | null;
  log_tail?: string[] | null;
  /** The release did not come up: asked for again as usual it is refused, so a retry may be offered. */
  retryable?: boolean | null;
}

/**
 * How an update starts. retryOf: the outcome id of an update whose release did not come up, which the
 * administrator confirmed trying again (the source refuses it once another update replaced that one).
 */
export interface ReleaseUpdateStartOptions {
  retryOf?: string;
}

/**
 * A build whose releases its own core installs: where the update check and the update dialog get
 * and apply one, since the built-in updater leaves a managed install alone.
 */
export interface ReleaseUpdateSource {
  status: () => Promise<ReleaseUpdateStatus>;
  start: (onProgress: (progress: UpdateProgress) => void, options?: ReleaseUpdateStartOptions) => Promise<void>;
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
