export type ThreatBookRegion = 'cn' | 'global';

export const THREATBOOK_REGION_CONFIG: Record<ThreatBookRegion, {
  activationUrl: string;
  apiServiceId: string;
  apiEndpoint: string;
  mcpEndpoint?: string;
}> = {
  cn: {
    activationUrl: 'https://x.threatbook.com/flocks/activate',
    apiServiceId: 'threatbook-cn',
    apiEndpoint: 'https://api.threatbook.cn',
    mcpEndpoint: 'https://mcp.threatbook.cn/mcp',
  },
  global: {
    activationUrl: 'https://i.threatbook.io/flocks/activate',
    apiServiceId: 'threatbook-io',
    apiEndpoint: 'https://api.threatbook.io',
  },
};

export const LEGACY_THREATBOOK_GLOBAL_MCP_ENDPOINT = 'https://mcp.threatbook.io/mcp';

export function getDefaultThreatBookRegion(language: string | undefined): ThreatBookRegion {
  return language?.toLowerCase().startsWith('en') ? 'global' : 'cn';
}

export function inferThreatBookRegionFromMcpUrl(url: string | undefined): ThreatBookRegion | null {
  const normalizedUrl = (url || '').trim().toLowerCase();
  if (normalizedUrl.startsWith(LEGACY_THREATBOOK_GLOBAL_MCP_ENDPOINT)) return 'global';
  if (normalizedUrl.startsWith(THREATBOOK_REGION_CONFIG.cn.mcpEndpoint || '')) return 'cn';
  return null;
}
