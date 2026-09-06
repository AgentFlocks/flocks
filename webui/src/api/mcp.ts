import client from './client';
import type {
  MCPServer,
  MCPServerDetail,
  MCPCredentials,
  MCPCredentialInput,
  MCPCatalogEntry,
  MCPCatalogCategory,
  MCPCatalogStats,
} from '@/types';
import type { ThreatBookRegion } from '@/constants/threatbook';

export type { MCPServer, MCPServerDetail };

export const mcpAPI = {
  list: () =>
    client.get<Record<string, any>>('/api/mcp'),
  
  get: (server: string) =>
    client.get<MCPServerDetail>(`/api/mcp/${server}`),

  update: (server: string, config: Record<string, any>) =>
    client.put<{success: boolean; message: string; config: any}>(`/api/mcp/${server}`, { config }),
  
  connect: (server: string) =>
    client.post(`/api/mcp/${server}/connect`),
  
  disconnect: (server: string) =>
    client.post(`/api/mcp/${server}/disconnect`),
  
  refresh: (server: string) =>
    client.post<number>(`/api/mcp/${server}/refresh`),
  
  getTools: (server: string) =>
    client.get(`/api/mcp/${server}/tools`),
  
  getResources: (server: string) =>
    client.get(`/api/mcp/${server}/resources`),
  
  // Credentials management
  getCredentials: (server: string) =>
    client.get<MCPCredentials>(`/api/mcp/${server}/credentials`),

  revealCredentials: (server: string) =>
    client.post<{api_key: string}>(`/api/mcp/${server}/credentials/reveal`),
  
  setCredentials: (server: string, credentials: MCPCredentialInput) =>
    client.post<{success: boolean; message: string}>(`/api/mcp/${server}/credentials`, credentials),
  
  deleteCredentials: (server: string) =>
    client.delete<{success: boolean}>(`/api/mcp/${server}/credentials`),
  
  testCredentials: (server: string) =>
    client.post<{success: boolean; message: string; latency_ms?: number; tools_count?: number; error?: string}>(
      `/api/mcp/${server}/test-credentials`
    ),

  remove: (server: string) =>
    client.delete<{success: boolean}>(`/api/mcp/${server}`),

  test: (name: string, config: Record<string, any>) =>
    client.post<{success: boolean; message: string; latency_ms?: number; tools_count?: number; error?: string}>(
      '/api/mcp/test',
      { name, config }
    ),

  testExisting: (server: string, config: Record<string, any>) =>
    client.post<{success: boolean; message: string; latency_ms?: number; tools_count?: number; error?: string}>(
      `/api/mcp/${server}/test`,
      { config }
    ),

  configureThreatBook: (server: string, payload: { region: ThreatBookRegion; api_key: string }) =>
    client.post<{
      success: boolean;
      message: string;
      region: ThreatBookRegion;
      endpoint: string;
      connected: boolean;
      tools_count: number;
    }>(`/api/mcp/${server}/threatbook-configure`, payload),

  // Catalog
  catalogList: () =>
    client.get<MCPCatalogEntry[]>('/api/mcp/catalog/entries'),

  catalogCategories: () =>
    client.get<Record<string, MCPCatalogCategory>>('/api/mcp/catalog/categories'),

  catalogStats: () =>
    client.get<MCPCatalogStats>('/api/mcp/catalog/stats'),

  catalogSearch: (params: { query?: string; category?: string; language?: string; tags?: string[]; official_only?: boolean }) =>
    client.post<MCPCatalogEntry[]>('/api/mcp/catalog/search', params),

  catalogGet: (serverId: string) =>
    client.get<MCPCatalogEntry>(`/api/mcp/catalog/entries/${serverId}`),

  catalogInstall: (serverId: string, options?: { enabled?: boolean; env_overrides?: Record<string, string>; credentials?: Record<string, string> }) =>
    client.post<{success: boolean; server_id: string; name: string; config: any; message: string; requires_env: any[]}>('/api/mcp/catalog/install', {
      server_id: serverId,
      ...options,
    }, { timeout: 360000 }),

  catalogAutoSetup: () =>
    client.post<{newly_configured: string[]; skipped: string[]; all_configured_ids: string[]}>('/api/mcp/catalog/auto-setup'),

  catalogConfigured: () =>
    client.get<string[]>('/api/mcp/catalog/configured'),
};
