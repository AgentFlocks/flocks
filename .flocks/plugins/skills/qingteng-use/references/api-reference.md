# 青藤 API 调用指南

青藤当前优先复用已有 provider tool。

## 先看这张路由表

| 用户意图 | 推荐 tool | 推荐 action | 最小参数 |
|---|---|---|---|
| 查主机、进程、账号、端口、网站、数据库、安装包 | `qingteng_assets` | `list` | `resource`、`os_type` |
| 刷新资产 | `qingteng_assets` | `refresh` / `refresh_all` | `resource`、`os_type` 或仅 `os_type` |
| 查可疑操作、暴力破解、异常登录、WebShell、后门、蜜罐 | `qingteng_detect` | 对应 `*_list` | 多数至少要 `os_type` 或分页参数 |
| 查补丁、风险、弱密码、风险文件、漏洞检测 | `qingteng_risk` | `patch_list` / `risk_list` / `weakpwd_list` / `weakfile_list` / `poc_list` | 多数需要 `os_type`，部分需要 `risk_type` |
| 查基线任务、基线结果、授权 | `qingteng_baseline` | `job_list` / `job_status` / `spec_check_result` / `auth_list` | 通常至少要 `os_type` |
| 查系统审计日志 | `qingteng_system_audit` | tool 直接调用 | 可空参，常补 `eventName`、`userName` |
| 做快速风险体检 | `qingteng_vul_check` | tool 直接调用 | 常见 `risk_type`、`os_type` |
| 查/创建/执行快速安全检测任务 | `qingteng_fastjob` | `task_list` / `job_create` / `job_execute` / `task_result` | `taskId`、`name` 等 |
| 查发现的主机、管理资产扫描任务 | `qingteng_asset_discovery` | `discovered_host_list` / `job_create` / `job_execute` | `name`、`specId` 等 |
| 主机网络隔离、微隔离策略管理 | `qingteng_microseg` | `seg_create` / `seg_delete` / `host_list` / `black_list` | `agentIds`、`ids` 等 |

## 通用规则

- 青藤的参数模式大多是 `action + 平铺字段`
- 资产 / 检测 / 风险 / 基线四类 grouped tool 的差异主要在 `action` 和参数组合
- 高频关键参数是：
  - `os_type`
  - `resource`
  - `risk_type`
  - `page` / `size`
  - `groups`
  - `hostId` / `hostIds`

## 1. 资产查询：`qingteng_assets`

### 适合什么

- 主机资产
- 进程
- 账号 / 账号组
- 端口
- 服务
- Web 应用 / 网站
- 数据库
- 安装包 / JAR / Web 框架

### 高频 action

- `list`
- `refresh`
- `refresh_status`

### 最小调用参数

查询资产列表最小示例：

```json
{
  "action": "list",
  "resource": "host",
  "os_type": "linux"
}
```

带分页与筛选的示例：

```json
{
  "action": "list",
  "resource": "process",
  "os_type": "linux",
  "page": 0,
  "size": 20,
  "hostname": "server-01",
  "processName": "bash"
}
```

### `resource` 选择规则

- `host`: 主机
- `process`: 进程
- `account` / `accountgroup`: 账号与账号组
- `port`: 端口
- `service`: 服务
- `webapp` / `website`: Web 资产
- `dbinfo`: 数据库
- `pkg` / `jar_pkg` / `app` / `webframe`: 软件与框架资产

### resource 专属字段

- `resource=process`:
  - `processName`
  - `processPath`
  - `processPid`
- `resource=account/accountgroup`:
  - `accountName`
  - `accountUid`
  - `accountGroup`
- `resource=port`:
  - `portNumber`
  - `portProtocol`
- `resource=service`:
  - `serviceName`
  - `serviceState`
  - `serviceStartType`
- `resource=dbinfo`:
  - `dbName`
  - `dbType`
- `resource=website/webapp`:
  - `websiteName`
  - `websiteDomain`

### 返回结果重点关注

- 资产主键 ID
- 主机名 / IP / 组
- 风险或暴露状态
- 端口 / 服务 / 进程路径 / 站点域名等资源特有字段

### 常见错误

- `resource` 和专属字段不匹配
- 漏传 `os_type`
- 把页码当作从 1 开始，实际上默认从 0 开始

## 2. 检测查询：`qingteng_detect`

### 适合什么

- Linux 可疑操作
- 暴力破解结果与封停
- 异常登录
- WebShell 结果、扫描、状态、下载
- 后门结果、扫描、状态
- 蜜罐结果与规则管理

### 高频只读 action

- `shelllog_list`
- `brutecrack_list`
- `brutecrack_log`
- `abnormallogin_list`
- `webshell_list`
- `backdoor_list`
- `honeypot_list`

### 高频最小示例

查询异常登录：

```json
{
  "action": "abnormallogin_list",
  "os_type": "linux",
  "page": 0,
  "size": 20
}
```

查询 WebShell：

```json
{
  "action": "webshell_list",
  "os_type": "linux",
  "page": 0,
  "size": 20,
  "severity": "high"
}
```

查询暴力破解结果：

```json
{
  "action": "brutecrack_list",
  "os_type": "linux",
  "page": 0,
  "size": 20,
  "ip": "1.1.1.1"
}
```

### 高风险 action

- `brutecrack_block`
- `webshell_scan`
- `backdoor_scan`
- `honeypot_rule_create`
- `honeypot_rule_update`
- `honeypot_rule_delete`

只有用户明确要求执行时才调用。

## 3. 风险、补丁、弱密码、漏洞：`qingteng_risk`

### 高频只读 action

- `patch_list`
- `risk_list`
- `weakpwd_list`
- `weakfile_list`
- `poc_list`
- `poc_job_list`

### 参数组合规则

- 补丁类通常需要 `os_type`
- 风险类需要 `risk_type + os_type`
- 明细类动作需要对象 ID，如 `id`、`recordId`、`jobId`

### 最小示例

查询补丁结果：

```json
{
  "action": "patch_list",
  "os_type": "linux",
  "page": 0,
  "size": 20
}
```

查询系统风险：

```json
{
  "action": "risk_list",
  "risk_type": "system",
  "os_type": "linux",
  "page": 0,
  "size": 20
}
```

查询弱密码：

```json
{
  "action": "weakpwd_list",
  "os_type": "linux",
  "page": 0,
  "size": 20
}
```

查询漏洞检测结果：

```json
{
  "action": "poc_list",
  "os_type": "linux",
  "page": 0,
  "size": 20
}
```

### 高风险 action

- `patch_scan`
- `risk_scan`
- `weakpwd_scan`
- `weakfile_scan`
- `poc_scan`
- `poc_job_add`
- `poc_job_fix`
- `poc_job_execute`
- `linux_all_scan`

### 返回结果重点关注

- 风险 ID / 记录 ID / 作业 ID
- 严重级别
- 受影响主机数
- 规则 / CVE / 补丁 / 文件路径等关键字段

## 4. 基线任务与基线结果：`qingteng_baseline`

### 高频只读 action

- `job_list`
- `job_status`
- `spec_rule_list`
- `spec_check_result`
- `spec_failed_host`
- `auth_list`

### 最小示例

查询基线任务：

```json
{
  "action": "job_list",
  "os_type": "linux",
  "page": 0,
  "size": 20
}
```

查询任务状态：

```json
{
  "action": "job_status",
  "os_type": "linux",
  "specId": "spec-001"
}
```

查询基线检查结果：

```json
{
  "action": "spec_check_result",
  "os_type": "linux",
  "specId": "spec-001",
  "page": 0,
  "size": 20
}
```

### 高风险 action

- `job_create`
- `job_update`
- `job_execute`
- `job_batch_create`
- `auth_create`
- `auth_update`
- `auth_delete`

## 5. 系统审计：`qingteng_system_audit`

这是最直接的读接口之一。

最小示例：

```json
{}
```

按操作名和账号筛选：

```json
{
  "eventName": "删除",
  "userName": "admin",
  "page": 0,
  "size": 20,
  "sorts": "-eventTime"
}
```

返回结果重点关注：

- `rows`
- `total`
- `eventTime`
- `requestParam`

## 6. 快速风险体检：`qingteng_vul_check`

最小示例：

```json
{
  "risk_type": "system",
  "os_type": "linux"
}
```

适合：

- 需要先粗略判断某类风险是否存在
- 还不确定后续应该走 `risk_list` 还是扫描任务

## 高风险写操作总表

默认需要再次确认用户意图的动作：

- 资产刷新与批量变更
- 主机删除 / 卸载 Agent
- 暴力破解封停
- WebShell / 后门 / 漏洞 / 弱密码扫描
- 基线任务创建 / 更新 / 执行
- 授权信息变更
- 快速任务作业创建 / 执行
- 资产发现扫描任务创建 / 执行（会触发网络扫描）
- 微隔离策略创建 / 编辑 / 删除（会阻断或恢复主机网络）
- 主机阻断状态变更（`host_protect_status`）

## 7. 快速任务：`qingteng_fastjob`

适用于对单台或多台主机快速执行预定义的安全检测和应急响应任务。

### 典型流程

1. `task_list` — 查询可用的检测项模板（`osType=1` Linux，`2` Windows）
2. `job_create` — 创建作业，绑定 `taskId` 和目标主机范围 `realm`
3. `job_execute` — 立即执行，获取 `taskRecordId`
4. `task_result` — 查看执行结果；`task_error` — 查看失败主机

### 最小示例

查询检测项列表：

```json
{
  "action": "task_list",
  "osType": 1,
  "name": "Weblogic",
  "page": 0,
  "size": 20
}
```

创建作业（全部主机）：

```json
{
  "action": "job_create",
  "name": "weblogic-check-2024",
  "osType": 1,
  "taskType": 1,
  "taskId": "0478ee5024763edc6d3c",
  "realm": {"type": 0},
  "realmName": "全部主机"
}
```

立即执行：

```json
{
  "action": "job_execute",
  "id": "5d1b568b67657c1b743cf33b"
}
```

## 8. 资产发现：`qingteng_asset_discovery`

适用于发现未安装 Agent 的主机，了解全局资产覆盖情况。

### 典型流程

1. `discovered_host_list` — 查看当前已发现的主机
2. `job_create` — 创建扫描任务，配置发起主机和目标 IP 段
3. `job_execute` — 立即触发扫描
4. 扫描完成后再次调用 `discovered_host_list` 查看新发现主机

### 最小示例

查看发现主机：

```json
{
  "action": "discovered_host_list"
}
```

创建扫描任务：

```json
{
  "action": "job_create",
  "name": "内网段扫描",
  "kind": 2,
  "values": [],
  "ipList": ["192.168.0.0/24"],
  "osDetection": true
}
```

执行扫描任务：

```json
{
  "action": "job_execute",
  "specId": "5fdacbf3edc90d7a292ae9a5"
}
```

## 9. 微隔离：`qingteng_microseg`

⚠️ **高风险操作模块**，隔离类操作会直接阻断主机网络连接，操作前必须确认 `agentId` 正确。

### 三类子功能

| 分组 | 代表 action |
|---|---|
| 隔离策略（一键隔离） | `seg_create`、`seg_edit`、`seg_delete`、`seg_list`、`seg_detail` |
| 主机管理 | `host_list`、`host_ms_enable`、`host_protect_status`、`host_limit_out` |
| 黑名单策略 | `black_list`、`black_create`、`black_update`、`black_delete`、`black_detail` |

### 典型应急响应流程

1. `host_list` — 查找目标主机 `agentId`
2. `seg_create` — 对目标主机实施网络隔离
3. 取证完成后 `seg_delete` — 解除隔离

```json
{
  "action": "seg_create",
  "agentIds": ["5fa27259dae9af8a"],
  "remark": "疑似失陷主机紧急隔离",
  "direction": "out",
  "ipList": [],
  "portList": []
}
```

解除隔离：

```json
{
  "action": "seg_delete",
  "agentIds": ["5fa27259dae9af8a"]
}
```

查看微隔离主机列表：

```json
{
  "action": "host_list",
  "page": 0,
  "size": 20
}
```

## 何时回退浏览器

以下情况优先回退浏览器：

- API 不覆盖页面级详情
- 需要人工登录或页面确认
- 需要导出、下载、交互式筛选、菜单定位

## 常见失败原因

- `os_type` 漏传或与目标资源不匹配
- `risk_type` 漏传
- `resource` 与字段组合不匹配
- 把查询动作错用成扫描 / 变更动作
