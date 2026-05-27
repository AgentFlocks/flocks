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

function buildBaseDevicePluginRequirements(deviceName: string, vendorName: string): string[] {
  const vendorKey = buildCustomDeviceVendorKey(vendorName);
  const serviceId = buildCustomDeviceServiceId(deviceName);
  return [
    '最终产物必须落到 `~/.flocks/plugins/tools/device/<plugin_id>/` 目录。',
    '必须生成 `_provider.yaml`，并满足以下要求：',
    `- \`name\` 必须精确使用产品名：\`${deviceName}\``,
    `- \`vendor\` 建议使用：\`${vendorKey}\``,
    `- \`service_id\` 建议使用：\`${serviceId}\``,
    '- 必须包含 `integration_type: device`',
    '- 必须声明 `version`、`credential_fields`、`description`、`description_cn`',
    '- `description` / `description_cn` 会直接展示在设备接入页、概览页和 Hub 列表，不能留空，也不能写成“自定义设备插件”这类泛化占位文案',
    '- `description` 用英文、`description_cn` 用中文；都要明确说明：这是哪个产品/版本、提供什么能力、主要认证方式，以及用户需要填写哪些关键连接信息',
    '- 首段先写 1 到 3 句高密度摘要；更长的兼容性、版本差异、使用限制和调试说明优先写进 `notes`',
    '- 如果这是某个产品的特殊版本或兼容变体，必须在描述中写清楚差异点，避免与同系列标准插件混淆',
    '- `credential_fields` 只定义运行时需要用户填写的字段，不能把真实凭据写进插件文件',
    '- 至少生成一个可被设备页识别的 device YAML/script 工具，必要时补充 `.handler.py`',
    '- 新建工具默认启用，并确保工具刷新后能出现在设备接入页',
  ];
}

function buildBaseDeviceSessionContext(): string[] {
  return [
    '你是 Flocks 的自定义设备接入助手。',
    '你必须先阅读并参考项目中现有 device 插件结构，例如 `.flocks/plugins/tools/device/*/_provider.yaml` 与对应 YAML/script handler。',
    '请重点参考现有 `_provider.yaml` 中 `description`、`description_cn`、`notes` 的写法，因为这些内容会直接影响设备页展示效果。',
    '不要把用户在表单里填写的账号、密码、Token、Cookie 直接写入插件；这些都应该通过 `credential_fields` 暴露为设备实例配置项。',
  ];
}

function buildApiDeviceRequirements(deviceName: string, vendorName: string): string {
  return [
    '请严格遵循当前项目的 device 插件契约，不要生成普通 api 工具后就停止。',
    ...buildBaseDevicePluginRequirements(deviceName, vendorName),
    '完成后请提醒用户返回设备页查看是否已经出现对应 device 插件，并继续后续配置。',
  ].join('\n');
}

function buildWebCliDeviceRequirements(deviceName: string, vendorName: string): string {
  return [
    '请严格遵循当前项目的 web2cli skill 流程，不要只停留在临时抓包或一次性脚本。',
    'WebCLI 接入必须先按 `references/cli-in-skill.md` 沉淀到 skill 中；这是必选步骤，用于承载 CLI 使用说明、浏览器恢复和认证失效处理。',
    '如果目标是安全设备接入，还必须在 skill 集成完成后，再额外生成可在设备页识别、配置和调用的标准 device 插件。',
    '请优先遵循 `web2cli` skill 中的捕获、导出、生成 CLI 的完整流程，再把沉淀出的能力包装为长期资产。',
    ...buildBaseDevicePluginRequirements(deviceName, vendorName),
    '- 自定义 CLI / WebCLI 默认认证方式为 `auth-state`；默认保存位置为 `~/.flocks/browser/<name>/auth-state.json`，优先使用 `auth_state_path`',
    '- `auth_state_json`、`cookie`、`csrf_token`、`access_token` 仅作为补充或兜底字段，不要和 `auth-state` 并列设计成多个默认认证入口',
    '- skill 集成始终必选；CLI 主脚本默认放在 skill 的 `scripts/` 中，如设备运行时确有需要，可额外在 device 插件目录下保留适配层',
    '- MVP 阶段优先生成单文件 handler；CLI 可以作为调试入口保留，但不应作为设备运行时主路径',
    '运行时能力设计要求：',
    '- 对外暴露统一业务 action，例如 `list_alerts`、`get_asset_detail`，不要把内部实现暴露成“API 版 / WebCLI 版”动作名',
    '- handler 内部允许按 `api`、`webcli_api`、`process`、`composed` 四类来源编排，但对外返回统一结构',
    '- 如果正式 API 稳定可用，优先正式 API；正式 API 缺能力时，再使用 WebCLI 抓到的隐藏接口',
    '- 只有必须页面交互、验证码或强动态状态时，才记录为 browser fallback，不要作为默认设备运行时主路径',
    '- 高风险写操作必须设置 `requires_confirmation: true`',
    '完成后请提醒用户返回设备页查看是否已经出现对应 WebCLI device 插件，并继续后续配置。',
  ].join('\n');
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
      '虽然参考 tool-builder skill，但最终输出目录和插件结构必须符合 device 插件规范。',
    ].join('\n');
  }
  if (mode === 'webcli') {
    return [
      ...buildBaseDeviceSessionContext(),
      '本次接入方式是 WebCLI 接入。',
      '你必须先读取并使用 web2cli skill，再开始捕获与转换流程。',
      '用户会提供产品 URL 和需要获取的接口/页面行为。你可以使用 web2cli 思路抓取接口，但必须先完成 skill 集成；如果目标是安全设备接入，再额外生成 device 插件。',
      '自定义 CLI 默认复用 `auth-state`；只有在站点确实需要补充 header、cookie 或 token 时，才额外暴露对应字段。',
      'MVP 阶段优先生成单文件 script handler；可复用 CLI 只作为调试/回归入口，不作为设备运行时主路径。',
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
    return '请补充产品 URL、目标接口和认证提示，我会用 web2cli skill 先生成并集成 CLI/skill 资产；如果是安全设备接入，再额外生成 device 插件。默认使用 auth-state。';
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
    buildWebCliDeviceRequirements(draft.deviceName, draft.vendorName),
    '',
    '接入方式：WebCLI',
    `产品 URL：${draft.productUrl}`,
    `需要获取的接口/页面行为：${draft.targetInterfaces}`,
    draft.authHint.trim() ? `认证/权限提示：${draft.authHint.trim()}` : '',
    '',
    '必须先读取并使用 web2cli skill，再执行捕获、导出与转换流程。',
    '请使用 browser-use / web2cli 思路分析页面请求，并先完成 skill 集成；如果当前目标是安全设备接入，再把结果整理成 device 插件，不要只保留临时脚本。',
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
