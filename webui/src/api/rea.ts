import client from './client';

/** The unified REA plugin state shared by the Rex shortcut, the plugin square and Rex. */
export type ReaState =
  | 'not_installed'
  | 'installing'
  | 'validating'
  | 'ready'
  | 'install_failed'
  | 'degraded'
  | 'uninstalling'
  | 'uninstall_failed';
export type ReaTier = 'light' | 'full';
export type ReaInstallStage = 'check' | 'components' | 'register' | 'validate' | 'done';
export type ReaCapabilityKey = 'javascript' | 'dotnet' | 'archive' | 'native';
export type ReaComponentKey = 'skill' | 'rea' | 'jdk' | 'ghidra';
export type ReaCheckStatus = 'passed' | 'failed' | 'incomplete' | 'not_applicable';

export interface ReaEnvironmentCheck {
  key: 'node' | 'npx' | 'storage' | string;
  ok: boolean;
  code?: string | null;
  detail?: string | null;
}

/** Something only the user can do (root rights, a writable directory); localized by ``code``. */
export interface ReaBlocker {
  code: 'jdk_no_dnf' | 'jdk_needs_root' | 'ghidra_dir_not_writable' | 'ghidra_dir_occupied' | 'ghidra_decompiler_missing' | string;
  command?: string;
  path?: string;
  env?: string;
  javaHome?: string;
  platform?: string;
}

export interface ReaComponentState {
  key?: string;
  status: 'reused' | 'installed' | 'failed' | 'blocked' | string;
  attempts?: number;
  detail?: string | null;
  blocker?: ReaBlocker | null;
  /** What was checked, e.g. the official SHA-256 of the Ghidra download. */
  evidence?: { sha256?: string; verified?: boolean; source?: 'mirror' | 'official' | string; rejected?: string[] } | null;
}

export interface ReaCapabilityState {
  key?: string;
  status: ReaCheckStatus | string;
  static?: ReaCheckStatus | string | null;
  runtime?: ReaCheckStatus | string | null;
  expected?: string | null;
  actual?: string | null;
  tools?: string[];
  notes?: string[];
  /** Notes and results left out of conversations (in the plugin square for administrators). */
  hiddenDetails?: number;
  /** REA calls of the check; administrators get what the failed ones returned. */
  calls?: { tool?: string; ok?: boolean; summary?: string | null }[];
  /** The sample checked: file name (or path for administrators), SHA-256, binary format. */
  sample?: { name?: string | null; path?: string | null; sha256?: string | null; format?: string | null } | null;
}

/** Why an install that passed before is not usable now; localized by ``code``. */
export interface ReaProblem {
  code:
    | 'skill_missing' | 'mcp_not_registered' | 'service_down' | 'validation_failed'
    | 'jdk_missing' | 'ghidra_missing' | 'ghidra_decompiler_missing' | string;
  status?: string | null;
  error?: string | null;
  path?: string | null;
  platform?: string | null;
}

export interface ReaInstallJob {
  id: string;
  status: 'running' | 'succeeded' | 'failed';
  stage: ReaInstallStage;
  progress: number;
  tier: ReaTier;
  errorCode?: string | null;
  errorDetail?: string | null;
  blockers?: ReaBlocker[];
  components?: Record<string, ReaComponentState>;
  registration?: 'written' | 'reused' | null;
  validation?: Record<string, ReaCapabilityState>;
  startedAt: number;
  finishedAt?: number | null;
  sessionId?: string | null;
}

export interface ReaUninstallTask {
  kind: 'install' | 'session';
  id: string;
  title: string;
  sessionId?: string | null;
}

export interface ReaUninstallJob {
  id: string;
  status: 'running' | 'succeeded' | 'failed';
  removed: string[];
  kept: string[];
  residues: string[];
  stoppedTasks: ReaUninstallTask[];
  errorDetail?: string | null;
  startedAt: number;
  finishedAt?: number | null;
  previousState?: ReaState | null;
}

/** Evidence of the last tier validation: the service check and each capability. */
export interface ReaVerification {
  ok: boolean;
  tier?: ReaTier | null;
  verifiedAt?: number | null;
  service?: {
    ok?: boolean | null;
    serverVersion?: string | null;
    toolsObserved?: number | null;
    toolsExpected?: number | null;
    requestTool?: string | null;
    requestOk?: boolean | null;
  };
  capabilities?: Record<string, ReaCapabilityState>;
}

export interface ReaStatus {
  package: string;
  version: string;
  state: ReaState;
  installed: boolean;
  tier?: ReaTier | null;
  installedVersion?: string | null;
  canInstall: boolean;
  /** Show the 「新」 badge on the Rex shortcut. */
  badge: boolean;
  firstInstallCompleted: boolean;
  /** This administrator's install conversation, while the install runs and shortly after (recorded by Rex's install). */
  installSessionId?: string | null;
  /** Something of REA is in place that an uninstall would remove. */
  removable: boolean;
  /** Administrators only: what is broken while the state is ``degraded``. */
  degradedProblems: ReaProblem[];
  verification?: ReaVerification | null;
  environment: {
    ok: boolean;
    nodeRequirement: string;
    checks: ReaEnvironmentCheck[];
  };
  job?: ReaInstallJob | null;
  uninstallJob?: ReaUninstallJob | null;
}

export interface ReaUninstallPreview {
  state: ReaState;
  removes: string[];
  keeps: string[];
  tasks: ReaUninstallTask[];
}

export interface ReaReportEntry {
  label: string;
  value: string;
}

export class ReaApiError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly status: number,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = 'ReaApiError';
  }
}

/** HTTP errors arrive as `{error, message}` where message may be `{code, message, ...}`. */
export function toReaApiError(error: unknown): ReaApiError {
  const response = (error as { response?: { status?: number; data?: any } })?.response;
  const payload = response?.data?.message ?? response?.data?.detail;
  if (payload && typeof payload === 'object') {
    return new ReaApiError(
      typeof payload.message === 'string' ? payload.message : 'Request failed',
      typeof payload.code === 'string' ? payload.code : 'request_failed',
      response?.status ?? 0,
      payload as Record<string, unknown>,
    );
  }
  const message = typeof payload === 'string' && payload
    ? payload
    : error instanceof Error ? error.message : String(error);
  return new ReaApiError(message, response ? 'request_failed' : 'network', response?.status ?? 0);
}

async function call<T>(request: Promise<{ data: T }>): Promise<T> {
  try {
    return (await request).data;
  } catch (error) {
    throw toReaApiError(error);
  }
}

export const reaApi = {
  status: (refresh = false) => call<ReaStatus>(
    client.get('/api/rea/status', { params: refresh ? { refresh: true } : undefined }),
  ),
  uninstallPreview: () => call<ReaUninstallPreview>(client.get('/api/rea/uninstall/preview')),
  /** 409 ``tasks_running`` (with ``details.tasks``) while related tasks run and ``stopTasks`` is false. */
  uninstall: (stopTasks: boolean) => call<ReaStatus>(client.post('/api/rea/uninstall', { stopTasks })),
};
