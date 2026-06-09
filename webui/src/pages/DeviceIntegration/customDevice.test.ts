import { describe, expect, it } from 'vitest';
import type { CustomDeviceApiDraft, CustomDeviceWebCliDraft } from '@/types';
import type { DeviceTemplate } from '@/api/device';
import {
  buildCustomDevicePrompt,
  buildCustomDeviceServiceId,
  buildCustomDeviceVendorKey,
  findTemplateForCustomDevice,
} from './customDevice';

describe('customDevice helpers', () => {
  it('builds api prompt with device plugin constraints', () => {
    const draft: CustomDeviceApiDraft = {
      accessMode: 'api',
      deviceName: 'Acme Guard',
      vendorName: 'Acme Security',
      version: 'v3.2.1',
      baseUrl: 'https://device.example.com/api',
      docsUrl: 'https://device.example.com/openapi',
      capabilities: '全部 API',
    };

    const prompt = buildCustomDevicePrompt(draft);

    expect(prompt).toContain('设备产品名：Acme Guard');
    expect(prompt).toContain('API 文档链接：https://device.example.com/openapi');
    expect(prompt).toContain('integration_type: device');
    expect(prompt).toContain('~/.flocks/plugins/tools/device/<plugin_id>/');
    expect(prompt).toContain('`name` 必须精确使用产品名：`Acme Guard`');
    expect(prompt).toContain('`service_id` 建议使用：`acme_guard_device`');
    expect(prompt).toContain('`description` / `description_cn` 会直接展示在设备接入页、概览页和 Hub 列表');
    expect(prompt).toContain('更长的兼容性、版本差异、使用限制和调试说明优先写进 `notes`');
    expect(prompt).toContain('tool-builder skill');
    expect(prompt).toContain('返回设备页查看是否已经出现对应 device 插件');
  });

  it('builds webcli prompt with device plugin constraints', () => {
    const draft: CustomDeviceWebCliDraft = {
      accessMode: 'webcli',
      deviceName: 'Acme Portal',
      vendorName: 'Acme Security',
      version: '',
      productUrl: 'https://portal.example.com',
      targetInterfaces: '告警列表和资产详情',
      authHint: 'Cookie + CSRF Token',
    };

    const prompt = buildCustomDevicePrompt(draft);

    expect(prompt).toContain('接入方式：WebCLI');
    expect(prompt).toContain('产品 URL：https://portal.example.com');
    expect(prompt).toContain('需要获取的接口/页面行为：告警列表和资产详情');
    expect(prompt).toContain('browser-use / web2cli');
    expect(prompt).toContain('web2cli skill');
    expect(prompt).toContain('integration_type: device');
    expect(prompt).toContain('~/.flocks/plugins/tools/device/<plugin_id>/');
    expect(prompt).toContain('`service_id` 建议使用：`acme_portal_device`');
    expect(prompt).toContain('`auth_state_path`');
    expect(prompt).toContain('`cookie/auth-state`');
    expect(prompt).toContain('`username` / `password`');
    expect(prompt).toContain('`flocks browser`');
    expect(prompt).toContain('`flocks browser state save <auth_state_path>`');
    expect(prompt).toContain('`api`、`webcli_api`、`process`、`composed`');
    expect(prompt).toContain('不要生成 `auth_state_json`');
    expect(prompt).toContain('返回设备页查看是否已经出现对应 WebCLI device 插件');
  });

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
