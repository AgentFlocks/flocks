import type { CustomDeviceAccessMode } from '@/types';
import type { DeviceTemplate } from '@/api/device';

function sanitizeSlug(value: string, fallback: string): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return normalized || fallback;
}

export function buildCustomDeviceVendorKey(vendorName: string): string {
  return sanitizeSlug(vendorName, 'custom_vendor');
}

export function buildCustomDeviceServiceId(deviceName: string): string {
  const base = sanitizeSlug(deviceName, 'custom_device');
  return base.endsWith('_device') ? base : `${base}_device`;
}

function buildBaseDeviceSessionContext(): string[] {
  return [
    '你是 Flocks 的自定义设备接入助手。',
    '在正式开始构建设备插件之前，必须先做需求澄清：盘点已知信息、列出缺失/不确定信息，并向用户提出必要问题。',
    '当需要用户补充关键信息或澄清不确定项时，使用 `question` 工具明确。',
    '除非用户已经提供了足够的信息，否则不要直接写文件或生成插件；优先通过简短问题确认产品名、厂商、版本、认证方式、目标能力、API/页面文档。',
    '澄清问题应聚焦关键阻塞项，一次提出 3 到 6 个最重要的问题（支持多选）。',
    '不要把用户在表单里填写的账号、密码、Token、Cookie 直接写入插件；这些都应该通过 `credential_fields` 暴露为设备实例配置项。',
  ];
}

export function buildCustomDeviceSessionContext(mode: CustomDeviceAccessMode): string {
  if (mode === 'api') {
    return [
      ...buildBaseDeviceSessionContext(),
      '目标是把用户描述的 API 能力接入为可在“设备接入”页面出现的 device 插件，而不是普通 API 服务。',
      '本次接入方式是 API 接入。',
      '你必须先读取并使用 tool-builder skill，再开始生成插件。',
      '用户会提供 API 文档链接或后续上传文档文件，请根据文档盘点接口后生成 device 插件。',
      '优先选择 YAML-HTTP；如存在签名、登录换 token、复杂预处理，则使用 YAML-Script + handler。',
      '最终输出目录和插件结构必须符合 device 插件规范。',
    ].join('\n');
  }
  if (mode === 'webcli') {
    return [
      ...buildBaseDeviceSessionContext(),
      '本次接入方式是 WebCLI 接入。',
      '你必须先读取并使用 web2cli skill，再开始捕获与转换流程。',
      '用户会提供产品 URL 和需要获取的接口/页面行为。目标是安全设备接入，需要生成 device 插件。',
      '自定义 CLI 默认复用 `cookie/auth-state`；可选暴露 `username` / `password` 仅用于 cookie 失效后的浏览器认证恢复。只有在站点确实需要补充 header、cookie 或 token 时，才额外暴露对应字段。',
      '最终输出目录和插件结构必须符合 device 插件规范。',
    ].join('\n');
  }
  return [
    '本次是 Workflow 接入引导，不需要创建 device 插件。',
    '请引导用户前往工作流发布页面，根据实际场景选择 Syslog、Kafka 或 Webhook。',
  ].join('\n');
}

export function buildCustomDeviceWelcomeMessage(mode: CustomDeviceAccessMode): string {
  if (mode === 'api') {
    return [
      '请提供待接入设备的 API 资料。',
      '',
      '建议包含以下内容：',
      '1. 产品、厂商与版本信息',
      '2. API 文档链接或文档附件',
      '3. Base URL 或典型部署地址',
      '4. 认证方式与凭据类型',
      '',
      '资料确认后，Rex 将生成可在设备接入页识别和配置的 device 插件。',
    ].join('\n');
  }
  if (mode === 'webcli') {
    return [
      '请提供待接入设备的 Web 控制台资料。',
      '',
      '建议包含以下内容：',
      '1. 产品、厂商与版本信息',
      '2. 登录 URL 或目标页面 URL',
      '3. 需要沉淀的页面行为或接口',
      '4. 认证限制、权限要求与可用登录态',
      '',
      '资料确认后，Rex 将沉淀 WebCLI 资产，并按需生成可在设备接入页识别和配置的 device 插件。',
    ].join('\n');
  }
  return 'Workflow 接入不在这里创建插件，请前往工作流发布页面，根据需要配置 Syslog、Kafka 或 Webhook。';
}

export function findTemplateForCustomDevice(
  templates: DeviceTemplate[],
  deviceName: string,
): DeviceTemplate | undefined {
  const normalized = deviceName.trim().toLowerCase();
  if (!normalized) return undefined;
  return templates.find((template) => template.name.trim().toLowerCase() === normalized)
    ?? templates.find((template) => template.name.trim().toLowerCase().includes(normalized));
}
