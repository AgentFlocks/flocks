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
}

export const flocksproSecurityApi = {
  getOverview: async (): Promise<SecurityOverview> =>
    (await client.get('/api/flockspro/policy/security/overview')).data,
  setRollout: async (payload: {
    policy: RolloutMode;
    command: RolloutMode;
    ingress: IngressRolloutMode;
    visibility: RolloutMode;
  }): Promise<{
    effective: SecurityOverview['rollout']['effective'];
    source: string;
    message: string;
  }> => (await client.put('/api/flockspro/policy/security/rollout', payload)).data,
};
