import type {
  CustomDeviceAccessMode,
  CustomDeviceApiDraft,
  CustomDeviceWebCliDraft,
} from '@/types';
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
    '在正式开始构建设备插件之前，必须先做需求澄清：盘点已知信息、列出缺失/不确定信息，并向用户提出必要问题。',
    '当需要用户补充关键信息或澄清不确定项时，使用 `question` 工具明确。',
    '除非用户已经提供了足够的信息，否则不要直接写文件或生成插件；优先通过简短问题确认产品名、厂商、版本、认证方式、目标能力、API/页面文档、测试环境和高风险写操作范围。',
    '澄清问题应聚焦关键阻塞项，一次提出 3 到 6 个最重要的问题；可以给用户一个可直接复制填写的资料清单。',
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
    '- 自定义 CLI / WebCLI 默认认证方式为 `cookie/auth-state`；默认保存位置为 `~/.flocks/browser/<name>/auth-state.json`，优先使用 `auth_state_path`',
    '- 模板应允许可选填写 `username` / `password`：前者用 config text，后者用 secret password；它们仅用于 cookie 失效后的浏览器认证恢复，不替代 `auth_state_path`',
    '- 不要生成 `auth_state_json` 或 `Legacy Auth State JSON` 这类内联 JSON 兜底字段',
    '- `cookie`、`csrf_token`、`access_token` 或特定认证头仅作为补充字段，不要和 `auth_state_path` 并列设计成多个默认认证入口',
    '- skill 集成始终必选；CLI 主脚本默认放在 skill 的 `scripts/` 中，如设备运行时确有需要，可额外在 device 插件目录下保留适配层',
    '- MVP 阶段优先生成单文件 handler；CLI 可以作为调试入口保留，但不应作为设备运行时主路径',
    '运行时能力设计要求：',
    '- 对外暴露统一业务 action，例如 `list_alerts`、`get_asset_detail`，不要把内部实现暴露成“API 版 / WebCLI 版”动作名',
    '- handler 内部允许按 `api`、`webcli_api`、`process`、`composed` 四类来源编排，但对外返回统一结构',
    '- 如果正式 API 稳定可用，优先正式 API；正式 API 缺能力时，再使用 WebCLI 抓到的隐藏接口',
    '- 只有必须页面交互、验证码或强动态状态时，才记录为 browser fallback，不要作为默认设备运行时主路径',
    '- cookie 失效时，返回明确认证失败话术，让 Rex 使用 `flocks browser` 和对应 skill 的认证失败处理刷新登录态；如已配置 `username` / `password`，Rex 可读取后辅助登录，再执行 `flocks browser state save <auth_state_path>`',
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
      '自定义 CLI 默认复用 `cookie/auth-state`；可选暴露 `username` / `password` 仅用于 cookie 失效后的浏览器认证恢复。只有在站点确实需要补充 header、cookie 或 token 时，才额外暴露对应字段。',
      'MVP 阶段优先生成单文件 script handler；可复用 CLI 只作为调试/回归入口，不作为设备运行时主路径。',
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
  templates: DeviceTemplate[],
  deviceName: string,
): DeviceTemplate | undefined {
  const normalized = deviceName.trim().toLowerCase();
  if (!normalized) return undefined;
  return templates.find((template) => template.name.trim().toLowerCase() === normalized)
    ?? templates.find((template) => template.name.trim().toLowerCase().includes(normalized));
}
