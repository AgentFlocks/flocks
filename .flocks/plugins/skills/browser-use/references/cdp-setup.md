# Flocks browser setup

本地浏览器连接以复用用户当前 profile 为主。独立 profile 固定端口只用于当前 profile 授权并重试后仍无法连接的兜底场景。

先区分两种情况：

1. `daemon alive` ok 但 `active browser connections` 为 0：
   - 不要先反复执行 `flocks browser --setup`，因为 setup 在 daemon 已运行且协议正常时可能直接输出 nothing to do。
   - 先执行 `flocks browser -c 'print(page_info())'` 或 `flocks browser -c 'print(list_tabs(include_chrome=False))'` 触发一次实际连接/观察。
   - 如果仍失败，再执行 `flocks browser --reload` 清旧 daemon，然后执行 `flocks browser --setup`。
2. daemon 不存在/不通，且浏览器已运行或配置了 `BU_CDP_URL` / `BU_CDP_WS`：
   - 执行 `flocks browser --setup` 触发 attach，不要用短超时包装该命令。

只有在错误明确指向 remote debugging 未启用、`DevToolsActivePort` 缺失、403 handshake 或 not live yet 时，才提示用户完成当前 profile 的授权：

```text
打开对应浏览器的 inspect 页面（例如 chrome://inspect/#remote-debugging 或 edge://inspect/#remote-debugging），选择日常使用的 profile，并勾选或点击 Allow remote debugging。不要从 chrome://inspect 查找 webSocketDebuggerUrl；Flocks 会自行发现 endpoint。
```

用户完成 Allow 后，`flocks browser --setup` 会再次尝试 attach。不要要求用户先关闭日常浏览器，也不要默认创建独立 profile。

## 独立 profile 兜底

只有当前 profile 的 inspect/Allow 流程重试后仍失败，才提供以下独立 profile 方案。候选命令按平台选择一个即可；如果浏览器安装路径不同，替换可执行文件路径：

Windows PowerShell：

```powershell
& "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$env:USERPROFILE\.flocks\chrome-debug-profile"
& "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222 --user-data-dir="$env:USERPROFILE\.flocks\edge-debug-profile"
chromium.exe --remote-debugging-port=9222 --user-data-dir="$env:USERPROFILE\.flocks\chromium-debug-profile"
& "$env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe" --remote-debugging-port=9222 --user-data-dir="$env:USERPROFILE\.flocks\brave-debug-profile"
```

macOS：

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.flocks/chrome-debug-profile"
/Applications/Microsoft\ Edge.app/Contents/MacOS/Microsoft\ Edge --remote-debugging-port=9222 --user-data-dir="$HOME/.flocks/edge-debug-profile"
/Applications/Chromium.app/Contents/MacOS/Chromium --remote-debugging-port=9222 --user-data-dir="$HOME/.flocks/chromium-debug-profile"
/Applications/Brave\ Browser.app/Contents/MacOS/Brave\ Browser --remote-debugging-port=9222 --user-data-dir="$HOME/.flocks/brave-debug-profile"
```

Linux：

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.flocks/chrome-debug-profile"
microsoft-edge --remote-debugging-port=9222 --user-data-dir="$HOME/.flocks/edge-debug-profile"
chromium --remote-debugging-port=9222 --user-data-dir="$HOME/.flocks/chromium-debug-profile"
brave-browser --remote-debugging-port=9222 --user-data-dir="$HOME/.flocks/brave-debug-profile"
```

输出命令后等待用户进一步指示，不要占用当前终端盲目重试。不要把该方案描述成默认设置方式。

当用户确认 `http://127.0.0.1:9222/json/version` 已可访问后:
1. 执行 `flocks browser --setup` 触发 attach，不要用短超时包装该命令
2. 再运行 `flocks browser --doctor` 做只读确认。
3. 如果还失败，先执行 `flocks browser --reload` 清理旧 daemon，再重新执行 `flocks browser --setup`，避免因为残留 daemon 造成干扰。
