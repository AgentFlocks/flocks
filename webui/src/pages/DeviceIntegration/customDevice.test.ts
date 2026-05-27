import { describe, expect, it } from 'vitest';
import type { APIServiceSummary, CustomDeviceApiDraft, CustomDeviceWebCliDraft } from '@/types';
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
    expect(prompt).toContain('tool-builder skill');
  });

  it('builds webcli prompt with browser capture guidance', () => {
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
    expect(prompt).toContain('CLI');
    expect(prompt).toContain('skill');
  });

  it('sanitizes vendor key and service id', () => {
    expect(buildCustomDeviceVendorKey('Acme Security CN')).toBe('acme_security_cn');
    expect(buildCustomDeviceServiceId('Acme Guard')).toBe('acme_guard_device');
  });

  it('finds matching template by exact or partial name', () => {
    const templates: APIServiceSummary[] = [
      {
        id: 'existing_v1',
        name: 'Existing Device',
        enabled: true,
        status: 'unknown',
        tool_count: 1,
        verify_ssl: false,
        integration_type: 'device',
      },
      {
        id: 'acme_guard_device_v1',
        name: 'Acme Guard',
        enabled: true,
        status: 'unknown',
        tool_count: 2,
        verify_ssl: false,
        integration_type: 'device',
      },
    ];

    expect(findTemplateForCustomDevice(templates, 'Acme Guard')?.id).toBe('acme_guard_device_v1');
    expect(findTemplateForCustomDevice(templates, 'Acme')?.id).toBe('acme_guard_device_v1');
  });
});
