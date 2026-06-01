# CDP 直连（flocks browser 内核）

本文件是 `browser-use` 的 CDP 模式参考文档。只有当 `browser-use/SKILL.md` 判定应使用 `CDP 直连` 时，才读取并遵循本文件；不要和 `references/agent-browser.md` 同时加载。

## CDP 无头模式入口

如果当前任务需要 headless，即任务本身是后台任务/定时任务，或系统不支持可视化，先读取：

- `references/cdp-headless.md`

先按该文档完成专用 headless 浏览器启动、`BU_CDP_URL` / `BU_CDP_WS` 设置，以及 `flocks browser --setup` 连通；连通成功后，再回到本文件，后续 tab、页面、CDP helper、提取与排障流程都按本文件执行。

## 操作原则

- 默认不要直接操作用户已有 tab。优先 `new_tab(url, activate=True)` 创建自己的 tab，或者用 `open_or_attach_tab(url, activate=True)` 复用当前任务自己已经创建的 tab。
- 后续命令恢复同一个 tab 时，优先 `attach_tab(target_id)`，不要反复 `switch_tab(target_id)` 抢用户当前浏览器焦点。只有需要让用户看到页面、登录、授权或手动操作时才使用 `switch_tab(target_id)`。
- 只关闭自己创建的 tab，不关闭用户原有 tab。`close_tab()` 默认会拒绝关闭未托管 tab。
- 不要主动停止或重启浏览器。唯一例外是 `cdp-headless` 场景下由当前任务临时启动的专用实例，这种实例可以在任务完全结束后按 `references/cdp-headless.md` 的生命周期约定关闭；daemon stale 时可以 `restart_daemon()` 一次；
- 不建议主动停止 proxy 或远程调试连接；重启后可能需要用户重新授权浏览器调试连接。
- 每个可见动作后立即用 `page_info()` 或 `js(...)` 重新观察并验证，不要凭假设继续。
- 优先读取 DOM、文本、可交互元素、URL、网络状态等结构化信息。
- 只有用户明确需要视觉证据、调试像素坐标或外部流程可查看图片文件时，才用 `capture_screenshot(...)` 生成截图文件。
- CDP 坐标是 CSS 像素；如果必须坐标点击，先用 DOM/布局信息确认目标位置，必要时用 `js("window.devicePixelRatio")` 理解设备像素比例。

## 快速开始

先确认 `flocks browser` 可用：

```bash
flocks browser --doctor
```

通过`flocks browser -c '...'` 操作浏览器

## 语法说明

- `flocks browser -c '...'` 执行的是一段 Python 代码，不是交互式 REPL；如果希望看到结果，必须显式 `print(...)`。
- 多行代码请直接写成真正的多行 shell 字符串或 heredoc；不要把 `\n` 当字面量塞进单引号参数里。
- 在 `Windows PowerShell` 中，默认写法是：把整段 Python 代码放进一对外层双引号里，并尽量压成单行，用分号分隔语句。
- 在 `Windows PowerShell` 中，内层字符串尽量统一改用单引号，例如 `js('document.body.innerText.slice(0, 5000)')`；这样可以减少外层双引号、内层双引号互相打架导致的截断或变形。
- 如果代码里本身包含很多引号、反引号、`$`，或者已经长到不适合单行，先写到临时 `.py` 文件，再用 `Get-Content -Raw` 读出后传给 `-c`；不要硬拼多行单引号字符串。

Windows PowerShell 推荐示例：

```powershell
flocks browser -c "r = js('document.body.innerText.slice(0, 5000)'); print(r)"
```

Windows PowerShell 多行代码推荐写法：

```powershell
@'
tid = new_tab("https://example.com", activate=True)
wait_for_load()
print(page_info())
'@ | Set-Content "$env:TEMP\flocks-browser-cmd.py"

flocks browser -c (Get-Content -Raw "$env:TEMP\flocks-browser-cmd.py")
```

## 核心操作循环
> 打开页面 -> 观察当前状态 -> 执行动作 -> 再观察验证

1. 新建或打开已打开 tab
2. 用 `page_info()` / `js(...)` 观察当前页面、URL、文本、结构和阻塞状态
3. 选择当前最稳妥的动作方式
4. 动作后重新观察，确认页面是否真的变化
5. 如果未达成目标，先解释当前卡点，再换一种方式继续


## 标准工作流

1. 如果是具体网站/产品任务，先搜索已存在的skill，看 `<产品>-use` skill 下是否存在浏览器相关操作；如果没有，再自己探索。
2. 创建自己的 tab，保存 `targetId`，等待加载，并先读取页面基础状态：

```bash
flocks browser -c '
tid = new_tab("https://example.com", activate=True)
wait_for_load()
print(page_info())
'
```

如需进一步观察页面结构、文本或候选交互元素，再按当前站点实际情况用 `js(...)` 做针对性提取；

后续步骤继续使用同一个 tab 时，先恢复它：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(page_info())
'
```

3. 执行动作并验证。能稳定定位 DOM 时，**首选 `click(selector)`**（自动 scroll-into-view、`elementFromPoint` 遮挡检查、DPR 修正、pointer+mouse+click 完整事件链，React/Vue 组件库也能正确响应）；退路是 `js(...)` 自定义逻辑；最后才用 `click_at_xy(...)`、`type_text(...)`、`press_key(...)`：

```bash
flocks browser -c '
click("button[type=submit]")
wait(0.5)
print(page_info())
'
```

4. 需要提取数据时用 `js(...)` 或 `http_get(...)`。静态页面/接口批量抓取优先 `http_get`，不要浪费浏览器。
5. 结束时只关闭自己创建的 tab。若不确定 tab 是否属于自己，保留它并说明；如果当前任务还临时拉起了专用 headless 浏览器实例，tab 清理完后再按 `references/cdp-headless.md` 的约定决定是否关闭整个浏览器进程。

注意：

- 每次点击、输入、提交、弹窗处理、导航、滚动或重渲染后，都重新调用 `page_info()` 或 `js(...)`。
- 页面变化后，之前读取到的元素状态、坐标和文本都可能过期，必须重新观察。
- 提取大量结构化数据时，优先在页面内用 `js(...)` 组装 JSON 后返回。
- 判断内容是否已在 DOM 中，不要只依赖当前可见区域；懒加载或虚拟列表再配合 `scroll(...)` 分段读取。


## Tab 与可见性

- `new_tab(url, activate=True)` 会创建并 attach 到新 tab，默认同时让 tab 在浏览器中可见；这是需要用户登录或观察页面时的默认入口。
- `open_or_attach_tab(url, activate=True)` 只会复用当前任务自己创建过的同 URL tab；不会按 URL 复用用户已有 tab。
- `new_tab(url, activate=False)` 会创建后台 tab 并 attach，不主动抢当前可见 tab。
- `attach_tab(target_id)` 只 attach 到目标 tab，不激活浏览器 UI；后续读取页面状态、导出数据、保存认证状态等命令优先使用它。
- `switch_tab(target_id)` 会 attach 到目标 tab 并执行 `Target.activateTarget`，让目标 tab 在浏览器中可见；只在需要用户看到或手动操作时使用。
- `managed_tabs()` 只列出当前任务创建并仍然存在的 tab，可用于调试和确认复用目标。
- `close_tab(target_id, activate_next=False)` 默认只允许关闭自己创建的 tab，且不自动切到其他已打开 tab；确实需要关闭用户 tab 时必须显式传 `allow_unmanaged=True`。
- `list_tabs()` 默认会包含 `chrome://`、`about:` 等内部页面；要面向用户页面时用 `list_tabs(include_chrome=False)`。
- 忽略 `chrome://omnibox-popup.top-chrome/` 这类假 page target。页面 `w=0 h=0` 时通常是 attach 到了错误 target。
- 当当前 session stale、内部页或不可见，并且确实要恢复到某个用户页面时，先 `ensure_real_tab()`；它会先 attach 到非内部页而不是主动激活浏览器 UI。

## 读取、定位与执行

本节的核心不是罗列所有操作方式，而是确定优先级：先读清楚，再选最稳的执行方式。

默认先用 `page_info()` 与 `js(...)` 观察页面，不依赖截图：

```python
print(page_info())
print(js("document.body.innerText.slice(0, 2000)"))
```

推荐顺序：

1. 先用 `page_info()` 看 URL、标题、滚动位置、页面尺寸，以及是否被原生对话框阻塞
2. 再用 `js(...)` 读取文本、DOM 结构、元素状态、业务字段
3. **稳定点击首选 `click(selector)`**：scroll-into-view + `elementFromPoint` 遮挡校验 + DPR 换算 + 完整 `pointer* + mouse* + click` 事件链
4. 能稳定定位 DOM 时，优先直接在页面内执行 JS 或 raw CDP
5. 只有 DOM 难以稳定定位，或需要操作 compositor 级控件时，才退到坐标点击
6. iframe / 特殊 target 场景，再考虑 `iframe_target(...)` 配合 `js(..., target_id=...)`

## 稳定点击（首选 `click(selector)`）

`click(selector)` 是 CDP 直连模式下处理大多数点击场景的主路径：

```python
click("button.ant-btn-primary")                            # CSS selector
click("nav#menu li:nth-child(3) a")                        # 复合选择器
click("'#login-submit'", frame=iframe_target("login"))     # iframe 内点击
```

内部依次完成：定位元素 → `scrollIntoView` → `getBoundingClientRect` 读物理像素坐标 → `elementFromPoint` 校验未被遮挡 → `Input.dispatchMouseEvent type=mouseMoved` 预热 → 在元素上派发 `pointerover / pointerenter / pointermove / pointerdown / mousedown / pointerup / mouseup / click` 完整事件链。`click(selector)` 不可用或命中失败时会直接抛错，不会静默吞错。

能稳定定位 DOM 时，优先通过页面内 JS 或 selector 相关能力操作；需要穿过 iframe、shadow DOM、cross-origin 或自定义控件时，再使用 compositor 级坐标点击：

```python
click_at_xy(x, y)
```

坐标点击前尽量用 DOM 布局信息确认目标位置，例如 `getBoundingClientRect()`。坐标点击不稳定、目标不可见或需要隐藏 input 时，改用 DOM、iframe target 或 raw CDP。

截图仅作为可选调试产物：

```python
capture_screenshot("<workspace_dir>/tmp/shot.png", max_dim=1800)
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

## 页面操作排障

如果页面操作没有达到预期，优先按“先确认现状，再换方法”的顺序排障。

推荐排障顺序：

1. 先确认当前控制的是不是对的 tab：看 `current_tab()`，必要时看 `list_tabs()` / `managed_tabs()`
2. 再确认页面有没有真的变化：重新跑 `page_info()`、`js(...)`，不要复用旧判断
3. 如果动作无效，先检查是否有对话框、遮罩、iframe、局部刷新或 target 切换
4. 如果当前动作方式不稳定，就降级或升级方式，而不是重复同一动作

常见情况：

- 点击无效：先确认元素是否真的可交互、是否被遮罩覆盖、是否点在错误 tab；DOM 可定位时优先改用 `js(...)`
- 输入无效：先确认焦点和元素类型；必要时改用页面内 JS 赋值，或 `type_text(...)` 配合 `press_key(...)`
- 页面内容没变：先确认是否只是局部刷新、异步加载或滚动区变化，再决定是否继续等待
- attach 到错误上下文：检查 `current_tab()`、`list_tabs(include_chrome=False)`，必要时重新 `attach_tab(...)`
- iframe 场景异常：先确认是否确实进入了 iframe；需要时用 `iframe_target(...)` 缩小目标范围

原则上，不要连续重复同一个失败动作两次以上；第二次失败后就应回到观察阶段。

## 文件上传与下载

本节主要使用：`upload_file(...)`、`http_get(...)`、必要时配合 `js(...)`。

上传优先使用真实 file input：

```python
upload_file("input[type=file]", "/absolute/path/to/file")
```

如果页面用自定义上传控件，先用 `js(...)` 找按钮、label、隐藏 input 或 dropzone，再点击触发；必要时用 DOM 找隐藏 input。下载任务需要证明下载确实开始或文件已落盘；如果资源可直接访问，优先 `http_get(url)` 保存到输出目录或 `/tmp/`。

## 登录与认证状态

`flocks browser` 复用用户当前浏览器 profile。cookies、localStorage、sessionStorage 会自然保留；用户完成登录后，同一 profile 下的新 tab 通常自动带登录态。

- 先打开目标页判断是否已登录；需要密码、验证码、TOTP 或授权确认时，让用户在浏览器中操作。
- 用户完成后刷新或重新打开目标 URL，再用 `page_info()` / `js(...)` 验证目标内容是否可见。
- 可以用 CDP 读写 cookies，用 `js(...)` 读写 localStorage / sessionStorage。
- 明确需要导出或保存登录状态时，可以用 flocks browser state save auth-state.json
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

1. 先运行 `flocks browser --doctor` 看版本、安装模式、daemon 和浏览器状态。
2. 首次安装或冷启动优先运行 `flocks browser --setup`。
3. Chrome / Chromium / Edge 未运行时只启动浏览器，再重试；不要直接让用户改设置。
4. 只有在明确提示 remote debugging 未启用或 `DevToolsActivePort` 缺失时，才让用户打开对应浏览器的 inspect 页面（例如 `chrome://inspect/#remote-debugging` 或 `edge://inspect/#remote-debugging`）并勾选 Allow remote debugging。
5. 用户刚开启 remote debugging 时，不要立刻再次运行 `flocks browser --doctor`；先执行一次 `flocks browser --setup`，或直接执行 `flocks browser -c 'print(page_info())'` 触发 daemon attach，再用 `--doctor` 做只读确认。
6. `connection refused`、`DevTools not live yet`、`/json/version` 404 通常是浏览器正在启动，轮询等待，不要重启。
7. stale websocket / stale socket 时执行一次：

```bash
flocks browser -c 'restart_daemon()'
```

8. 页面操作异常但连接本身正常时，优先回到上面的“页面操作排障”，不要把所有问题都归因为浏览器没连上。

## The self-heal loop 自修复/扩展 循环

`flocks/browser/helpers.py`：该文件是浏览器操作的通用行为实现，具备生长能力

如果现有 helper 无法稳定实现目标，不要立刻放弃当前流程。`flocks browser -c '...'` 可以直接实现任意自定义逻辑。

推荐顺序是：
1. 先尝试内置 helper
2. 如果不够，就直接在 `flocks browser -c '...'` 中补当前任务所需的自定义代码
3. 如果这段逻辑会被重复使用，或已经证明值得沉淀时，可以沉淀操作函数到对应产品 skill

典型流程如下：

```text
打开页面 -> 观察 -> 尝试现有 helper -> 发现缺口
                                |
                                v
        直接在 flocks browser -c 中补一段自定义逻辑
                                |
                    +-----------+-----------+
                    |                       |
                    v                       v
         当前任务一次性使用            沉淀到 skill 复用
                    |                       |
                    +-----------+-----------+
                                |
                                v
                    重新执行当前观察-执行-验证循环
```

## 沉淀可复用经验
把特定产品页/网站的浏览器操作经验，沉淀到对应产品 skill，实现可复用。

适合沉淀的经验包括：

- 已确认某产品的稳定登录的方法
- 更稳定的页面进入方式，例如“优先直接拼 URL，不走菜单”
- 表格、筛选器、分页、弹窗、下载、详情展开等可靠操作路径
- 某站点特有的等待条件、重渲染特征、虚拟列表/SPA 交互怪癖
- 特定操作的成功经验，失败案例（特定操作失败 2 次以上，最终成功的经验）

具体怎么沉淀到 产品skill，请阅读 `references/browser-experience-in-skill.md`。
