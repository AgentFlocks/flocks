import client from './client';

/** The Python enterprise package of before, still on this machine. */
export interface PreviousEdition {
  version: string | null;
  license_id: string | null;
  license_status: string | null;
  pro_enabled: boolean;
  expires_at: string | number | null;
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
  log_tail?: string[];
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
  manual_command: string;
  upgrade: EnterpriseUpgradeState | null;
}

export interface InstallerOffer {
  url: string;
  sha256: string;
  release_id: string;
  version: string;
  size: number | null;
  filename: string;
}

export interface EnterpriseUpgradeEvent {
  stage: 'downloading' | 'downloaded' | 'installing' | 'done' | 'error';
  version?: string;
  release_id?: string;
  received?: number;
  total?: number | null;
  installer?: string;
  message?: string;
  state?: EnterpriseUpgradeState;
}

export const enterpriseUpgradeApi = {
  situation: async (): Promise<EnterpriseSituation> => (await client.get('/api/enterprise-upgrade')).data,

  check: async (): Promise<InstallerOffer> => (await client.post('/api/enterprise-upgrade/check')).data.offer,

  progress: async (): Promise<EnterpriseUpgradeState | null> =>
    (await client.get('/api/enterprise-upgrade/progress')).data.upgrade,

  /** Downloads, checks and starts the installer; each event of the stream goes to onEvent. */
  start: async (onEvent: (event: EnterpriseUpgradeEvent) => void): Promise<EnterpriseUpgradeState> => {
    const res = await fetch('/api/enterprise-upgrade/start', { method: 'POST', credentials: 'same-origin' }); // secret-guard: allow (fetch's credentials mode)
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
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
      if (event.stage === 'error') throw new Error(event.message || 'upgrade failed');
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
