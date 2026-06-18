# Flocks browser setup

浏览器已运行，但 daemon 不存在/不通，或 active browser connection 不可用

先区分两种情况：

1. `daemon alive` ok 但 `active browser connections` 为 0：
   - 不要先反复执行 `flocks browser --setup`，因为 setup 在 daemon 已运行且协议正常时可能直接输出 nothing to do。
   - 先执行 `flocks browser -c 'print(page_info())'` 或 `flocks browser -c 'print(list_tabs(include_chrome=False))'` 触发一次实际连接/观察。
   - 如果仍失败，再执行 `flocks browser --reload` 清旧 daemon，然后执行 `flocks browser --setup`。
2. daemon 不存在/不通，且浏览器已运行或配置了 `BU_CDP_URL` / `BU_CDP_WS`：
   - 执行 `flocks browser --setup` 触发 attach，不要用短超时包装该命令。

只有在错误明确指向 remote debugging 未启用、`DevToolsActivePort` 缺失、403 handshake 或 not live yet 时，才提示用户：

```text
browser: not connected — 请确保 Chrome / Chromium / Edge 已打开，然后访问对应浏览器的 inspect 页面（例如 chrome://inspect/#remote-debugging 或 edge://inspect/#remote-debugging）并勾选 Allow remote debugging
```

然后等待用户进一步指示，不要直接操作。

当用户确认已开启remote-debugging后:
1. 执行 `flocks browser --setup` 触发交互式 attach，不要用短超时包装该命令
2. 再运行 `flocks browser --doctor` 做只读确认。
3. 如果还失败，先执行 `flocks browser --reload` 清理旧 daemon，再重新执行 `flocks browser --setup`，避免因为残留 daemon 造成干扰。setup 可能需要多次，直到用户完成浏览器 Allow/inspect 授权或错误信息稳定。
