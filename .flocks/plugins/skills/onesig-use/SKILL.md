---
name: onesig-use
description: 用于处理 OneSIG（安全互联网网关 / Secure Internet Gateway）相关任务，当前项目内优先适配 OneSIG Strategy API v2.5.3（`onesig_strategy_api_query` / `onesig_strategy_api_ops`）：设备状态、资产、策略、全局白名单、全局黑名单、封禁白名单、HTTP 黑名单的查询与写操作。只要用户提到 OneSIG、SIG、安全互联网网关、微步互联网网关等相关操作时，必须先加载本 skill。本 skill 是 OneSIG 平台操作的唯一决策入口：在未阅读本 skill 并完成模式判断前，不要直接调用任何 `onesig_*` tool，也不要把 OneSEC 的调用约定套用到 OneSIG。
---

# OneSIG Use

## First

先判断操作模式：API V.S Browser。

### 何时使用 API

- 默认先判断当前诉求是否被新版 Strategy API 覆盖；覆盖时优先使用 API
- 查询类请求与配置写入类请求必须严格区分；用户没有明确要求写操作时，默认只使用只读查询能力
- 当前项目内最新工具包是 `.flocks/plugins/tools/device/onesig_v2_5_3`，只暴露两个 grouped tool：
  - `onesig_strategy_api_query`：只读查询，`requires_confirmation=false`
  - `onesig_strategy_api_ops`：写操作，`requires_confirmation=true`

当前 Strategy API 覆盖的能力：

- 设备状态：平台状态、系统状态、网络状态
- 资产：资产组、资产列表、资产类型，以及资产 / 资产组的新增、更新、删除
- 防护策略：策略列表、策略更新、策略删除
- 全局白名单、全局黑名单、封禁白名单
- HTTP 黑名单

### 何时使用浏览器

- 当前 Strategy API 没有覆盖目标能力，例如威胁监控、仪表盘、失陷主机、入站 / 出站事件、报告、IPS、Syslog 自动封禁、FTP/SFTP 联动、高危端口防护、用户管理、HTTPS 解密、网口 / 路由 / DNS、HA、OneCC、升级备份、license、MDR、诊断、帮助文档
- API 工具未检测到、未配置、无权限、ApiKey / Secret 缺失、认证失败、SSL 验证失败或服务不可达
- 任务必须查看页面级详情、攻击链、威胁图、报表预览或人工确认弹窗
- 页面需要人工登录、图形验证码、TOTP、强制改密或页面级确认
- 用户明确要求使用浏览器，或者已经在浏览器操作过程中

### 请求确认

除非用户要求使用浏览器，否则如果 API 不可用，应提示用户检查 OneSIG Strategy API 配置，或确认是否改用浏览器模式。

当确定操作模式后：

- API 模式：必须阅读 API 模式使用指南
- 浏览器模式：必须阅读浏览器模式使用指南

## API 模式使用指南

必须阅读：

各 grouped tool 与 action 的详细说明、最小调用示例、失败处理见 [references/api-reference.md](references/api-reference.md)。

核心调用约定：

- `onesig_strategy_api_query` 用于所有只读查询
- `onesig_strategy_api_ops` 用于所有写操作；调用前必须确认影响范围
- 入参优先使用 `{ "action": "...", "body": { ... } }`
- Strategy API 认证是 `ApiKey + Secret` 的 HMAC-SHA1 签名，handler 会自动生成 `apikey` / `timestamp` / `sign`，agent 不要手工拼签名
- Strategy API 走 `/api/v3/...` 路径；不要混用旧控制台 Cookie 版 `/v3/...` 工具说明
- 分页字段使用 `pageNo` / `pageSize`；不要传 OneSEC 的 `cur_page` / `page_size` / `page_items_num`
- 当前 Strategy API action 没有 OneSEC 风格的 `time_from` / `time_to`；不要把 OneSEC 时间参数套进 OneSIG
- 查询单条或写操作前，如果缺少业务 ID，应先调用对应 list action 获取主键，再执行下一步

高风险写操作要特别谨慎，例如：

- `asset_group_create` / `asset_group_update` / `asset_group_delete`
- `asset_create` / `asset_update` / `asset_delete`
- `protection_policy_update` / `protection_policy_delete`
- `whitelist_create` / `whitelist_update` / `whitelist_delete` / `whitelist_remove`
- `blacklist_create` / `blacklist_update` / `blacklist_delete` / `blacklist_remove`
- `banned_whitelist_create` / `banned_whitelist_update` / `banned_whitelist_delete`
- `http_blacklist_create` / `http_blacklist_update` / `http_blacklist_enable` / `http_blacklist_delete`

## 浏览器模式使用指南

- 如果 OneSIG 设备的访问地址不清楚，请先询问用户，不要擅自填写域名
- 用 `--headed` 打开浏览器，人工完成登录（OneSIG 部署可能启用了图形验证码 / TOTP / 强制改密策略）
- OneSIG 控制台与 OneSEC / 青藤是不同产品；不要把 OneSEC 的页面路径或 OneSEC 的 API 套用到 OneSIG

只要进入浏览器模式，就请阅读并按照 browser-workflow 操作，不要直接跳过本 skill 去套用其他通用浏览器 skill。

请严格按照以下文档执行：

- [references/browser-workflow.md](references/browser-workflow.md)
