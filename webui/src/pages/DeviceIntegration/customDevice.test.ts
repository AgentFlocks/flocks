import { describe, expect, it } from 'vitest';
import type { DeviceTemplate } from '@/api/device';
import {
  buildCustomDeviceServiceId,
  buildCustomDeviceVendorKey,
  findTemplateForCustomDevice,
} from './customDevice';

describe('customDevice helpers', () => {
  it('sanitizes vendor key and service id', () => {
    expect(buildCustomDeviceVendorKey('Acme Security CN')).toBe('acme_security_cn');
    expect(buildCustomDeviceServiceId('Acme Guard')).toBe('acme_guard_device');
  });

  it('finds matching template by exact or partial name', () => {
    const templates: DeviceTemplate[] = [
      {
        plugin_id: 'existing_v1',
        storage_key: 'existing_v1',
        service_id: 'existing',
        name: 'Existing Device',
        credential_schema: [],
        tool_count: 1,
        installed: true,
        state: 'installed',
        source: 'project',
      },
      {
        plugin_id: 'acme_guard_device_v1',
        storage_key: 'acme_guard_device_v1',
        service_id: 'acme_guard_device',
        name: 'Acme Guard',
        credential_schema: [],
        tool_count: 2,
        installed: true,
        state: 'installed',
        source: 'project',
      },
    ];

    expect(findTemplateForCustomDevice(templates, 'Acme Guard')?.storage_key).toBe('acme_guard_device_v1');
    expect(findTemplateForCustomDevice(templates, 'Acme')?.storage_key).toBe('acme_guard_device_v1');
  });
});
