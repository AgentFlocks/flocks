# CDP 无头模式（flocks browser / cdp-headless）

本文件是 `browser-use` 的 `cdp-headless` 模式参考文档。只有当 `browser-use/SKILL.md` 判定当前任务应使用无头 CDP 时，才读取并遵循本文件。

本文件只负责：

- 启动专用 headless Chromium 实例
- 设置 `BU_CDP_URL` / `BU_CDP_WS`
- 让 `flocks browser` 连接到正确的无头实例
- 说明无头场景下最常见的排障方式

连接成功后，立即继续阅读并遵循：

- `references/cdp-direct.md`

后续 tab 管理、页面操作、helper API、数据提取与通用排障，统一按 `cdp-direct` 工作流执行。

## 何时使用

只有在下面这些场景才使用 headless CDP：

- 用户明确要求本次任务用 headless 模式执行
- 后台任务、定时任务、CI/cron、无人值守采集
- 系统不支持可视化，如 CentoOS 服务器等 Linux 系统和 Windows server

不要把它作为默认模式。常规人工协作任务，优先仍然使用用户的可见浏览器。

## 全平台启动原则

安装脚本已经负责浏览器安装或识别时，优先使用 `AGENT_BROWSER_EXECUTABLE_PATH`，不要在 skill 里硬编码某个浏览器安装路径。

通用启动参数：

- `--headless=new`
- `--remote-debugging-port=9222`
- `--remote-debugging-address=0.0.0.0`
- `--disable-gpu`
- `--user-data-dir=<dedicated-dir>`
- `--remote-allow-origins=*`

说明：

- `--remote-allow-origins=*` 基本是必须项；缺少它时，headless Chrome 往往会拒绝 WebSocket 握手并返回 `HTTP 403`
- `--user-data-dir` 请使用单独目录，不要复用用户日常 Chrome profile
- `--no-sandbox` 只在 Linux 容器、root 或受限沙箱环境中按需添加；不要在 macOS / Windows 默认加
- 如果安装脚本没有设置 `AGENT_BROWSER_EXECUTABLE_PATH`，再退回到系统已知浏览器命令或绝对路径

## Windows 启动示例

优先 PowerShell：

```powershell
& $env:AGENT_BROWSER_EXECUTABLE_PATH `
  --headless=new `
  --remote-debugging-port=9222 `
  --remote-debugging-address=0.0.0.0 `
  --disable-gpu `
  --user-data-dir="$env:TEMP\chrome-profile" `
  --remote-allow-origins=*
```

## macOS 启动示例

```bash
"$AGENT_BROWSER_EXECUTABLE_PATH" \
  --headless=new \
  --remote-debugging-port=9222 \
  --remote-debugging-address=0.0.0.0 \
  --disable-gpu \
  --user-data-dir=/tmp/chrome-profile \
  '--remote-allow-origins=*'
```

在 zsh 中建议把 `'--remote-allow-origins=*'` 用单引号整体包起来，避免 `*` 被 shell 展开。

## Linux 启动示例

```bash
"$AGENT_BROWSER_EXECUTABLE_PATH" \
  --headless=new \
  --remote-debugging-port=9222 \
  --remote-debugging-address=0.0.0.0 \
  --disable-gpu \
  --user-data-dir=/tmp/chrome-profile \
  --remote-allow-origins=* \
  --no-sandbox
```

只有在 Linux 桌面环境正常、非容器、非 root 且浏览器可正常启动时，才考虑去掉 `--no-sandbox`。

## 连接到 flocks browser

优先显式设置 `BU_CDP_URL` 或 `BU_CDP_WS`，不要依赖 `DevToolsActivePort` 自动发现。

更推荐 `BU_CDP_URL`，因为不需要先手动提取 `<uuid>`：

Windows PowerShell：

```powershell
$env:BU_CDP_URL = "http://127.0.0.1:9222"
flocks browser --setup
```

macOS / Linux：

```bash
export BU_CDP_URL="http://127.0.0.1:9222"
rm -f /tmp/bu-default.sock
flocks browser --setup
```

如果必须显式指定 websocket，也可以直接设置 `BU_CDP_WS`：

```bash
export BU_CDP_WS="ws://127.0.0.1:9222/devtools/browser/<uuid>"
flocks browser --setup
```

## 验证连接

Windows PowerShell：

```powershell
$env:BU_CDP_URL = "http://127.0.0.1:9222"
flocks browser -c "print(page_info())"
```

macOS / Linux：

```bash
export BU_CDP_URL="http://127.0.0.1:9222"
flocks browser -c 'print(page_info())'
```

如果验证成功，再继续读取 `references/cdp-direct.md` 并进入正常页面操作流程。

## 常见排障

- 无头专用 Chrome 与用户日常 Chrome 同时存在时，不要依赖 `DevToolsActivePort` 自动发现；它可能让 daemon 连到错误的浏览器实例
- 这种场景必须显式设置 `BU_CDP_WS` 或 `BU_CDP_URL`，让 `flocks browser` 直连你启动的 headless 实例
- 如果只是旧 daemon 残留，优先尝试 `flocks browser -c 'restart_daemon()'`
- 当 daemon 已经死掉但 POSIX socket 文件还留在 `/tmp/` 时，再手动删除 `/tmp/bu-default.sock`；Windows 不需要这一步
- 如果失败并出现 `HTTP 403`，优先回头检查 headless Chrome 是否带了 `--remote-allow-origins=*`
