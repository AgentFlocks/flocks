---
name: api-capture
description: 使用 `agent-browser` 与注入脚本捕获网站的 XHR/Fetch 请求，并按固定 9 步流程保存认证状态、导出 `captures/*.json`、分析接口、再生成 CLI 工具。适用于用户要求抓取隐藏 API、逆向前端请求、复现登录后操作、沉淀接口调用样例，或基于页面操作生成自动化工具时。
---

# API Capture

按固定流程执行，不要跳步，不要引入额外的一键脚本。

## 使用的资源

- 使用 `scripts/inject-hook-simple.js` 作为默认注入脚本。
- 优先使用 `agent-browser --session-name` 命令完成整个流程。

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
- 浏览器认证状态：`$CAPTURE_ROOT/auth-state.json`
- 生成的 CLI 工具：`$CAPTURE_ROOT/${CAPTURE_NAME}_cli.py`
- 生成的接口文档：`$CAPTURE_ROOT/${CAPTURE_NAME}_api.md`
- 生成的 Postman 集合：`$CAPTURE_ROOT/${CAPTURE_NAME}_postman.json`

## 标准流程

### 1. 打开浏览器

```bash
agent-browser --headed --session-name "$CAPTURE_NAME" open "<URL>"
```


### 2. 等待用户手动登录

要求用户在可见浏览器中完成登录、验证码、二次确认等人工步骤。

### 3. 注入 Hook

```bash
agent-browser --session-name "$CAPTURE_NAME" wait --load networkidle
agent-browser --session-name "$CAPTURE_NAME" eval --stdin < scripts/inject-hook-simple.js # 或者 inject-hook.js
```

注入后默认从 `window.__capturedRequests` 读取结果。

默认过滤策略为智能捕获：

- 仅捕获同源请求。
- 排除静态资源、埋点监控、常见 websocket 连接。
- 默认保留非 `GET` 请求。
- `GET` 请求只要路径不像静态文件，也会保留。

如果站点请求特别特殊，仍可在注入后切换为全抓模式：

```bash
agent-browser --session-name "$CAPTURE_NAME" eval "window.__apiCapture.config.captureMode = 'all'"
```

### 4. 等待用户执行业务操作

要求用户完成要捕获的页面动作，例如查询、翻页、筛选、提交表单、点击按钮、导出数据。

需要确认捕获是否开始时，执行：

```bash
agent-browser --session-name "$CAPTURE_NAME" eval "window.__capturedRequests.length"
```

### 5. 提取捕获数据

```bash
agent-browser --session-name "$CAPTURE_NAME" eval "JSON.stringify(window.__capturedRequests, null, 2)" > "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json"
```

需要先确认数量时，先执行：

```bash
agent-browser --session-name "$CAPTURE_NAME" eval "window.__capturedRequests.length"
```

### 6. 保存认证状态

```bash
agent-browser --session-name "$CAPTURE_NAME" state save "$CAPTURE_ROOT/auth-state.json"
```

将 cookie 和 localStorage 保存为后续 CLI 调用的认证输入。

### 7. 关闭浏览器

```bash
agent-browser --session-name "$CAPTURE_NAME" close
```

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
uv run python .flocks/plugins/skills/api-capture/scripts/generate-cli.py \
  "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json" \
  --format python \
  --base-url "https://example.com" \
  --output "$CAPTURE_ROOT/${CAPTURE_NAME}_cli.py"
```

如需同时产出文档或 Postman 集合，可继续执行：

```bash
uv run python .flocks/plugins/skills/api-capture/scripts/generate-cli.py \
  "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json" \
  --format markdown \
  --title "${CAPTURE_NAME} API Documentation" \
  --output "$CAPTURE_ROOT/${CAPTURE_NAME}_api.md"

uv run python .flocks/plugins/skills/api-capture/scripts/generate-cli.py \
  "$CAPTURE_ROOT/captures/${CAPTURE_NAME}_api.json" \
  --format postman \
  --base-url "https://example.com" \
  --output "$CAPTURE_ROOT/${CAPTURE_NAME}_postman.json"
```

参考 onesec-cli-tool

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

重新登录后再次执行：

```bash
agent-browser --session-name "$CAPTURE_NAME" state save "$CAPTURE_ROOT/auth-state.json"
```