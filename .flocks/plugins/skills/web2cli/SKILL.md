---
name: web2cli
description: 使用统一的 Web2CLI 流程捕获网站的 XHR/Fetch 请求，并生成可复用的 CLI、Markdown 文档。通过浏览器的 `cdp-direct` 模式复用用户 Chromium 系浏览器登录态与 CDP 能力。适用于复现登录后操作、沉淀接口调用样例，或基于页面操作生成自动化工具时。
required: browser-use
---

# Web2CLI

> 正式开始前，先明确需要操作的网站或tab

## 模式

### `cdp-direct`

适用于需要复用用户 Chromium 系浏览器登录态、通过 `browser-use` 的 `flocks browser` 内核直连 CDP 的场景。

使用此模式前必须检查可用性：
```bash
flocks browser --doctor
```

如果 doctor 提示浏览器已运行但 remote debugging 未连接，则提示用户
```text
browser: not connected — 请确保 Chrome / Chromium / Edge 已打开，然后访问对应浏览器的 inspect 页面（例如 chrome://inspect/#remote-debugging 或 edge://inspect/#remote-debugging）并勾选 Allow remote debugging
```

用户完成后，不要立刻再次运行 `flocks browser --doctor`；先执行一次 `flocks browser --setup`，或直接执行 `flocks browser -c 'print(page_info())'` 触发 attach，再用 `--doctor` 做只读确认。

## 输出目录约定

捕获产生的文件统一落到 `~/.flocks/workspace/outputs/web2cli/<name>/`。

开始前先准备目录：

```bash
MODE="${MODE:-cdp-direct}"
CAPTURE_NAME="<name>"
CAPTURE_ROOT="$HOME/.flocks/workspace/outputs/web2cli/$CAPTURE_NAME"
WEB2CLI_SKILL=".flocks/plugins/skills/web2cli"
mkdir -p "$CAPTURE_ROOT/captures"
```

补充说明：

- `flocks browser -c '...'` 会把代码直接交给 Python `exec()`，表达式不会像 REPL 一样自动回显；需要输出时必须显式 `print(...)`。
- 多行代码要直接写成真正的多行字符串或 heredoc，不要把 `\n` 当成字面量塞进单引号字符串里。
- 在 `Windows PowerShell` 中，优先把 `flocks browser -c` 写成单行并用分号分隔；多行单引号字符串的换行/转义处理不稳定，容易让代码没有完整传给 Python。

各类输出位置固定如下：

- 浏览器内存中的原始捕获数据：`window.__capturedRequests`
- 导出的接口抓包 JSON：`$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json`
- 浏览器认证状态：`$CAPTURE_ROOT/auth-state.json`
- 站点自适应 Hook（仅当 base 失败时创建）：`$CAPTURE_ROOT/hook.js`
- 生成的 CLI 工具：`$CAPTURE_ROOT/<normalized_capture_name>_cli.py`，文件名中的 `-` 等非 Python 模块名字符需替换为 `_`
- 生成的验证材料：`$CAPTURE_ROOT/${CAPTURE_NAME}_verify.json`
- 生成的接口文档：`$CAPTURE_ROOT/cli-reference.md`
- 生成的 Postman 集合：`$CAPTURE_ROOT/${CAPTURE_NAME}_postman.json`

## 标准流程

> 按照以下 1-11 的操作流程完成任务

### 1. 打开浏览器或创建 Tab

```bash
TARGET_ID=$(
  flocks browser -c '
tid = new_tab("<URL>", activate=True)
wait_for_load()
print(tid)
' | tail -n 1
)
echo "Created tab: $TARGET_ID"
```

### 2. 等待用户手动登录

要求用户在可见浏览器中完成登录、验证码、二次确认等人工步骤。在刚创建的浏览器 tab 中完成登录，必要时让用户手动处理验证码、TOTP 或授权弹窗。

登录完成后告知 agent 继续。

### 3. 注入 Hook

默认使用 `scripts/inject-hook-base.js`。这是通用基线脚本，负责捕获 XHR/Fetch、页面上下文、最近用户动作与导航信息，并提供更完整的调试输出。

```bash
WEB2CLI_HOOK="$(pwd)/$WEB2CLI_SKILL/scripts/inject-hook-base.js"

export TARGET_ID WEB2CLI_HOOK
flocks browser -c '
import os
from pathlib import Path

target_id = os.environ.get("TARGET_ID")
if target_id:
    switch_tab(target_id)

hook_path = os.environ.get("WEB2CLI_HOOK", "")
source = Path(hook_path).read_text(encoding="utf-8")
cdp("Page.addScriptToEvaluateOnNewDocument", source=source)
js(source)
print(js("typeof window.__apiCapture !== \"undefined\" ? \"installed v\" + window.__apiCapture.version : \"NOT installed\""))
' 
```

注入后默认从 `window.__capturedRequests` 读取结果。

默认过滤策略为智能捕获：

- 仅捕获同源请求
- 排除静态资源、埋点监控、常见 websocket 连接
- 默认保留非 `GET` 请求
- `GET` 请求只要路径不像静态文件，也会保留

如果站点请求特别特殊，仍可在注入后切换为全抓模式：

```bash
(
  TARGET_ID="$TARGET_ID" flocks browser -c '
import os

target_id = os.environ.get("TARGET_ID")
if target_id:
    attach_tab(target_id)

js("window.__apiCapture.config.captureMode = \"all\"")
print(js("window.__apiCapture.config.captureMode"))
'
)
```

### 4. 明确需要捕获的功能/操作

- 方式 1：要求用户手动操作要捕获的页面动作，例如查询、翻页、筛选、提交表单、点击按钮、导出数据。
- 方式 2：请求用户描述需要 hook 的操作或功能，你直接去页面代替用户执行
- 方式 3：用户之前已经描述了需要的 CLI功能，你直接去页面代替用户执行

需要确认捕获是否开始时：

```bash
(
  TARGET_ID="$TARGET_ID" flocks browser -c '
import os

target_id = os.environ.get("TARGET_ID")
if target_id:
    attach_tab(target_id)

print(js("window.__capturedRequests.length"))
'
)
```

### 5. 提取捕获数据

先确认数量：

```bash
(
  TARGET_ID="$TARGET_ID" flocks browser -c '
import os

target_id = os.environ.get("TARGET_ID")
if target_id:
    attach_tab(target_id)

print(js("window.__capturedRequests.length"))
'
)
```

然后导出：

```bash
CAPTURE_OUT="$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json"

(
  TARGET_ID="$TARGET_ID" CAPTURE_OUT="$CAPTURE_OUT" flocks browser -c '
import json
import os

target_id = os.environ.get("TARGET_ID")
if target_id:
    attach_tab(target_id)

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
  TARGET_ID="$TARGET_ID" CAPTURE_OUT="$CAPTURE_OUT" flocks browser -c '
import json
import os

target_id = os.environ.get("TARGET_ID")
if target_id:
    attach_tab(target_id)

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

```bash
(
  TARGET_ID="$TARGET_ID" flocks browser -c '
import os

target_id = os.environ.get("TARGET_ID")
if target_id:
    attach_tab(target_id)
'
  && flocks browser state save "$CAPTURE_ROOT/auth-state.json"
)
```

将 cookie 和 localStorage 保存为后续 CLI 调用的认证输入。

### 7. 分析捕获的 web API

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

### 8. CLI 工具生成

根据 `references/cli-requirements.md` 的要求，基于抓包结果、认证状态和用户目标，生成 CLI、验证材料和接口文档。

生成前必须读取并遵循：

- `$WEB2CLI_SKILL/references/cli-requirements.md`

最终产物必须包含：

- `$CAPTURE_ROOT/<normalized_capture_name>_cli.py`
- `$CAPTURE_ROOT/${CAPTURE_NAME}_verify.json`
- `$CAPTURE_ROOT/cli-reference.md`

如果 `CAPTURE_NAME` 包含 `-` 等不能作为 Python 模块名的字符，生成 CLI 文件名时必须规范化为 `_`，例如 `test-domain_cli.py` 应写为 `test_domain_cli.py`。

### 9. CLI工具验证与修改

根据生成的 CLI ，任意选择一个接口调用测试可用性
- CLI 工具可用性
- 认证状态可用性
- `verify.json` 的输出约束是否满足
- method、endpoint、query/body/payload 的一致性，必要时根据${CAPTURE_NAME}_api.json调整

推荐先查看 `"$CAPTURE_ROOT/${CAPTURE_NAME}_verify.json"`，再用生成的 CLI 以默认参数执行一次，确认固定输出列与认证状态都正确。

### 10. 将 WebCLI 能力沉淀为最终产物

分为基础层和可选增强层：

#### 基础要求：必须集成到 skill

- 所有 `web2cli` 结果都必须先按 `references/cli-in-skill.md` 集成为可长期维护的 skill / CLI 资产
- 这是必选步骤，因为认证失效、浏览器恢复、重新抓包、CLI 参数说明和日常排障都依赖 skill 文档入口
- `references/browser-workflow.md` 必须记录浏览器连接检查、登录步骤、state 保存位置和认证恢复流程

#### CLI 放置原则

- 主 CLI 的落点是二选一：要么放在 skill 的 `scripts/`，要么放在 `tools/device/<plugin_id>/` 下
- 通用网站、查询等场景：优先放在 skill 的 `scripts/`
- 安全设备接入：按 `references/cli-in-device.md` 将能力整理成 `tools/device/<plugin_id>/` 下的 `_provider.yaml`、工具 YAML 和 handler
- 不要同时维护两份独立演进的主 CLI，避免能力漂移和认证逻辑分叉
- 无论主 CLI 放在哪，skill 集成都始终是必选

不要只停留在一次性 CLI 或临时抓包结果；最终都要沉淀成可长期维护的资产。

### 11. summary并关闭浏览器 tab

1. 总结当前生成的 CLI 工具有哪些接口/能力
2. 确保 CLI 可用后关闭浏览器或 Tab

#### 关闭浏览器或 Tab

```bash
(
  TARGET_ID="$TARGET_ID" flocks browser -c '
import os

target_id = os.environ.get("TARGET_ID")
if target_id:
    close_tab(target_id, activate_next=False)
else:
    close_tab(activate_next=False)
'
)
```

必须保留用户原有的 tab 不受影响。

## 故障处理

### Hook 注入报错

默认脚本 `scripts/inject-hook-base.js` 失败时，必须根据目标站点的实际情况自适应创建新的 `hook.js` 文件，并保存到 `$CAPTURE_ROOT/hook.js` 后再注入。创建时遵循以下原则：

1. 先保留 base Hook 的核心能力：XHR/Fetch 捕获、页面上下文、动作追踪、调试接口。
2. 再针对站点特征补充适配逻辑，例如：
   - 请求被框架二次封装，需要额外 hook Axios、`$.ajax`、自定义 SDK。
   - 页面在 iframe、shadow DOM、微前端容器内运行，需要调整注入位置或元素定位方式。
   - 站点有特殊过滤规则、CSP、长连接、二进制请求或加密包装，需要定制白名单/忽略规则与序列化逻辑。
3. 新建的 `$CAPTURE_ROOT/hook.js` 必须只为当前站点服务，不要反向覆盖仓库中的 base Hook。

创建完成后，改为注入 `$CAPTURE_ROOT/hook.js`，直至完成当前 hook 任务。

### 没有捕获到请求

依次检查：

1. 是否先注入 Hook，再执行页面动作。
2. `window.__capturedRequests` 是否存在。
3. 目标请求是否被脚本中的过滤规则排除。
4. 必要时切换 `window.__apiCapture.config.captureMode = 'all'` 后重试。
5. 修改sameOriginOnly 参数
6. 以上方法都不可行时，按照Hook 注入报错的原则，自定义hook.js

### CLI认证失效

- 登录状态有效：利用已有知识和查找公开资料尝试解决。
- 登录状态失效：重新登录后再次执行保存状态命令。

## Reference
- references/cli-in-device.md 在 skill 集成完成后，将 WebCLI 能力进一步封装为 device 插件
- references/cli-in-skill.md 将生成的 CLI 集成到 skill 中使用
