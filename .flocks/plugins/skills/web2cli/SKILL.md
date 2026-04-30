---
name: web2cli
description: 使用统一的 Web2CLI 流程捕获网站的 XHR/Fetch 请求，并生成可复用的 CLI、Markdown 文档和 Postman 集合。支持 `agent-browser` 与 `cdp-direct` 两种模式：前者适合独立浏览器会话，后者通过 `browser-use` 的 `browser-harness` 内核复用用户 Chrome 登录态与 CDP 能力。适用于复现登录后操作、沉淀接口调用样例，或基于页面操作生成自动化工具时。
required: browser-use
---

# Web2CLI

按固定流程执行，不要跳步，不要引入额外的一键脚本。

## 使用的资源

- 默认注入脚本：`scripts/inject-hook-simple.js`
- 可选增强脚本：`scripts/inject-hook.js`
- CLI 生成器：`.flocks/plugins/skills/web2cli/scripts/generate-cli.py`
- 支持两种模式：
  - `MODE=agent-browser`
  - `MODE=cdp-direct`

## 模式选择

### `agent-browser`

适用于需要独立浏览器会话、命令式浏览器自动化、和 `agent-browser --session-name` 工作流的场景。

### `cdp-direct`

适用于需要复用用户 Chrome 登录态、通过 `browser-use` 的 `browser-harness` 内核直连 CDP、或者希望保留用户原有 tab 不受影响的场景。

使用此模式前必须：

1. 加载 `browser-use` skill，并按其中规则明确选择 `cdp-direct` 模式。
2. 在 `.flocks/plugins/skills/browser-use` 目录运行 doctor。
3. doctor 通过后，立即阅读 `.flocks/plugins/skills/browser-use/references/cdp-direct.md`。
4. 后续所有 `cdp-direct` 命令都通过 `uv run python -m scripts.run -c '...'` 执行。

```bash
(
  cd .flocks/plugins/skills/browser-use &&
  uv run python -m scripts.run --doctor
)
```

如果 doctor 提示 Chrome 已运行但 remote debugging 未连接，按 `browser-use` 的规则提示用户开启 Chrome remote debugging 后重试，不要继续使用旧 CDP proxy。

## 输出目录约定

捕获产生的文件统一落到 `~/.flocks/workspace/outputs/<today>/web2cli/<name>/`。

开始前先准备目录：

```bash
MODE="${MODE:-agent-browser}"
CAPTURE_NAME="<name>"
TODAY="$(date +%F)"
CAPTURE_ROOT="$HOME/.flocks/workspace/outputs/$TODAY/web2cli/$CAPTURE_NAME"
BROWSER_USE_SKILL=".flocks/plugins/skills/browser-use"
WEB2CLI_SKILL=".flocks/plugins/skills/web2cli"
mkdir -p "$CAPTURE_ROOT/captures"
```

各类输出位置固定如下：

- 浏览器内存中的原始捕获数据：`window.__capturedRequests`
- 导出的接口抓包 JSON：`$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json`
- 浏览器认证状态：`$CAPTURE_ROOT/auth-state.json`
- 生成的 CLI 工具：`$CAPTURE_ROOT/${CAPTURE_NAME}_cli.py`
- 生成的接口文档：`$CAPTURE_ROOT/${CAPTURE_NAME}_api.md`
- 生成的 Postman 集合：`$CAPTURE_ROOT/${CAPTURE_NAME}_postman.json`

## 标准流程

### 1. 打开浏览器或创建 Tab

`agent-browser` 模式：

```bash
agent-browser --headed --session-name "$CAPTURE_NAME" open "<URL>"
```

`cdp-direct` 模式：

```bash
TARGET_ID=$(
  cd "$BROWSER_USE_SKILL" &&
  uv run python -m scripts.run -c '
tid = new_tab("<URL>")
wait_for_load()
print(tid)
' | tail -n 1
)
echo "Created tab: $TARGET_ID"
```

### 2. 等待用户手动登录

要求用户在可见浏览器中完成登录、验证码、二次确认等人工步骤。

- `agent-browser`：在当前会话窗口中完成登录。
- `cdp-direct`：在刚创建的 Chrome tab 中完成登录，必要时让用户手动处理验证码、TOTP 或授权弹窗。

登录完成后告知 agent 继续。

### 3. 注入 Hook

默认使用 `scripts/inject-hook-simple.js`。如果确实需要更强的上下文推断和导出能力，再切换 `scripts/inject-hook.js`。

`agent-browser` 模式：

```bash
agent-browser --session-name "$CAPTURE_NAME" wait --load networkidle
agent-browser --session-name "$CAPTURE_NAME" eval --stdin < .flocks/plugins/skills/web2cli/scripts/inject-hook-simple.js
```

`cdp-direct` 模式：

```bash
WEB2CLI_HOOK="$(pwd)/$WEB2CLI_SKILL/scripts/inject-hook-simple.js"

(
  cd "$BROWSER_USE_SKILL" &&
  TARGET_ID="$TARGET_ID" WEB2CLI_HOOK="$WEB2CLI_HOOK" uv run python -m scripts.run -c '
import os
from pathlib import Path

target_id = os.environ.get("TARGET_ID")
if target_id:
    switch_tab(target_id)

source = Path(os.environ["WEB2CLI_HOOK"]).read_text(encoding="utf-8")
cdp("Page.addScriptToEvaluateOnNewDocument", source=source)
js(source)
print(js("typeof window.__apiCapture !== \"undefined\" ? \"installed v\" + window.__apiCapture.version : \"NOT installed\""))
'
)
```

如果 `cdp-direct` 返回 `NOT installed`，再立即对当前页面补一次：

```bash
(
  cd "$BROWSER_USE_SKILL" &&
  TARGET_ID="$TARGET_ID" WEB2CLI_HOOK="$WEB2CLI_HOOK" uv run python -m scripts.run -c '
import os
from pathlib import Path

target_id = os.environ.get("TARGET_ID")
if target_id:
    switch_tab(target_id)

js(Path(os.environ["WEB2CLI_HOOK"]).read_text(encoding="utf-8"))
print(js("typeof window.__apiCapture !== \"undefined\" ? \"installed v\" + window.__apiCapture.version : \"NOT installed\""))
'
)
```

注入后默认从 `window.__capturedRequests` 读取结果。

默认过滤策略为智能捕获：

- 仅捕获同源请求
- 排除静态资源、埋点监控、常见 websocket 连接
- 默认保留非 `GET` 请求
- `GET` 请求只要路径不像静态文件，也会保留

如果站点请求特别特殊，仍可在注入后切换为全抓模式。

`agent-browser` 模式：

```bash
agent-browser --session-name "$CAPTURE_NAME" eval "window.__apiCapture.config.captureMode = 'all'"
```

`cdp-direct` 模式：

```bash
(
  cd "$BROWSER_USE_SKILL" &&
  TARGET_ID="$TARGET_ID" uv run python -m scripts.run -c '
import os

target_id = os.environ.get("TARGET_ID")
if target_id:
    switch_tab(target_id)

js("window.__apiCapture.config.captureMode = \"all\"")
print(js("window.__apiCapture.config.captureMode"))
'
)
```

### 4. 等待用户执行业务操作

要求用户完成要捕获的页面动作，例如查询、翻页、筛选、提交表单、点击按钮、导出数据。

需要确认捕获是否开始时：

`agent-browser` 模式：

```bash
agent-browser --session-name "$CAPTURE_NAME" eval "window.__capturedRequests.length"
```

`cdp-direct` 模式：

```bash
(
  cd "$BROWSER_USE_SKILL" &&
  TARGET_ID="$TARGET_ID" uv run python -m scripts.run -c '
import os

target_id = os.environ.get("TARGET_ID")
if target_id:
    switch_tab(target_id)

print(js("window.__capturedRequests.length"))
'
)
```

### 5. 提取捕获数据

先确认数量：

`agent-browser` 模式：

```bash
agent-browser --session-name "$CAPTURE_NAME" eval "window.__capturedRequests.length"
```

`cdp-direct` 模式：

```bash
(
  cd "$BROWSER_USE_SKILL" &&
  TARGET_ID="$TARGET_ID" uv run python -m scripts.run -c '
import os

target_id = os.environ.get("TARGET_ID")
if target_id:
    switch_tab(target_id)

print(js("window.__capturedRequests.length"))
'
)
```

然后导出：

`agent-browser` 模式：

```bash
agent-browser --session-name "$CAPTURE_NAME" eval "JSON.stringify(window.__capturedRequests, null, 2)" > "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json"
```

`cdp-direct` 模式：

```bash
CAPTURE_OUT="$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json"

(
  cd "$BROWSER_USE_SKILL" &&
  TARGET_ID="$TARGET_ID" CAPTURE_OUT="$CAPTURE_OUT" uv run python -m scripts.run -c '
import json
import os

target_id = os.environ.get("TARGET_ID")
if target_id:
    switch_tab(target_id)

raw = js("JSON.stringify(window.__capturedRequests || [])")
data = json.loads(raw or "[]")
out = os.environ["CAPTURE_OUT"]
with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"Saved {len(data)} requests to {out}")
'
)
```

如果 `cdp-direct` 模式下数据量过大导致 `Runtime.evaluate` 响应截断，可分段导出：

```bash
CAPTURE_OUT="$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json"

(
  cd "$BROWSER_USE_SKILL" &&
  TARGET_ID="$TARGET_ID" CAPTURE_OUT="$CAPTURE_OUT" uv run python -m scripts.run -c '
import json
import os

target_id = os.environ.get("TARGET_ID")
if target_id:
    switch_tab(target_id)

total = int(js("window.__capturedRequests.length") or 0)
data = []
for start in range(0, total, 50):
    raw = js(f"JSON.stringify(window.__capturedRequests.slice({start}, {start + 50}))")
    data.extend(json.loads(raw or "[]"))

out = os.environ["CAPTURE_OUT"]
with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"Saved {len(data)} requests to {out}")
'
)
```

### 6. 保存认证状态

`agent-browser` 模式：

```bash
agent-browser --session-name "$CAPTURE_NAME" state save "$CAPTURE_ROOT/auth-state.json"
```

`cdp-direct` 模式：

```bash
AUTH_OUT="$CAPTURE_ROOT/auth-state.json"

(
  cd "$BROWSER_USE_SKILL" &&
  TARGET_ID="$TARGET_ID" AUTH_OUT="$AUTH_OUT" uv run python -m scripts.run -c '
import json
import os

target_id = os.environ.get("TARGET_ID")
if target_id:
    switch_tab(target_id)

info = page_info()
cookies = cdp("Network.getCookies", urls=[info["url"]]).get("cookies", [])
local_storage = json.loads(js("JSON.stringify(Object.entries(localStorage).map(([name, value]) => ({name, value})))") or "[]")
session_storage = json.loads(js("JSON.stringify(Object.entries(sessionStorage).map(([name, value]) => ({name, value})))") or "[]")

state = {
    "cookies": cookies,
    "localStorage": local_storage,
    "sessionStorage": session_storage,
}
out = os.environ["AUTH_OUT"]
with open(out, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, indent=2)
print(f"Saved {len(cookies)} cookies, {len(local_storage)} localStorage items, {len(session_storage)} sessionStorage items to {out}")
'
)
```

将 cookie、localStorage 和 sessionStorage 保存为后续 CLI 调用的认证输入。认证文件包含敏感值，只能写入 `$CAPTURE_ROOT` 这类工作区输出目录，不要写入代码仓库。

### 7. 关闭浏览器或 Tab

`agent-browser` 模式：

```bash
agent-browser --session-name "$CAPTURE_NAME" close
```

`cdp-direct` 模式：

```bash
(
  cd "$BROWSER_USE_SKILL" &&
  TARGET_ID="$TARGET_ID" uv run python -m scripts.run -c '
import os

target_id = os.environ.get("TARGET_ID")
if target_id:
    switch_tab(target_id)
close_tab()
'
)
```

`cdp-direct` 必须保留用户原有的 tab 不受影响。

### 8. 分析 API

至少执行端点去重分析：

```bash
jq -r '.[].url' "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json" | sed 's/?.*$//' | sort -u
```

需要进一步分析时，可补充：

```bash
jq 'length' "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json"
jq -r '.[].method' "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json" | sort | uniq -c
jq '.[] | select(.method == "POST") | {url: .url, body: .requestBody}' "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json"
```

### 9. 生成 CLI 工具

基于 `"$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json"` 与 `"$CAPTURE_ROOT/auth-state.json"` 生成新的 CLI 工具。

```bash
uv run python .flocks/plugins/skills/web2cli/scripts/generate-cli.py \
  "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json" \
  --format python \
  --base-url "https://example.com" \
  --output "$CAPTURE_ROOT/${CAPTURE_NAME}_cli.py"
```

如需同时产出文档或 Postman 集合，可继续执行：

```bash
uv run python .flocks/plugins/skills/web2cli/scripts/generate-cli.py \
  "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json" \
  --format markdown \
  --title "${CAPTURE_NAME} API Documentation" \
  --output "$CAPTURE_ROOT/${CAPTURE_NAME}_api.md"

uv run python .flocks/plugins/skills/web2cli/scripts/generate-cli.py \
  "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json" \
  --format postman \
  --base-url "https://example.com" \
  --output "$CAPTURE_ROOT/${CAPTURE_NAME}_postman.json"
```

### 10. summary

总结当前 生成 的CLI 工具有哪些能力，然后可提示用户：
- 调用CLI 工具，验证可用性
- 精简 CLI

## 故障处理

### Hook 注入报错

优先继续使用 `scripts/inject-hook-simple.js`。这是默认脚本，兼容性高于旧版增强脚本。

### 没有捕获到请求

依次检查：

1. 是否先注入 Hook，再执行页面动作。
2. `window.__capturedRequests` 是否存在。
3. 目标请求是否被脚本中的过滤规则排除。
4. 必要时切换 `window.__apiCapture.config.captureMode = 'all'` 后重试。

### 认证失效

- `agent-browser`：重新登录后再次执行保存状态命令。
- `cdp-direct`：重新登录后再次执行步骤 6 保存认证状态。
