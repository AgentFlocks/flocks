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

export function buildCustomDeviceModeRoutingPrompt(): string {
  return [
    '如果没有合适的已安装设备模板，按以下规则引导用户进入自定义设备接入路径：',
    '- 设备提供 API 能力、API 文档或开放接口时，选择「API 接入」。',
    '- 设备没有开放 API、主要通过 Web 控制台操作时，选择「浏览器接入」。',
    '- 数据通过 Syslog、Kafka 或 Webhook 上报时，选择「Workflow 接入」；不要创建 device 插件，请提示用户前往 Workflow 接入完成配置。',
    '只有用户没有明确选择接入方式时，才使用 `question` 工具询问用户选择接入方式，选项固定为「API 接入」「浏览器接入」「Workflow 接入」。',
    '如果用户当前消息已经明确写了「API 接入」或「浏览器接入」，不要再询问接入方式，直接按对应规则继续澄清和推进。',
    '识别到自定义设备时，不要继续输出设备配置 JSON；先完成对应自定义接入资产创建流程。',
    '如果能根据用户描述判断推荐路径，先说明推荐原因，再让用户确认或改选接入方式。',
    '用户确认接入方式后，必须使用下方对应规则继续澄清和推进：',
    '',
    '【API 接入规则】',
    buildCustomDeviceModeInstruction('api'),
    '',
    '【浏览器接入规则】',
    buildCustomDeviceModeInstruction('webcli'),
    '',
    '【Workflow 接入规则】',
    buildCustomDeviceModeInstruction('workflow'),
  ].join('\n');
}

export function buildCustomDeviceModeInstruction(mode: CustomDeviceAccessMode): string {
  if (mode === 'api') {
    return [
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
      '本次接入方式是浏览器接入。',
      '你必须先读取并使用 web2cli skill，再开始捕获与转换流程。',
      '用户会提供产品 URL 和需要获取的接口/页面行为。目标是安全设备接入，需要生成 device 插件。',
      '自定义 CLI 默认复用 `cookie/auth-state`；优先使用 `auth_state_path` 指向 `~/.flocks/browser/<name>/auth-state.json`。',
      '`username` / `password` 仅用于 cookie 失效后的浏览器认证恢复，二者都必须声明为 `storage: secret`，不要把账号或密码明文写入数据库字段。',
      '如果需要保存内联登录态，只能使用 `auth_state`，并声明 `storage: secret` 与 `internal: true`；不要在表单中展示 Cookie、localStorage、token 明文。',
      'handler 只读取 `auth_state_path` 指向的 auth-state 文件；如果文件缺失、过期或无法匹配当前站点，应返回明确错误并提示用户重新登录后保存 state。',
      '只有在站点确实需要补充 header、cookie 或 token 时，才额外暴露对应字段，并且必须使用 `storage: secret`。',
      '最终输出目录和插件结构必须符合 device 插件规范。',
    ].join('\n');
  }
  return [
    '本次是 Workflow 接入引导，不需要创建 device 插件。',
    '请引导用户前往工作流发布页面，根据实际场景选择 Syslog、Kafka 或 Webhook。',
  ].join('\n');
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
