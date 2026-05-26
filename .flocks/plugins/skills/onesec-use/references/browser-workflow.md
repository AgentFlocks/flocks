# OneSEC 终端安全平台浏览器自动化

> 本文档统一按 `browser-use` 的 `cdp-direct` 流程执行。进入浏览器模式后，先跑 `flocks browser --doctor`；doctor 通过后只使用 `flocks browser`。
> 对后台任务 / 定时任务，或系统不支持可视化，使用 `browser-use` 的 `cdp-headless` 模式。

## 零、登录认证

State 文件路径：`~/.flocks/browser/onesec/auth-state.json`（固定，全局唯一）。

### 首次登录 / Session 过期重新登录

```bash
flocks browser --doctor
```

如果 `flocks browser --doctor` 提示浏览器已运行，但 daemon 或 active browser connection 不可用，必须直接提示用户：

```text
browser: not connected — 请确保 Chrome / Chromium / Edge 已打开，然后访问对应浏览器的 inspect 页面（例如 chrome://inspect/#remote-debugging 或 edge://inspect/#remote-debugging）并勾选 Allow remote debugging
```

然后等待用户进一步指示，不要直接操作。

当用户确认已开启 remote debugging 后：

1. 执行 `flocks browser --setup` 触发交互式 attach，不要用短超时包装该命令。
2. 再运行 `flocks browser --doctor` 做只读确认。
3. 如果还失败，先执行 `flocks browser --reload` 清理旧 daemon，再重新执行 `flocks browser --setup`，避免因为残留 daemon 造成干扰。
4. 只有随后 `--doctor` 通过后，才继续后面的登录或页面操作。

```bash
flocks browser -c '
tid = new_tab("https://<onesec-domain>/login", activate=True)
wait_for_load()
print(tid)
print(page_info())
'
```

等待用户登录结束，收到通知后继续：

```bash
flocks browser state save ~/.flocks/browser/onesec/auth-state.json
```

### CLI 或页面认证失败时的恢复流程

当出现以下任一情况，优先判定为认证问题：

- 页面被重定向到登录页
- CLI 返回 HTTP `401` / `403`
- CLI 输出包含未登录、认证失败、`Unauthorized`、`login`
- `auth-state.json` 已存在，但 CLI 或页面仍提示无权限

恢复步骤（最多尝试 1 次）：

```bash
flocks browser state load ~/.flocks/browser/onesec/auth-state.json --url "https://<onesec-domain>/pcedr/dashboard"
```

```bash
URL=$(flocks browser -c '
info = page_info()
print(info.get("url", ""))
' | tail -n 1)
if [[ "$URL" == *"/login"* ]]; then
  echo "Session 仍无效，需重新登录"
else
  flocks browser state save ~/.flocks/browser/onesec/auth-state.json
  echo "Session 已恢复，可重试 CLI 或页面操作"
fi
```

如果仍然落回登录页，再要求用户重新登录，不要无限循环重试。

## 一、产品导航与功能模块

> ⚠️ 如果 OneSEC 域名不清楚，请先询问用户，不要擅自填写域名。
> 如找不到功能入口或遇到 404，查阅 [references/onesec-menu.md](references/onesec-menu.md) 获取完整 URL。

**进入页面首选直接拼接 URL**（比菜单点击更稳定）：

```bash
flocks browser -c '
tid = new_tab("https://<onesec-domain>/<path>", activate=True)
wait_for_load()
print(tid)
print(page_info())
print(js("document.body.innerText.slice(0, 2000)"))
'
```

| 模块 | 子功能 | URL 路径 | 主要用途 |
|------|--------|---------|---------|
| **监控和报告** | 概览 | `/monitor/overview` | 系统整体安全态势总览 |
| | 终端安全概览 | `/pcedr/dashboard` | EDR 安全状态总览 |
| | DNS防护概览 | `/onedns/console/dashboard` | DNS 防护状态总览 |
| | 银狐防治 | `/antisilverfox` | 银狐专项治理入口 |
| | 报告中心 | `/onedns/console/reports` | 各类安全报表查看入口 |
| **终端检测与响应 (EDR)** | 威胁事件 | `/pcedr/threatincidents` | EDR 聚合威胁事件列表（事件维度） |
| | 检出行为 | `/pcedr/anomalyactivities` | 异常行为检测结果列表 |
| | 恶意文件 | `/pcedr/threatfiles` | 恶意文件检测与管理入口 |
| | 日志调查 | `/pcedr/investigation` | EDR 原始告警 / 行为记录高级查询（日志维度） |
| | 响应中心 | `/pcedr/tasks` | 响应任务管理入口（处置维度） |
| | 威胁狩猎 | `/pcedr/threat_hunting` | 主动威胁狩猎场景入口 |
| **DNS安全防护** | 域名解析报表 | `/onedns/console/domains` | DNS 解析统计报表 |
| | 域名解析日志 | `/onedns/console/domainLog` | DNS 原始解析记录查询（日志维度） |
| | 安全事件报表 | `/onedns/console/securityincident` | DNS 安全事件统计报表 |
| | 内容分类报表 | `/onedns/console/contentCategory` | 网站内容分类统计报表 |
| | 威胁定位处置 | `/onedns/console/threatMitigation` | DNS 威胁告警定位与处置入口 |
| | VA溯源日志 | `/onedns/console/vaInvestigation` | VA 溯源调查日志 |
| **漏洞补丁管理** | 漏洞管理 | `/vulnerability_manage` | 漏洞清单查询入口（资产维度） |
| | 补丁管理 | `/patch_manage` | 补丁分发与安装状态管理 |
| **软件安全** | 已安装软件/AI应用 | `/pcedr/softwarelist` | 终端软件资产清单（资产维度） |
| | 软件管控 | `/software_control` | 软件黑白名单与远控软件管控入口 |
| | 软件管控日志 | `/pcedr/software_log` | 软件管控执行记录查询（日志维度） |
| **外设管控** | 外设管控日志 | `/device_control_log` | 外设使用记录查询（日志维度） |
| **组织架构** | 职场/分组管理 | `/groupmanagement` | 职场与分组组织结构管理 |
| **终端接入管理** | 终端管理 | `/pcedr/agent_group` | 终端设备清单与状态查询（资产维度） |
| | 终端策略 | `/pcedr/policies` | 终端安全策略配置入口 |
| | 信任名单 | `/pcedr/whitelist` | 信任文件与信任进程管理 |
| | 自定义IOC/IOA | `/pcedr/ioc` | 自定义威胁指标与检测规则配置 |
| | 终端部署 | `/pcedr/deployment` | Agent 部署管理入口 |
| **DNS接入管理** | 网络出口配置 | `/onedns/console/deployNetworkConfig` | DNS 出口网络配置 |
| | VA部署 | `/onedns/console/sysConfig/vaclientConfig` | VA 设备部署管理 |
| | DNS防护策略 | `/onedns/console/allPolicies` | DNS 防护策略配置入口 |
| | 拦截放行域名 | `/onedns/console/polices/destList` | 域名黑白名单管理 |
| **平台管理** | 开放接口 | `/apiList` | API 接口文档入口 |
| | 登录管理 | `/pcedr/users` | 账号与权限管理 |
| | 通知管理 | `/pcedr/notice` | 消息通知配置 |
| | 审计日志 | `/pcedr/audit_log` | 平台操作审计日志 |
| | 平台配置 | `/platformconfig` | 系统参数配置入口 |
| | 敏感数据加密 | `/pcedr/encrypt_data` | 敏感数据加密配置 |

## 二、数据查询与调查

> 进入浏览器模式后，对于查询类诉求，优先阅读 [references/cli-reference.md](references/cli-reference.md) 并使用本地 CLI，只有在需要详情下钻或复杂交互时才继续页面点击。

### 入口选择说明

OneSEC 中 **事件**、**告警 / 日志**、**DNS 告警**、**资产数据** 分布在不同页面，必须先判断查询目标再选入口：

| 查询目标 | 使用入口 | 数据维度 |
|---------|---------|---------|
| 查看最新威胁事件、事件总览、处置状态 | 威胁事件 `/pcedr/threatincidents` | **事件**维度 |
| 精细查询高危告警、原始行为记录、溯源轨迹 | 日志调查 `/pcedr/investigation` | **告警 / 日志**维度 |
| 查看 DNS 威胁告警和终端处置 | 威胁定位处置 `/onedns/console/threatMitigation` | **DNS 告警**维度 |
| 查询 DNS 原始解析记录 | 域名解析日志 `/onedns/console/domainLog` | **DNS 日志**维度 |
| 查看漏洞清单与高危漏洞 | 漏洞管理 `/vulnerability_manage` | **漏洞资产**维度 |
| 查询终端已安装软件 | 已安装软件 `/pcedr/softwarelist` | **软件资产**维度 |

### 2.1 EDR 威胁事件查询

→ 页面：`/pcedr/threatincidents`

> 事件详情查看的两种方式（威胁图 vs 事件概览）见 [references/onesec-incident.md](references/onesec-incident.md)

#### 默认方式：CLI 直接查询（推荐）

```bash
ONESEC_BASE_URL=https://<onesec-domain> \
ONESEC_AUTH_STATE=~/.flocks/browser/onesec/auth-state.json \
uv run python .flocks/plugins/skills/onesec-use/scripts/onesec_cli.py \
  threat search [--days <N>] [--page <N>] [--page-size <N>] [--keyword "<关键词>"]

ONESEC_BASE_URL=https://<onesec-domain> \
ONESEC_AUTH_STATE=~/.flocks/browser/onesec/auth-state.json \
uv run python .flocks/plugins/skills/onesec-use/scripts/onesec_cli.py \
  threat top [--days <N>] [--limit <N>]
```

只有在需要查看威胁图、事件概览、事件详情页时，才继续页面操作。

```bash
flocks browser -c '
tid = new_tab("https://<onesec-domain>/pcedr/threatincidents", activate=True)
wait_for_load()
print(tid)
print(js("document.body.innerText.slice(0, 2000)"))
'
```

**查看事件详情（默认方式，威胁图）**：

```bash
flocks browser -c '
tid = new_tab("https://<onesec-domain>/pcedr/threatincidents/incident?umid=...&guid=...", activate=True)
wait_for_load()
print(tid)
print(js("document.body.innerText.slice(0, 2500)"))
'
```

**查看事件概览（快速方式）**：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("document.querySelectorAll(\"tbody tr\")[0]?.click()")
wait(0.8)
print(js("document.body.innerText.slice(0, 2000)"))
'
```

顶部筛选（判定结果、PUA 检测、处置状态等）是自定义组件，优先直接用 `js(...)`：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("""
Array.from(document.querySelectorAll("*"))
  .find(el => el.textContent?.trim() === "APT")
  ?.click()
""")
wait(0.5)
print(page_info())
'
```

### 2.2 告警日志调查（EDR 原始告警 / 行为日志，高级查询）

→ 页面：`/pcedr/investigation`

> 完整字段列表（50+ 字段）、枚举值和查询语法见 [references/instruction.md](references/instruction.md)

#### 默认方式：CLI 直接查询（推荐）

```bash
ONESEC_BASE_URL=https://<onesec-domain> \
ONESEC_AUTH_STATE=~/.flocks/browser/onesec/auth-state.json \
uv run python .flocks/plugins/skills/onesec-use/scripts/onesec_cli.py \
  log search "<SQL条件>" [--days <N>] [--hours <N>] [--limit <N>]

ONESEC_BASE_URL=https://<onesec-domain> \
ONESEC_AUTH_STATE=~/.flocks/browser/onesec/auth-state.json \
uv run python .flocks/plugins/skills/onesec-use/scripts/onesec_cli.py \
  log types [--days <N>]

ONESEC_BASE_URL=https://<onesec-domain> \
ONESEC_AUTH_STATE=~/.flocks/browser/onesec/auth-state.json \
uv run python .flocks/plugins/skills/onesec-use/scripts/onesec_cli.py \
  log trend [--days <N>]
```

只有在需要点击单条记录详情、使用页面 AI 查询、查看联动面板或复杂筛选时，才继续页面操作。

**进入高级查询模式**：

> ⚠️ 高级查询使用字段名直接写条件语句，**必须先查阅 [references/instruction.md](references/instruction.md)** 确认字段名、枚举值和语法，否则查询无效。

```bash
flocks browser -c '
tid = new_tab("https://<onesec-domain>/pcedr/investigation", activate=True)
wait_for_load()
print(tid)
js("""
Array.from(document.querySelectorAll("button"))
  .find(el => el.textContent?.trim() === "高级查询")
  ?.click()
""")
wait(1.0)
js("""
(() => {
  const el = document.querySelector("textarea, input[placeholder*=查询], input[placeholder*=SQL]");
  if (!el) return false;
  el.focus();
  el.value = "查询语句";
  el.dispatchEvent(new Event("input", {bubbles: true}));
  el.dispatchEvent(new Event("change", {bubbles: true}));
  return true;
})()
""")
js("""
Array.from(document.querySelectorAll("button"))
  .find(el => el.textContent?.trim() === "查询")
  ?.click()
""")
wait(1.5)
print(js("document.body.innerText.slice(0, 2500)"))
'
```

**使用 AI 查询**（输入自然语言 → 生成 SQL → 一键填入 → 查询）：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("""
Array.from(document.querySelectorAll("button"))
  .find(el => el.textContent?.includes("AI查询"))
  ?.click()
""")
wait(1.0)
js("""
(() => {
  const el = document.querySelector("textarea, input");
  if (!el) return false;
  el.focus();
  el.value = "查询最近一周内所有高危威胁日志";
  el.dispatchEvent(new Event("input", {bubbles: true}));
  el.dispatchEvent(new Event("change", {bubbles: true}));
  return true;
})()
""")
js("""
Array.from(document.querySelectorAll("button"))
  .find(el => el.textContent?.trim() === "生成SQL")
  ?.click()
""")
wait(2.0)
print(js("document.body.innerText.slice(0, 2500)"))
'
```

### 2.3 DNS 查询与告警处置

#### 域名解析日志

→ 页面：`/onedns/console/domainLog`

按终端内网 IP、终端名称、MAC 地址、DNS 查询域名、威胁类型、组内资产标记、时间范围等条件查询 DNS 解析记录。

#### 威胁定位处置

→ 页面：`/onedns/console/threatMitigation`

左侧为**威胁终端列表**，右侧为**威胁告警**（含严重级别、威胁名称、威胁类型、响应状态）。支持对终端下发处置任务。

### 2.4 资产与配置查询

#### 漏洞管理

→ 页面：`/vulnerability_manage`

#### 已安装软件（软件资产查询）

→ 页面：`/pcedr/softwarelist`

> ⚠️ 查询终端上安装了哪些软件，应使用此页面，而非日志调查。

#### 终端管理

→ 页面：`/pcedr/agent_group`

#### 终端策略

→ 页面：`/pcedr/policies`

### 2.5 响应与威胁狩猎

#### 响应中心

→ 页面：`/pcedr/tasks`

```bash
flocks browser -c '
tid = new_tab("https://<onesec-domain>/pcedr/tasks", activate=True)
wait_for_load()
js("""
Array.from(document.querySelectorAll("*"))
  .find(el => el.textContent?.trim() === "自动响应")
  ?.click()
""")
wait(1.0)
print(js("document.body.innerText.slice(0, 2000)"))
'
```

#### 威胁狩猎

→ 页面：`/pcedr/threat_hunting`

```bash
flocks browser -c '
tid = new_tab("https://<onesec-domain>/pcedr/threat_hunting", activate=True)
wait_for_load()
print(tid)
print(js("document.body.innerText.slice(0, 2000)"))
js("""
const el = document.evaluate(
  '//*[contains(text(),"执行与下载")]',
  document,
  null,
  XPathResult.FIRST_ORDERED_NODE_TYPE,
  null
).singleNodeValue;
if (el) el.click();
""")
wait(0.8)
print(js("document.body.innerText.slice(0, 2500)"))
'
```

## 三、浏览器操作技巧

**核心原则**：OneSEC 是 SPA（React / Ant Design），自定义组件通常不适合依赖通用点击语义；优先 `page_info()` 观察页面，再用 `js(...)` 直接读取 DOM 或触发点击。

### 推荐操作模式

1. 先用 `page_info()` 看 URL、标题、滚动位置
2. 再用 `js("document.body.innerText.slice(...)")` 或更具体的 DOM 读取确认页面结构
3. 能稳定定位 DOM 时，直接 `js(...)` 点击或填值
4. 页面变化后重新读取状态，不复用上一步的判断结果

### 复杂 JS 推荐写法

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("""
Array.from(document.querySelectorAll("*"))
  .find(el => el.textContent?.trim() === "目标文字")
  ?.click()
""")
wait(0.8)
print(page_info())
'
```

### 等待结果出现

`browser-use` 下不再使用旧的 `wait --text` / `wait --fn` 模型。推荐做法是：

- 动作后 `wait(0.5 ~ 2.0)`
- 再次 `page_info()` 或 `js(...)`
- 必要时循环 2 到 3 次观察结果是否出现

### 滚动与表格点击

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("window.scrollTo(0, document.body.scrollHeight)")
wait(0.8)
print(js("document.body.innerText.slice(0, 2000)"))
'
```

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("document.querySelectorAll(\"tbody tr\")[0]?.click()")
wait(0.8)
print(js("document.body.innerText.slice(0, 2000)"))
'
```

### 调试技巧

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(js("""
Array.from(document.querySelectorAll("a, button"))
  .slice(0, 100)
  .map(el => ({
    tag: el.tagName,
    text: el.textContent?.trim()?.slice(0, 30),
  }))
"""))
'
```

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(js("""
(() => {
  const rows = document.querySelectorAll("tbody tr");
  return {
    rowCount: rows.length,
    firstRowHtml: rows[0]?.outerHTML?.slice(0, 200) || "",
  };
})()
"""))
'
```

## 四、重要提醒

1. **优先直接拼 URL**：OneSEC 菜单使用 SPA 路由，URL 跳转比菜单点击更稳定可靠。
2. **不要主动关闭浏览器**：除非用户明确要求，否则不要关闭用户当前浏览器或用户已有 tab。
3. **滚动必须用 JavaScript**：滚动后再重新读取页面状态。
4. **列表只展示摘要**：威胁事件、漏洞、补丁等列表只展示摘要，需点击进入详情获取完整信息。
5. **查询优先级**：浏览器模式下，如果需求只是拉列表、跑 SQL、看趋势或统计，优先使用 [references/cli-reference.md](references/cli-reference.md) 中的 CLI，不要直接开始页面点击。

## 附加资源

- **OneSEC 完整菜单结构与 URL 映射**：[references/onesec-menu.md](references/onesec-menu.md)
- **威胁事件详情查看方式**：[references/onesec-incident.md](references/onesec-incident.md)
- **日志调查字段说明与高级查询**：[references/instruction.md](references/instruction.md)
- **OneSEC CLI 参考**：[references/cli-reference.md](references/cli-reference.md)
