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

> 按照以下 1-12 的操作流程完成任务

Copy this checklist and check off items as you complete them:

```text
Task Progress:
- [ ] Step 1: Open or create the target browser tab
- [ ] Step 2: Wait for required manual login or authorization
- [ ] Step 3: Inject the Web2CLI capture hook and verify it is installed
- [ ] Step 4: Perform or ask the user to perform the target page operation
- [ ] Step 5: Export captured API data from window.__capturedRequests
- [ ] Step 6: Save browser auth state to auth-state.json
- [ ] Step 7: Analyze captured web APIs and remove unrelated traffic
- [ ] Step 8: Decide whether the final asset belongs in a skill or device plugin
- [ ] Step 9: Generate the target implementation, verify.json, and cli-reference.md
- [ ] Step 10: Validate the generated CLI or device tool with live auth data
- [ ] Step 11: Integrate the WebCLI capability into long-term skill/device assets
- [ ] Step 12: Summarize generated capability and close only the Web2CLI tab
```

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

### 8. 判断最终产物落点

生成任何 CLI、device tool 或最终文件前，必须先判断本次 WebCLI 能力最终应该沉淀到哪里。不要先生成一个孤立 CLI，再在后续步骤才决定是否改成 device tool。

根据用户目标和场景二选一：

- **通用网站、查询脚本、内部系统操作、非设备页接入**：最终主 CLI 放在 skill 的 `scripts/`，按 `references/skill-integration.md` 集成为长期维护的 skill / CLI 资产。
- **安全设备接入、来自设备接入页、需要出现在设备页配置和调用**：最终主实现放在 `tools/device/<plugin_id>/` 下，按 `references/device-tool-requirements.md` 生成 device plugin，并按 `references/skill-integration.md` 补齐 skill 文档入口。CLI 只可作为可选调试/回归入口，不作为设备运行时主路径。

如果用户目标不清楚，先用 `question` 明确最终落点，再继续生成。

### 9. 按目标落点生成可验证实现

第 8 步确定的最终落点决定主实现形态：**通用 CLI** 和 **device plugin** 二选一。无论选择哪一种，都必须同时完成 skill 集成；区别在于 CLI 场景的 skill 包含 `scripts/` 主脚本，device 场景的 skill 只沉淀文档入口、浏览器经验、认证恢复和 device tool 使用说明，不在 skill 中放置独立 CLI 主实现。

两种场景共用 `$CAPTURE_ROOT/cli-reference.md`，它既可以记录 CLI 用法，也可以记录 device tool 的参数、能力、验证方式和回归方法。

#### 9.1 通用 CLI / Skill 场景

选择通用 CLI 作为主实现时，生成前必须读取并遵循：

- `$WEB2CLI_SKILL/references/cli-requirements.md`
- `$WEB2CLI_SKILL/references/skill-integration.md`

基于抓包结果、认证状态和用户目标，生成 CLI、验证材料和接口文档。阶段性产物至少包含：

- `$CAPTURE_ROOT/<normalized_capture_name>_cli.py`
- `$CAPTURE_ROOT/${CAPTURE_NAME}_verify.json`
- `$CAPTURE_ROOT/cli-reference.md`

如果 `CAPTURE_NAME` 包含 `-` 等不能作为 Python 模块名的字符，生成 CLI 文件名时必须规范化为 `_`，例如 `test-domain_cli.py` 应写为 `test_domain_cli.py`。

随后按 `references/skill-integration.md` 将主 CLI 集成到 skill 的 `scripts/`，并补齐 skill 级文档：

- `$HOME/.flocks/plugins/skills/<name>-use/scripts/<name>_cli.py`
- `$HOME/.flocks/plugins/skills/<name>-use/references/browser-workflow.md`
- `$HOME/.flocks/plugins/skills/<name>-use/references/cli-reference.md`
- `$HOME/.flocks/plugins/skills/<name>-use/SKILL.md`

不要把最终 CLI 保留成一次性抓包文件名。

#### 9.2 安全设备接入场景

选择 device plugin 作为主实现时，生成前必须读取并遵循：

- `$WEB2CLI_SKILL/references/device-tool-requirements.md`
- `$WEB2CLI_SKILL/references/skill-integration.md`

基于抓包结果、认证状态和用户目标，生成 device 插件目录、验证材料和接口文档。主实现只落到 device plugin：

- `$HOME/.flocks/plugins/tools/device/<plugin_id>/_provider.yaml`
- `$HOME/.flocks/plugins/tools/device/<plugin_id>/<domain>.yaml`
- `$HOME/.flocks/plugins/tools/device/<plugin_id>/<name>.handler.py`
- `$CAPTURE_ROOT/${CAPTURE_NAME}_verify.json`
- `$CAPTURE_ROOT/cli-reference.md`

同时创建或更新对应产品 skill，但该 skill 不应包含 `scripts/` 主 CLI：

- `$HOME/.flocks/plugins/skills/<name>-use/references/browser-workflow.md`
- `$HOME/.flocks/plugins/skills/<name>-use/references/cli-reference.md`
- `$HOME/.flocks/plugins/skills/<name>-use/SKILL.md`

device 场景不要求先生成 `$CAPTURE_ROOT/<normalized_capture_name>_cli.py`，也不要在 skill 的 `scripts/` 下放置一份与 device tool 平行演进的 CLI 主实现。如确实需要 CLI 做调试或回归，只能作为 device plugin 目录下的可选辅助文件，并必须明确它不是设备运行时主路径。

### 10. 验证与修改

根据第 8 步确定的目标落点验证可用性：

- 通用 CLI / Skill 场景：用生成的 CLI 任意选择一个接口调用测试可用性
- 安全设备接入场景：用生成的 device tool 或可选 CLI 任意选择一个低风险接口调用测试可用性
- 认证状态可用性
- `verify.json` 的输出约束是否满足
- method、endpoint、query/body/payload 的一致性，必要时根据 `${CAPTURE_NAME}_api.json` 调整

推荐先查看 `"$CAPTURE_ROOT/${CAPTURE_NAME}_verify.json"`，再以默认参数执行一次最小验证，确认固定输出列与认证状态都正确。

### 11. 将 WebCLI 能力沉淀为最终产物

无论主实现放在哪里，都必须保留 skill 级文档入口，供长期维护、认证恢复、重新抓包和排障使用：

- `references/browser-workflow.md` 必须记录浏览器连接检查、登录步骤、state 保存位置和认证恢复流程
- `references/cli-reference.md` 必须记录 CLI 或 device tool 的能力、参数、验证方式和回归方法
- `SKILL.md` 必须说明当前能力最终落点：`scripts/` 或 `tools/device/<plugin_id>/`

注意：skill 文档入口必选，不等于必须把主 CLI 代码也放进 skill 的 `scripts/`。安全设备接入场景下，主实现应以 device tool 为准。

不要只停留在一次性 CLI 或临时抓包结果；最终都要沉淀成可长期维护的资产。

### 12. summary并关闭浏览器 tab

1. 总结当前生成的 CLI 或 device tool 有哪些接口/能力
2. 确保生成的主实现可用后关闭浏览器或 Tab

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
- references/cli-requirements.md 说明通用 CLI 主实现的生成要求
- references/device-tool-requirements.md 说明 device tool 主实现的生成要求
- references/skill-integration.md 说明 CLI 和 device tool 两种主实现如何接入长期维护的产品 skill
