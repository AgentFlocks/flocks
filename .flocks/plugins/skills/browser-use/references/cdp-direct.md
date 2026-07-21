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

## 基础操作速查

这些操作对应本地 `browser-use` skill 的常用能力。这里仅提供命令模板；执行顺序和选择原则见“主流程”。

### 打开页面或复用目标 tab

```bash
flocks browser -c '
tid = open_or_attach_tab("https://example.com", activate=True)
wait_for_load()
print({"targetId": tid, "page": page_info()})
'
```

如果用户说页面已经在浏览器或侧边栏打开，先列出非内部页并选择目标 tab；不要直接新开 headless：

```bash
flocks browser -c '
for tab in list_tabs(include_chrome=False):
    print(tab)
'
```

确认目标后：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(page_info())
'
```

### 观察页面状态

```bash
flocks browser -c '
print(page_info())
print(js("document.body.innerText.slice(0, 4000)"))
'
```

查找可点击元素：

```bash
flocks browser -c '
items = js("""
Array.from(document.querySelectorAll("a,button,input,textarea,select,[role=button],[onclick]"))
  .slice(0, 80)
  .map((el, i) => ({
    i,
    tag: el.tagName,
    text: (el.innerText || el.value || el.getAttribute("aria-label") || el.title || "").trim().slice(0, 120),
    selector: el.id ? "#" + el.id : el.name ? el.tagName.toLowerCase() + "[name=" + JSON.stringify(el.name) + "]" : null,
    disabled: !!el.disabled,
    rect: (() => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,w:r.width,h:r.height}; })()
  }))
""")
print(items)
'
```

### 点击、输入与按键

优先用稳定 DOM 操作：

```bash
flocks browser -c '
js("document.querySelector(\"button[type=submit]\")?.click()")
wait(0.5)
print(page_info())
'
```

输入字段必须触发 `input` / `change` 事件：

```bash
flocks browser -c '
js("""
const el = document.querySelector("input[name=q], textarea[name=q]");
el.value = "search text";
el.dispatchEvent(new Event("input", {bubbles: true}));
el.dispatchEvent(new Event("change", {bubbles: true}));
""")
press_key("Enter")
wait(0.5)
print(page_info())
'
```

只有 DOM 难以稳定定位时，才退到坐标：

```bash
flocks browser -c '
click_at_xy(420, 315)
type_text("text")
press_key("Enter")
wait(0.5)
print(page_info())
'
```

### 滚动、等待与截图

```bash
flocks browser -c '
scroll(500, 500, dy=-800)
wait(0.5)
print(page_info())
'
```

等待指定文本或选择器时，用短轮询，避免盲等：

```bash
flocks browser -c '
import time
deadline = time.time() + 10
while time.time() < deadline:
    if js("document.body.innerText.includes(\"Success\")"):
        print("found")
        break
    wait(0.5)
else:
    print("not found")
'
```

截图只在需要视觉证据或调试时保存：

```bash
flocks browser -c '
print(capture_screenshot("/tmp/browser-use-shot.png", full=False, max_dim=1800))
'
```

### 提取数据

提取文本、HTML、链接或结构化数据时，优先在页面内组装 JSON：

```bash
flocks browser -c '
print(js("document.title"))
print(js("location.href"))
print(js("document.body.innerText.slice(0, 8000)"))
'
```

```bash
flocks browser -c '
rows = js("""
Array.from(document.querySelectorAll("a[href]")).map(a => ({
  text: a.innerText.trim(),
  href: a.href
})).filter(x => x.text || x.href).slice(0, 200)
""")
print(rows)
'
```

静态资源或接口可直接访问时，优先 `http_get(url)`，不要浪费浏览器上下文。

### 关闭当前任务资源

只关闭自己创建或确认属于本任务的 tab：

```bash
flocks browser -c '
close_tab("<TARGET_ID>", activate_next=False)
'
```

不确定是否是用户原有 tab 时，保留 tab 并说明原因。

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

## 主流程

按同一个循环执行：准备目标 tab -> 观察页面 -> 选择动作 -> 执行 -> 再观察验证。不要凭旧状态继续操作。

1. 如果是具体网站/产品任务，先搜索已存在的 skill，看 `<产品>-use` skill 下是否存在浏览器相关操作；如果没有，再自己探索。
2. 创建自己的 tab，或在用户明确要求继续当前页面时先列出并 attach 目标 tab。保存 `targetId`，等待加载，并读取页面基础状态。后续步骤继续使用同一个 tab 时，优先 `attach_tab(target_id)`，不要反复 `switch_tab(...)` 抢用户焦点。

3. 先用 `page_info()` 看 URL、标题、滚动位置、页面尺寸和对话框阻塞状态，再用 `js(...)` 读取文本、DOM 结构、元素状态或业务字段。
4. 能稳定定位 DOM 时，优先直接在页面内执行 JS 或 raw CDP；只有 DOM 难以稳定定位，或需要操作 compositor 级控件时，才退到坐标点击。iframe / 特殊 target 场景，再考虑 `iframe_target(...)` 配合 `js(..., target_id=...)`。

5. 坐标点击前用 DOM 布局信息确认目标位置，例如 `getBoundingClientRect()`。坐标点击不稳定、目标不可见或需要隐藏 input 时，改用 DOM、iframe target 或 raw CDP。
6. 需要提取数据时用 `js(...)` 或 `http_get(...)`。提取大量结构化数据时，优先在页面内用 `js(...)` 组装 JSON 后返回；静态页面/接口批量抓取优先 `http_get`，不要浪费浏览器。
7. 判断内容是否已在 DOM 中，不要只依赖当前可见区域；懒加载或虚拟列表再配合 `scroll(...)` 分段读取。
8. 结束时只关闭自己创建的 tab。若不确定 tab 是否属于自己，保留它并说明；如果当前任务还临时拉起了专用 headless 浏览器实例，tab 清理完后再按 `references/cdp-headless.md` 的约定决定是否关闭整个浏览器进程。


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

## 表单填充与点击操作模式

### 字段类型（js 优先）

| 字段 | 推荐 | 备注 |
|---|---|---|
| text / email / tel / password / textarea | `js("el=document.querySelector('[name=x]');el.value='...';el.dispatchEvent(new Event('input'))")` | **必须** dispatchEvent |
| radio / checkbox | `js("document.querySelector('[name=size][value=small]').click()")` | `click_at_xy` 不可靠 |
| select | `js("el=document.querySelector('[name=size]');el.value='small';el.dispatchEvent(new Event('change'))")` | 必须 dispatch change |
| file | `upload_file('input[type=file]', '/abs/path')` | 唯一方式 |

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

1. 先运行 `flocks browser --doctor` 看版本、安装模式、daemon 和浏览器状态；不要只看退出码，优先读 `next action`，再看 `browser running`、`daemon alive`、`active browser connections`。
2. `next action` 为 `attach`，或 `daemon alive` ok 但 `active browser connections` 为 0 时，不要先反复 `--setup`。先用一次实际命令触发连接/观察：`flocks browser -c 'print(page_info())'` 或 `flocks browser -c 'print(list_tabs(include_chrome=False))'`。
3. 如果上一步失败或仍无连接，再执行 `flocks browser --reload` 清旧 daemon，然后执行 `flocks browser --setup`；setup 可能需要多次，因为用户可能需要完成浏览器 inspect/Allow 授权，或浏览器需要时间写入 remote debugging 状态。
4. 首次安装、冷启动、daemon 不存在/不通，且浏览器已经运行或配置了 `BU_CDP_URL` / `BU_CDP_WS` 时，优先运行 `flocks browser --setup`。
5. Chrome / Chromium / Edge 未运行且没有显式 CDP endpoint 时，只提示用户启动浏览器或提供 endpoint；不要直接让用户改设置。
6. 只有在明确提示 remote debugging 未启用、`DevToolsActivePort` 缺失、403 handshake、remote-debugging page 或 not live yet 时，才让用户打开对应浏览器的 inspect 页面（例如 `chrome://inspect/#remote-debugging` 或 `edge://inspect/#remote-debugging`）并勾选 Allow remote debugging。
7. 用户刚开启 remote debugging 时，不要立刻再次运行 `flocks browser --doctor`；先执行一次 `flocks browser --setup`，或直接执行 `flocks browser -c 'print(page_info())'` 触发 daemon attach，再用 `--doctor` 做只读确认。
8. `connection refused`、`DevTools not live yet`、`/json/version` 404 通常是浏览器正在启动，轮询等待，不要重启。
9. stale websocket / stale socket 时执行一次：

```bash
flocks browser -c 'restart_daemon()'
```

10. 页面操作异常但连接本身正常时，优先回到上面的“页面操作排障”，不要把所有问题都归因为浏览器没连上。

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
