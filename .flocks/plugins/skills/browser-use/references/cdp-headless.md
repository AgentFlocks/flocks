# CDP 无头模式（flocks browser / cdp-headless）

本文件是 `browser-use` 的 `cdp-headless` 模式参考文档。只有当 `browser-use/SKILL.md` 判定当前任务应使用无头 CDP 时，才读取并遵循本文件。

本文件只负责：

- 启动专用 headless Chromium 实例
- 设置 `BU_CDP_URL` / `BU_CDP_WS`
- 让 `flocks browser` 连接到正确的无头实例
- 约定专用 headless 浏览器的生命周期与关闭时机
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
- `--remote-debugging-port=<dedicated-port>`
- `--remote-debugging-address=0.0.0.0`
- `--disable-gpu`
- `--user-data-dir=<dedicated-dir>`
- `--remote-allow-origins=*`

说明：

- `--remote-allow-origins=*` 基本是必须项；缺少它时，headless Chrome 往往会拒绝 WebSocket 握手并返回 `HTTP 403`
- `--user-data-dir` 请使用单独目录，不要复用用户日常 Chrome profile
- `--remote-debugging-port` 请使用专用未占用端口，不要写死成 `9222`；具体规则见下节
- `--no-sandbox` 只在 Linux 容器、root 或受限沙箱环境中按需添加；不要在 macOS / Windows 默认加
- 如果安装脚本没有设置 `AGENT_BROWSER_EXECUTABLE_PATH`，再退回到系统已知浏览器命令或绝对路径

## 关键约束：为 headless 实例分配专用端口

不要假设 `9222` 永远可用。

推荐规则：

- 当前任务自己启动专用 headless 浏览器时，先选一个未被占用的本地端口
- `9222` 只能作为示例或最后的候选值，不能当成固定端口写死
- 启动命令、`BU_CDP_URL`、`BU_CDP_WS` 必须引用同一个端口变量
- 如果当前机器上已经有用户浏览器或其他自动化实例在监听 `9222`，换一个专用端口，例如 `19222`、`29222`、`39222`

典型冲突现象：

- 新浏览器进程直接退出，日志里出现端口占用或 bind/listen 失败
- `BU_CDP_URL=http://127.0.0.1:9222` 实际命中了旧浏览器实例，而不是你刚启动的 headless 实例
- `flocks browser --setup` 看起来连上了，但后续 tab/page 行为落在错误浏览器上

## 关键约束：浏览器进程必须独立存活

`flocks browser --setup` 只会把 `flocks browser` 的 daemon 放到后台并尝试 attach；
它不会替你托管 headless Chrome/Chromium 本身。

因此在 `cdp-headless` 模式下：

- 专用 headless 浏览器必须以独立后台子进程方式启动，不能只在当前 `flocks bash` 里前台运行
- 不要启动后立刻结束浏览器所在 shell，除非该浏览器已经通过 `nohup`、`Start-Process` 等方式脱离当前 shell 生命周期
- 启动时要同时记录 PID、日志文件、`--user-data-dir`，方便后续排障和清理
- `flocks browser --setup` 连上之后，也要让这个 headless 浏览器继续存活，直到当前任务真正结束

如果只是直接执行一个前台浏览器命令：

- 当前 shell 会一直被占住
- shell 结束后，浏览器进程可能收到挂断信号并退出
- daemon 之后会表现为 `/json/version` 404、`BU_CDP_URL unreachable` 或 websocket 断开

## Windows 后台启动示例

优先 PowerShell：

```powershell
$browser = $env:AGENT_BROWSER_EXECUTABLE_PATH
$port = 19222
$profile = Join-Path $env:TEMP "chrome-profile-headless"
$stdout = Join-Path $env:TEMP "bu-headless.out.log"
$stderr = Join-Path $env:TEMP "bu-headless.err.log"
$pidfile = Join-Path $env:TEMP "bu-headless.pid"

$proc = Start-Process -FilePath $browser -ArgumentList @(
  "--headless=new",
  "--remote-debugging-port=$port",
  "--remote-debugging-address=0.0.0.0",
  "--disable-gpu",
  "--user-data-dir=$profile",
  "--remote-allow-origins=*"
) -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru

$proc.Id | Set-Content $pidfile
```

如需先检查端口是否已被占用：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 19222 -ErrorAction SilentlyContinue
```

不要用 PowerShell 的 `&` 直接前台拉起 long-running headless 浏览器；那样当前命令会一直阻塞，session 结束时进程也可能一起消失。

## POSIX 后台启动示例（macOS / Linux）

```bash
BU_HEADLESS_PORT=19222
BU_HEADLESS_DIR="/tmp/chrome-profile-headless-$$"
BU_HEADLESS_LOG="/tmp/bu-headless-$$.log"
BU_HEADLESS_PID="/tmp/bu-headless-$$.pid"

nohup "$AGENT_BROWSER_EXECUTABLE_PATH" \
  --headless=new \
  --remote-debugging-port="$BU_HEADLESS_PORT" \
  --remote-debugging-address=0.0.0.0 \
  --disable-gpu \
  --user-data-dir="$BU_HEADLESS_DIR" \
  '--remote-allow-origins=*' \
  >"$BU_HEADLESS_LOG" 2>&1 </dev/null &

echo $! >"$BU_HEADLESS_PID"
```

在 zsh 中建议把 `'--remote-allow-origins=*'` 用单引号整体包起来，避免 `*` 被 shell 展开。

如需先检查端口是否已被占用：

```bash
lsof -nP -iTCP:"$BU_HEADLESS_PORT" -sTCP:LISTEN
```

Linux 容器、root 或受限沙箱环境：在上面的命令里额外加入 `--no-sandbox`。只有在 Linux 桌面环境正常、非容器、非 root 且浏览器可正常启动时，才考虑不加它。

如果当前 shell 是交互式 shell，额外执行一次 `disown` 也可以；但 `nohup ... &` 已经是这里的关键要求。

## 连接并验证

优先显式设置 `BU_CDP_URL` 或 `BU_CDP_WS`，不要依赖 `DevToolsActivePort` 自动发现。

更推荐 `BU_CDP_URL`，因为不需要先手动提取 `<uuid>`。连接和验证都应复用同一个 endpoint 变量：

Windows PowerShell：

```powershell
$env:BU_CDP_URL = "http://127.0.0.1:$port"
flocks browser --setup
flocks browser -c "print(page_info())"
```

macOS / Linux：

```bash
export BU_CDP_URL="http://127.0.0.1:$BU_HEADLESS_PORT"
rm -f /tmp/bu-default.sock
flocks browser --setup
flocks browser -c 'print(page_info())'
```

如果必须显式指定 websocket，也可以直接设置 `BU_CDP_WS`：

```bash
export BU_CDP_WS="ws://127.0.0.1:$BU_HEADLESS_PORT/devtools/browser/<uuid>"
flocks browser --setup
flocks browser -c 'print(page_info())'
```

如果当前 `BU_NAME` 下已有旧 daemon，显式设置 `BU_CDP_URL` / `BU_CDP_WS` 后再次执行 `flocks browser --setup`，应让 daemon 重新按当前 endpoint 建连，而不是继续复用旧连接。若仍怀疑有残留状态，先执行一次 `flocks browser -c 'restart_daemon()'` 再重试。

如果验证成功，再继续读取 `references/cdp-direct.md` 并进入正常页面操作流程。

## 生命周期与关闭时机

记住两件事：

- `flocks browser` daemon 生命周期
- 专用 headless 浏览器进程生命周期

两者不是同一个东西。`restart_daemon()` 只会重启 daemon，不会替你重启或关闭 headless 浏览器。

推荐约定：

1. 如果这个 headless 浏览器是当前任务临时启动的专用实例：
   - 启动后保持它存活，直到当前任务所有 `flocks browser -c '...'` 操作完成
   - 任务结束时，先按 `references/cdp-direct.md` 的规则关闭自己创建的 tab
   - 确认后续不再复用这个实例后，再停止整个浏览器进程，并按需删除专用 `--user-data-dir`
2. 如果 `BU_CDP_URL` / `BU_CDP_WS` 指向的是用户已有远程浏览器、外部守护进程、共享环境或长期服务：
   - 不要主动关闭整个浏览器
   - 只关闭当前任务自己创建的 tab
3. 如果 setup 失败或任务中途放弃，而浏览器是你刚刚启动的：
   - 应清理掉这个专用浏览器进程，避免残留孤儿进程和 profile 目录

关闭示例：

Windows PowerShell：

```powershell
$pidfile = Join-Path $env:TEMP "bu-headless.pid"
if (Test-Path $pidfile) {
  Stop-Process -Id (Get-Content $pidfile)
}
```

macOS / Linux：

```bash
if [ -f "$BU_HEADLESS_PID" ]; then
  kill "$(cat "$BU_HEADLESS_PID")" 2>/dev/null || true
fi
```

## 常见排障

- 无头专用 Chrome 与用户日常 Chrome 同时存在时，不要依赖 `DevToolsActivePort` 自动发现；它可能让 daemon 连到错误的浏览器实例
- 这种场景必须显式设置 `BU_CDP_WS` 或 `BU_CDP_URL`，让 `flocks browser` 直连你启动的 headless 实例
- 如果 `9222` 已被其他浏览器实例占用，不要复用它；改用新的专用端口，并同步更新浏览器启动参数与 `BU_CDP_URL` / `BU_CDP_WS`
- 如果显式切换到了新的 `BU_CDP_URL` / `BU_CDP_WS`，但当前 `BU_NAME` 下还有旧 daemon，优先让 `flocks browser --setup` 重新建连；若仍异常，再执行 `flocks browser -c 'restart_daemon()'`
- 当 daemon 已经死掉但 POSIX socket 文件还留在 `/tmp/` 时，再手动删除 `/tmp/bu-default.sock`；Windows 不需要这一步
- 如果失败并出现 `HTTP 403`，优先回头检查 headless Chrome 是否带了 `--remote-allow-origins=*`
