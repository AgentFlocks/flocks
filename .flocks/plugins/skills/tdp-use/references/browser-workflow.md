
# TDP 威胁检测平台浏览器自动化

> 本文档统一按 `browser-use` 的 `cdp-direct` 流程执行。进入浏览器模式后，先跑 `flocks browser --doctor`；doctor 通过后只使用 `flocks browser`。
> 对后台任务 / 定时任务，或系统不支持可视化，使用 `browser-use` 的 `cdp-headless` 模式。

## 零、登录认证

State 文件路径：`~/.flocks/browser/tdp/auth-state.json`（固定，全局唯一）。

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
tid = new_tab("https://<tdp-domain>/login", activate=True)
wait_for_load()
print(tid)
print(page_info())
'
```

等待用户登录结束，收到通知后继续：

```bash
flocks browser state save ~/.flocks/browser/tdp/auth-state.json
```

### 已知用户名 / 密码时的自动登录流程

适用前提：登录页是标准账号密码表单，且只有协议勾选，不涉及验证码、短信验证码、滑块、多因子认证或人工确认。

1. 使用 `flocks browser -c '...'` 打开 `/login` 页面后，先执行 `page_info()`，确认用户名输入框、密码输入框、协议 checkbox 和登录按钮的 DOM 结构。
2. 填写账号密码时，优先使用原生 `HTMLInputElement.prototype.value` setter 赋值，避免被 React / Angular 一类前端框架拦截，导致页面看起来有值但内部状态未更新。
3. 勾选协议 checkbox 后，对相关输入控件补发 `input` 和 `change` 事件，确保页面的响应式绑定同步生效。
4. 确认表单状态正常后，点击登录按钮提交。
5. 等待约 3 秒，再次执行 `page_info()` 检查是否已跳转到 `/dashboard`；只有 URL 确认跳转后，才视为登录成功。
6. 登录成功后，立即执行 `flocks browser state save ~/.flocks/browser/tdp/auth-state.json` 保存 session，供后续 CLI 和浏览器复用。
7. 如果页面没有跳转、按钮不可点击，或者出现验证码 / 二次确认，则停止自动登录流程，改为人工登录，不要在同一方式上重复重试。

### CLI 认证失败时的恢复流程

当 CLI 调用出现以下任一情况，优先判定为认证问题（**不要立刻要求用户重新登录**）：

- 返回 HTTP `401` / `403`
- 返回内容包含 `Unauthorized`、`login`、未登录、认证失败
- `auth-state.json` 存在，但 CLI 请求仍失败

**恢复步骤（最多尝试 1 次）**：

```bash
flocks browser state load ~/.flocks/browser/tdp/auth-state.json --url "https://<tdp-domain>/dashboard"
```

```bash
URL=$(flocks browser -c '
info = page_info()
print(info.get("url", ""))
' | tail -n 1)
if [[ "$URL" == *"/login"* ]]; then
  echo "Session 仍无效，需重新登录"
else
  flocks browser state save ~/.flocks/browser/tdp/auth-state.json
  echo "Session 已恢复，可重试 CLI"
fi
```

---

## 一、产品导航与功能模块

**进入页面首选直接拼接 URL**（比菜单点击更稳定）：

```bash
flocks browser -c '
tid = new_tab("https://<tdp-domain>/<path>", activate=True)
wait_for_load()
print(page_info())
print(js("document.body.innerText.slice(0, 2000)"))
'
```

| 模块 | 子功能 | URL 路径 | 主要用途 |
|------|--------|---------|---------|
| **监控** | 首页/仪表板（默认） | `/dashboard` | 安全态势总览：告警趋势、失陷主机、TOP威胁 |
| **威胁** | 威胁事件对应的告警主机（默认） | `/hosts` | 被告警命中的主机列表，含告警次数和类型 |
| | 全部威胁→实时监控 | `/threatMonitor` | 威胁事件查询入口，支持高级查询 |
| | 外部攻击→智能聚合 | `/attack` | 外部攻击事件聚合视图（按事件维度） |
| | 外部攻击→外部攻击 | `/incidents/external` | 外部攻击威胁事件列表 |
| | 内网渗透→内网聚合 | `/lateralconverge` | 内网横向移动事件聚合 |
| | 内网渗透→内网渗透 | `/incidents/lateral` | 内网渗透威胁事件列表 |
| | 失陷破坏 | `/incidents/compromise` | 失陷主机的对外通信/C2连接 |
| | 蜜罐诱捕 | `/hfish` | 蜜罐命中记录，含攻击来源IP和攻击手法 |
| **资产&风险** | 全部服务（默认） | `/asset/serviceList` | 宽泛查询所有服务，左侧分类列表可按类型筛选 |
| | Web应用/框架 | `/asset/webapp` | Web资产，含框架指纹（OA/CMS/管理工具等） |
| | 域名资产 | `/asset/domains` | 内部域名，含解析IP/是否对外开放 |
| | 登录入口 | `/asset/loginApi` | 所有暴露的登录口（SSH/RDP/数据库/FTP/Web），含弱口令/爆破统计 |
| | 主机资产 | `/asset/allDevices` | 全部主机，含服务/Web框架/对外开放情况 |
| | 上传接口 | `/asset/uploadApi` | 文件上传接口，含是否被攻击/存在上传漏洞 |
| | API | `/risk/api` | API列表，含敏感信息类型（身份证/银行卡/AK·SK） |
| | 云服务 | `/cloudService` | 内网主机访问公有云的流量分析 |
| | 脆弱性 | `/asset/vulnerability` | 漏洞/配置不当/访问风险，按严重级别和主机数 |
| | 弱口令 | `/asset/weakPwd` | 弱口令检测结果，含已成功登录记录 |
| | 敏感信息 | `/asset/sensitive` | 传输中的敏感数据（身份证/手机/邮箱/银行卡） |
| | 风险策略配置 | `/asset/riskPolicies` | 自定义风险告警规则（端口开放/异常登录等） |
| **调查** | 日志分析 | `/investigation/logquery` | 原始告警日志高级查询（SPL语法） |
| | 跟踪与狩猎 | `/hunting` | 基于IP/域名/Hash的威胁狩猎 |
| | 攻击者分析 | `/attacker` | 分析特定攻击者IP的攻击行为全貌 |
| **处置** | 取证溯源 | `/endpoint_forensics` | 对失陷主机发起取证，获取终端行为记录 |

> 如找不到功能入口或遇到 404，查阅 [tdp-menu.md](tdp-menu.md) 获取完整 URL。

## 二、数据查询与调查

> 写 CLI 查询前，先查 [cli-reference.md](cli-reference.md) 确认命令、参数和常用示例；如果 SQL 字段、枚举值或操作符不确定，再查 [instruction.md](instruction.md)。不要凭记忆臆测字段名。

### 入口选择说明

**威胁事件** 和 **告警日志** 是两个不同维度的数据，优先使用 CLI 调用，只在需要查看原始报文时才用浏览器：

| 查询目标 | 推荐入口 | 数据维度 |
|---------|---------|---------|
| 查看/筛选威胁事件总览（按类型、方向、严重级别、时间） | **CLI `monitor threats`** → 浏览器 `/threatMonitor` | **事件**维度，TDP已聚合，一条 = 一个威胁事件，含检出次数 |
| 精细条件查询原始告警日志（按 IP、端口、URL、payload） | **CLI `logs search`** → 浏览器 `/investigation/logquery` | **告警**维度，未聚合，一条 = 一条原始告警记录 |
| 查看 PCAP / 原始报文 | 浏览器点击告警详情（仅此方式） | 原始数据 |

**用词识别规则（必须遵守）**：
- 用户说"**告警**"、"**告警记录**"、"**告警日志**"、"**最近 X 小时/天的告警**" → **一律走 `告警日志查询`**
- 用户说"**威胁事件**"、"**攻击事件**"、"**有哪些事件**" → 走 `事件查询`
- 未明确区分时：**默认走 `告警日志查询`**，除非用户明确要求"事件聚合"或"事件总览"

### 2.1 告警日志调查（原始告警日志，SQL查询）

查的是每一条原始检测记录，而非聚合后的事件。适合：精细条件筛选、查某事件关联的所有原始告警、分析某 IP 的完整行为轨迹。

#### 默认方式：CLI 直接调用 API（推荐）

```bash
THREATBOOK_BASE_URL=https://<tdp-domain> \
THREATBOOK_COOKIE_FILE=~/.flocks/browser/tdp/auth-state.json \
THREATBOOK_SSL_VERIFY=false \
uv run python scripts/tdp_cli.py \
  logs search [--sql "<SQL条件>"] [时间参数] [--limit <条数>]
```

> CLI 用法和查询示例见 [cli-reference.md](cli-reference.md)

#### 备用方式：浏览器操作（需查看 PCAP / 原始报文时使用）

```bash
flocks browser -c '
tid = new_tab("https://<tdp-domain>/investigation/logquery", activate=True)
wait_for_load()
print(tid)
print(page_info())
print(js("document.body.innerText.slice(0, 1200)"))
'
```

执行高级查询的推荐方式，是直接用 `js(...)` 选择按钮和输入框，而不是再依赖 `snapshot/@eN/find`：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("""
Array.from(document.querySelectorAll("button"))
  .find(el => el.textContent?.trim() === "高级查询")
  ?.click()
""")
wait(1.0)
input_el = js("""
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
print({"input_ready": input_el})
js("""
Array.from(document.querySelectorAll("button"))
  .find(el => el.textContent?.trim() === "查询")
  ?.click()
""")
wait(1.5)
print(js("document.body.innerText.slice(0, 2000)"))
')
```

点击具体日志条目可查看完整的 HTTP 请求/响应、PCAP 原始报文等详细信息。

### 2.2 威胁事件查询

查询**已聚合的威胁事件**（一条记录 = 一个事件，含检出次数、攻击者、受害主机等汇总信息）。

#### 默认方式：CLI 直接调用 API（推荐）

```bash
THREATBOOK_BASE_URL=https://<tdp-domain> \
THREATBOOK_COOKIE_FILE=~/.flocks/browser/tdp/auth-state.json \
THREATBOOK_SSL_VERIFY=false \
uv run python scripts/tdp_cli.py \
  monitor threats [--sql "<SQL条件>"] [时间参数] [--limit <条数>]
```

> CLI 用法和查询示例见 [cli-reference.md](cli-reference.md)

#### 备用方式：浏览器操作（需交互式探索或查看页面详情时使用）

当查询涉及时间范围时，优先通过 URL 操作，把用户要求转为时间戳查询（UTC+8 时区），例如：

`https://<tdp-domain>/threatMonitor?time_from=<start_timestamp>&time_to=<end_timestamp>`

```bash
flocks browser -c '
tid = new_tab("https://<tdp-domain>/threatMonitor", activate=True)
wait_for_load()
print(tid)
print(page_info())
print(js("document.body.innerText.slice(0, 1600)"))
'
```

需要展开筛选或点进详情时，优先写成明确的 JS 片段，例如按文字点“高级查询”或点第一行数据，具体模式见 [browser-tips.md](browser-tips.md)。

#### 特定类型威胁事件入口（浏览器）

**外部攻击事件**：`/attack`（按事件聚合视图）或 `/incidents/external`（原始事件列表）

**内网渗透事件**：`/lateralconverge`（按事件聚合视图）或 `/incidents/lateral`（原始事件列表）

**失陷破坏**：`/incidents/compromise`

### 2.3 资产查询

#### 资产攻击面

| 功能 | URL | 说明 |
|------|-----|------|
| 全部服务 | `/asset/serviceList` | 服务汇总（数据库/Web/远程登录/认证等）；左侧分类为自定义组件，优先用 `js(...)` 切换 |
| Web 应用 | `/asset/webapp` | 框架指纹（Spring/Shiro/OA/CMS等）、对外开放、关联 URL |
| 数据库资产 | `/asset/serviceList` 左侧"数据库"分类 | 查有哪些数据库服务在运行；查攻击告警用 `logs search "net.app_proto IN ('mysql','redis','oracle') AND threat.level = 'attack'"` |
| 域名资产 | `/asset/domains` | 内部域名、解析IP，可过滤是否对外开放 |
| 登录入口 | `/asset/loginApi` | 所有暴露的认证入口（SSH/RDP/Web/数据库），重点看爆破/弱口令数量 |
| 主机资产 | `/asset/allDevices` | 全部主机，含服务/Web框架、开放端口 |
| 上传接口 | `/asset/uploadApi` | 重点关注"对外开放"和"存在风险"标记 |
| API | `/risk/api` | 含敏感信息的 API（身份证/银行卡/AK·SK） |

#### 风险查询

| 功能 | URL | 说明 |
|------|-----|------|
| 脆弱性 | `/asset/vulnerability` | 漏洞/配置不当/访问风险，含受影响主机数和严重级别 |
| 弱口令 | `/asset/weakPwd` | 重点关注**登录结果为"成功"**的条目（已被利用） |
| 敏感信息 | `/asset/sensitive` | 传输中检测到的敏感数据，可按身份证/手机/邮箱过滤 |

## 三、浏览器操作原则

> 浏览器只用于以下场景：查看 PCAP / 原始报文 / 页面详情，或 CLI 无法完成的交互式探索。复杂定位、调试、截图时再阅读 [browser-tips.md](browser-tips.md)。

1. **优先直接拼 URL**：TDP 是 SPA，`new_tab("https://<tdp-domain>/<path>")` 比菜单点击更稳定。
2. **定位优先级**：先 `page_info()`，再 `js(...)` 读取 DOM 和页面文本；能稳定定位 DOM 时，直接用 `js(...)` 点击或填值。
3. **自定义组件处理**：TDP 大量按钮 / Tab / 折叠项是自定义组件；不要依赖旧的 ref 语义，优先用 XPath、`querySelectorAll` 或按文字遍历元素。
4. **等待规则**：导航后先 `wait_for_load()`；页面异步刷新后用 `wait(...)`，然后重新 `page_info()` 或 `js(...)` 验证结果是否出现。
5. **滚动规则**：只能使用 JavaScript 滚动；滚动后必须重新读取页面状态。
6. **需要继续下钻时的判断**：如果列表内容被截断、页面有大量空白、出现“加载更多”，或还没看到预期数据，应继续滚动、展开或进入详情，而不是基于当前摘要下结论。

常用最小模板：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(page_info())
print(js("document.body.innerText.slice(0, 1200)"))
'
```

## 四、重要提醒

1. **必须查看告警详情**：列表页只展示摘要，必须点击条目进入详情才能获取完整的 HTTP 请求 / 响应、原始报文、PCAP 等信息。
2. **结论必须基于详细数据**：调查和溯源场景中，列表摘要信息不足以支撑结论；一个事件包含多条告警时，需抽查关键告警明细，不能只看第一条。
3. **不要主动关闭浏览器**：除非用户明确要求，否则不要关闭用户当前浏览器或用户已有 tab。
4. **查询结果为空时直接告知用户**：若页面显示无数据或表格为空，不要反复调整条件重试。
5. **Session 管理**：详见[零、登录认证](#零、登录认证)。任务开始时先验证 session 有效性再执行任务；CLI 认证失败时先走恢复流程，不要立刻重新登录。
6. **禁止连续失败循环**：
   - 同一个目标操作最多尝试 **3 次**
   - 第一次失败后，必须更换方法，不要重复同样操作
   - 同一页面连续失败达到 **5 次**，直接停止本页面操作，不再继续尝试
   - **以下错误属于需要用户干预的基础设施问题，立即停止所有重试，直接告知用户处理**：
     - `ERR_CERT_AUTHORITY_INVALID`：TDP 站点证书不被本机信任，请求用户处理
     - `ERR_NAME_NOT_RESOLVED`：TDP 域名无法解析，告知用户确认域名是否正确，或检查 DNS / hosts 配置

## 附加资源

- **CLI 参考**：[cli-reference.md](cli-reference.md)
- **事件详情查看与分析**：[event-detail.md](event-detail.md)
- **TDP 完整菜单结构与 URL 映射**：[tdp-menu.md](tdp-menu.md)
- **高级查询字段和操作符说明**：[instruction.md](instruction.md)
- **浏览器操作详细技巧**：[browser-tips.md](browser-tips.md)
