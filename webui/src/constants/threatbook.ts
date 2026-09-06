export type ThreatBookRegion = 'cn' | 'global';

export const THREATBOOK_REGION_CONFIG: Record<ThreatBookRegion, {
  activationUrl: string;
  apiServiceId: string;
  mcpEndpoint: string;
}> = {
  cn: {
    activationUrl: 'https://x.threatbook.com/flocks/activate',
    apiServiceId: 'threatbook-cn',
    mcpEndpoint: 'https://mcp.threatbook.cn/mcp',
  },
  global: {
    activationUrl: 'https://i.threatbook.io/flocks/activate',
    apiServiceId: 'threatbook-io',
    mcpEndpoint: 'https://mcp.threatbook.io/mcp',
  },
};

export function getDefaultThreatBookRegion(language: string | undefined): ThreatBookRegion {
  return language?.toLowerCase().startsWith('en') ? 'global' : 'cn';
}

export function inferThreatBookRegionFromMcpUrl(url: string | undefined): ThreatBookRegion | null {
  const normalizedUrl = (url || '').trim().toLowerCase();
  if (normalizedUrl.startsWith(THREATBOOK_REGION_CONFIG.global.mcpEndpoint)) return 'global';
  if (normalizedUrl.startsWith(THREATBOOK_REGION_CONFIG.cn.mcpEndpoint)) return 'cn';
  return null;
}
