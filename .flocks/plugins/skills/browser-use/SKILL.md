---
name: browser-use
description: 统一处理浏览器使用任务，支持 CDP 直连用户日常 Chrome 与 agent-browser CLI 两种模式。Use when the user asks to browse websites, interact with pages, fill forms, capture screenshots, reuse an existing Chrome login session, access internal/login-only pages, or automate browser actions.
---

# Browser Use

## 适用范围

当任务需要真实浏览器环境时使用本 skill，包括：

- 打开网页、浏览页面、点击按钮、填写表单
- 截图、抓取页面内容、提取链接或媒体资源
- 访问登录后页面、内部系统、动态渲染页面
- 复用用户当前 Chrome 的登录态

## 先判定模式

默认先尝试 `CDP 直连`，不要一开始就把两种模式都读一遍。

### 第一步：先跑 CDP 可用性检测

先执行：

```bash
flocks browser --doctor
```

该命令会检查 `flocks browser` 的 daemon 是否可用、Chrome/Edge 是否运行，以及当前是否有可用的浏览器连接。

### 第二步：根据检测结果决定模式

#### 结果 A：doctor 通过

这时立即确定使用 `CDP 直连`，然后马上阅读：

- `references/cdp-direct.md`

之后只按 CDP 流程执行，不再切到 `agent-browser`。

#### 结果 B：Chrome 已运行，但 daemon 或 active browser connection 不可用

必须直接提示用户：

```text
chrome: not connected — 请确保 Chrome 已打开，然后访问 chrome://inspect/#remote-debugging 并勾选 Allow remote debugging
或
不使用 CDP 模式，使用agent-browser
```

然后等待用户进一步指示。如果用户确认已开启后，重新运行同一个检测命令。

- 如果重新检测通过：立即使用 `CDP 直连`，并立刻阅读 `references/cdp-direct.md`
- 如果仍未通过：继续提示用户检查 remote debugging，或提示切到 `agent-browser`

#### 结果 C：`flocks browser --doctor` 失败，或当前机器没有可用 Chrome/Edge

说明当前环境不适合 `CDP 直连`。此时要：

1. 明确告诉用户是哪一项不满足，提示需要做什么操作才能达到要求
2. 切换到 `agent-browser` 模式
3. 立即阅读：
   - `references/agent-browser.md`

不要继续尝试 CDP。

## 执行规则

1. 模式一旦确定，立即只读取对应的 reference。
2. 不要同时加载 `references/cdp-direct.md` 和 `references/agent-browser.md`。
3. 涉及账号风控时，先提示用户存在自动化检测和封禁风险。
4. 操作结束后清理自己创建的资源：
   - `CDP 直连`：关闭自己创建的 tab，不关闭用户已有 tab。
   - `agent-browser`：按需关闭 tab 或 browser session。

## References

- `references/cdp-direct.md`：以 `flocks browser` 作为 CDP 直连入口的启动方式、API、页面探索策略、错误处理
- `references/agent-browser.md`：agent-browser 的 snapshot/ref 工作流、交互命令、等待、截图、排障
