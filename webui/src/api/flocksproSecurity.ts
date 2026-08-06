import client from './client';

export type RolloutMode = 'shadow' | 'enforce';
export type IngressRolloutMode = 'disabled' | 'shadow' | 'enforce';

export interface SecurityOverview {
  rollout: {
    effective: {
      policy: RolloutMode;
      command: RolloutMode;
      ingress: IngressRolloutMode;
      visibility: RolloutMode;
      filesystem: RolloutMode;
      network?: RolloutMode;
    };
    source: string;
  };
  hardDeny: {
    systemRuleIds: string[];
  };
  network?: {
    hardDeny: string[];
    allowlist: string[];
    blocklist: string[];
    trustedTools: Array<{ name: string; source?: string }>;
    revision: number;
    modeDefaults: Record<string, string>;
  };
  readonlyCeiling: {
    denyPatterns: string[];
  };
  audit: {
    webhookConfigured: boolean;
  };
  filesystem: {
    excludedTools: string[];
    policyVersion: string;
    decisionMatrix: Array<{
      region: string;
      operation: string;
      readonly: string;
      requireConfirm: string;
      autoAllowAll: string;
    }>;
    runtimeOverrides: Array<{
      region: string;
      operation: string;
      devMode: string;
      exeMode: string;
    }>;
    hardDenies: Record<string, boolean>;
    sharedPermissionMode: {
      supported: string[];
      default: string;
    };
    permissionDefaults: Record<string, string>;
    runtimeDefaults: Record<string, string>;
  };
}

export type NetworkSecurityRules = NonNullable<SecurityOverview['network']>;

export const flocksproSecurityApi = {
  getOverview: async (): Promise<SecurityOverview> =>
    (await client.get('/api/flockspro/policy/security/overview')).data,
  setRollout: async (payload: {
    policy: RolloutMode;
    command: RolloutMode;
    ingress: IngressRolloutMode;
    visibility: RolloutMode;
    filesystem: RolloutMode;
    network?: RolloutMode;
  }): Promise<{
    effective: SecurityOverview['rollout']['effective'];
    source: string;
    message: string;
  }> => (await client.put('/api/flockspro/policy/security/rollout', payload)).data,
  getNetworkRules: async (): Promise<NetworkSecurityRules> =>
    (await client.get('/api/flockspro/policy/security/network-rules')).data,
  setNetworkRules: async (payload: {
    allowlist: string[];
    blocklist: string[];
    trustedTools?: Array<{ name: string; source?: string }>;
    revision?: number;
  }): Promise<NetworkSecurityRules> =>
    (await client.put('/api/flockspro/policy/security/network-rules', payload)).data,
};
