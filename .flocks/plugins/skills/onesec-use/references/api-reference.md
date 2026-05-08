# OneSEC API 调用指南

OneSEC 当前优先复用 grouped tool，而不是直接从页面做数据获取。

## 先看这张路由表

| 用户意图 | 推荐 tool | 推荐 action | 最小参数 |
|---|---|---|---|
| 查威胁事件 | `onesec_edr` | `edr_get_incidents`（默认） / `edr_get_recent_incidents`（仅最近 24 小时增量） | 建议显式带 `time_from`、`time_to` |
| 查终端告警 | `onesec_edr` | `edr_get_endpoint_alerts` / `edr_get_recent_endpoint_alerts` | 常见至少带 `time_from`、`time_to` |
| 查恶意文件 / 威胁行为 / 时间线 / IOC | `onesec_edr` | 对应 `edr_get_*` action | 时间范围、分页、筛选字段 |
| 查 DNS 拦截 / 解析日志 / 受威胁终端 | `onesec_dns` | `dns_search_blocked_queries` / `dns_get_recent_blocked_queries` / `dns_search_queries` / `dns_search_threatened_endpoint` | 多数需要 `time_from`、`time_to` |
| 查软件清单或安装终端 | `onesec_software` | `software_query_page_list` / `software_query_agent_list` | 软件终端查询要 `name` + `publisher` |
| 查终端、任务、审计 | `onesec_ops` | `ops_query_agent_page_list` / `ops_query_task_page_list` / `ops_query_audit_log` | 审计/任务通常要时间范围 |
| 查病毒库 / 下发扫描 | `onesec_threat` | `threat_query_bd_version` / `threat_virus_scan` | 查询通常空参，写操作需明确目标终端 |

## 通用规则

- OneSEC 绝大多数 tool 都是 `action + 参数平铺`
- 不像 TDP 那样把大量字段放在统一 `body` 里
- 时间字段多数为秒级 Unix 时间戳
- 查询类 action 默认优先；写操作只有在用户明确授权时才执行

## 时间参数注意事项（重点）

### 时间参数计算
调用任何时间相关 OneSEC API 时，必须**动态计算**时间戳，禁止手动估算。

**错误方法（禁止）**

```python
# 手动估算，硬编码
time_from = 1741536000
time_to = 1741622400
```

**正确方法**

```python
import datetime

# 按执行时刻动态计算最近 24 小时窗口
now = datetime.datetime.now()
time_to = int(now.timestamp())
time_from = int((now - datetime.timedelta(hours=24)).timestamp())
```

使用动态计算的时间戳调用工具执行
```
onesec_dns(action="dns_get_recent_blocked_queries", time_from=time_from, time_to=time_to)
```

如果要查“今天”或“最近 7 天”，也必须动态计算，不要手填固定时间戳。

###  时间参数规则
- 秒级时间戳，不是毫秒，UTC+8 时区
- 分页接口建议显式传 `time_from`、`time_to`
- `recent` 系列只适合最近 24 小时的增量查询
- DNS `dns_search_queries` 也只支持最近 24 小时内的数据
- `edr_get_threat_files`、`edr_get_threat_activities`、`edr_get_incidents`、`edr_get_endpoint_alerts` 的时间窗口最长三个月
- `ops_query_audit_log` 仅支持最近 30 天内的审计日志
- 未传时间时，返回范围由服务端默认窗口决定，仅作兜底，不推荐依赖


## 时间窗口选择表

| 查询目标 | 推荐 action | 时间参数策略 | 备注 |
|---|---|---|---|
| 查本周事件 | `edr_get_incidents` | 显式传 `time_from`、`time_to` | 默认做法 |
| 查最近 7 天事件 | `edr_get_incidents` | 显式传 `time_from`、`time_to` | 适合历史回溯 |
| 查最近 30 天事件 | `edr_get_incidents` | 显式传 `time_from`、`time_to` | 适合历史回溯 |
| 查最近 24 小时增量 | `edr_get_recent_incidents` | 显式传 `time_from`、`time_to`，且跨度不超过 24 小时 | 仅增量同步 |
| 未传时间兜底查询 | `edr_get_incidents` | 不传时间 | 返回范围由服务端默认窗口决定，不推荐依赖 |

补充规则：

- 若用户说“recent 事件”但时间范围是本周、最近 7 天、最近 30 天，agent 应主动改用 `edr_get_incidents`
- `edr_get_recent_incidents` 只用于最近 24 小时增量，不要拿它做历史回溯

## 标准时间模板

以下模板仅用于给 agent 组装请求时参考：

- 时间戳单位一律是秒
- 以下示例按 `Asia/Shanghai` 计算
- 以下示例假设当前时间是 `2026-04-23 10:00:00 +08:00`
- 实际调用时，应按执行当时的业务时区重新计算

## 先区分事件、告警、DNS 和资产

OneSEC 中几个相邻页面经常被混用，建议先按语义路由：

| 用户实际想查什么 | 推荐 tool / action 类型 | 说明 |
|---|---|---|
| EDR 威胁事件 | `onesec_edr` 的 `edr_get_incidents` 等事件类 action | 事件维度，平台已聚合 |
| 终端告警 / 原始行为日志 | `onesec_edr` 的 `edr_get_endpoint_alerts` 等告警类 action | 明细维度，适合精细筛选 |
| DNS 告警 / DNS 解析日志 | `onesec_dns` | OneDNS 独立视角，不应和 EDR 事件混用 |
| 软件、终端、漏洞、任务、审计 | `onesec_software` / `onesec_ops` / 其他对应 tool | 资产或运维维度，不是日志调查 |

建议按用词做第一轮判断：

- “威胁事件”“最近有什么事件”“事件处置状态” => 事件类 action
- “告警”“告警日志”“查某终端最近的告警”“查某进程行为” => 告警类 action
- “DNS 告警”“DNS 威胁”“域名解析日志”“拦截记录” => `onesec_dns`
- “安装了什么软件”“终端清单”“漏洞列表”“任务记录” => 资产或运维类 tool

## 高频场景

### 1. 查询威胁事件

推荐：

- `onesec_edr`
- `action=edr_get_incidents`
- 历史回溯场景显式传 `time_from` + `time_to`
- 仅最近 24 小时增量才使用 `edr_get_recent_incidents`
- 未传时间只作为兜底，不要把它当成稳定窗口

最小示例：

```json
{
  "action": "edr_get_incidents",
  "time_from": 动态计算秒级时间戳,
  "time_to": 动态计算秒级时间戳,
  "cur_page": 1,
  "page_size": 20
}
```

适合：

- 看最近威胁事件
- 按时间窗口拉事件列表
- 先拿 `incident_id` 再继续查处置清单

返回结果重点关注：

- `incident_id`
- 终端 `umid`
- 威胁名称、严重级别、处置状态

何时回退浏览器：

- 需要威胁图、事件概览、详情页攻击链

### 2. 查询终端告警

推荐：

- `onesec_edr`
- `action=edr_get_endpoint_alerts`

最小示例：

```json
{
  "action": "edr_get_endpoint_alerts",
  "time_from": 动态计算秒级时间戳,
  "time_to": 动态计算秒级时间戳,
  "sql": "threat.level = 'attack'",
  "cur_page": 1,
  "page_size": 20
}
```

常见补充参数：

- `search_fields`
- `sql`
- `group_list`
- `umid_list`

返回结果重点关注：

- `host_name`
- `host_ip`
- `threat.name`
- `threat.severity`
- `proc.cmdline`
- `proc_file.path`

何时回退浏览器：

- 需要页面高级查询、AI 查询、明细联动、人工点击详情

### 3. 查询恶意文件 / 威胁行为 / 时间线

推荐 action：

- `edr_get_threat_files`
- `edr_get_threat_activities`
- `edr_get_threat_timeline`

示例：

```json
{
  "action": "edr_get_threat_files",
  "time_from": 动态计算秒级时间戳,
  "time_to": 动态计算秒级时间戳,
  "cur_page": 1,
  "page_size": 20
}
```

若只查近期窗口，可用：

- `edr_get_recent_threat_files`
- `edr_get_recent_threat_activities`
- `edr_get_recent_threat_timeline`

其中时间线类 action 需要额外注意：

- `edr_get_threat_timeline` 需要 `incident_id`
- `edr_get_recent_threat_timeline` 也需要 `incident_id`
- 如果还没有 `incident_id`，应先调用 `edr_get_incidents`
- `edr_get_recent_threat_timeline` 仅适合最近 24 小时内的增量时间线查询
- `edr_get_threat_files`、`edr_get_threat_activities` 等分页接口按文档时间窗口最长三个月

### 4. 查询威胁处置清单

推荐：

- `onesec_edr`
- `action=edr_get_threat_disposals`

这是一个有真实必填组合的 action：

- `incident_id`
- `umid`

最小示例：

```json
{
  "action": "edr_get_threat_disposals",
  "incident_id": "incident-001",
  "umid": "umid-001",
  "cur_page": 1,
  "page_size": 20
}
```

如果只知道事件列表，还没拿到 `incident_id`，应先调用 `edr_get_incidents`。

### 5. DNS 拦截记录 / 解析日志 / 受威胁终端

推荐：

- `onesec_dns`

高频 action：

- `dns_search_blocked_queries`
- `dns_get_recent_blocked_queries`
- `dns_search_queries`
- `dns_search_threatened_endpoint`
- `dns_get_public_ip_list`

DNS 拦截记录示例：

```json
{
  "action": "dns_search_blocked_queries",
  "time_from": 动态计算秒级时间戳,
  "time_to": 动态计算秒级时间戳,
  "domain": "evil.com",
  "keyword": "evil",
  "show_unblocked_threat": 1
}
```

如果用户只明确给了一个完整域名，优先把 `domain` 和 `keyword` 都设成该域名；当前工具在缺少 `keyword` 时也会默认复用 `domain`。

如果用户没有给域名/关键字，只有 `public_ip` + 时间范围，并且查询目标是最近 24 小时拦截记录，优先改用 `dns_get_recent_blocked_queries`；不要硬套 `dns_search_blocked_queries`。

DNS 近期拦截增量示例：

```json
{
  "action": "dns_get_recent_blocked_queries",
  "time_from": 动态计算秒级时间戳,
  "time_to": 动态计算秒级时间戳,
  "block_reason": "threat",
  "show_unblocked_threat": 1,
  "threat_level": [2, 3, 4]
}
```

DNS 解析日志示例：

```json
{
  "action": "dns_search_queries",
  "time_from": 动态计算秒级时间戳,
  "time_to": 动态计算秒级时间戳,
  "domain": "evil.com",
  "page_items_num": 20
}
```

注意：

- 有些 DNS action 对时间窗口要求严格
- DNS 查询优先使用 Unix 秒级时间戳；当前工具也会兼容常见日期字符串，如 `YYYY-MM-DD HH:MM:SS`
- `public_ip` 按文档是数组；当前工具也会兼容单个字符串并自动包装成单元素数组
- 查询具体域名或关键字的 DNS 拦截明细时，优先使用 `dns_search_blocked_queries`
- `dns_get_recent_blocked_queries` 仅适合最近 24 小时增量拉取，不支持 `domain`、`keyword`、`private_ip`、`threat_type` 和分页参数
- `page_items_num` 与 `page_size` 不是同一个字段
- DNS 拦截结果优先读取 `result` 字段；当前工具也会补充 `is_blocked`
- 目标地址列表增删改是写操作，不要误用

### 6. 查询软件资产

推荐：

- `onesec_software`
- `software_query_page_list`
- `software_query_agent_list`

查询软件清单：

```json
{
  "action": "software_query_page_list",
  "cur_page": 1,
  "page_size": 20,
  "sort_by": "install_time",
  "sort_order": "desc"
}
```

查询安装某软件的终端：

```json
{
  "action": "software_query_agent_list",
  "name": "ToDesk",
  "publisher": "海南有趣科技有限公司",
  "cur_page": 1,
  "page_size": 20
}
```

这里的关键点是：

- `software_query_agent_list` 需要 `name + publisher`

### 7. 查询终端、审计、任务

推荐：

- `onesec_ops`

高频 action：

- `ops_query_agent_page_list`
- `ops_query_audit_log`
- `ops_query_task_page_list`
- `ops_query_task_execute_list`

查询审计日志示例：

```json
{
  "action": "ops_query_audit_log",
  "begin_time": 动态计算的 begin_time 秒级时间戳,
  "end_time": 动态计算的 end_time 秒级时间戳,
  "cur_page": 1,
  "page_size": 20
}
```

查询任务列表示例：

```json
{
  "action": "ops_query_task_page_list",
  "time_type": "create_time",
  "begin_time": 动态计算的 begin_time 秒级时间戳,
  "end_time": 动态计算的 end_time 秒级时间戳,
  "auto": 0,
  "cur_page": 1,
  "page_size": 20
}
```

注意：

- 审计和任务查询通常要求时间范围
- `ops_query_audit_log` 仅支持最近 30 天内的日志
- `ops_query_task_page_list` 还要求 `time_type` 和 `auto`
- `time_type` 仅支持 `create_time`、`update_time`
- `auto` 仅支持 `0`（人工响应）和 `1`（自动响应）

### 8. 病毒库与扫描任务

推荐：

- `onesec_threat`

查询病毒库版本：

```json
{
  "action": "threat_query_bd_version"
}
```

下发扫描任务：

```json
{
  "action": "threat_virus_scan",
  "agent_list": ["umid-001"],
  "task_type": 10110,
  "scanmode": 1
}
```

注意：

- 这是写操作
- 扫描范围越大，对终端影响越大
- `task_type` 仅支持 `10110`（快速扫描）、`10120`（全盘扫描）、`10130`（自定义扫描）
- `scanmode` 仅支持 `1`（极速）、`2`（均衡）、`3`（低耗）
- `threat_update_bd_version` 的 `os_platform` 仅支持 `windows/macos`，macOS 架构仅支持 `Apple Silicon/Intel Chip`

## 高风险写操作清单

以下 action 默认视为高风险：

- `edr_isolate_endpoints`
- `edr_unisolate_endpoints`
- `edr_quarantine_files`
- `edr_quarantine_proc_files`
- `edr_restore_quarantined_files`
- `edr_block_network_connections`
- `edr_unblock_network_connections`
- `edr_disable_service`
- `edr_restore_disabled_service`
- `edr_delete_registry_startup`
- `edr_add_ioc`
- `edr_delete_ioc`
- `dns_add_domains_to_destination_list`
- `dns_delete_domains_from_destination_list`
- `dns_replace_destination_list`
- `ops_uninstall_agent`
- `ops_edit_strategy_scope`
- `ops_edit_agent_info`
- `threat_virus_scan`
- `threat_stop_virus_scan`
- `threat_upgrade_bd_version_task`
- `threat_update_bd_version`

## 常见失败原因

- 时间戳传成毫秒
- 应传 `page_items_num` 的地方错传 `page_size`
- 处置类接口缺少 `incident_id`、`umid`、`agent_list`
- 查询类和处置类 action 选错

## 何时回退浏览器

以下情况优先回退浏览器：

- 需要威胁图、事件概览、详情页链路
- 需要页面 AI 查询
- 需要点击表格、查看右侧详情、查看图谱
- 需要用户人工登录或页面确认

浏览器页面和字段说明见：

- [onesec-menu.md](onesec-menu.md)
- [onesec-incident.md](onesec-incident.md)
- [instruction.md](instruction.md)
