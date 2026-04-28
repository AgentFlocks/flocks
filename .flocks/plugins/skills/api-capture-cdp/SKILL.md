---
name: api-capture-cdp
description: 使用 web-access (CDP) 与注入脚本捕获网站的 XHR/Fetch 请求，并按固定 9 步流程保存认证状态、导出 captures/*.json、分析接口、再生成 CLI 工具。适用于用户要求抓取隐藏 API、逆向前端请求、复现登录后操作、沉淀接口调用样例，或基于页面操作生成自动化工具时。是 api-capture skill 的 CDP 替代方案，不再依赖 agent-browser。
---

# API Capture (CDP)

按固定流程执行，不要跳步，不要引入额外的一键脚本。

## 使用的资源

- 使用 `scripts/inject-hook-simple.js` 作为默认注入脚本（从 api-capture skill 复用）。
- 通过 `web-access` skill 提供的 CDP Proxy API 完成整个流程。

## 前置条件

1. 加载 `web-access` skill 并运行 `node ~/.hermes/skills/web-access/scripts/check-deps.mjs` 确保 CDP Proxy 就绪。
2. 在回复中向用户展示风险告知：
   ```
   温馨提示：部分站点对浏览器自动化操作检测严格，存在账号封禁风险。已内置的防护措施但无法完全避免，Agent 继续操作即视为接受。
   ```

## 输出目录约定

捕获产生的文件统一落到 `~/.flocks/workspace/outputs/<today>/api-capture/<name>/`。

开始前先准备目录：

```bash
CAPTURE_NAME="<name>"
TODAY="$(date +%F)"
CAPTURE_ROOT="$HOME/.flocks/workspace/outputs/$TODAY/api-capture/$CAPTURE_NAME"
mkdir -p "$CAPTURE_ROOT/captures"
```

各类输出位置固定如下：

- 浏览器内存中的原始捕获数据：`window.__capturedRequests`
- 导出的接口抓包 JSON：`$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json`
- 浏览器认证状态（cookies + localStorage）：`$CAPTURE_ROOT/auth-state.json`
- 生成的 CLI 工具：`$CAPTURE_ROOT/${CAPTURE_NAME}_cli.py`
- 生成的接口文档：`$CAPTURE_ROOT/${CAPTURE_NAME}_api.md`
- 生成的 Postman 集合：`$CAPTURE_ROOT/${CAPTURE_NAME}_postman.json`

## 标准流程

### 1. 创建浏览器 Tab

通过 CDP Proxy 创建新后台 tab 并导航到目标 URL：

```bash
TARGET_ID=$(curl -s "http://localhost:3456/new?url=<URL>" | jq -r '.targetId')
```

### 2. 等待用户手动登录

要求用户在可见 Chrome 浏览器中完成登录、验证码、二次确认等人工步骤。登录完成后告知 agent 继续。

### 3. 注入 Hook（持久注入）

通过 `/inject` 端点使用 CDP 的 `Page.addScriptToEvaluateOnNewDocument` 持久注入脚本——**页面导航不会丢失**。

```bash
# 注入脚本（持久化，每次新文档加载时自动执行）
curl -s -X POST "http://localhost:3456/inject?target=$TARGET_ID" \
  --data-binary @"$HOME/.hermes/skills/api-capture-cdp/scripts/inject-hook-simple.js"
```

注入后验证是否生效：
```bash
curl -s -X POST "http://localhost:3456/eval?target=$TARGET_ID" \
  -d 'typeof window.__apiCapture !== "undefined" ? "installed v" + window.__apiCapture.version : "NOT installed"'
```

如果返回 `NOT installed`（页面已加载完成但持久脚本还没触发），先用 `eval` 注入一次当前页面：
```bash
curl -s -X POST "http://localhost:3456/eval?target=$TARGET_ID" \
  --data-binary @"$HOME/.hermes/skills/api-capture-cdp/scripts/inject-hook-simple.js"
```

**双重保险**：`/inject` 确保后续导航不丢失，`/eval` 确保当前页面立即生效。

默认过滤策略为智能捕获：
- 仅捕获同源请求
- 排除静态资源、埋点监控、常见 websocket 连接
- 默认保留非 GET 请求
- GET 请求只要路径不像静态文件，也会保留

如果站点请求特别少或被过滤过多，可切换为全抓模式：
```bash
curl -s -X POST "http://localhost:3456/eval?target=$TARGET_ID" \
  -d 'window.__apiCapture.config.captureMode = "all"'
```

### 4. 等待用户执行业务操作

要求用户完成要捕获的页面动作，例如查询、翻页、筛选、提交表单、点击按钮、导出数据。

需要确认捕获是否开始时，执行：
```bash
curl -s -X POST "http://localhost:3456/eval?target=$TARGET_ID" \
  -d 'window.__capturedRequests.length'
```

### 5. 提取捕获数据

先确认数量：
```bash
curl -s -X POST "http://localhost:3456/eval?target=$TARGET_ID" \
  -d 'window.__capturedRequests.length'
```

然后导出：
```bash
# 先获取数据
CAPTURED_DATA=$(curl -s -X POST "http://localhost:3456/eval?target=$TARGET_ID" \
  -d 'JSON.stringify(window.__capturedRequests)')

# 写入文件
echo "$CAPTURED_DATA" | python3 -m json.tool > "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json"
```

如果数据量过大导致 eval 响应截断，改用分段导出：
```bash
curl -s -X POST "http://localhost:3456/eval?target=$TARGET_ID" \
  -d 'JSON.stringify(window.__capturedRequests.slice(0, 50))' > /tmp/cap_part1.json
curl -s -X POST "http://localhost:3456/eval?target=$TARGET_ID" \
  -d 'JSON.stringify(window.__capturedRequests.slice(50, 100))' > /tmp/cap_part2.json
# 合并
python3 -c "
import json
parts = []
for f in ['/tmp/cap_part1.json', '/tmp/cap_part2.json']:
    with open(f) as fh: parts.extend(json.load(fh))
print(json.dumps(parts, indent=2))
" > "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json"
```

### 6. 保存认证状态

通过 CDP 提取 cookies 和 localStorage：

```bash
# 通过 eval 读取 cookies（document.cookie，不含 HttpOnly）和 localStorage
python3 -c "
import json, subprocess

target = '$TARGET_ID'
out = '$CAPTURE_ROOT/auth-state.json'

# 读取 cookies（document.cookie 可访问的部分）
r = subprocess.run(['curl', '-s', '-X', 'POST',
    f'http://localhost:3456/eval?target={target}',
    '-d', 'JSON.stringify(document.cookie.split(\";\").map(c=>{const p=c.split(\"=\");return{name:p[0].trim(),value:p.slice(1).join(\"=\")}}))'],
    capture_output=True, text=True)
cookies = json.loads(json.loads(r.stdout).get('value', '[]'))

# 读取 localStorage
r2 = subprocess.run(['curl', '-s', '-X', 'POST',
    f'http://localhost:3456/eval?target={target}',
    '-d', 'JSON.stringify(Object.entries(localStorage).map(([k,v])=>({name:k,value:v})))'],
    capture_output=True, text=True)
local_storage = json.loads(json.loads(r2.stdout).get('value', '[]'))

state = {'cookies': cookies, 'localStorage': local_storage}
with open(out, 'w') as f:
    json.dump(state, f, indent=2)
print(f'Saved {len(cookies)} cookies, {len(local_storage)} localStorage items to {out}')
"
```

将 cookie 和 localStorage 保存为后续 CLI 调用的认证输入。

### 7. 关闭浏览器 Tab

```bash
curl -s "http://localhost:3456/close?target=$TARGET_ID"
```

必须保留用户原有的 tab 不受影响。

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
python3 ~/.hermes/skills/api-capture-cdp/scripts/generate-cli.py \
  "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json" \
  --format python \
  --base-url "https://example.com" \
  --output "$CAPTURE_ROOT/${CAPTURE_NAME}_cli.py"
```

如需同时产出文档或 Postman 集合，可继续执行：

```bash
python3 ~/.hermes/skills/api-capture-cdp/scripts/generate-cli.py \
  "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json" \
  --format markdown \
  --title "${CAPTURE_NAME} API Documentation" \
  --output "$CAPTURE_ROOT/${CAPTURE_NAME}_api.md"

python3 ~/.hermes/skills/api-capture-cdp/scripts/generate-cli.py \
  "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json" \
  --format postman \
  --base-url "https://example.com" \
  --output "$CAPTURE_ROOT/${CAPTURE_NAME}_postman.json"
```

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

重新登录后再次执行步骤 6 保存认证状态。

### CDP Proxy 未启动

运行 `node ~/.hermes/skills/web-access/scripts/check-deps.mjs` 确保：
- Node.js 22+ 可用
- Chrome 已开启远程调试（`chrome://inspect` 中勾选 "Allow remote debugging"）
