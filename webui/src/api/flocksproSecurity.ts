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
    };
    source: string;
  };
  hardDeny: {
    systemRuleIds: string[];
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
    decisionMatrix: Record<string, Record<string, string>>;
    runtimeOverrides: Record<string, Record<string, string>>;
    hardDenies: Record<string, boolean>;
    sharedPermissionMode: {
      supported: string[];
      default: string;
    };
    permissionDefaults: Record<string, string>;
    runtimeDefaults: Record<string, string>;
  };
}

export const flocksproSecurityApi = {
  getOverview: async (): Promise<SecurityOverview> =>
    (await client.get('/api/flockspro/policy/security/overview')).data,
  setRollout: async (payload: {
    policy: RolloutMode;
    command: RolloutMode;
    ingress: IngressRolloutMode;
    visibility: RolloutMode;
    filesystem: RolloutMode;
  }): Promise<{
    effective: SecurityOverview['rollout']['effective'];
    source: string;
    message: string;
  }> => (await client.put('/api/flockspro/policy/security/rollout', payload)).data,
};
