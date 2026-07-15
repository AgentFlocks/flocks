# OneSIG API 调用指南

OneSIG 当前的 6 个 grouped tool（`onesig_login` / `onesig_assets` / `onesig_device` / `onesig_helper` / `onesig_monitoring` / `onesig_strategy`）都遵循"`action` + 业务键平铺"的调用模式。处理器会自动登录、自动续会话、自动按需做 RSA-OAEP 密码加密，所以业务调用通常**不需要**先单独调登录接口。

## 先看这张路由表

| 用户意图 | 推荐 tool | 推荐 action | 必备参数 |
|---|---|---|---|
| 看仪表盘总览 / 出入站 / 零日 | `onesig_monitoring` | `dashboard_overview` / `dashboard_outbound` / `dashboard_inbound` / `dashboard_zeroday` | 通常空参，部分需 `startTime`/`endTime` |
| 看威胁防护大屏（事件/资产/趋势/占比） | `onesig_monitoring` | `overview_*` 系列 | 多数必填 `startTime`+`endTime`，部分要 `incIntervalSec` 或 `interval` |
| 看入站/出站威胁事件列表与详情 | `onesig_monitoring` | `event_inbound_*` / `event_outbound_*` | 列表与详情类必填 `startTime`+`endTime` |
| 看失陷主机统计 / 列表 / 详情 | `onesig_monitoring` | `alert_host_*` | 列表与详情类必填 `startTime`+`endTime`，详情类还要 `source` |
| 看 / 改报表 | `onesig_monitoring` | `report_form_*` / `report_task_*` | 下载需 `uniqueId`+`fileName`，删除需 `uniqueId` |
| 看设备状态（CPU/内存/网口/平台） | `onesig_monitoring` | `device_platform_status` / `device_system_status` / `device_network_status` / `common_interface_list` / `basic_cpu_attr` | `device_system_status` 必填 `time` |
| 改 / 查白黑名单 | `onesig_strategy` | `whitelist_*` / `blacklist_*` | 列表用 `*_list`，删除用 `uniqueIds` |
| 多维封锁规则 | `onesig_strategy` | `multiblock_rule_*` / `multiblock_executelog_*` | 单条规则用 `name` 做主键 |
| API 联动密钥 | `onesig_strategy` | `apikey_*` | `apikey_secret` 必填 `key`+`password` |
| Syslog 自动封禁 | `onesig_strategy` | `auto_blacklist_*` | `auto_blacklist_trend`/`_sample` 必填 `srcIp`（+组合键） |
| FTP/SFTP 联动 | `onesig_strategy` | `linkage_*` | `linkage_info` 必填 `uniqueId` |
| IPS 规则 / 规则集 | `onesig_strategy` | `ips_rule_*` / `ips_ruleset_*` / `ips_threat_types` | 单条规则集查询用 `name`，引用关系用 `ruleId`+`assetIp` |
| HTTP 黑名单 / 高级 / XFF | `onesig_strategy` | `http_blacklist_*` / `*_advanced_config` / `*_xff_config` | 列表 `_list`、写操作走 `_create` / `_update` / `_delete` |
| 高危端口防护 | `onesig_strategy` | `port_protect_group_*` / `port_protect_port_*` | 端口列表 `port_protect_port_list` 必填 `groupName`+`pageNo`+`pageSize` |
| 防护策略首页 | `onesig_strategy` | `protection_policy_*` / `device_onekey_bypass` | `protection_policy_get` 必填 `uniqueId` |
| 资产 / 资产组 / 资产类型 | `onesig_assets` | `asset_*` / `asset_group_*` / `asset_type_*` / `common_asset_group_tree` | 资产用 `uniqueId`（删除还需 `password`）；资产组写操作用 `uid`（新增是 `pid`+`name`）；导入用 `file_path` |
| 告警 / 通知策略 | `onesig_device` | `alert_policy_*` / `*_notice_config` / `test_email/syslog/webhook` | `alert_policy_find_by_config` 必填 `search`+`type` |
| 管理日志（审计） | `onesig_device` | `aclog_*` / `*_clean_config` | 列表 / 导出常需 `startTime`+`endTime`；删除需 `password` |
| 用户与登录策略 | `onesig_device` | `user_*` / `*_login_config` | 改密 / 删除 / 重置密码需 `password`（自动 RSA 加密） |
| HTTPS 解密 | `onesig_device` | `tls_decrypt_policy_*` / `tls_cert_*` / `tls_detect_*` | TLS 详情必填 `server`+`port`+`orderBy`+`sortBy` |
| 网口 / 部署引导 | `onesig_device` | `interface_*` | `interface_select_list` 必填 `workMode` |
| 路由（v4/v6） | `onesig_device` | `route_*` / `ipv6_route_*` | 写操作要带完整路由项 |
| DNS / 代理 / 网络测试 | `onesig_device` | `*_dns_config` / `hosts_*` / `*_proxy_config` / `test_network` / `test_proxy` | hosts 写操作走 create/update/delete |
| 高可用 HA | `onesig_device` | `ha_*` / `*_ha_config` | `ha_sync_status` 必填 `syncId`（前置 `ha_sync_config` 返回） |
| 集中管控 OneCC | `onesig_device` | `*_onecc_config` / `onecc_status` / `set_onecc_status` / `test_onecc` | 写操作前先看 status |
| 设备升级 / 重启 / 备份 / 恢复 | `onesig_device` | `device_upgrade*` / `device_reboot/shutdown/reinit` / `backup_*` / `system_upgrade` | 升级 / 上传备份用 `file_path`，敏感写操作需 `password` |
| 日志外发 | `onesig_device` | `logaccess_*` | 列表必填 `pageNo`+`pageSize`+`type`；样例必填 `srcIp`+`protocol`+`type` |
| 基本信息 / license / MDR | `onesig_device` | `basic_*` / `mdr_service_*` | license / 离线情报库导入用 `file_path` |
| 设备诊断 | `onesig_device` | `device_coredump_*` / `device_pcap_*` | coredump/pcap 删除走 DELETE |
| 帮助文档 / 产品反馈 | `onesig_helper` | `document_list` / `document_preview` / `product_*` | `document_preview` 必填 `id`（来自 `document_list`） |
| 登录 / 退出 / 改密 / 账户 | `onesig_login` | `login` / `logout` / `change_password` / `get_account` 等 | 改密必填 `new_password`，启用 captcha/TOTP 时 `login` 要传 `captcha`/`totp` |

## 通用规则

- OneSIG 全部 grouped tool 的入参形态：`{action: "...", ...业务字段}`（不像 TDP 把所有筛选放进统一 `body`，也不像 OneSEC 用 `time_from`/`time_to`）
- 时间字段统一是 **Unix 秒**：`startTime` / `endTime` / `time`，没有"recent"系列接口
- 分页字段统一是 `pageNo`（默认 1）+ `pageSize`（默认 20）；不要传 `cur_page` / `page_size` / `page_items_num`
- 排序统一是 `sortBy`（字段名）+ `orderBy`（`asc` / `desc`）
- POST 类 action 多数 passthrough body —— 任何过滤字段直接放在请求体顶层即可，与 Web 控制台抓包字段一一对应
- 查询类 action 默认优先；写操作只有用户明确授权时才执行（特别是黑白名单批量写、设备升级、HTTPS 解密策略、HA / OneCC 配置）
- 业务键参考第二节的"业务 ID 字段"；如果没有 ID，应先调对应列表 / namelist / tree 接口拿主键

## 业务 ID 字段对照（`required` 主键）

OneSIG 不同模块用不同字段做"主键"，没有 ID 是无法调单条接口的。如果 agent 拿不到主键，应该**先调对应的列表 / namelist / tree** action 取得主键，再调单条接口。

| 字段 | 用在哪些 action | 含义 |
|---|---|---|
| `id` | `document_preview` | 帮助文档主键（先 `document_list`） |
| `key` + `password` | `apikey_secret` | API Key 名称 + 当前用户登录密码（自动 RSA 加密） |
| `uniqueId` | `asset_update` / `asset_delete`（+`password`）/ `linkage_info` / `protection_policy_get` / `report_form_download` / 多数 `*_delete` 单条目 / 多数全局白黑名单 `*_update` | 资源唯一 ID，先调列表/树拿到 |
| `uniqueIds` | 批量删除（`whitelist_remove_batch` 等于 `globalWhitelist` DELETE、`blacklist_remove_batch` 等于 `globalBlacklist` DELETE、`*_delete` 批量） | 数组形式，多个资源一起删 |
| `uid` | `asset_group_update` / `asset_group_delete`（+`password`） | **资产组**主键（不是 `uniqueId`！） |
| `pid` + `name` | `asset_group_create` | 父级资产组 ID（根 = `0`） + 新组名 |
| `syncId` | `ha_sync_status` | HA 配置同步任务 ID（由前置 `ha_sync_config` 返回） |
| `name` | `multiblock_rule_get` / `multiblock_rule_preview` / `multiblock_executelog_list` / `ips_ruleset_info` / `ips_ruleset_delete` / `asset_type_delete` | OneSIG 用对象名当主键，没有独立数字 ID |
| `groupId` | `port_protect_group_update` / `_delete` / `_clone` 的 `fromGroupId`、`port_protect_port_*` 写操作、`port_protect_portinfo` | 端口防护**组**主键（写操作用） |
| `groupName` | `port_protect_port_list`（端口列表）/ `port_protect_port_export` | 端口防护组**名**（仅查列表 / 导出用，注意与 `groupId` 区分） |
| `ruleId` | `ips_rule_create`（**实际是查单条规则详情**，文档名为「新增 IPS 自定义规则」与实际语义不符）、`ips_ruleset_referred` 的一半 | IPS 规则 ID |
| `ruleId` + `assetIp` | `ips_ruleset_referred` | 单条 IPS 规则 + 涉事资产 IP，反查规则在哪台资产上命中 |
| `srcIp`(+`protocol`+`direction`) | `auto_blacklist_trend` / `auto_blacklist_sample` | 来自 syslog 自动黑名单的源 IP 复合主键 |
| `server` + `port` + `orderBy` + `sortBy` | `tls_detect_list_detail` | TLS 解密目标"主机:端口"复合键 |
| `workMode` | `interface_select_list` | 网口选择列表必须先选定工作模式（内联/旁路） |
| `search` + `type` | `alert_policy_find_by_config` | 按配置反查告警策略 |
| `time` | `device_system_status` | 系统资源采样时间窗（Unix 秒） |
| `incIntervalSec` / `interval` | `overview_asset_brief` / `overview_event_trend` / `overview_stat` | 聚合粒度（秒） |
| `key` (Body) + Query `type=physical` | `apikey_delete` / `_update`，新增 `apikey_create` 也要 `type=physical` | API 联动密钥的接口要求 Query `?type=physical`（厂商文档强制）—— 当前 handler 走 passthrough body，按目前实测把 `type:"physical"` 一起放进 body 即可，但若服务端严格要求 query，请改用浏览器流程 |

## 时间窗口与时区

- 时间戳一律 Unix **秒**（不是毫秒）
- 文档示例按 `Asia/Shanghai`，实际调用时按业务时区计算
- OneSIG 没有"最近 24 小时增量"专用接口；要"最近一段"统一用 `now - 24*3600`、`now - 7*86400` 之类自行算
- 未传时间走服务端默认窗口，仅作兜底，不推荐依赖

## 高频场景

### 1. 看入站 / 出站威胁事件

推荐：`onesig_monitoring` + `event_inbound_list` 或 `event_outbound_list`。

最小示例（查最近 24 小时入站事件）：

```json
{
  "action": "event_inbound_list",
  "startTime": 1745683200,
  "endTime": 1745769600,
  "pageNo": 1,
  "pageSize": 20
}
```

后续动作：

- 拿到事件后调 `event_inbound_detail` / `_detail_trend` / `_detail_list` 看详情、趋势、关联记录
- 导出文件用 `event_inbound_export` / `event_inbound_detail_export`（返回的是文件，不是 JSON）

返回结果重点关注：

- 事件 `uniqueId`、源/目的 IP、威胁类型 `threatType`、威胁等级 `severity`、威胁标签 `threatLabel`、是否 TLS `isTls`

何时回退浏览器：需要事件原始报文、PCAP 取证、详情页攻击链。

### 2. 失陷主机调查

推荐：`onesig_monitoring` + `alert_host_*`。

```json
{
  "action": "alert_host_list",
  "startTime": 1745683200,
  "endTime": 1745769600,
  "pageNo": 1,
  "pageSize": 20
}
```

详情维度：

```json
{
  "action": "alert_host_detail",
  "startTime": 1745683200,
  "endTime": 1745769600,
  "source": "10.0.0.5"
}
```

`source` 是失陷主机的 IP（必填）。`alert_host_detail_list` / `_export` 同步要求 `startTime` / `endTime` / `source`。

### 3. 看仪表盘 / 大屏

仪表盘：`dashboard_overview` / `dashboard_outbound` / `dashboard_inbound` / `dashboard_zeroday` / `dashboard_status` / `dashboard_ioc_type_sum` —— 通常空参或只需时间窗。

威胁防护大屏（`overview_*`）—— 多数需要时间窗 + 聚合粒度：

```json
{
  "action": "overview_asset_brief",
  "startTime": 1745683200,
  "endTime": 1745769600,
  "incIntervalSec": 3600
}
```

```json
{
  "action": "overview_event_trend",
  "startTime": 1745683200,
  "endTime": 1745769600,
  "interval": 3600
}
```

```json
{
  "action": "overview_event_inbound_agg",
  "startTime": 1745683200,
  "endTime": 1745769600,
  "type": "threatType",
  "pageNo": 1,
  "pageSize": 20
}
```

注意：`overview_*_export` 系列返回文件，不要拿来在 chat 里展开。

### 4. 资产管理

推荐：`onesig_assets`。

查列表：

```json
{
  "action": "asset_list",
  "pageNo": 1,
  "pageSize": 20,
  "search": "10.0.0."
}
```

新增 / 更新 / 删除（参数严格按厂商 `assetsSegment.md`）：

```json
{
  "action": "asset_create",
  "name": "host-1",
  "type": "服务器",
  "groupId": 101,
  "ip": ["10.0.0.1", "10.0.0.2"],
  "remark": "生产环境"
}
```

`type` 是字符串字段（不是 `assetType`），`ip` 必须是**字符串数组**（支持单 IP / 网段 / 区间），`groupId` 是数字（先调 `asset_group_get` 拿）。

```json
{
  "action": "asset_update",
  "uniqueId": "asset-id-1",
  "name": "host-1-renamed",
  "type": "服务器",
  "groupId": 101,
  "ip": ["10.0.0.1"]
}
```

更新接口必填 `uniqueId` + `name` + `type` + `groupId` + `ip`（厂商文档要求**全量**字段，不是部分更新）。

```json
{ "action": "asset_delete", "uniqueId": "asset-id-1", "password": "<当前登录用户密码>" }
```

⚠️ **删除资产是单条调用**（仅 `uniqueId`），并且**必须带 `password`**（当前登录用户密码），处理器会自动 RSA-OAEP 加密。要批量删，请按行循环调用。

资产组：`asset_group_get`（拿整树）/ `asset_group_create`（`pid`+`name`）/ `asset_group_update`（`uid`+`name`）/ `asset_group_delete`（`uid`+`password`）。注意资产组主键叫 `uid`，**不是** `uniqueId`。整树缓存用 `common_asset_group_tree`。

资产导入：

```json
{ "action": "asset_import", "file_path": "/abs/path/to/assets.csv" }
```

`asset_template` / `asset_export` 返回文件（CSV）。

### 5. 黑白名单 / 多维封锁 / API 联动

白名单（写）—— 注意 `condition` / `comments` 与 `whiteList` **平级**，不是嵌进数组元素：

```json
{
  "action": "whitelist_add",
  "whiteList": [{ "direction": "inbound" }],
  "condition": [{ "type": "srcIp", "cond": "equal", "value": "10.0.0.1" }],
  "comments": "策略备注"
}
```

新增时 `whiteList` 数组每项**只含 `direction`**（值为 `inbound` / `outbound` / `both`），`condition` 与 `comments` 在顶层。

```json
{
  "action": "whitelist_update",
  "uniqueId": "id-1",
  "direction": "inbound",
  "condition": [{ "type": "srcIp", "cond": "equal", "value": "10.0.0.1" }],
  "comments": "更新备注"
}
```

更新时**没有** `whiteList` 字段，只有 `uniqueId` + 单值 `direction` + `condition` + `comments`。

```json
{ "action": "whitelist_remove_batch", "uniqueIds": ["id-1", "id-2"] }
```

黑名单（同 OneSIG `globalBlacklist`）：

```json
{
  "action": "blacklist_add",
  "blackList": [
    { "object": "1.1.1.1", "direction": "inbound", "threatName": "", "sLifeCycle": "", "comments": "" }
  ]
}
```

新增时整批用 `blackList` 数组；写入前可先 `blacklist_check`（IP 类冲突校验）。`_update` / `_delete`（按 `uniqueIds` 批量）/ `_list` 用法与白名单类似。

多维封锁：

```json
{
  "action": "multiblock_executelog_list",
  "name": "rule-A",
  "startTime": 1745683200,
  "endTime": 1745769600,
  "pageNo": 1,
  "pageSize": 20
}
```

`multiblock_rule_get` / `_preview` 必填 `name`；`_create` / `_update` 是写操作，要谨慎。

API 联动：

```json
{ "action": "apikey_list", "pageNo": 1, "pageSize": 20 }
```

`apikey_list` 必填 `pageNo`+`pageSize`，否则服务端回 `responseCode=1004`。

```json
{ "action": "apikey_secret", "key": "my-key", "password": "<当前登录密码>" }
```

`apikey_secret` 会回吐明文 secret，处理器自动用 RSA-OAEP 加密 `password` 后发送（注意：`password` 在该接口走 Query 而非 Body）—— 不要替用户保存这个值，更不要回显在 chat 里。

写操作（创建 / 更新 / 删除）：厂商接口要求带 Query `?type=physical`。当前 handler 走 passthrough body，请把 `type:"physical"` 一起放进 body 顶层（实测多数实例服务端会接受），如果遇到 `responseCode=1004` 提示 `type` 缺失则回退浏览器模式：

```json
{ "action": "apikey_create", "type": "physical", "name": "soc-bridge" }
```

```json
{ "action": "apikey_update", "type": "physical", "name": "soc-bridge", "key": "k1", "status": 1 }
```

```json
{ "action": "apikey_delete", "type": "physical", "key": "k1" }
```

### 6. IPS 规则 / 规则集

```json
{ "action": "ips_ruleset_namelist" }
```

```json
{ "action": "ips_ruleset_info", "name": "default-ruleset" }
```

```json
{ "action": "ips_ruleset_referred", "ruleId": "rule-001", "assetIp": "10.0.0.5" }
```

⚠️ 注意 `ips_rule_create`（厂商 `POST /v3/ips/rule`）**实际语义是「查单条规则详情」**，body 必填 `ruleId`，不会真的"新增"自定义规则；这是 yaml/handler 命名遗留问题。需要新增/编辑规则集请走 `ips_ruleset_create` / `_update`。`ips_rule_apply` 才是"应用规则变更到目标规则集"的写操作（body：`rulesetsName`数组+`rules`数组），调用前确认变更已就绪。

### 7. HTTP 黑名单 / 端口防护

HTTP 黑：`http_blacklist_list` / `_create` / `_update` / `_delete` / `_enable` / `_export`。

端口防护组：

```json
{ "action": "port_protect_group_list_full" }
```

```json
{
  "action": "port_protect_port_list",
  "groupName": "默认高危端口组",
  "pageNo": 1,
  "pageSize": 20,
  "sortBy": "updateTime",
  "orderBy": "desc"
}
```

⚠️ action 名是 `port_protect_port_list`（**不是** `port_protect_group_port_list`），必填 `groupName`+`pageNo`+`pageSize`；响应里总数字段叫 `data.totalCount`（不是 `data.total`）。

写操作：

- `port_protect_group_create` Body `groupName`；`_update` Body `groupId`+`groupName`；`_delete` Body `groupId`；`_clone` Body `fromGroupId`+`groupName`
- 端口规则：`port_protect_port_create` / `_update` Body `groupId`+`ports`(字符串)+`serviceName`(可选)+`comments`(可选)；`_delete` Body `groupId`+`ports`(数组)；`_onekey_import` Body `groupId`+`ports`(对象数组 `[{ports, serviceName, comments}]`)
- 端口占用预查：`port_protect_portinfo` Body `groupId`+`ports`

### 8. Syslog 自动封禁

```json
{ "action": "auto_blacklist_list", "pageNo": 1, "pageSize": 20 }
```

```json
{ "action": "auto_blacklist_trend", "srcIp": "1.2.3.4", "startTime": 1745683200, "endTime": 1745769600 }
```

```json
{ "action": "auto_blacklist_sample", "srcIp": "1.2.3.4", "protocol": "tcp", "direction": "inbound" }
```

`auto_blacklist_check`（必填 `name` / `port` / `srcIp` / `protocol` / `direction`）是冲突校验，写入前调用。

### 9. 用户管理与改密

查询：`user_list` / `user_export`。

新增用户（写，敏感）—— 字段严格按 `deviceLoginManagement.md`：

```json
{
  "action": "user_create",
  "username": "alice",
  "role": 2,
  "nickname": "",
  "phone": "",
  "expireTime": 0,
  "loginLimit": [],
  "password": "<明文新密码>",
  "dupPassword": "<明文新密码>"
}
```

字段说明：

- `username`（必填，**不是 `name`**）：1～20 字符，符合 `userEditor` 用户名正则
- `role`（必填）：数字角色（如 `0`/`2`/`3`/`4`，`role=3` 跳审计页、`role=4` 跳大屏）
- `expireTime`（必填）：到期时间 Unix 秒；选「长期」时填 `0`
- `loginLimit`（必填）：可登录 IP 字符串数组，ALL 时填 `[]`
- `password` / `dupPassword`（必填）：明文新密码，处理器自动 RSA-OAEP 加密。注意 **handler 只对 `password` 与 `dupPassword`（驼峰）做加密**，不要传 `dup_password`（蛇形不会被加密）

更新用户用 `user_update`，必填 `username`（定位主键，**不可改**）+ `expireTime` + `loginLimit`，可选 `nickname` / `phone`；改密**不在** `user_update` 路径，要走 `Main/changePwdModalData`（即顶栏改密弹窗，agent 用 `change_password` 走 `onesig_login`）。

改密（当前用户）走 `onesig_login`：

```json
{
  "action": "change_password",
  "old_password": "<明文旧>",
  "new_password": "<明文新>",
  "dup_password": "<明文新>"
}
```

`user_secret_reset` / `user_delete` / `aclog_delete` / `interface_update`（启停场景）/ `device_upgrade*` 同样要传 `password`，规则一致。

如果 `POST /v3/login` 返回 responseCode `1009` / `1017` 但密码确实正确，先把 `oaep_hash` 切到 `sha256` 重试 —— OneSIG v2.5.x 多数走 SHA-1（JSEncrypt 默认），但有个别部署是 SHA-256。

### 10. HTTPS 解密

策略：`tls_decrypt_policy_list` / `_create` / `_update` / `_enable` / `_delete` / `_batch`。

证书：`tls_cert_list` / `_create`（multipart：`file_path` 指向 `.crt`/`.pem`）/ `_update` / `_delete` / `_set_default`。

检测对象：

```json
{
  "action": "tls_detect_list_detail",
  "server": "internal-svc.example.com",
  "port": 443,
  "orderBy": "desc",
  "sortBy": "lastSeenTime"
}
```

`server` + `port` + `orderBy` + `sortBy` 这 4 个是必填的复合主键。

### 11. 网口 / 部署引导 / 路由 / DNS

网口：`interface_list` / `interface_select_list`（必填 `workMode`，"内联"/"旁路"二选一）/ `interface_update`（启停时 `password` 必填）/ `interface_check_loop` / `interface_relation_list`。

虚拟线 / 监听 / 桥：`interface_virtual_line_*` / `interface_listen_*` / `interface_bridge_*`。

路由：`route_outif_list` / `route_static_list` / `_create` / `_update` / `_delete` / `route_table_list`，IPv6 同名加 `ipv6_` 前缀。

DNS：`get_dns_config` / `set_dns_config` / `hosts_get` / `hosts_create` / `_update` / `_delete` / `test_network`。

### 12. HA 高可用

```json
{ "action": "ha_status" }
```

```json
{ "action": "ha_sync_config" }
```

```json
{ "action": "ha_sync_status", "syncId": "<上一步返回的 syncId>" }
```

`ha_switching` 是主备切换，**强破坏性写操作**，没有用户明确授权不要调。

### 13. 集中管控 OneCC

```json
{ "action": "onecc_status" }
```

```json
{ "action": "test_onecc", "...": "..." }
```

`set_onecc_config` / `set_onecc_status` 都会改设备纳管状态，调用前先用 `get_onecc_config` / `onecc_status` 看清当前形态。

### 14. 设备配置 / 升级 / 备份

升级：

- `basic_version` / `device_upgrade_info` —— 看版本与可升级状态
- `get_upgrade_config` / `set_upgrade_config` —— 在线升级源配置
- `device_download_package` —— 触发后台下载升级包（写）
- `device_upgrade` —— 已下载的包升级（写，敏感，`password` 必填）
- `device_upgrade_upload` —— 本地上传升级包（multipart：`file_path`，`password` 必填）
- `system_upgrade` —— 系统级升级（multipart：`file_path`）

备份：

```json
{ "action": "backup_list", "pageNo": 1, "pageSize": 20 }
```

```json
{ "action": "backup_create", "name": "manual-2026-04-27" }
```

```json
{ "action": "backup_recover", "uniqueId": "<backup id>" }
```

```json
{ "action": "backup_import", "file_path": "/abs/path/to/backup.tar" }
```

`backup_recover` / `backup_import` / `backup_delete` 都强破坏性，确认后再调。

设备其它：`device_quick_bypass` / `device_onekey_bypass`（流量直通）、`device_reboot` / `device_shutdown` / `device_reinit`（重启 / 关机 / 出厂重置）—— 全是高风险，不要在不需要的时候触发。

### 15. 日志外发

```json
{
  "action": "logaccess_list",
  "pageNo": 1,
  "pageSize": 20,
  "type": "syslog"
}
```

```json
{
  "action": "logaccess_sample",
  "srcIp": "10.0.0.1",
  "protocol": "udp",
  "type": "syslog"
}
```

`logaccess_check` 用来诊断外发链路是否正常。

### 16. 基本信息 / license / MDR

- `basic_information` —— 设备基础信息
- `basic_information_enable` —— 启用模块（写）
- `basic_information_import` —— 离线情报库更新（multipart：`file_path` + Query 需 `name`）
- `basic_license_get` —— 查 license
- `basic_license_upload` —— 授权 license 文件上传（multipart：`file_path`）
- `mdr_service_status` / `mdr_service_enable` —— MDR 服务

### 17. 设备诊断

- `device_coredump_list` / `_download` / `_delete`
- `device_pcap_get` / `_set` / `_file_list` / `_download` / `_file_delete`

抓包写 (`device_pcap_set`) 与删除 (`*_delete`) 是写操作。

### 18. 帮助文档与产品反馈

```json
{ "action": "document_list", "pageNo": 1, "pageSize": 20, "search": "ips" }
```

```json
{ "action": "document_preview", "id": "<文档列表项里的 id>" }
```

⚠️ `document_preview` 必填 Query `id`（**不是 `fileName`**），其值来自 `document_list` 返回的 `data.list[].id`。返回的 `data` 是**路径字符串**（不是对象），前端会拼到 `window.location.origin` 后用 `window.open` 打开 —— agent 拿到这个路径后通常需要走浏览器模式才能查看 PDF/HTML 内容。

`product_news_get` / `product_news_mark_read` / `product_version` / `product_issue` 用于看红点 / 标已读 / 看版本 / 提反馈。其中 `product_news_mark_read` 的 body 应是先 `product_news_get` 返回对象的副本，再把要清除的标记位（如 `documentUpdate`）置 `false`，其它字段保持原值。

### 19. 登录 / 会话

绝大多数业务场景**不需要**显式登录 —— 处理器会按需自动登录、Cookie 过期会自动重登。下面这些场景才需要主动调 `onesig_login`：

- 设备启用了图形验证码（`GET /v3/captcha` 返回 `enableCaptcha=true`）：

  ```json
  { "action": "login", "captcha": "xyzw" }
  ```

  前端校验长度恰为 4。

- 设备启用了 TOTP / 双因素（`enableTotp=true` 或 `POST /v3/login` 返回 `responseCode=1012` 进入扫码页）：

  ```json
  { "action": "login", "totp": "123456" }
  ```

  注意：handler 入参统一叫 `totp`（**不要传 `checksum`**），底层会按场景自动映射 —— inline 模式（`/v3/login` 同屏 TOTP）作为 `checksum` 字段拼进登录 body；扫码模式（先 `/v3/login` 拿到 `responseCode=1012`、再补一次 `/v3/login/totp`）作为 `{"checksum": "..."}` 体提交。也可以传恢复码（最长 12 字符）。

- 想立刻拿账户信息：

  ```json
  { "action": "get_account" }
  ```

- 改当前用户密码：

  ```json
  {
    "action": "change_password",
    "old_password": "<明文旧>",
    "new_password": "<明文新>",
    "dup_password": "<明文新>"
  }
  ```

- 显式退出（调试 / 切换账号）：

  ```json
  { "action": "logout" }
  ```

注意：

- `logout` 会清空内存里的会话**和**已落盘的 Cookie 持久化（`~/.flocks/config/.secret.json` 中以 `onesig_session_cookie__<sha1[:12]>` 命名的条目），下次调用会从 captcha → pubkey → /v3/login 重走完整链路
- `regenerate_recovery_code` 一旦调用，旧恢复码立即失效，请在用户明确授权后再用
- `get_pubkey` / `get_captcha` 通常不需要手动调 —— 处理器在登录前与每次发送 RSA 加密字段前都会自动拉

## 文件类返回 / 上传

下列 action 返回的是**二进制文件**（导出 / 模板 / 下载），不应该在 chat 里直接展开：

- 导出：`asset_export` / `whitelist_export` / `blacklist_export` / `http_blacklist_export` / `port_protect_port_export` / `event_inbound_export` / `event_inbound_detail_export` / `event_outbound_export` / `event_outbound_detail_export` / `alert_host_export` / `alert_host_detail_export` / `tls_detect_group_export` / `tls_detect_list_export` / `aclog_export` / `user_export` / `alert_policy_export` / `multiblock_executelog_export` / `overview_export_*`
- 模板：`asset_template` / `whitelist_template` / `blacklist_template` / `linkage_template`
- 下载：`backup_download` / `report_form_download`（必填 `uniqueId`+`fileName`）/ `device_coredump_download` / `device_pcap_download`

下列 action 是 **multipart 上传**，需要 `file_path` 指向本地绝对路径：

- `asset_import`（CSV）
- `tls_cert_create` / `tls_cert_update`（`.crt`/`.pem`）
- `basic_information_import`（离线情报库包，Query 需 `name`）
- `basic_license_upload` / `system_upgrade` / `device_upgrade_upload`（升级 / 授权包）
- `backup_import`（备份包）

## 高风险写操作清单

以下 action 默认视为高风险，agent 在执行前**必须**先确认用户授权：

- `device_onekey_bypass` / `device_quick_bypass`
- `device_reboot` / `device_shutdown` / `device_reinit`
- `device_upgrade` / `device_upgrade_upload` / `system_upgrade`
- `ha_switching` / `ha_sync_config`（同步会改对端）
- `set_ha_config` / `set_onecc_config` / `set_onecc_status`
- `user_create` / `user_delete` / `user_update` / `user_secret_reset` / `change_password`
- `aclog_delete`
- `whitelist_add` / `whitelist_update` / `whitelist_delete` / `whitelist_remove_batch` / `whitelist_import`
- `blacklist_add` / `blacklist_update` / `blacklist_delete` / `blacklist_remove_batch` / `blacklist_import`
- `multiblock_rule_create` / `multiblock_rule_update` / `multiblock_rule_delete` / `multiblock_rule_active`
- `auto_blacklist_create` / `auto_blacklist_update` / `auto_blacklist_delete`
- `linkage_create` / `linkage_update` / `linkage_delete` / `linkage_enable`
- `ips_rule_create` / `ips_rule_apply` / `ips_rule_all` / `ips_ruleset_create` / `ips_ruleset_update` / `ips_ruleset_delete`
- `http_blacklist_create` / `http_blacklist_update` / `http_blacklist_delete` / `http_blacklist_enable`
- `port_protect_group_create` / `_update` / `_delete` / `_clone`、`port_protect_port_create` / `_update` / `_delete` / `_onekey_import`
- `protection_policy_update` / `protection_policy_delete`
- `tls_decrypt_policy_create` / `_update` / `_enable` / `_delete` / `_batch`
- `tls_cert_create` / `_update` / `_delete` / `_set_default`、`tls_detect_delete`
- `set_decrypt_config` / `set_detect_config`
- `interface_update`（启停）/ `interface_*_create/update/delete`
- `route_static_create` / `_update` / `_delete`、`ipv6_route_static_create` / `_update` / `_delete`
- `set_dns_config` / `hosts_create` / `_update` / `_delete`、`set_proxy_config`
- `backup_create` / `backup_recover` / `backup_delete` / `backup_update` / `backup_import`
- `set_storage_config` / `set_clean_config` / `set_dnslog_config`
- `logaccess_create` / `_update` / `_delete`
- `basic_information_enable` / `basic_information_import` / `basic_license_upload`
- `mdr_service_enable`
- `device_pcap_set` / `device_coredump_delete` / `device_pcap_file_delete`
- `apikey_create` / `apikey_update` / `apikey_delete` / `apikey_secret`（回吐明文 secret，需 `password`）
- `regenerate_recovery_code`
- `report_form_create` / `_delete`、`report_task_create` / `_update` / `_delete` / `_test`
- `set_advanced_config` / `set_xff_config` / `set_scan_config` / `set_login_config` / `set_upgrade_config` / `set_overview_config` / `set_custom_config` / `web_custom_column_set`

## 常见失败原因

- 时间戳传成毫秒（要用秒）
- 错传 `cur_page` / `page_size` / `page_items_num`（应为 `pageNo` / `pageSize`）
- 漏传 OneSIG 必填的"业务 ID"主键 —— `name` / `uniqueId` / `srcIp` / `groupName` / `server`+`port` / `ruleId`+`assetIp` 任意一个缺失都会回 `responseCode=1004`（请求数据非法）
- 单条 GET 类接口忘了先调列表拿主键
- 改密 / 删除 / 启停接口手动加密了 `password` —— 处理器会自己加密，要传**明文**
- `oaep_hash` 配置和设备实际不匹配（多数 v2.5.x 走 SHA-1，少数走 SHA-256），登录返回 `responseCode=1009`/`1017` 时优先怀疑这里
- `api_prefix` 与设备反代部署形态不一致 —— 厂商前端代码里写的是 `/api/v3/...`，但接口规范文档里去掉了 `/api`。我们 v2.5.x 实测的实例多数是直连后端（`/v3/...`），所以 handler 的 `DEFAULT_API_PREFIX=""`；如果换到一个走 nginx 代理的实例，需要把 `api_prefix` 设成 `"/api"`。**现象**：登录前的 `/v3/pubkey` 直接 404，handler 会在错误信息里给出对应提示
- `verify_ssl` 与设备证书不匹配（OneSIG 多用自签证书，默认应**关闭**）—— 现象是 SSL handshake 失败
- 报错"Cookie 过期"：调一下 `onesig_login` 的 `logout` 再让处理器自动重登
- API Key 写操作返回 `responseCode=1004` 提示 `type` 缺失：服务端要求 Query `type=physical`；先尝试把 `type:"physical"` 放进 body 透传，如果仍然 1004 请回退浏览器模式

## 何时回退浏览器

以下情况优先回退浏览器（参考 [browser-workflow.md](browser-workflow.md)）：

- 需要威胁防护大屏的可视化、IOC 关系图、攻击链
- 需要看事件 / 失陷主机的页面级详情、报文 hex view、PCAP 在线播放
- 需要点表格右侧抽屉、复杂筛选弹窗、一些深层报表预览
- 需要图形验证码 / TOTP / 强制改密之类的人工交互
- 需要看 Web 控制台原生导出文件并下载
