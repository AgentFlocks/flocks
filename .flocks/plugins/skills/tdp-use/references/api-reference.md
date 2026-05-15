# TDP API 调用指南

本 skill 默认直接调用 `tdp_api` provider 下的 tool。

## 先看这张路由表

| 用户意图 | 推荐 tool | 常用 action | 最小参数 |
|---|---|---|---|
| 看安全态势、趋势、TOP 统计 | `tdp_dashboard_status` | `status` / `security` / `threat_event` / `alert_level_trend` | 通常可空参；列表类 action 可补 `machine_type`、`severity`、分页参数 |
| 查原始告警日志 | `tdp_log_search` | `search` | `time_from`、`time_to`、`sql` |
| 查字段聚合统计 | `tdp_log_search` | `terms` | `time_from`、`time_to`、`term` |
| 查威胁事件列表 | `tdp_incident_list` | `search` | `time_from`、`time_to`；可补 `severity`、`phase`、`result`、`keyword`、分页参数 |
| 看事件时间线 / 结果分布 / 攻击者明细 | `tdp_incident_list` | `timeline` / `result_distribution` / `attacker_ip_detail` | 通常先要 `incident_id` |
| 查外部攻击严重性分布 | `tdp_threat_inbound_attack` | 默认 | `time_from`、`time_to`；可补 `severity`、`result_list`、`keyword` |
| 查告警主机汇总 / 主机下事件 | `tdp_host_threat_list` | `summary` / `events` | `summary` 可补 `severity`、`direction`、`threat_type`、`keyword`；`events` 至少要 `asset_machine` |
| 查脆弱性 | `tdp_vulnerability_list` | 默认 | 常见补 `time_from`、`time_to`、`severity`、`status`、`keyword`、分页参数 |
| 查弱口令 | `tdp_login_weakpwd_list` | 默认 | 常见补 `time_from`、`time_to`、`data`、`result`、`app_class`、`keyword` |
| 查服务 / 主机 / 框架资产 | `tdp_machine_asset_list` | `service_list` / `host_asset_list` / `web_app_framework_list` | 可空参；常见补 `service`、`service_class`、`is_public`、`keyword` |
| 查域名资产 | `tdp_assets_domain_list` | 默认 | 可空参；常见补 `domain_name_or_ip`、`second_level_domain`、`is_public` |
| 查登录入口 | `tdp_login_api_list` | `list` / `summary` / `category` | 常见补时间范围、`threat_tag`、`keyword`、`is_public` |
| 查上传接口 | `tdp_asset_upload_api` | `summary` / `host_list` / `interface_list` | 常见补时间范围、`host`、`keyword`、分页参数 |
| 查 API 接口 / API 风险 | `tdp_interface_list` / `tdp_interface_risk_list` | 默认 | 常见补 `host`、`methods`、`api_risk_type`、`keyword`、分页参数 |
| 查隐私拓扑 / 云服务访问 | `tdp_privacy_diagram` / `tdp_cloud_facilities` | `access_source` / `instance_list` 等 | 常见补时间范围、`itag`、`methods`、`cloud_vendor`、`keyword` |
| 查 MDR 研判列表 / 指标 | `tdp_mdr_alert_list` | `list` / `indicator` | 常见补时间范围、`section_list`、`threat_severity`、`judge_result_status`、`keyword` |
| 查系统状态 | `tdp_system_status` | `all` / `core` / `database` 等 | 通常空参 |
| 下载 PCAP / 恶意文件 | `tdp_pcap_download` / `tdp_file_download` | 默认 | `alert_id + occ_time` 或 `hash` |
| 管理平台配置 / 策略 | `tdp_platform_config` / `tdp_policy_settings` | 多 action | 写操作，必须先获用户确认 |

## 时间参数注意事项（重点）

调用任何时间相关 API 时，必须**动态计算**时间戳，禁止手动估算。

** 错误方法（禁止） **
```python
# 手动估算，硬编码
time_from = 1740332800  # 瞎猜的值
```

** 正确方法 **
```
import datetime

# 动态获取今日时间范围
now = datetime.datetime.now()
today_start = int(datetime.datetime.combine(now.date(), datetime.time.min).timestamp())
today_end = int(datetime.datetime.combine(now.date(), datetime.time.max).timestamp())

# 使用计算出的时间戳
tdp_log_search(time_from=today_start, time_to=today_end, sql="...")
``` 


## 优先级原则：

- 优先使用 tool schema 暴露的顶层语义化参数。
- `keyword` 会由 handler 自动转换成正确的 `condition.fuzzy.fieldlist`，不要手写 `fuzzy`。
- 列表类工具的 `cur_page`、`page_size`、`sort_by`、`sort_order` 会由 handler 自动转换成 `page`，不要为了普通分页手写 `page`。
- `tdp_log_search` 是特例：日志搜索没有 `cur_page` / `page_size`，控制返回条数必须用 `size`。
- 外部攻击严重性分布是特例：攻击结果参数名必须用 `result_list`，不要写成 `result`。
- `condition` / `page` 只作为高级兼容入口；只有顶层参数没有覆盖目标底层字段时才使用。

最常见的调用形态是：

```json
{
  "action": "search",
  "time_from": 1741536000,
  "time_to": 1741622400
}
```

带筛选、关键词和分页时，优先这样传：

```json
{
  "severity": [3, 4],
  "keyword": "nginx",
  "cur_page": 1,
  "page_size": 20,
  "sort_by": "severity",
  "sort_order": "desc"
}
```

易错参数速查：

| tool | 正确参数 | 不要使用 | 说明 |
|---|---|---|---|
| `tdp_log_search` | `size` | `page_size`、`cur_page` | `/api/v1/log/searchBySql` 只支持返回数量控制，不支持分页参数 |
| `tdp_log_search.sql` | 过滤表达式 | `SELECT * FROM alert` | TDP 只接受类似 WHERE 条件的表达式，不接受完整 SQL 查询 |
| `tdp_log_search(action="terms")` | 可只传 `term` | 强制补 `sql` | `/api/v1/log/terms` 的 `sql` 是可选过滤条件 |
| `tdp_threat_inbound_attack` | `result_list` | `result` | API 文档字段为 `condition.result_list` |
| `tdp_incident_list` | `result` | `result_list` | 事件搜索 API 文档字段为 `condition.result` |


示例：

```json
{
  "time_from": 1741536000,
  "time_to": 1741622400
}
```

## 先区分事件、告警和主机

在 TDP 中，这几类数据不是一回事：

| 用户实际要查什么 | 推荐 tool | 说明 |
|---|---|---|
| 威胁事件 / 攻击事件 / 事件总览 | `tdp_incident_list` / `tdp_threat_inbound_attack` | 事件维度，平台已聚合 |
| 告警 / 告警日志 / 原始检测记录 | `tdp_log_search` | 告警维度，一条就是一条原始记录 |
| 告警主机 / 受害主机 / 主机下事件 | `tdp_host_threat_list` | 主机维度，按主机聚合 |
| 漏洞、弱口令、登录入口、API 风险等 | 对应资产或风险类 tool | 资产/风险维度，不要混进日志查询 |

建议按下面的用词来路由：

- 提到“告警”“最近一小时告警”“查某 IP 的告警”时，默认优先 `tdp_log_search`
- 提到“威胁事件”“攻击事件”“看下最近有什么事件”时，优先 `tdp_incident_list`
- 提到“哪些主机被打了”“告警主机”“受害主机”时，优先 `tdp_host_threat_list`
- 用户没说清时，默认把“明细”理解为告警日志，把“总览/聚合”理解为事件

## 高频场景

### 1. 安全看板 / 态势统计

推荐：

- `tdp_dashboard_status`

高频 action：

- `status`: 整体概览
- `security`: 安全统计
- `threat_event`: 威胁事件统计
- `threat_topic`: 威胁主题
- `alert_level_trend`: 告警级别趋势
- `attack_assets_all` / `attack_assets_public` / `attack_assets_new`: 攻击资产视角
- `vulnerability`: 脆弱性看板
- `login_api`: 登录入口看板
- `privacy_info`: 敏感信息看板

最小示例：

```json
{
  "action": "status"
}
```

带时间范围的示例：

```json
{
  "action": "alert_level_trend",
  "time_from": 1741536000,
  "time_to": 1741622400
}
```

返回结果重点关注：

- 总数、趋势、TOP 排名、按级别/类型聚合结果
- 是否存在 `list`、`rows`、`series`、`data` 等列表字段

### 2. 原始告警日志检索

推荐：

- `tdp_log_search`
- `action=search`

如果只是常规查告警，直接按本节示例构造查询即可；只有在 SQL 操作符、字段名、枚举值不确定时，再查看 [instruction.md](instruction.md)。

注意：这里的 `sql` 不是数据库 SQL，不支持 `SELECT * FROM alert`、`FROM`、`JOIN` 这类完整查询语句。它只接受 TDP 日志查询过滤表达式，例如：

```sql
threat.level = 'attack'
threat.level = 'attack' AND threat.result = 'success'
threat.name LIKE '%SQL注入%'
machine = '192.168.1.100'
```

最小可运行参数集：

- `action=search` 时 `sql` 为必填

```json
{
  "action": "search",
  "time_from": 1741536000,
  "time_to": 1741622400,
  "sql": "threat.level = 'attack'",
  "net_data_type": ["attack", "risk", "action"],
  "size": 10
}
```

注意：`tdp_log_search` 不支持 `page_size`，限制返回条数要用 `size`。

按页面常见字段返回的示例：

```json
{
  "action": "search",
  "time_from": 1741536000,
  "time_to": 1741622400,
  "sql": "threat.level = 'attack' AND threat.result = 'success'",
  "net_data_type": ["attack"],
  "columns": [
    {"label": "类型", "value": ["threat.level", "threat.result"]},
    {"label": "日期", "value": "time"},
    {"label": "威胁名称", "value": "threat.name"},
    {"label": "源IP", "value": "net.src_ip"},
    {"label": "目的IP", "value": "net.dest_ip"}
  ],
  "size": 10
}
```

查询单条告警详情时，优先用全字段模式：

```json
{
  "action": "search",
  "time_from": 1741536000,
  "time_to": 1741622400,
  "sql": "threat.id = '1769'",
  "net_data_type": ["attack", "risk", "action"],
  "size": 10
}
```

返回结果重点关注：

- `threat.result`
- `threat.name`
- `net.src_ip` / `net.dest_ip`
- `net.http.url`
- `threat.msg`
- `threat.id`
- 原始报文、payload、HTTP 请求/响应相关字段

常见失败原因：

- `sql` 字段和值不匹配
- 数值字段误加引号
- `time_from` / `time_to` 单位错成毫秒
- 需要全字段详情却仍在使用精简列模式

字段、操作符或枚举值仍不确定时，再下钻查看 [instruction.md](instruction.md)。


### 3. 字段聚合统计

推荐：

- `tdp_log_search`
- `action=terms`

最小示例：

```json
{
  "action": "terms",
  "time_from": 1741536000,
  "time_to": 1741622400,
  "term": "threat.name"
}
```

`terms` 的 `sql` 是可选过滤条件；用户只想按字段聚合时，不要为了凑参数强行补 `sql`。

适合：

- 统计某时间段内最多的威胁名称
- 聚合源 IP、目的 IP、URL、威胁类型

### 4. 威胁事件列表

推荐：

- `tdp_incident_list`
- `action=search`

最小可运行参数集：

```json
{
  "action": "search",
  "time_from": 1741536000,
  "time_to": 1741622400,
  "severity": [3, 4],
  "phase": ["exploit"],
  "result": ["success"],
  "keyword": "sql",
  "cur_page": 1,
  "page_size": 20,
  "sort_by": "time",
  "sort_order": "desc"
}
```

如果必须传 `sql`、`refresh_rate` 等尚未提升到顶层的字段，再使用 `condition` 高级兼容参数。

高频 action：

- `search`: 事件列表
- `top_attacked_entity`: 受攻击实体
- `result`: 事件研判结果
- `timeline`: 时间线
- `alert_search`: 事件下告警列表
- `result_distribution`: 结果分布
- `attacker_ip_list`: 攻击者 IP 列表
- `attacker_ip_detail`: 攻击者 IP 详情

时间线示例：

```json
{
  "action": "timeline",
  "incident_id": "b62899499fec914d6246137eed3b6ec4-1777334454",
  "time_from": 1777305600,
  "time_to": 1777391999
}
```

`timeline` 会默认传 `show_attack=true`。如果 TDP 仍返回 `show_attack is false`，说明该事件当前不支持攻击过程展开，改用 `result`、`alert_search` 或回退浏览器查看事件详情。

返回结果重点关注：

- 事件 ID
- 攻击者 / 受害者
- `threat.result`
- `threat.severity`
- 检出次数、最近发现时间

何时回退浏览器：

- 需要进入事件详情页
- 需要威胁图、原始报文、PCAP 或页面级联动

### 5. 主机、外部攻击与风险类查询

外部攻击分布：

```json
{
  "time_from": 1741536000,
  "time_to": 1741622400,
  "severity": [3, 4],
  "result_list": ["success"],
  "keyword": "sqlmap"
}
```

注意：这里的攻击结果字段是 `result_list`，来自 API 文档的 `condition.result_list`；不要使用事件列表里的 `result` 参数名。

告警主机汇总：

```json
{
  "action": "summary",
  "time_from": 1741536000,
  "time_to": 1741622400,
  "severity": [3, 4],
  "direction": ["in"],
  "keyword": "webshell",
  "cur_page": 1,
  "page_size": 20
}
```

`summary` 默认覆盖全部常见威胁类型，且 `threat_characters` 默认不限制。用户明确要“失陷主机”时再传 `threat_characters=["is_compromised"]`。

某主机下事件：

```json
{
  "action": "events",
  "asset_machine": "asset-123",
  "time_from": 1741536000,
  "time_to": 1741622400
}
```

MDR 研判列表：

```json
{
  "action": "list",
  "time_from": 1741536000,
  "time_to": 1741622400,
  "section_list": ["服务器"],
  "threat_severity": [3, 4],
  "judge_result_status": [2],
  "keyword": "10.10.10.1",
  "cur_page": 1,
  "page_size": 20
}
```

### 6. 脆弱性 / 弱口令 / 资产类查询

常见思路：

- 单接口工具通常直接传根级参数
- 优先补齐根级 `time_from`、`time_to`，但服务/主机/Web 应用框架资产是当前资产视图，不需要时间范围
- 若 tool 支持筛选和分页，优先补顶层 `keyword`、`cur_page`、`page_size`、`sort_by`、`sort_order`
- `condition` / `page` 只用于顶层参数未覆盖的底层字段

脆弱性示例：

```json
{
  "time_from": 1741536000,
  "time_to": 1741622400,
  "assets_group": [237],
  "severity": [3, 4],
  "status": 0,
  "keyword": "struts",
  "cur_page": 1,
  "page_size": 20,
  "sort_by": "severity",
  "sort_order": "desc"
}
```

服务资产列表示例：

```json
{
  "action": "service_list",
  "service": "nginx",
  "service_class": "web",
  "is_public": true,
  "keyword": "10.0.0.5",
  "cur_page": 1,
  "page_size": 20
}
```

登录入口列表示例：

```json
{
  "action": "list",
  "time_from": 1741536000,
  "time_to": 1741622400,
  "threat_tag": ["弱口令"],
  "keyword": "wp-login",
  "is_public": 1,
  "vulnerable": 1,
  "cur_page": 1,
  "page_size": 20
}
```

弱口令列表示例：

```json
{
  "time_from": 1741536000,
  "time_to": 1741622400,
  "data": "172.16.71.129:8080/wp-login.php",
  "result": "success",
  "app_class": ["CMS"],
  "keyword": "admin",
  "cur_page": 1,
  "page_size": 20
}
```

域名资产列表示例：

```json
{
  "domain_name_or_ip": "example.com",
  "second_level_domain": "example.com",
  "is_public": true,
  "has_login_api": true,
  "has_upload_api": true,
  "cur_page": 1,
  "page_size": 20
}
```

上传接口列表示例：

```json
{
  "action": "interface_list",
  "time_from": 1741536000,
  "time_to": 1741622400,
  "host": "example.com",
  "search_for_upload": true,
  "keyword": "upload",
  "cur_page": 1,
  "page_size": 20,
  "sort_by": "last_upload_time",
  "sort_order": "desc"
}
```

API 接口列表示例：

```json
{
  "host": "example.com",
  "methods": ["POST"],
  "privacy_tags": ["leak_phone"],
  "is_public": true,
  "keyword": "login",
  "cur_page": 1,
  "page_size": 20
}
```

API 风险列表示例：

```json
{
  "time_from": 1741536000,
  "time_to": 1741622400,
  "assets_group": [237],
  "api_risk_type": "注入漏洞",
  "keyword": "graphql",
  "cur_page": 1,
  "page_size": 20,
  "sort_by": "last_occ_time",
  "sort_order": "desc"
}
```

`tdp_interface_risk_list` 会自动补齐 API 文档示例里的空 `api_risk_type`、空 `assets_group` 和空 `fuzzy`；如果后端仍返回 `No message available`，先缩小时间范围或去掉 `api_risk_type` 再查。

资产类结果常看字段：

- 名称
- IP / 域名 / URL
- 风险等级
- 暴露状态
- 关联主机数或命中次数

### 7. 隐私拓扑、云服务与系统状态

隐私拓扑示例：

```json
{
  "time_from": 1741536000,
  "time_to": 1741622400,
  "assets_group": [237],
  "itag": ["phone"],
  "methods": ["POST"],
  "fuzzy_url_host": "example.com",
  "fuzzy_url_path": "/submit",
  "fuzzy_src_ip": "10.0.0.5"
}
```

云服务访问源示例：

```json
{
  "action": "access_source",
  "time_from": 1741536000,
  "time_to": 1741622400,
  "cloud_vendor": "aliyun",
  "cloud_service_class": "云服务器",
  "keyword": "10.10.10.1",
  "cur_page": 1,
  "page_size": 20
}
```

云实例访问明细示例：

```json
{
  "action": "instance_access_list",
  "time_from": 1741536000,
  "time_to": 1741622400,
  "cloud_instance": "i-zadG8d4l",
  "keyword": "10.10.10.1",
  "cur_page": 1,
  "page_size": 20
}
```

系统状态示例：

```json
{
  "action": "database"
}
```

### 8. 下载类接口

PCAP 下载：

```json
{
  "alert_id": "alert-123",
  "occ_time": 1741622400
}
```

恶意文件下载：

```json
{
  "hash": "44d88612fea8a8f36de82e1278abb02f"
}
```

注意：

- 下载类接口会返回文件内容或触发真实下载语义
- 若用户只是想确认“有没有文件/PCAP”，先查列表，不要直接下载

### 9. 配置类接口边界

`tdp_platform_config` 和 `tdp_policy_settings` 含大量写操作。

只在以下情况下使用：

- 用户明确要求查询或修改平台配置
- 你已经确认 action、目标对象和影响范围
- 涉及新增、编辑、删除、状态变更时，已得到用户明确授权

示例，查询资产列表：

```json
{
  "action": "asset_list"
}
```

示例，查询处置日志：

```json
{
  "action": "disposal_log_list"
}
```

处置日志可直接走 `tdp_platform_config(action="disposal_log_list")`；handler 会补默认时间范围、分页和 `cts` 倒序。

## 高风险与低风险

TDP 这里大多数调查类 tool 是读操作，但以下情况仍要谨慎：

- 下载类接口会触发真实文件下载
- 某些查询会带较大时间范围，可能返回海量数据
- `tdp_platform_config`、`tdp_policy_settings` 包含新增、编辑、删除、状态修改等高风险动作
- 若用户只想“看看”，不要默认下载文件

## 常见错误与回退规则

- 缺少服务配置时，先检查 `tdp_api_key`、`tdp_secret`、`tdp_host`
- 查询结果为空时，先检查api参数是否正确，尤其是时间范围，但尽量不要擅自修改用户查询条件
- 写操作前要再次核对 action 是否为只读 list/search 还是 add/update/delete
- 需要原始报文、PCAP、交互式详情时，先征得用户同意，再回退浏览器

## 配合浏览器的边界

先 API，再浏览器：

- API 负责稳定取数
- 浏览器负责页面详情、原始报文、下载入口、交互式下钻

TDP 页面路径和浏览器技巧见：

- [tdp-menu.md](tdp-menu.md)
- [event-detail.md](event-detail.md)
- [browser-workflow.md](browser-workflow.md)
