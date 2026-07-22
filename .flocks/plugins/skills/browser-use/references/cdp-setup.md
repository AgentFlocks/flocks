# Flocks browser setup

本地固定端口 CDP 设置：daemon 不存在/不通，active browser connection 不可用，或浏览器尚未以 remote debugging 参数启动。

先区分两种情况：

1. `daemon alive` ok 但 `active browser connections` 为 0：
   - 不要先反复执行 `flocks browser --setup`，因为 setup 在 daemon 已运行且协议正常时可能直接输出 nothing to do。
   - 先执行 `flocks browser -c 'print(page_info())'` 或 `flocks browser -c 'print(list_tabs(include_chrome=False))'` 触发一次实际连接/观察。
   - 如果仍失败，再执行 `flocks browser --reload` 清旧 daemon，然后执行 `flocks browser --setup`。
2. daemon 不存在/不通，且浏览器已运行或配置了 `BU_CDP_URL` / `BU_CDP_WS`：
   - 执行 `flocks browser --setup` 触发 attach，不要用短超时包装该命令。

只有在错误明确指向 remote debugging 不可达、`DevToolsActivePort` 缺失、403 handshake 或 not live yet 时，才提示用户走本地固定端口流程：

```text
不要从 chrome://inspect 查找 webSocketDebuggerUrl。关闭对应 Chromium 系浏览器后，使用非默认 --user-data-dir 和 --remote-debugging-port=9222 启动浏览器，再访问 http://127.0.0.1:9222/json/version 验证。
```

候选命令按平台选择一个即可；如果浏览器安装路径不同，替换可执行文件路径：

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

输出命令后等待用户进一步指示，不要占用当前终端盲目重试。

当用户确认 `http://127.0.0.1:9222/json/version` 已可访问后:
1. 执行 `flocks browser --setup` 触发 attach，不要用短超时包装该命令
2. 再运行 `flocks browser --doctor` 做只读确认。
3. 如果还失败，先执行 `flocks browser --reload` 清理旧 daemon，再重新执行 `flocks browser --setup`，避免因为残留 daemon 造成干扰。
