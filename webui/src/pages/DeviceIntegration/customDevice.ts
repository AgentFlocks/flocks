import type {
  APIServiceSummary,
  CustomDeviceAccessMode,
  CustomDeviceApiDraft,
  CustomDeviceWebCliDraft,
} from '@/types';

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

function buildApiDeviceRequirements(deviceName: string, vendorName: string): string {
  const vendorKey = buildCustomDeviceVendorKey(vendorName);
  const serviceId = buildCustomDeviceServiceId(deviceName);
  return [
    '请严格遵循当前项目的 device 插件契约，不要生成普通 api 工具后就停止。',
    '最终产物必须落到 `~/.flocks/plugins/tools/device/<plugin_id>/` 目录。',
    '必须生成 `_provider.yaml`，并满足以下要求：',
    `- \`name\` 必须精确使用产品名：\`${deviceName}\``,
    `- \`vendor\` 建议使用：\`${vendorKey}\``,
    `- \`service_id\` 建议使用：\`${serviceId}\``,
    '- 必须包含 `integration_type: device`',
    '- 必须声明 `version`、`credential_fields`、`description`、`description_cn`',
    '- `credential_fields` 只定义运行时需要用户填写的字段，不能把真实凭据写进插件文件',
    '- 至少生成一个可被设备页识别的 device YAML/script 工具，必要时补充 `.handler.py`',
    '- 新建工具默认启用，并确保工具刷新后能出现在设备接入页',
    '完成后请提醒用户在设备页点击“刷新设备模板”。',
  ].join('\n');
}

function buildWebCliSkillRequirements(deviceName: string, vendorName: string): string {
  const vendorKey = buildCustomDeviceVendorKey(vendorName);
  return [
    '请严格遵循当前项目的 web2cli skill 流程，不要只停留在临时抓包或一次性脚本。',
    '最终结果应当生成可复用 CLI，并将 CLI 集成到 skill 中供后续调用。',
    '请优先遵循 `web2cli` skill 中的捕获、导出、生成 CLI、沉淀到 skill 的完整流程。',
    `建议使用厂商标识：\`${vendorKey}\`，产品名：\`${deviceName}\`，保证 skill/CLI 命名清晰可追踪。`,
    '不要把用户在表单里填写的账号、密码、Token、Cookie 直接硬编码到 CLI 或 skill 中。',
    '最终产物应包含清晰的 skill 使用入口、CLI 调用说明，以及必要的认证状态保存方式。',
    '不要把结果伪装成 device 插件，也不要要求用户回到设备页刷新模板。',
  ].join('\n');
}

export function buildCustomDeviceSessionContext(mode: CustomDeviceAccessMode): string {
  if (mode === 'api') {
    return [
      '你是 Flocks 的自定义设备接入助手。',
      '目标是把用户描述的 API 能力接入为可在“设备接入”页面出现的 device 插件，而不是普通 API 服务。',
      '你必须先阅读并参考项目中现有 device 插件结构，例如 `.flocks/plugins/tools/device/*/_provider.yaml` 与对应 YAML/script handler。',
      '不要把用户在表单里填写的账号、密码、Token、Cookie 直接写入插件；这些都应该通过 `credential_fields` 暴露为设备实例配置项。',
      '本次接入方式是 API 接入。',
      '你必须先读取并使用 tool-builder skill，再开始生成插件。',
      '用户会提供 API 文档链接或后续上传文档文件，请根据文档盘点接口后生成 device 插件。',
      '优先选择 YAML-HTTP；如存在签名、登录换 token、复杂预处理，则使用 YAML-Script + handler。',
      '虽然参考 tool-builder skill，但最终输出目录和插件结构必须符合 device 插件规范。',
    ].join('\n');
  }
  if (mode === 'webcli') {
    return [
      '你是 Flocks 的自定义设备接入助手。',
      '本次接入方式是 WebCLI 接入。',
      '你必须先读取并使用 web2cli skill，再开始捕获与转换流程。',
      '用户会提供产品 URL 和需要获取的接口/页面行为。你可以使用 web2cli 思路抓取接口，但最终结果应当是 CLI 集成到 skill 中。',
      '不要只留下临时 CLI 或抓包产物；最终结果必须是可复用 CLI + skill 集成入口。',
    ].join('\n');
  }
  return [
    '本次是 Syslog 引导，不需要创建 device 插件。',
  ].join('\n');
}

export function buildCustomDeviceWelcomeMessage(mode: CustomDeviceAccessMode): string {
  if (mode === 'api') {
    return '请补充 API 文档链接或直接上传文档文件，并说明目标能力，我会用 tool-builder skill 帮你生成可在设备页使用的 device 插件。';
  }
  if (mode === 'webcli') {
    return '请补充产品 URL、目标接口和认证提示，我会用 web2cli skill 帮你生成 CLI 并集成到 skill 中。';
  }
  return 'Syslog 方式不在这里创建插件，请前往工作流集成页面配置。';
}

export function buildCustomDevicePrompt(
  draft: CustomDeviceApiDraft | CustomDeviceWebCliDraft,
): string {
  const header = [
    `设备产品名：${draft.deviceName}`,
    `厂商名称：${draft.vendorName}`,
    draft.version ? `产品版本：${draft.version}` : '',
    '',
  ].filter(Boolean);

  if (draft.accessMode === 'api') {
    return [
      ...header,
      buildApiDeviceRequirements(draft.deviceName, draft.vendorName),
      '',
      '接入方式：API',
      `Base URL：${draft.baseUrl}`,
      `期望能力范围：${draft.capabilities.trim() || '全部 API'}`,
      draft.docsUrl.trim() ? `API 文档链接：${draft.docsUrl.trim()}` : '',
      '',
      '必须先读取并使用 tool-builder skill，再根据 API 文档盘点接口并生成 device 插件。',
      '请根据 API 文档盘点接口并生成 device 插件。若当前没有完整文档，请在后续 Rex 对话中继续索取或等待用户上传文档文件，不要假设接口细节。',
    ].filter(Boolean).join('\n');
  }

  return [
    ...header,
    buildWebCliSkillRequirements(draft.deviceName, draft.vendorName),
    '',
    '接入方式：WebCLI',
    `产品 URL：${draft.productUrl}`,
    `需要获取的接口/页面行为：${draft.targetInterfaces}`,
    draft.authHint.trim() ? `认证/权限提示：${draft.authHint.trim()}` : '',
    '',
    '必须先读取并使用 web2cli skill，再执行捕获、导出与转换流程。',
    '请使用 browser-use / web2cli 思路分析页面请求，并把结果生成 CLI 后集成到 skill 中，而不是只保留临时脚本。',
  ].filter(Boolean).join('\n');
}

export function findTemplateForCustomDevice(
  templates: APIServiceSummary[],
  deviceName: string,
): APIServiceSummary | undefined {
  const normalized = deviceName.trim().toLowerCase();
  if (!normalized) return undefined;
  return templates.find((template) => template.name.trim().toLowerCase() === normalized)
    ?? templates.find((template) => template.name.trim().toLowerCase().includes(normalized));
}
