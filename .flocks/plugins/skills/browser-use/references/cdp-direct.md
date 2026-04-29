# CDP 直连

通过 CDP 直连用户日常 Chrome，复用现有登录态和真实浏览器上下文。适合登录后页面、内部系统、动态页面、复杂 DOM 操作、媒体资源提取等任务。

## 何时使用

优先用于以下场景：

- 目标内容依赖用户当前 Chrome 的登录态
- 需要访问内部系统或用户已在浏览器中可见的页面
- 需要直接执行 JS 读取或操控 DOM
- 需要提取媒体 URL、视频帧或页面内隐藏内容
- 用户明确要求走 CDP

## 操作原则

- 默认不要直接操作用户已有 tab。
- 优先用 `/new` 创建自己的后台 tab，在其中完成任务。
- 只关闭自己创建的 tab，不关闭用户原有 tab。
- 不建议主动停止 proxy；重启后可能需要用户重新授权 Chrome 调试连接。

## 页面探索方法

CDP 模式下，`/eval` 是主要观察和操作入口：

- 先用 `/eval` 了解页面结构、文本、链接、按钮、媒体资源
- 再根据结果决定是否使用 `/click`、`/clickAt`、`/scroll`
- 提取大量结构化数据时，优先在页面里组装为 JSON 后返回
- 判断内容是否已在 DOM 中，不要只盯着“当前可见区域”

推荐流程：

1. `/new` 打开目标页面
2. `/info` 查看标题、URL、加载状态
3. `/eval` 读取 DOM 中的目标信息
4. 必要时 `/click`、`/clickAt`、`/scroll` 或 `/navigate`
5. 如需视觉结果，用 `/screenshot`
6. 结束后 `/close`

## 页面内导航策略

页面内跳转有两种常见方式：

- `/click`：在当前 tab 内点击元素，适合展开、翻页、进入详情页
- `/new` + 完整 URL：在新 tab 打开已提取出的完整链接，适合并行访问多个详情页

提取链接时保留完整 URL 和全部查询参数，不要自行裁剪。

## 登录判断

用户日常 Chrome 通常已带登录态。打开页面后先尝试获取目标内容，只有在确认内容拿不到且登录能够解决问题时，才让用户接管登录。

可直接提示：

```text
当前页面在未登录状态下无法获取[具体内容]，请在你的 Chrome 中登录 [网站名]，完成后告诉我继续。
```

用户登录完成后，直接刷新或继续当前 tab，无需重启 proxy。

## 媒体与视频

- 图片内容：优先用 `/eval` 从 DOM 中直接提取图片 URL
- 视频内容：可用 `/eval` 控制 `<video>`，例如读取时长、seek、暂停，再配合 `/screenshot` 采样画面
- 懒加载页面：滚动到底前，部分媒体资源可能还未出现在可提取状态

## API 端点

基础地址：

```text
http://localhost:3456
```

### 健康检查

```bash
curl -s http://localhost:3456/health
```

### 列出当前页面

```bash
curl -s http://localhost:3456/targets
```

返回所有已打开的 page target，可用于识别 `targetId`。

### 创建新后台 tab

```bash
curl -s "http://localhost:3456/new?url=https://example.com"
```

返回：

```json
{"targetId":"TARGET_ID"}
```

### 关闭 tab

```bash
curl -s "http://localhost:3456/close?target=TARGET_ID"
```

### 导航到新页面

```bash
curl -s "http://localhost:3456/navigate?target=TARGET_ID&url=https://example.com"
```

### 后退

```bash
curl -s "http://localhost:3456/back?target=TARGET_ID"
```

### 获取页面信息

```bash
curl -s "http://localhost:3456/info?target=TARGET_ID"
```

返回页面标题、URL、`readyState`。

### 执行 JavaScript

```bash
curl -s -X POST "http://localhost:3456/eval?target=TARGET_ID" -d 'document.title'
```

要点：

- POST body 为任意 JS 表达式
- 支持 `awaitPromise`
- 返回值必须可序列化
- 大量数据可先 `JSON.stringify()` 再返回

示例：提取页面里所有链接

```bash
curl -s -X POST "http://localhost:3456/eval?target=TARGET_ID" -d '
JSON.stringify(
  Array.from(document.querySelectorAll("a"))
    .map(a => ({ text: (a.textContent || "").trim(), href: a.href }))
    .filter(x => x.href)
)'
```

### 持久注入脚本

```bash
curl -s -X POST "http://localhost:3456/inject?target=TARGET_ID&identifier=default" -d 'window.__browserUse = true'
```

注册后脚本会在该 tab 后续新文档加载时自动注入。

### JS 层点击

```bash
curl -s -X POST "http://localhost:3456/click?target=TARGET_ID" -d 'button.submit'
```

适合简单点击，内部会 `scrollIntoView()` 后调用 `el.click()`。

### 真实鼠标点击

```bash
curl -s -X POST "http://localhost:3456/clickAt?target=TARGET_ID" -d 'button.upload'
```

适合需要真实用户手势的场景，例如触发文件选择器。

### 文件上传

```bash
curl -s -X POST "http://localhost:3456/setFiles?target=TARGET_ID" -d '{"selector":"input[type=file]","files":["/path/to/file.png"]}'
```

### 页面滚动

```bash
curl -s "http://localhost:3456/scroll?target=TARGET_ID&y=3000"
curl -s "http://localhost:3456/scroll?target=TARGET_ID&direction=bottom"
```

滚动后会短暂等待，以触发懒加载。

### 截图

```bash
curl -s "http://localhost:3456/screenshot?target=TARGET_ID&file=/tmp/shot.png"
```

可选 `format=jpeg`。

## 常见错误

| 错误 | 原因 | 处理 |
|------|------|------|
| `Chrome 未开启远程调试端口` | Chrome 未允许 remote debugging | 让用户打开 `chrome://inspect/#remote-debugging` 并勾选 Allow |
| `attach 失败` | `targetId` 已失效或 tab 已关闭 | 重新调用 `/targets` 获取最新 target |
| `CDP 命令超时` | 页面长时间未响应 | 重试，或先用 `/info` / `/targets` 确认状态 |
| `端口已被占用` | 已有 proxy 或其他程序占用监听端口 | 若是已有 proxy 可复用，否则更换 `CDP_PROXY_PORT` |

## 结束清理

任务完成后，关闭自己创建的 tab：

```bash
curl -s "http://localhost:3456/close?target=TARGET_ID"
```

保留用户已有 tab，不要误关闭。Proxy 可保持运行，供后续任务复用。
