import client from './client';
import type { APIServiceCredentialField } from '@/types';

// ---------------------------------------------------------------------------
// Groups (机房) — the current product locks this to a single default room
// (renamable). The list/get/patch endpoints are open; create/delete only
// succeed once the backend enables ``MULTI_GROUP_ENABLED``.
// ---------------------------------------------------------------------------

export interface DeviceGroup {
  id: string;
  name: string;
  description?: string | null;
  sort_order: number;
  created_at: number;
  updated_at: number;
}

export interface DeviceGroupCreate {
  name: string;
  description?: string;
  sort_order?: number;
}

export interface DeviceGroupUpdate {
  name?: string;
  description?: string;
  sort_order?: number;
}

// ---------------------------------------------------------------------------
// Devices (设备实例)
// ---------------------------------------------------------------------------

export interface DeviceIntegration {
  id: string;
  group_id: string;
  name: string;
  storage_key: string;
  service_id: string;
  enabled: boolean;
  verify_ssl: boolean;
  /** Sensitive fields are returned as masked strings (e.g. "sk-***abc").
   *  Non-sensitive fields are returned as plaintext. */
  fields: Record<string, string>;
  /** Indicates which fields have a value persisted. Useful for the
   *  "leave blank = keep current value" UX on secret inputs. */
  fields_set: Record<string, boolean>;
  status: string;
  message?: string | null;
  latency_ms?: number | null;
  checked_at?: number | null;
  created_at: number;
  updated_at: number;
}

export interface DeviceCredentialResponse {
  fields: Record<string, string>;
}

export interface DeviceCredentialRevealRequest {
  field?: string;
}

export interface DeviceIntegrationCreate {
  name: string;
  storage_key: string;
  /** Optional; backend defaults to the default room. */
  group_id?: string;
  /** Optional; backend derives from storage_key when omitted. */
  service_id?: string;
  enabled?: boolean;
  verify_ssl?: boolean;
  fields?: Record<string, string>;
}

export interface DeviceIntegrationUpdate {
  name?: string;
  group_id?: string;
  enabled?: boolean;
  verify_ssl?: boolean;
  /** Partial: keys absent are kept; for sensitive fields, empty-string
   *  values are also treated as "keep existing". */
  fields?: Record<string, string>;
}

export interface DeviceTestResult {
  success: boolean;
  message: string;
  latency_ms?: number | null;
}

export interface DeviceTestRequest {
  /** Override the persisted base_url for this probe only (typically the
   *  current value in the form, before it has been saved). */
  base_url?: string;
  /** Override the persisted verify_ssl for this probe only. */
  verify_ssl?: boolean;
}

export interface DeviceTemplate {
  plugin_id: string;
  storage_key: string;
  service_id: string;
  name: string;
  version?: string | null;
  vendor?: string | null;
  description?: string | null;
  description_cn?: string | null;
  credential_schema: APIServiceCredentialField[];
  tool_count: number;
  installed: boolean;
  state: 'available' | 'installed' | 'updateAvailable' | 'localOnly' | 'broken';
  source: 'bundled' | 'project' | 'global';
}

export interface CustomDeviceTemplateCreate {
  plugin_id: string;
  name: string;
  vendor?: string;
  service_id: string;
  version?: string;
  description?: string;
  description_cn?: string;
  credential_fields: APIServiceCredentialField[];
  tools: Array<{
    name: string;
    description: string;
    description_cn?: string;
    category?: string;
    inputSchema?: Record<string, any>;
    parameters?: Array<Record<string, any>>;
    handler: Record<string, any>;
    response?: Record<string, any>;
    requires_confirmation?: boolean;
  }>;
}

// ---------------------------------------------------------------------------
// Per-device tool settings
// ---------------------------------------------------------------------------

export interface DeviceToolInfo {
  name: string;
  description: string;
  description_cn?: string | null;
  /** 全局工具开关（影响所有同版本设备） */
  enabled_global: boolean;
  /** 本设备的独立覆盖值；null = 未设置，遵从全局 */
  enabled_device: boolean | null;
  /** 最终生效状态 */
  enabled_effective: boolean;
}

export const deviceAPI = {
  // groups
  listGroups: () =>
    client.get<DeviceGroup[]>('/api/devices/groups'),

  createGroup: (data: DeviceGroupCreate) =>
    client.post<DeviceGroup>('/api/devices/groups', data),

  updateGroup: (id: string, data: DeviceGroupUpdate) =>
    client.patch<DeviceGroup>(`/api/devices/groups/${id}`, data),

  deleteGroup: (id: string) =>
    client.delete(`/api/devices/groups/${id}`),

  // devices
  list: (params?: { group_id?: string; refresh?: boolean }) =>
    client.get<DeviceIntegration[]>('/api/devices', { params }),

  listTemplates: (params?: { refresh?: boolean }) =>
    client.get<DeviceTemplate[]>('/api/devices/templates', { params }),

  createCustomTemplate: (data: CustomDeviceTemplateCreate) =>
    client.post<DeviceTemplate>('/api/devices/templates/custom', data),

  get: (id: string) =>
    client.get<DeviceIntegration>(`/api/devices/${id}`),

  revealCredentials: (id: string, field?: string) => {
    const body: DeviceCredentialRevealRequest = field ? { field } : {};
    return client.post<DeviceCredentialResponse>(`/api/devices/${id}/credentials`, body);
  },

  create: (data: DeviceIntegrationCreate) =>
    client.post<DeviceIntegration>('/api/devices', data),

  update: (id: string, data: DeviceIntegrationUpdate) =>
    client.put<DeviceIntegration>(`/api/devices/${id}`, data),

  delete: (id: string) =>
    client.delete(`/api/devices/${id}`),

  test: (id: string, body?: DeviceTestRequest) =>
    client.post<DeviceTestResult>(`/api/devices/${id}/test`, body ?? {}),

  // per-device tool settings
  listDeviceTools: (device_id: string) =>
    client.get<DeviceToolInfo[]>(`/api/devices/${device_id}/tools`),

  updateDeviceTool: (device_id: string, tool_name: string, enabled: boolean) =>
    client.patch<DeviceToolInfo>(`/api/devices/${device_id}/tools/${tool_name}`, { enabled }),
};
