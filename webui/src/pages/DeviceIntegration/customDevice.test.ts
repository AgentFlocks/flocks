import { describe, expect, it } from 'vitest';
import type { DeviceTemplate } from '@/api/device';
import {
  buildCustomDeviceModeRoutingPrompt,
  buildCustomDeviceServiceId,
  buildCustomDeviceVendorKey,
  findTemplateForCustomDevice,
} from './customDevice';

describe('customDevice helpers', () => {
  it('sanitizes vendor key and service id', () => {
    expect(buildCustomDeviceVendorKey('Acme Security CN')).toBe('acme_security_cn');
    expect(buildCustomDeviceServiceId('Acme Guard')).toBe('acme_guard_device');
  });

  it('routes custom device onboarding through an explicit access-mode question', () => {
    const prompt = buildCustomDeviceModeRoutingPrompt();

    expect(prompt).toContain('必须先使用 `question` 工具询问用户选择接入方式');
    expect(prompt).toContain('选项固定为「API 接入」「WebCLI 接入」「Workflow 接入」');
    expect(prompt).toContain('Syslog、Kafka 或 Webhook');
    expect(prompt).toContain('【API 接入规则】');
    expect(prompt).toContain('tool-builder skill');
    expect(prompt).toContain('【WebCLI 接入规则】');
    expect(prompt).toContain('web2cli skill');
    expect(prompt).toContain('【Workflow 接入规则】');
    expect(prompt).toContain('不需要创建 device 插件');
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
