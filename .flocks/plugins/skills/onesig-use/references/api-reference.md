# OneSIG Strategy API 调用指南

当前项目内最新 OneSIG 工具包来自 `.flocks/plugins/tools/device/onesig_v2_5_3`，它不是旧控制台 Cookie 登录版的 6 个 grouped tool，而是 OneSIG v2.5.3 第三方 Strategy API 接入：

- `onesig_strategy_api_query`：只读查询
- `onesig_strategy_api_ops`：写操作

这两个工具使用 `ApiKey + Secret` 做 HMAC-SHA1 签名，调用 `/api/v3/...` 接口。不要把旧控制台插件的 `onesig_monitoring` / `onesig_strategy` / `onesig_assets` / `onesig_device` / `onesig_login` / `onesig_helper` 文档套到当前 Strategy API 工具上。

## 先看这张路由表

| 用户意图 | 推荐 tool | 推荐 action | body 说明 |
|---|---|---|---|
| 查平台运行状态 | `onesig_strategy_api_query` | `platform_status` | 通常空对象 |
| 查系统状态 | `onesig_strategy_api_query` | `system_status` | 通常空对象 |
| 查网络状态 | `onesig_strategy_api_query` | `network_status` | 通常空对象 |
| 查资产组 | `onesig_strategy_api_query` | `asset_group_list` | GET 类接口，通常空对象 |
| 查资产列表 | `onesig_strategy_api_query` | `asset_list` | 可传 `pageNo`、`pageSize`、`search` 等厂商文档字段 |
| 查资产类型 | `onesig_strategy_api_query` | `asset_type_list` | GET 类接口，通常空对象 |
| 查防护策略 | `onesig_strategy_api_query` | `protection_policy_list` | 可传分页 / 筛选字段 |
| 查全局白名单 | `onesig_strategy_api_query` | `whitelist_list` | 可传分页 / 筛选字段 |
| 查全局黑名单 | `onesig_strategy_api_query` | `blacklist_list` | 可传分页 / 筛选字段 |
| 查封禁白名单 | `onesig_strategy_api_query` | `banned_whitelist_list` | 可传分页 / 筛选字段 |
| 查 HTTP 黑名单 | `onesig_strategy_api_query` | `http_blacklist_list` | 可传分页 / 筛选字段 |
| 新建 / 更新 / 删除资产组 | `onesig_strategy_api_ops` | `asset_group_create` / `asset_group_update` / `asset_group_delete` | 按 api-onesig-2.5.3 文档填写 |
| 新建 / 更新 / 删除资产 | `onesig_strategy_api_ops` | `asset_create` / `asset_update` / `asset_delete` | 按 api-onesig-2.5.3 文档填写 |
| 更新 / 删除防护策略 | `onesig_strategy_api_ops` | `protection_policy_update` / `protection_policy_delete` | 先查策略列表拿业务 ID |
| 新建 / 更新 / 删除 / 移除全局白名单 | `onesig_strategy_api_ops` | `whitelist_create` / `whitelist_update` / `whitelist_delete` / `whitelist_remove` | 写入前先确认方向、条件和影响范围 |
| 新建 / 更新 / 删除 / 移除全局黑名单 | `onesig_strategy_api_ops` | `blacklist_create` / `blacklist_update` / `blacklist_delete` / `blacklist_remove` | 写入前先确认对象和影响范围 |
| 新建 / 更新 / 删除封禁白名单 | `onesig_strategy_api_ops` | `banned_whitelist_create` / `banned_whitelist_update` / `banned_whitelist_delete` | 写入前先确认对象和影响范围 |
| 新建 / 更新 / 启停 / 删除 HTTP 黑名单 | `onesig_strategy_api_ops` | `http_blacklist_create` / `http_blacklist_update` / `http_blacklist_enable` / `http_blacklist_delete` | `enable` 也属于写操作 |

## 通用规则

- 入参优先使用：

```json
{
  "action": "asset_list",
  "body": {
    "pageNo": 1,
    "pageSize": 20
  }
}
```

- handler 也兼容把业务字段平铺在 `action` 同级，但文档和新请求默认使用 `body`，避免和旧控制台工具混淆。
- `GET` 类 action 只发送签名 query，不发送 JSON body；不要依赖 `body` 给 `asset_group_list` / `asset_type_list` 传筛选条件。
- 分页字段使用 `pageNo` / `pageSize`。
- 排序 / 筛选字段按厂商 `api-onesig-2.5.3` 文档填写；如果不确定字段名，先用最小参数查询，不要猜旧控制台字段。
- 当前 Strategy API action 没有 `startTime` / `endTime`，也没有 OneSEC 的 `time_from` / `time_to`；不要混用时间字段。
- 查询优先；写操作必须在用户明确授权后执行。
- 写操作前应先通过 list action 获取当前对象和业务 ID，确认不会误改 / 误删。
- 当 `delete` 与 `remove` 的语义不确定时，不要猜；先查厂商文档或只执行只读查询。

## 认证与配置

`onesig_v2_5_3` 的 provider 信息：

- `service_id`: `onesig_api`
- `version`: `2.5.3`
- `base_url`: OneSIG 设备地址；缺少协议时 handler 自动补 `https://`
- `api_key`: ApiKey，优先读服务配置，也支持 secret `onesig_v2_5_3_api_key`
- `secret`: Secret，优先读服务配置，也支持 secret `onesig_v2_5_3_secret`
- 环境变量兜底：`ONESIG_V2_5_3_BASE_URL`、`ONESIG_V2_5_3_API_KEY`、`ONESIG_V2_5_3_SECRET`
- `verify_ssl`: 默认 `false`，适合自签证书设备；需要严格校验证书时在服务配置中开启

handler 会自动把以下签名参数追加到请求 query：

```text
apikey=<ApiKey>
timestamp=<UnixSeconds>
sign=<Base64(HMAC-SHA1(ApiKey + timestamp, Secret))>
```

agent 不需要、也不应该手工生成签名。

## 只读查询示例

### 1. 查设备平台状态

```json
{
  "action": "platform_status",
  "body": {}
}
```

### 2. 查系统状态

```json
{
  "action": "system_status",
  "body": {}
}
```

### 3. 查网络状态

```json
{
  "action": "network_status",
  "body": {}
}
```

### 4. 查资产列表

```json
{
  "action": "asset_list",
  "body": {
    "pageNo": 1,
    "pageSize": 20,
    "search": "10.0.0."
  }
}
```

返回后重点关注资产唯一标识、资产名称、IP / 网段、资产组和资产类型字段。字段名以设备实际返回为准。

### 5. 查资产组与资产类型

```json
{
  "action": "asset_group_list",
  "body": {}
}
```

```json
{
  "action": "asset_type_list",
  "body": {}
}
```

资产写操作前，先查这两个接口确认可用分组和类型。

### 6. 查防护策略列表

```json
{
  "action": "protection_policy_list",
  "body": {
    "pageNo": 1,
    "pageSize": 20
  }
}
```

策略更新 / 删除前，必须先用列表结果确认目标策略 ID、名称、作用范围和当前状态。

### 7. 查全局白名单 / 黑名单 / 封禁白名单

```json
{
  "action": "whitelist_list",
  "body": {
    "pageNo": 1,
    "pageSize": 20
  }
}
```

```json
{
  "action": "blacklist_list",
  "body": {
    "pageNo": 1,
    "pageSize": 20
  }
}
```

```json
{
  "action": "banned_whitelist_list",
  "body": {
    "pageNo": 1,
    "pageSize": 20
  }
}
```

### 8. 查 HTTP 黑名单

```json
{
  "action": "http_blacklist_list",
  "body": {
    "pageNo": 1,
    "pageSize": 20
  }
}
```

## 写操作示例

写操作统一调用 `onesig_strategy_api_ops`，且必须确认用户授权。下面示例只展示调用形态；业务字段必须按设备版本对应的 `api-onesig-2.5.3` 文档填写，不要从旧控制台插件文档里硬搬字段。

### 1. 新建资产组

```json
{
  "action": "asset_group_create",
  "body": {
    "...": "按厂商文档填写资产组字段"
  }
}
```

### 2. 更新资产

```json
{
  "action": "asset_update",
  "body": {
    "...": "先从 asset_list 获取目标资产 ID，再按厂商文档填写完整更新字段"
  }
}
```

### 3. 更新防护策略

```json
{
  "action": "protection_policy_update",
  "body": {
    "...": "先从 protection_policy_list 获取目标策略，再按厂商文档填写"
  }
}
```

### 4. 新建全局黑名单

```json
{
  "action": "blacklist_create",
  "body": {
    "...": "按厂商文档填写黑名单对象、方向、备注等字段"
  }
}
```

### 5. 启停 HTTP 黑名单

```json
{
  "action": "http_blacklist_enable",
  "body": {
    "...": "按厂商文档填写目标 ID 与启停状态"
  }
}
```

`http_blacklist_enable` 会改变防护行为，视为写操作。

## 高风险写操作清单

以下 action 默认视为高风险，agent 在执行前必须先确认用户授权、对象、范围和回滚预案：

- `asset_group_create`
- `asset_group_update`
- `asset_group_delete`
- `asset_create`
- `asset_update`
- `asset_delete`
- `protection_policy_update`
- `protection_policy_delete`
- `whitelist_create`
- `whitelist_update`
- `whitelist_delete`
- `whitelist_remove`
- `blacklist_create`
- `blacklist_update`
- `blacklist_delete`
- `blacklist_remove`
- `banned_whitelist_create`
- `banned_whitelist_update`
- `banned_whitelist_delete`
- `http_blacklist_create`
- `http_blacklist_update`
- `http_blacklist_enable`
- `http_blacklist_delete`

## 返回与错误处理

- handler 会把成功响应中的 `data` 解包为 tool output；如果响应不是对象，会原样返回。
- 如果响应包含 `response_code` 且不是 `0`，handler 会把结果标记为失败，并返回 `verbose_msg` 或原始响应。
- HTTP `4xx` / `5xx` 会返回状态码和前 500 字符响应体。
- `OneSIG Strategy API base_url is not configured.` 表示服务地址未配置。
- `OneSIG Strategy API ApiKey and Secret are required.` 表示 ApiKey / Secret 未配置或 secret 引用无法解析。
- `Request failed` 常见原因是设备网络不可达、证书验证配置不匹配或代理 / 防火墙拦截。

## 当前 Strategy API 不覆盖的场景

以下任务不要调用当前两个 Strategy API 工具硬凑，应改用浏览器模式，或在确认存在额外旧控制台工具并阅读对应文档后再处理：

- 威胁监控、仪表盘、威胁防护大屏
- 入站 / 出站威胁事件、失陷主机、报告管理、导出报表
- 多维封锁、API 联动密钥管理、Syslog 自动封禁、FTP/SFTP 联动
- IPS 规则 / 规则集、高危端口防护
- 告警通知、审计日志、用户管理、登录 / 改密
- HTTPS 解密、网口、部署引导、路由、DNS、代理
- HA、OneCC、设备升级 / 重启 / 备份 / 恢复
- 日志外发、license、MDR、coredump、pcap、帮助文档、产品反馈
- 图形验证码、TOTP、强制改密等人工交互

## 何时回退浏览器

以下情况优先回退浏览器（参考 [browser-workflow.md](browser-workflow.md)）：

- 当前 Strategy API 没有覆盖用户目标能力
- 需要页面级图表、详情抽屉、攻击链、威胁图、报表预览或文件下载
- 需要图形验证码 / TOTP / 强制改密之类的人工交互
- API 未配置、认证失败、设备不可达或反复返回权限 / 签名错误
- 用户明确要求使用浏览器
