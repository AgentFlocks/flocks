# 生成后的 WebCLI 如何接入 Device 插件

> 本文说明：`web2cli` 已经抓到页面请求、并整理出可复用调用逻辑后，怎样把它沉淀成可在设备页识别、配置和调用的 device 插件。

## 结论

`cli-in-device.md` 不是 `cli-in-skill.md` 的替代物，而是安全设备场景下的进一步封装：

- 所有 `web2cli` 结果都必须先完成 skill 集成
- 如果目标是安全设备接入，再继续按本文档额外生成 device 插件
- 最终交付关系是：`skill` 必选，`device 插件` 为安全设备场景下的额外交付

## 何时使用

在以下场景调用本文档：

- 当前任务明确来自“设备接入”页面，目标是把某个安全设备或安全产品接入到设备管理体系
- 最终产物需要出现在设备页，并允许用户填写实例配置、刷新模板、按 `device_id` 调用
- 当前 WebCLI 抓到的能力属于安全设备能力，而不是单纯给 skill 复用的站点操作脚本

不优先使用本文档的场景：

- 只是想保留一个可复用 CLI 供 agent 在 skill 中调用
- 目标不是设备接入，而是某个通用网站的操作自动化、查询脚本或内部工具
- 暂时只需要沉淀浏览器经验、CLI 参数和认证恢复流程，不需要设备页识别

如果当前任务来自“设备接入”页面，并且目标是安全设备接入，WebCLI 在完成 skill 集成后，还应当额外生成标准 device 插件：

```text
$HOME/.flocks/plugins/tools/device/<plugin_id>/
├── _provider.yaml
├── <domain>.yaml
├── <name>.handler.py
├── <name>_cli.py        # 可选，仅用于调试/回归
└── _test.yaml           # 可选，最小验证样例
```

其中：

- `_provider.yaml`：决定设备页是否能识别该模板，以及用户创建实例时需要填写哪些字段
- `<domain>.yaml`：定义可调用工具、参数和 action
- `<name>.handler.py`：设备运行时入口，负责读取配置、认证、发请求、清洗结果
- `<name>_cli.py`：只作为调试入口保留，不作为设备运行时主路径

认证默认规则：

- 自定义 CLI / WebCLI 默认认证方式为 `cookie/auth-state`：优先复用浏览器保存的 `auth-state.json`，从中按请求域名/path/secure 规则选择 Cookie，并在需要时读取 localStorage
- 默认认证状态文件：`~/.flocks/browser/<name>/auth-state.json`
- 优先使用 `auth_state_path` 指向 `~/.flocks/browser/<name>/auth-state.json`
- 可以额外暴露可选 `username` / `password`，但它们只用于 cookie 失效后的认证恢复，不替代默认的 `auth_state_path`
- 不要生成或使用 `auth_state_json` / `Legacy Auth State JSON` 这类内联 JSON 字段；设备配置只保存 state 文件路径，不粘贴 state 文件内容
- 只有在目标站点确实还依赖额外字段时，才补充 `cookie`、`csrf_token`、`access_token` 或特定认证头；这些字段是 `auth_state_path` 之外的补充，不替代默认的 cookie/auth-state
- 不要把 `cookie` 或 `token` 设计成和 `auth-state` 并列的多个默认入口；如果用户提供的是 state 文件路径，必须写入 `auth_state_path`

## 命名约定

- 插件目录：`$HOME/.flocks/plugins/tools/device/<plugin_id>/`
- `plugin_id`：推荐使用稳定产品名加版本，例如 `<name>_v1_0_0`
- `service_id`：推荐使用稳定能力标识，例如 `<name>_device`
- handler 文件：`<name>.handler.py`
- 可选 CLI 文件：`<name>_cli.py`

约定说明：

- `<name>` 用产品或系统的稳定标识，不用一次性任务名
- 目录名可以带版本；`service_id` 要尽量稳定，避免和临时抓包任务绑定
- Python 文件名统一用 `_`

## 最小 `_provider.yaml`

至少包含以下字段：

```yaml
name: Acme Portal
vendor: acme_security
service_id: acme_portal_device
version: "1.0.0"
integration_type: device
description: >
  Acme Portal WebCLI-backed device integration for alert listing and asset
  detail queries. Configure Base URL and the required login state fields
  separately in the credentials form.
description_cn: >
  Acme Portal 的 WebCLI 设备接入模板，支持告警列表和资产详情查询。
  请在设备配置中分别填写 Base URL 与所需登录态字段。
credential_fields:
  - key: base_url
    label: Base URL
    storage: config
    config_key: base_url
    input_type: url
    required: true
  - key: auth_state_path
    label: Auth State Path
    storage: config
    config_key: auth_state_path
    input_type: text
    default: "~/.flocks/browser/acme-portal/auth-state.json"
  - key: username
    label: Username
    storage: config
    config_key: username
    input_type: text
    required: false
    description: 仅在 cookie 失效后需要 Agent 辅助登录刷新 state 时填写
  - key: password
    label: Password
    storage: secret
    config_key: password
    secret_id: acme_portal_password
    input_type: password
    required: false
    description: 仅在 cookie 失效后需要 Agent 辅助登录刷新 state 时填写
  - key: cookie
    label: Cookie
    storage: secret
    config_key: cookie
    secret_id: acme_portal_cookie
    input_type: password
  - key: csrf_token
    label: CSRF Token
    storage: secret
    config_key: csrf_token
    secret_id: acme_portal_csrf_token
    input_type: password
defaults:
  timeout: 30
  category: custom
notes: |
  WebCLI 设备建议优先复用稳定隐藏接口，不建议把浏览器自动化作为默认运行时。
  若返回 401/403、跳转登录页或 CSRF 失效，应先按认证失效处理。
```

注意：

- 必须包含 `integration_type: device`
- `description` 用英文，`description_cn` 用中文
- 只把运行时真正需要用户填写的字段放进 `credential_fields`
- 不要把真实 cookie、token、密码、auth state JSON 写进插件文件
- 默认先放 `auth_state_path`，并指向 `~/.flocks/browser/<name>/auth-state.json`；不要添加 `auth_state_json` / `Legacy Auth State JSON`
- 可以补充可选 `username` / `password`，但必须标注它们仅用于认证恢复或浏览器辅助登录，不得作为默认运行时认证入口
- `cookie`、`csrf_token`、`access_token` 或特定认证头只有在实际站点需要时再补，并在 handler 中明确说明来源与刷新方式

## 最小工具 YAML

MVP 阶段推荐一个分组工具 + 多个 action：

```yaml
name: acme_portal_ops
description: >
  Acme Portal grouped device tool. Use the action parameter to query alerts,
  assets, and other WebCLI-backed operations.
description_cn: >
  Acme Portal 分组设备工具。通过 action 参数调用告警、资产和其他 WebCLI 能力。
category: custom
enabled: true
requires_confirmation: false
provider: acme_portal_device
inputSchema:
  type: object
  properties:
    action:
      type: string
      enum: [list_alerts, get_asset_detail]
      description: 统一业务动作名，不要暴露内部实现来源。
    alert_id:
      type: string
      description: 查询资产详情时可选使用的关联标识。
  required: [action]
handler:
  type: script
  script_file: acme_portal.handler.py
  function: handle
```

规则：

- `provider` 必须与 `_provider.yaml.service_id` 一致
- 高风险写操作必须设置 `requires_confirmation: true`
- 对外 action 用统一业务语义，不要命名成 `webcli_get_alerts`、`api_get_alerts`

## 最小 handler 结构

MVP 阶段优先单文件 handler，不强制拆 client 模块：

```python
from __future__ import annotations

from typing import Any

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult

SERVICE_ID = "acme_portal_device"


def _service_config() -> dict[str, Any]:
    raw = ConfigWriter.get_api_service_raw(SERVICE_ID)
    return raw if isinstance(raw, dict) else {}


async def handle(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    cfg = _service_config()
    if action == "list_alerts":
        return ToolResult(success=True, output={"items": [], "source": "webcli_api"})
    if action == "get_asset_detail":
        return ToolResult(success=True, output={"item": None, "source": "webcli_api"})
    return ToolResult(success=False, error=f"Unsupported action: {action}")
```

要求：

- 通过 `ConfigWriter.get_api_service_raw(SERVICE_ID)` 读取当前设备实例配置
- handler 内部负责认证头构造、分页、超时、重试和响应归一化
- handler 默认只读取 `auth_state_path` 指向的 `auth-state.json`；如果文件缺失、不是合法 JSON，或没有匹配当前 Base URL 的 Cookie，应返回明确错误并提示重新登录/保存 state
- handler 不要 fallback 到内联 `auth_state_json`；这会把路径字符串、占位文本或过期内容误当 JSON 解析，导致设备测试报错不清晰
- 如果模板提供了 `username` / `password`，handler 也不要在普通 tool 调用里静默自动登录；这些字段只用于后续由 Rex 进入浏览器认证恢复流程时辅助填表
- CLI 可选保留，但不要让设备运行时通过 subprocess 调 CLI

## 组合 API / WebCLI / 处理逻辑

同一设备可以混合多种能力来源，但对外仍然是统一 action：

- `api`：正式 API，可直接调用
- `webcli_api`：WebCLI 抓到的隐藏接口
- `process`：本地字段归一化、过滤、聚合、补全
- `composed`：先调一种来源，再补另一种来源，最后统一输出

推荐选择顺序：

1. 正式 API 稳定可用时，优先正式 API
2. 正式 API 缺能力但 WebCLI 接口稳定时，用 `webcli_api`
3. 需要字段清洗、补全、排序、聚合时，在 handler 内增加 `process`
4. 需要多个来源补齐同一业务结果时，用 `composed`
5. 必须验证码、强动态页面或人工交互时，只记录为 browser fallback，不放进默认设备运行时
6. 如果某个隐藏接口依赖 `Authorization`、`Tdp-Authentication`、CSRF 等临时头，只有在 handler 已实现可靠的恢复/刷新逻辑时才暴露为默认 action；否则保留在 CLI 或文档中，不放进设备默认动作

示例 action 映射：

```yaml
list_alerts: webcli_api
get_asset_detail: composed
list_users: api
normalize_alert: process
```

这里的映射可以写进 handler 常量、注释、`notes` 或单独的设计文档，但不要把“来源类型”直接暴露给最终用户。

## 认证失败处理

出现以下情况时，优先按认证失效处理：

- 返回 `401` 或 `403`
- 返回内容出现 `Unauthorized`、`login`、未登录、无权限
- Cookie / CSRF / access token 明显过期
- `auth_state_path` 已存在，但接口仍跳转登录页

处理原则：

1. 不要无限重试
2. 优先返回明确话术，提示 Rex 使用 `flocks browser` 和对应 skill 的认证失败处理去恢复登录态
3. 如果设备已配置可选 `username` / `password`，Rex 可以在浏览器恢复流程中读取它们辅助登录；如遇验证码、MFA、短信码或人工确认，立即停下并让用户接管
4. 登录成功后执行 `flocks browser state save <auth_state_path>` 更新 cookie/state 文件
5. 如仍失败，再提示用户重新登录或更新设备配置中的认证字段
6. 如果保留了 CLI，可用 CLI 做一次最小验证
7. 验证通过后，再让用户回到设备页点击“刷新设备模板”

## `_test.yaml` 建议

如果该 WebCLI 设备已经有最小可验证动作，建议补一个 `_test.yaml`，至少覆盖：

- 一个低风险读操作
- 最小必填参数
- 成功时的关键字段断言

这样后续更新 handler 或认证逻辑时更容易回归验证。

## 一句话原则

`web2cli` 生成的 CLI 是中间产物；只有在“安全设备接入”场景下，才把它整理成标准 device 插件，让设备页能识别、配置并调用。
