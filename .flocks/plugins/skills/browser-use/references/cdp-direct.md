# CDP 直连（flocks browser 内核）

本文件是 `browser-use` 的 CDP 模式参考文档。只有当 `browser-use/SKILL.md` 判定应使用 `CDP 直连` 时，才读取并遵循本文件；不要和 `references/agent-browser.md` 同时加载。

## 使用入口

`flocks browser` 是 Flocks 内置的薄 CDP harness：agent 直接控制真实浏览器，helpers 预加载，daemon 自动启动。通过 `flocks browser ...` 调用。

## 适用范围

使用 CDP 直连处理这些任务：

- 打开网页、点击、填写表单、上传文件、打印/下载、抓取动态页面内容。
- 需要复用用户当前 Chrome 登录态、访问内网页面、登录后页面或复杂 SPA。
- 需要比普通浏览器自动化更底层的 CDP 能力，例如 raw CDP、iframe/shadow DOM/cross-origin 点击、网络状态判断。

如果任务涉及账号、资金、生产环境配置、删除/发布/提交等高风险动作，先向用户确认再执行。遇到登录页、验证码、TOTP、授权弹窗或需要输入密码时，可以停下来提示用户操作。

## 操作原则

- 默认不要直接操作用户已有 tab。优先 `new_tab(url, activate=True)` 创建自己的 tab，在其中完成任务。每个任务或者每个站点的操作创建一个 tab 就可以，不要对同一个站点或任务多次创建 tab。
- 后续命令恢复同一个 tab 时，优先 `attach_tab(target_id)`，不要反复 `switch_tab(target_id)` 抢用户当前浏览器焦点。只有需要让用户看到页面、登录、授权或手动操作时才使用 `switch_tab(target_id)`。
- 只关闭自己创建的 tab，不关闭用户原有 tab。
- 不要主动停止或重启 Chrome。daemon stale 时可以 `restart_daemon()` 一次；杀 Chrome 只能作为最后排障手段并先说明影响。
- 不建议主动停止 proxy 或远程调试连接；重启后可能需要用户重新授权 Chrome 调试连接。
- 每个可见动作后立即用 `page_info()` 或 `js(...)` 重新观察并验证，不要凭假设继续。
- 优先读取 DOM、文本、可交互元素、URL、网络状态等结构化信息。
- 只有用户明确需要视觉证据、调试像素坐标或外部流程可查看图片文件时，才用 `capture_screenshot(...)` 生成截图文件。
- CDP 坐标是 CSS 像素；如果必须坐标点击，先用 DOM/布局信息确认目标位置，必要时用 `js("window.devicePixelRatio")` 理解设备像素比例。

## 快速开始

先确认 `flocks browser` 可用：

```bash
flocks browser --doctor
```

基本用法：

```bash
flocks browser -c '
tid = new_tab("https://example.com", activate=True)
wait_for_load()
print(page_info())
'
```

`flocks browser -c` 内部可直接使用预加载 helpers；daemon 会自动启动并连接已运行的 Chrome。

常用 helpers：

- `new_tab(url, activate=True)`, `goto_url(url)`, `wait_for_load(timeout=15)`, `page_info()`
- `click_at_xy(x, y)`, `type_text(text)`, `press_key(key)`, `scroll(x, y, dy=-300)`
- `js(expression)`, `cdp("Domain.method", **params)`, `drain_events()`
- `list_tabs(include_chrome=False)`, `current_tab()`, `attach_tab(target_id)`, `switch_tab(target_id)`, `close_tab()`, `ensure_real_tab()`
- `upload_file(selector, path)`, `http_get(url, headers=None)`
- `capture_screenshot(path="/tmp/shot.png", full=False, max_dim=1800)` 仅用于可查看图片文件的调试或交付场景

## 标准工作流

`flocks browser` 的基础循环是：打开页面 -> 读取结构化状态 -> 执行动作 -> 重新读取状态验证。

1. 如果是具体网站任务，先搜索已存在的站点经验。优先看 `~/.flocks/workspace/domain-skills/`；如果没有，再自己探索。
2. 创建自己的 tab，等待加载，并读取页面基础状态与候选交互元素：

```bash
flocks browser -c '
tid = new_tab("https://example.com", activate=True)
wait_for_load()
print(page_info())

state = js("""
(() => ({
  title: document.title,
  url: location.href,
  text: document.body.innerText.slice(0, 2000),
  controls: Array.from(document.querySelectorAll("a,button,input,textarea,select"))
    .slice(0, 40)
    .map((el, index) => ({
      index,
      tag: el.tagName,
      text: (el.innerText || el.value || el.placeholder || el.ariaLabel || el.href || "").trim(),
      disabled: el.disabled || el.getAttribute("aria-disabled") === "true"
    }))
}))()
""")
print(state)
'
```

3. 执行动作并验证。能稳定定位 DOM 时，优先用 `js(...)`；必须操作可见但 DOM 难以稳定定位的控件时，再使用 `click_at_xy(...)`、`type_text(...)`、`press_key(...)`：

```bash
flocks browser -c '
js("document.querySelector(\"button[type=submit]\")?.click()")
wait(0.5)
print(page_info())
'
```

4. 需要提取数据时用 `js(...)` 或 `http_get(...)`。静态页面/接口批量抓取优先 `http_get`，不要浪费浏览器。
5. 结束时只关闭自己创建的 tab。若不确定 tab 是否属于自己，保留它并说明。

注意：

- 每次点击、输入、提交、弹窗处理、导航、滚动或重渲染后，都重新调用 `page_info()` 或 `js(...)`。
- 页面变化后，之前读取到的元素状态、坐标和文本都可能过期，必须重新观察。
- 提取大量结构化数据时，优先在页面内用 `js(...)` 组装 JSON 后返回。
- 判断内容是否已在 DOM 中，不要只依赖当前可见区域；懒加载或虚拟列表再配合 `scroll(...)` 分段读取。

## Tab 与可见性

- `new_tab(url, activate=True)` 会创建并 attach 到新 tab，默认同时让 tab 在 Chrome 中可见；这是需要用户登录或观察页面时的默认入口。
- `new_tab(url, activate=False)` 会创建后台 tab 并 attach，不主动抢当前可见 tab。
- `attach_tab(target_id)` 只 attach 到目标 tab，不激活 Chrome UI；后续读取页面状态、导出数据、保存认证状态等命令优先使用它。
- `switch_tab(target_id)` 会 attach 到目标 tab 并执行 `Target.activateTarget`，让目标 tab 在 Chrome 中可见；只在需要用户看到或手动操作时使用。
- `close_tab(target_id, activate_next=False)` 可关闭自己创建的 tab 且不自动切到其他已打开 tab。
- `list_tabs()` 默认会包含 `chrome://`、`about:` 等内部页面；要面向用户页面时用 `list_tabs(include_chrome=False)`。
- 忽略 `chrome://omnibox-popup.top-chrome/` 这类假 page target。页面 `w=0 h=0` 时通常是 attach 到了错误 target。
- 当当前 session stale、内部页或不可见，并且确实要恢复到某个用户可见页面时，先 `ensure_real_tab()`。

## 结构化观察与点击

默认先用 `page_info()` 与 `js(...)` 观察页面，不依赖截图：

```python
print(page_info())
print(js("document.body.innerText.slice(0, 2000)"))
```

能稳定定位 DOM 时，优先通过页面内 JS 或 selector 相关能力操作；需要穿过 iframe、shadow DOM、cross-origin 或自定义控件时，再使用 compositor 级坐标点击：

```python
click_at_xy(x, y)
```

坐标点击前尽量用 DOM 布局信息确认目标位置，例如 `getBoundingClientRect()`。坐标点击不稳定、目标不可见或需要隐藏 input 时，改用 DOM、iframe target 或 raw CDP。

截图仅作为可选调试产物：

```python
capture_screenshot("/tmp/shot.png", max_dim=1800)
```

如果启用点击调试：

```bash
flocks browser --debug-clicks -c '
click_at_xy(420, 315)
'
```

## 对话框与阻塞状态

浏览器原生 `alert`、`confirm`、`prompt`、`beforeunload` 会冻结 JS 线程。动作后如果 `page_info()` 返回 `{"dialog": ...}`，先处理对话框：

```python
cdp("Page.handleJavaScriptDialog", accept=True)   # OK / Leave
cdp("Page.handleJavaScriptDialog", accept=False)  # Cancel / Stay
```

需要读取历史事件时：

```python
for event in drain_events():
    if event["method"] == "Page.javascriptDialogOpening":
        print(event["params"]["type"], event["params"]["message"])
```

不要在未确认语义时自动接受会导致提交、删除、支付或离开未保存页面的弹窗。

## 文件上传与下载

上传优先使用真实 file input：

```python
upload_file("input[type=file]", "/absolute/path/to/file")
```

如果页面用自定义上传控件，先用 `js(...)` 找按钮、label、隐藏 input 或 dropzone，再点击触发；必要时用 DOM 找隐藏 input。下载任务需要证明下载确实开始或文件已落盘；如果资源可直接访问，优先 `http_get(url)` 保存到输出目录或 `/tmp/`。

## 登录与认证状态

`flocks browser` 复用用户当前 Chrome profile。cookies、localStorage、sessionStorage 会自然保留；用户完成登录后，同一 profile 下的新 tab 通常自动带登录态。

- 先打开目标页判断是否已登录；需要密码、验证码、TOTP 或授权确认时，让用户在 Chrome 中操作。
- 用户完成后刷新或重新打开目标 URL，再用 `page_info()` / `js(...)` 验证目标内容是否可见。
- 可以用 CDP 读写 cookies，用 `js(...)` 读写 localStorage / sessionStorage；默认不要打印 value。
- 不要记录或沉淀 cookie value、token、Authorization header、密码、验证码等敏感值。

检查登录态时只看 cookie 名称和 storage key：

```bash
flocks browser -c '
tid = new_tab("https://example.com", activate=True)
wait_for_load()
cookies = cdp("Network.getCookies", urls=["https://example.com"]).get("cookies", [])
print({"cookies": [c["name"] for c in cookies], "localStorage": js("Object.keys(localStorage)")})
'
```

恢复测试登录态时可用 `cdp("Network.setCookie", ...)` 或 `js("localStorage.setItem(...)")`，写入后刷新验证。不要把完整 cookies/localStorage 导出到仓库；临时迁移只写 `/tmp/`。

## 安装与连接排障

如果 `flocks browser` 不可用或连接失败：

1. 先运行 `flocks browser --doctor` 看版本、安装模式、daemon 和 Chrome 状态。
2. 首次安装或冷启动优先运行 `flocks browser --setup`。
3. Chrome 未运行时只启动 Chrome，再重试；不要直接让用户改设置。
4. 只有在明确提示 remote debugging 未启用或 `DevToolsActivePort` 缺失时，才让用户打开 `chrome://inspect/#remote-debugging` 并勾选 Allow remote debugging。
5. `connection refused`、`DevTools not live yet`、`/json/version` 404 通常是 Chrome 正在启动，轮询等待，不要重启。
6. stale websocket / stale socket 时执行一次：

```bash
flocks browser -c 'restart_daemon()'
```

## 沉淀可复用经验

针对特定网站的多次操作，如果有可复用信息，优先沉淀到 `~/.flocks/workspace/domain-skills/<site>/`：

- URL 模式、必要 query 参数、能跳过 loader 的直接路由。
- 私有 API、请求方法、payload 结构、认证依赖。
- 稳定 selector、语义结构、滚动容器、虚拟列表规则。
- 框架交互怪癖、必须等待的状态、容易踩坑的弹窗或 beforeunload。

不要写入 secrets、cookies、token、用户个人数据、原始像素坐标或本次任务流水账。

## The self-heal loop 自修复/扩展 循环
> 终极模式（非必要不进入）
`flocks/browser/` 是当前 CDP 直连模式使用的内核实现：
- `helpers.py`：该文件是浏览器操作的行为实现，支持增减、修改函数实现自定义功能，具备生长能力
- 其他源码文件是支撑`helpers.py`运行的设施，不支持修改
