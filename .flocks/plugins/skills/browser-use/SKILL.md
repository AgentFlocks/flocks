---
name: browser-use
description: 统一处理浏览器使用任务，支持 CDP 直连/无头模式 使用用户本机 Chromium 系浏览器。Use when the user asks to browse websites, interact with pages, fill forms, capture screenshots, reuse an existing Chrome/Chromium/Edge login session, access internal/login-only pages, handle access-restricted content, when websearch/webfetch are unavailable, or automate browser actions.
---

# Browser Use

## 适用范围

当任务需要真实浏览器环境时使用本 skill，包括：

- 打开网页、浏览页面、点击按钮、填写表单
- 截图、抓取页面内容、提取链接或媒体资源
- 访问登录后页面、内部系统、动态渲染页面
- 复用用户当前浏览器的登录态

## 浏览器使用模式说明

| 模式 | 说明 | 何时使用 |
| --- | --- | --- |
| `agent-browser` | 独立浏览器自动化模式 | 用户明确说用 `agent-browser`|
| `cdp-direct` | 复用本机 Chromium 系浏览器的 CDP 直连模式 | 用户明确说用 CDP 模式|
| `cdp-headless` | 通过 `BU_CDP_WS` / `BU_CDP_URL` 连接独立 headless Chromium 实例 | 只有用户明确要求 headless，或任务本身是后台任务/定时任务，或系统不支持可视化 |

 - 用户明确指出模式后，直接阅读执行规则部分
 - 当用户没有明确指出使用模式时，进入下一步自动判定
 - 不要默认切到 `cdp-headless`；能用用户正常浏览器完成的任务，优先保持可见浏览器流程

## 自动判定与失败处理

当用户没有明确指出使用模式时,按以下 4 步自动判定 + 失败处理:

### Step 1: 是否需要 headless

满足以下任一条件时,判定为“使用 headless 浏览器实例的 `cdp-direct` 流程”:

- 任务天然是后台执行
- 任务属于定时任务 / `CI` / `cron`
- 用户明确要求本次使用 headless 浏览器
- 系统不支持可视化,如服务器

如果判定需要 headless,则按以下顺序执行:

- 先读取 `references/cdp-headless.md`
- 优先使用显式提供的 `BU_CDP_WS` / `BU_CDP_URL`
- 不要引导用户去操作日常浏览器 profile 的 inspect 授权页
- 如果没有显式 CDP endpoint,再按 `references/cdp-headless.md` 中当前平台对应的后台启动方式启动专用 Chromium 实例;必须让浏览器进程脱离当前 shell 独立存活,并为它分配未被占用的专用 remote debugging 端口,优先复用安装脚本设置的 `AGENT_BROWSER_EXECUTABLE_PATH`
- 连通后读取 `references/cdp-direct.md`,后续页面操作统一按 `cdp-direct` 工作流执行

### Step 2: 跑 CDP 可用性检测

先执行:

```bash
flocks browser --doctor
```

该命令会检查 `flocks browser` 的 daemon 是否可用、Chrome/Chromium/Edge 是否运行,以及当前是否有可用的浏览器连接。

### Step 3: 根据检测结果决定模式（if-then 三段式）

| 结果 | 触发条件 | 一线修复 | 仍失败兜底 |
|---|---|---|---|
| **A** | `flocks browser --doctor` 通过 | 立即确定 `CDP 直连`,阅读 `references/cdp-direct.md`,之后不再切到 `agent-browser` | — |
| **B** | 浏览器已运行,但 daemon 或 active connection 不可用 | 提示用户 `browser: not connected — 请确保 Chrome/Chromium/Edge 已打开,访问 `chrome://inspect/#remote-debugging` 勾选 Allow`,等用户确认 | 用户确认后 `flocks browser --setup`（不包短超时）→ `--doctor` 确认 → 仍失败则 `flocks browser --reload` 清旧 daemon → 重试 `--setup`。Windows PowerShell 中 `flocks browser -c` 用单行 `;` 分隔,避开多行单引号转义 |
| **C** | `--doctor` 失败,或当前机器没 Chrome/Chromium/Edge | 明确告知缺哪项,提示安装路径 | **不**擅自降级到 curl/webfetch;坚持告知 skill 边界 |

### Step 4: 跨模式通用失败（if-then 三段式）

| 触发条件 | 一线修复 | 仍失败兜底 |
|---|---|---|
| `cdp-headless` 启动了专用 Chromium 实例 | 记 PID + 日志 + 专用 profile 路径 | 任务结束或明确放弃才清理;**不**关闭用户提供的远程浏览器 |
| 模式已确定后用户改主意 | 重新跑 `--doctor` 走 Result A/B/C 判定 | 避免同时加载 `cdp-direct.md` + `agent-browser.md` |


## 执行规则

1. 模式一旦确定，立即读取对应的 reference。
2. `cdp-headless` 是唯一例外：先读取 `references/cdp-headless.md` 完成浏览器启动与连接，再读取 `references/cdp-direct.md` 执行通用页面操作。
3. 在 `cdp-headless` 中，如果当前任务自己启动了专用浏览器实例，必须记录 PID / 日志 / 专用 profile，并只在任务结束或明确放弃后清理自己启动的实例；不要关闭用户提供的远程浏览器。
4. 不要同时加载 `references/cdp-direct.md` 和 `references/agent-browser.md`。
5. `flocks browser` 的 daemon 文件固定放在 `~/.flocks/browser/`,例如 `bu.sock`、`bu.log`、`bu.pid`、`bu.port`。

## 产品经验Skill

把特定产品页/网站的浏览器操作经验，沉淀到对应产品 skill，实现可复用。

适合沉淀的经验包括：

- 已确认某产品的稳定登录的方法
- 更稳定的页面进入方式，例如“优先直接拼 URL，不走菜单”
- 表格、筛选器、分页、弹窗、下载、详情展开等可靠操作路径
- 某站点特有的等待条件、重渲染特征、虚拟列表/SPA 交互怪癖
- 特定操作的成功经验，失败案例（特定操作失败 2 次以上，最终成功的经验）

具体怎么沉淀到 产品skill，请阅读 `references/browser-experience-in-skill.md`。

## References

- `references/browser-experience-in-skill.md`：如何把浏览器经验沉淀到产品 skill，以及推荐记录模板
- `references/cdp-headless.md`：`cdp-headless` 的全平台启动、连接与排障方式
- `references/cdp-direct.md`：以 `flocks browser` 作为 CDP 直连入口的启动方式、API、页面探索策略、错误处理
- `references/agent-browser.md`：agent-browser 的使用说明、错误处理等
