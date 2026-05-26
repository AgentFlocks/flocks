---
name: sangfor-xdr-use
description: 用于处理深信服 XDR（扩展检测与响应）相关任务，适合通过 API 或者结合浏览器进行以下任务：告警查询与处置、事件调查与响应、脆弱性管理、资产盘点、主机隔离、白名单管理、系统运维状态查看、节点健康监控等。只要用户提到 深信服 XDR、XDR、sangfor XDR 等需求时，必须先加载本 skill。本 skill 是 XDR 平台操作的唯一决策入口：在未阅读本 skill 并完成模式判断前，不要直接调用任何 `sangfor_xdr_*` tool 或使用 browser-use skill。
---

# 深信服 XDR Use

## First

操作模式 API V.S Browser

### 何时使用 API（默认）
- 默认模式，默认使用 API
- API 覆盖大部分运营场景（告警、事件、资产、漏洞、响应、白名单）

### 何时使用浏览器（CDP 直连）
- API 没有覆盖目标能力（如**系统运维页面/节点状态/CPU内存磁盘趋势**没有 API）
- 需要查看页面详情、交互式筛选、图表数据
- 页面需要人工登录、验证码、多因子认证
- 用户要求使用浏览器，或已在浏览器操作过程中

### 请求确认

- 默认走 API 模式；仅当 API 调用失败（如 401/403/连接失败）或目标能力不在 API 覆盖范围内时，再提示用户："XDR API 不可用，请检查配置或切换到浏览器模式"。
- 用户明确要求使用浏览器时，直接进入浏览器模式。

确定模式后：
- API 模式 → 阅读 API 模式使用指南
- 浏览器模式 → 阅读浏览器模式使用指南

## API 模式使用指南

### 设备定位（首要步骤，不可跳过）

在调用任何 `sangfor_xdr_*` 工具之前，**必须**先确定目标设备的 `device_id`：

1. **调用 `device_context`** 获取当前所有已接入的深信服 XDR 设备列表及其 `device_id`
2. **按用户提到的设备名称匹配**：
   - 用户说出了具体名称（如"XDR 生产""sangfor-xdr-01"）→ 在列表里找名称匹配的设备，取其 `device_id`
   - 用户未指定具体设备 → 若只有一台则直接使用；若有多台，**必须向用户确认**使用哪台
   - 用户指定了但列表中找不到匹配 → **告知用户没有找到该设备**，列出可用设备供选择
3. **所有后续工具调用必须带上 `device_id`**，即使该参数在 schema 中标注为可选

!!! important: 禁止在不明确 `device_id` 的情况下直接发起 `sangfor_xdr_*` 工具调用，否则请求可能落到错误的设备上。

### 工具速查

| 工具 | 能力 | 适用场景 |
|------|------|---------|
| `sangfor_xdr_alerts` | 告警列表查询 / 处置状态修改 / 举证信息 | 安全事件分诊、告警运营 |
| `sangfor_xdr_incidents` | 事件列表查询 / 处置状态修改 / 举证信息 / 关联实体 | 事件响应、攻击故事线分析 |
| `sangfor_xdr_vulns` | 基线合规 / 漏洞列表 / 弱密码列表 / 修复状态 | 漏洞管理、合规检查 |
| `sangfor_xdr_assets` | 资产列表 / IP段树 / 资产分类 / 接入设备 / 部门树 | 资产盘点、攻击面管理 |
| `sangfor_xdr_responses` | 查询已隔离主机 / 解除隔离 | 事件遏制、恢复 |
| `sangfor_xdr_whitelists` | 白名单 CRUD + 启用/禁用切换 | 抑制已知合规告警 |

### 路由规则

| 用户意图 | 推荐工具 | 常用 action |
|---------|---------|------------|
| 查告警、告警列表、分诊 | `sangfor_xdr_alerts` | `list` |
| 修改告警处置状态 | `sangfor_xdr_alerts` | `update_status` |
| 查告警当前处置状态 | `sangfor_xdr_alerts` | `status_list` |
| 查告警举证信息 | `sangfor_xdr_alerts` | `get_proof` |
| 查事件、安全事件 | `sangfor_xdr_incidents` | `list` |
| 修改事件处置状态 | `sangfor_xdr_incidents` | `update_status` |
| 查事件关联实体（主机/IP/文件/进程/DNS） | `sangfor_xdr_incidents` | `get_entities` |
| 查事件举证信息 | `sangfor_xdr_incidents` | `get_proof` |
| 查漏洞/基线合规 | `sangfor_xdr_vulns` | `baseline` / `vuln_list` |
| 查弱密码 | `sangfor_xdr_vulns` | `vuln_list(data_type="weakpwd")` |
| 修改漏洞修复状态 | `sangfor_xdr_vulns` | `update_status` |
| 查数据源设备 | `sangfor_xdr_vulns` | `source_device` |
| 查资产列表 | `sangfor_xdr_assets` | `list` |
| 查 IP 段树结构 | `sangfor_xdr_assets` | `ip_segment_tree` |
| 查资产分类/类型 | `sangfor_xdr_assets` | `asset_class` |
| 查接入设备 | `sangfor_xdr_assets` | `device_list` |
| 查部门组织树 | `sangfor_xdr_assets` | `department_tree` |
| 删除资产 | `sangfor_xdr_assets` | `delete` |
| 查已隔离主机 | `sangfor_xdr_responses` | `isolate_list` |
| 解除主机隔离 | `sangfor_xdr_responses` | `unisolate` |
| 查/增/改/删白名单 | `sangfor_xdr_whitelists` | `list` / `create` / `update` / `delete` |
| 切换白名单启用状态 | `sangfor_xdr_whitelists` | `toggle_status` |
| 系统运维、节点状态、CPU/内存/磁盘/IO趋势 | **CDP 脚本** | 见浏览器模式 |

### 时间参数说明

- 时间字段支持 Unix 秒级时间戳或 ISO8601 字符串（如 `"2024-01-01T00:00:00"`）
- 默认时间范围：最近 24 小时
- 需精确时间范围时，先用 `uv run python` 计算时间戳再传参

### 关键返回字段

**告警（list）**
- `uuId`：告警唯一ID
- `name`：告警名称
- `riskLevel`：风险等级（0=严重 1=高危 2=中危 3=低危 4=信息）
- `severity`：严重性分值(0-100)
- `srcIp / dstIp`：源/目的IP
- `dealStatus`：处置状态
- `attackState`：攻击状态（0=尝试 1=失败 2=成功 3=失陷）
- `hostIp`：受影响主机IP
- `firstTimestamp / lastTimestamp`：首/末发现时间
- `riskTag`：风险标签数组

**事件（list）**
- `uuId`：事件唯一ID
- `name`：事件名称
- `riskLevel`：风险等级
- `alertIds`：关联告警ID列表
- `dataSource`：数据来源（EDR/NDR）
- `hostIp`：受影响主机
- `dealStatus`：处置状态
- `detectionStatus`：检测状态（0=事中，1=事后）
- `attackStory`：攻击故事线(JSON)
- `rootCauseAnalysis`：根因分析(JSON)

### 高风险操作

| 工具 | action | 风险 | 说明 |
|------|--------|------|------|
| `sangfor_xdr_responses` | `unisolate` | 需确认 | 解除主机隔离，恢复网络通信 |
| `sangfor_xdr_whitelists` | `create/update/delete` | 需确认 | 修改白名单规则，可能导致告警漏报 |
| `sangfor_xdr_assets` | `delete` | 需确认 | 删除资产记录 |
| `sangfor_xdr_vulns` | `update_status` | 需确认 | 修改漏洞修复状态 |

### 常见错误与回退

- API 返回 401/403 → **会话已失效**，告知用户"XDR 会话已过期，请在浏览器中重新登录 XDR 后再试"
- 查询结果为空 → 确认时间范围和筛选条件
- 写操作（处置/删除）→ 必须先获用户明确授权

## 浏览器模式使用指南

> ⚠️ 进入浏览器模式前，**必须询问用户 XDR URL**（如 `https://xdr.example.com/`）。

请阅读以下文档获取完整流程：
- [references/cdp-workflow.md](references/cdp-workflow.md)

### CDP 模式适用场景

- **系统运维页面**（`#/apex-business/settings/run/state`）：节点状态、CPU/内存/磁盘/IO 趋势、数据接入指标
- 页面详情、交互式筛选
- API 不可用的场景

### 可用工具脚本

| 脚本路径 | 功能 | 必需参数 |
|---------|------|---------|
| `references/fetch_xdr_system_state.py` | 系统运行状态抓取 | `--url {XDR_URL}` |

脚本位于 skill 目录的 `references/` 下，无硬编码 URL 或敏感信息。

### 执行示例

脚本位于 `<flocks-plugins-root>/skills/sangfor-xdr-use/references/fetch_xdr_system_state.py`，请按当前平台选择对应命令。

**Windows（PowerShell）**

```powershell
powershell -Command "& '<FLOCKS_VENV>\Scripts\python.exe' '<FLOCKS_PLUGINS>\skills\sangfor-xdr-use\references\fetch_xdr_system_state.py' --url '{XDR_URL}'"
```

**macOS / Linux（bash / zsh）**

```bash
"<FLOCKS_VENV>/bin/python" "<FLOCKS_PLUGINS>/skills/sangfor-xdr-use/references/fetch_xdr_system_state.py" --url "{XDR_URL}"
```

**占位符说明**

| 占位符 | Windows 典型值 | macOS/Linux 典型值 |
|--------|---------------|-------------------|
| `<FLOCKS_VENV>` | `D:\Flocks Project\flocks\.venv` | `~/Flocks/flocks/.venv`（取决于实际安装位置） |
| `<FLOCKS_PLUGINS>` | `%USERPROFILE%\.flocks\plugins` | `~/.flocks/plugins` |

> 必须使用 Flocks 虚拟环境（`.venv`）执行；系统 Python 可能缺少依赖。

## API 与 CDP 功能对照

| 能力 | API 工具 | CDP 浏览器 | 说明 |
|------|---------|----------|------|
| 告警列表查询 | ✅ `sangfor_xdr_alerts` | ❌ | |
| 告警处置状态修改 | ✅ `sangfor_xdr_alerts` | ❌ | |
| 事件列表查询 | ✅ `sangfor_xdr_incidents` | ❌ | |
| 事件关联实体查询 | ✅ `sangfor_xdr_incidents` | ❌ | |
| 脆弱性/漏洞管理 | ✅ `sangfor_xdr_vulns` | ❌ | |
| 资产盘点 | ✅ `sangfor_xdr_assets` | ❌ | |
| 主机隔离/解除 | ✅ `sangfor_xdr_responses` | ❌ | |
| 白名单管理 | ✅ `sangfor_xdr_whitelists` | ❌ | |
| 系统运维状态 | ❌ | ✅ `fetch_xdr_system_state.py` | 需提供 `--url {XDR_URL}` |

## 关键坑点（必须避免）

| 坑 | 原因 | 解法 |
|---|---|---|
| `flocks browser -c js(...)` 返回空文本 | daemon session 指向错误的 tab | 用 Python socket 直连 daemon，通过 `Runtime.evaluate` 在正确 context 执行 |
| `flocks browser -c new_tab()` 后后续命令无响应 | tab 切换导致 session 错位 | 用 `switch_tab(targetId)` 明确切到 XDR tab |
| 多行代码转义失败 | PowerShell 引号嵌套 | 使用 `fetch_xdr_system_state.py` 脚本，无需手动转义 |
| `scroll()` 导致 TimeoutError | CDP mouse event 阻塞 | 避免在 XDR 页面使用 scroll，改用 `captureBeyondViewport: true` |
| XDR 页面数据为空或加载中 | 页面需等待渲染 | 导航后等待 3-5 秒再抓取 |

## 巡检数据输出格式

详见 [references/xdr-inspection-template.md](references/xdr-inspection-template.md)。