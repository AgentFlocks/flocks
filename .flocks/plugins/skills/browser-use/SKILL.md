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

## 浏览器使用模式说明

| 模式 | 说明 | 何时使用 |
| --- | --- | --- |
| `agent-browser` | 独立浏览器自动化模式 | 用户明确说用 `agent-browser`|
| `cdp-direct` | 复用本机 Chrome 的 CDP 直连模式 | 用户明确说用 CDP 模式|

 - 用户明确指出模式后，直接阅读执行规则部分
 - 当用户没有明确指出使用模式时，进入下一步自动判定

## 自动判定模式

当用户没有明确指出使用模式时,开始自动判断

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

然后等待用户进一步指示。如果用户确认已开启后，不要立刻重跑 `flocks browser --doctor`；先执行一次 `flocks browser --setup`，或直接执行 `flocks browser -c 'print(page_info())'` 触发 attach，再运行 `flocks browser --doctor` 做只读确认。

- 如果 `--setup` / `-c` 成功，或随后 `--doctor` 通过：立即使用 `CDP 直连`，并立刻阅读 `references/cdp-direct.md`
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

## References

- `references/cdp-direct.md`：以 `flocks browser` 作为 CDP 直连入口的启动方式、API、页面探索策略、错误处理
- `references/agent-browser.md`：agent-browser 的使用说明、错误处理等
