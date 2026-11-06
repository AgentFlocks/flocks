import client from './client';
import { getCurrentRelease, type VersionInfo } from './update';
import { extractErrorMessage } from '@/utils/error';
import { ENTERPRISE_UPDATE_CHANNEL } from '@/utils/updateDismissal';

/** The Python enterprise package of before, still on this machine. */
export interface PreviousEdition {
  version: string | null;
  license_id: string | null;
  license_status: string | null;
  pro_enabled: boolean;
  expires_at: string | number | null;
  max_admins?: number | null;
  max_members?: number | null;
  /** When the licence last heard from Console; when it came to this machine, if nothing since. */
  last_sync_at?: string | number | null;
  install_id?: string | null;
  fingerprint?: string | null;
}

/** The last upgrade started from the page (flocks.updater.enterprise.read_state). */
export interface EnterpriseUpgradeState {
  phase: 'downloading' | 'installing' | 'finished' | 'failed' | 'command';
  release_id?: string;
  version?: string;
  received?: number;
  total?: number | null;
  installer?: string;
  command?: string;
  prepare_command?: string | null;
  message?: string;
  exit_code?: number;
  started_at?: string;
  /** The attempt this record is of (the upgrade stream's started event says which that is). */
  attempt?: string;
  /** The installer's log on the machine, once it has started. */
  log?: string;
  log_tail?: string[];
  /** Once it has failed: the step it stopped at. */
  failed_step?: 'download' | 'install';
  /** Once it has failed: why, in the installer's words. */
  reason?: string | null;
  /** Once it has failed: how the machine was left (restored, unchanged, retained, failed, unknown). */
  recovery?: string | null;
}

export interface EnterpriseSituation {
  available: boolean;
  previous: PreviousEdition | null;
  platform: string;
  account: string;
  root: boolean;
  systemd: boolean;
  install_root: string;
  install_root_ready: boolean;
  prepare_command: string | null;
  can_start: boolean;
  /** Empty where the installer cannot run (not a Unix machine). */
  upgrade: EnterpriseUpgradeState | null;
}

export interface InstallerOffer {
  url: string;
  sha256: string;
  release_id: string;
  version: string;
  size: number | null;
  release_notes?: string | null;
  filename: string;
}

/** An application for a licence this machine sent to Console (flocks.updater.enterprise_application). */
export interface LicenceApplication {
  request_id: string;
  /** pending, reviewing, approved, activated, rejected, cancelled, expired_unactivated */
  status: string;
  license_type: 'poc' | 'commercial' | null;
  company: string | null;
  applicant_name: string | null;
  applicant_email: string | null;
  reason: string | null;
  suggestion: string | null;
  license_id: string | null;
  license_status: string | null;
  expires_at: string | number | null;
  created_at: string | null;
  updated_at: string | null;
  can_withdraw: boolean;
  can_take: boolean;
}

export interface LicenceApplications {
  items: LicenceApplication[];
  /** Why the open ones could not be asked of Console just now; null when they were. */
  note: string | null;
  /** A submission went out and Console's answer never came: sending again gets it back. */
  pending_submission?: boolean;
}

export interface LicenceApplicationForm {
  license_type: 'poc' | 'commercial';
  company: string;
  applicant_name: string;
  applicant_email: string;
  applicant_phone: string;
  sales_rep_name?: string | null;
  notes?: string | null;
}

export interface EnterpriseUpgradeEvent {
  stage: 'started' | 'downloading' | 'downloaded' | 'installing' | 'done' | 'error';
  attempt?: string;
  version?: string;
  release_id?: string;
  received?: number;
  total?: number | null;
  installer?: string;
  message?: string;
  state?: EnterpriseUpgradeState;
}

/** The server said it stopped, and why; a stream that merely ended says neither. */
export class EnterpriseUpgradeRefused extends Error {}

export const enterpriseUpgradeApi = {
  situation: async (): Promise<EnterpriseSituation> => (await client.get('/api/enterprise-upgrade')).data,

  check: async (): Promise<InstallerOffer> => (await client.post('/api/enterprise-upgrade/check')).data.offer,

  progress: async (): Promise<EnterpriseUpgradeState | null> =>
    (await client.get('/api/enterprise-upgrade/progress')).data.upgrade,

  applications: async (): Promise<LicenceApplications> =>
    (await client.get('/api/enterprise-upgrade/applications')).data,

  apply: async (form: LicenceApplicationForm): Promise<LicenceApplication> =>
    (await client.post('/api/enterprise-upgrade/applications', form)).data.application,

  withdraw: async (requestId: string): Promise<LicenceApplication> =>
    (await client.post(`/api/enterprise-upgrade/applications/${encodeURIComponent(requestId)}/withdraw`)).data.application,

  /** Writes the approved licence on this machine; the machine's situation after it. */
  take: async (requestId: string): Promise<{ application: LicenceApplication; situation: EnterpriseSituation }> =>
    (await client.post(`/api/enterprise-upgrade/applications/${encodeURIComponent(requestId)}/take`)).data,

  /** Downloads, checks and starts the installer; each event of the stream goes to onEvent. */
  start: async (onEvent: (event: EnterpriseUpgradeEvent) => void): Promise<EnterpriseUpgradeState> => {
    const res = await fetch('/api/enterprise-upgrade/start', { method: 'POST', credentials: 'same-origin' }); // secret-guard: allow (fetch's credentials mode)
    if (!res.ok || !res.body) {
      // The server's refusal says why (signed out, already upgrading...); the status alone does not.
      const said = await res.json().catch(() => null) as { detail?: unknown; message?: unknown } | null;
      const detail = typeof said?.detail === 'string' ? said.detail : typeof said?.message === 'string' ? said.message : '';
      throw new EnterpriseUpgradeRefused(detail || `HTTP ${res.status}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalState: EnterpriseUpgradeState | null = null;
    const handle = (line: string) => {
      if (!line.startsWith('data:')) return;
      let event: EnterpriseUpgradeEvent;
      try {
        event = JSON.parse(line.slice(5).trim());
      } catch {
        return;
      }
      onEvent(event);
      if (event.stage === 'error') throw new EnterpriseUpgradeRefused(event.message || 'upgrade failed');
      if (event.stage === 'done' && event.state) finalState = event.state;
    };
    try {
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        lines.forEach(handle);
        if (done) {
          if (buffer.trim()) handle(buffer);
          break;
        }
      }
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
    if (!finalState) throw new Error('the upgrade stream ended early');
    return finalState;
  },
};

/** Where the page for the enterprise upgrade is: applications, Console login, what root has to run. */
export const ENTERPRISE_UPGRADE_PAGE = '/settings/flockspro';

/**
 * The version check of a machine with an enterprise licence, in the terms of any other: its update
 * is the new enterprise edition, which Console has for it. A Console that will not say is an error
 * to show, not "up to date".
 */
export const enterpriseVersionInfo = async (checkFailed?: string): Promise<VersionInfo> => {
  const current = await getCurrentRelease().catch(() => null);
  const base = {
    current_version: current?.current_version ?? '',
    channel: ENTERPRISE_UPDATE_CHANNEL,
    release_notes: null,
    release_url: null,
    ...(current?.deploy_mode ? { deploy_mode: current.deploy_mode } : {}),
    ...(current?.update_allowed === false ? { update_allowed: false } : {}),
  };
  try {
    const offer = await enterpriseUpgradeApi.check();
    return { ...base, latest_version: offer.version, has_update: true, error: null };
  } catch (err) {
    return { ...base, latest_version: null, has_update: false, error: extractErrorMessage(err, checkFailed) };
  }
};
