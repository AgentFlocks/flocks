# 青藤安全平台浏览器自动化

> 本文档统一按 `browser-use` 的 `cdp-direct` 流程执行。进入浏览器模式后，优先使用 `flocks browser --doctor` 检查环境；doctor 通过后只使用 `flocks browser`，不要再切回旧的浏览器命令。
> 对后台任务 / 定时任务，或系统不支持可视化，使用 `browser-use` 的 `cdp-headless` 模式。

## 零、登录认证

State 文件路径：`~/.flocks/browser/qingteng/auth-state.json`（固定，全局唯一）。

### 首次登录 / Session 过期重新登录

先确保本机可复用 Chromium 系浏览器：

```bash
flocks browser --doctor
```

如果 `flocks browser --doctor` 提示浏览器已运行，但 daemon 或 active browser connection 不可用，必须直接提示用户：

```text
browser: not connected — 请确保 Chrome / Chromium / Edge 已打开，然后访问对应浏览器的 inspect 页面（例如 chrome://inspect/#remote-debugging 或 edge://inspect/#remote-debugging）并勾选 Allow remote debugging
```

然后等待用户进一步指示，不要直接操作。

当用户确认已开启 remote debugging 后：

1. 执行 `flocks browser --setup` 触发交互式 attach，不要用短超时包装该命令。
2. 再运行 `flocks browser --doctor` 做只读确认。
3. 如果还失败，先执行 `flocks browser --reload` 清理旧 daemon，再重新执行 `flocks browser --setup`，避免因为残留 daemon 造成干扰。
4. 只有随后 `--doctor` 通过后，才继续后面的登录或页面操作。

打开登录页并等待用户手动完成登录：

```bash
flocks browser -c '
tid = new_tab("https://<domain>/login", activate=True)
wait_for_load()
print(tid)
print(page_info())
'
```

用户登录完成后，立即保存状态：

```bash
flocks browser state save ~/.flocks/browser/qingteng/auth-state.json
```

### CLI 或页面认证失败时的恢复流程

当出现以下任一情况，优先判定为认证问题：

- 返回 HTTP `401` / `403`
- 返回内容包含 `Unauthorized`、`login`、未登录、认证失败
- `auth-state.json` 存在，但 CLI 请求或页面访问仍失败

恢复步骤（最多尝试 1 次）：

```bash
# 1) 重新加载 state，并直接带目标 URL 做验证
flocks browser state load ~/.flocks/browser/qingteng/auth-state.json --url "https://<domain>/"

# 2) 读取当前页面状态
flocks browser -c '
print(page_info())
'
```

如果输出 URL 仍然落回登录页，再要求用户重新登录；不要无限循环重试。

## 浏览器最小操作模板

```bash
flocks browser -c '
tid = new_tab("https://<domain>/<path>", activate=True)
wait_for_load()
print(page_info())
print(js("document.body.innerText.slice(0, 2000)"))
'
```

后续继续使用同一个 tab 时，优先先 attach 再操作：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(page_info())
'
```

## 页面操作原则

- 能直接拼 URL 时，优先直接拼 URL，不走左侧菜单点击。
- 先用 `page_info()` 和 `js(...)` 读取页面状态，再执行点击、输入或滚动。
- 需要点击自定义组件时，优先用 `js(...)` 定位 DOM 后直接点击；只有 DOM 很难稳定定位时，才退到 `click_at_xy(...)`。
- 页面变化后，之前读取到的文本、DOM 状态和坐标都可能失效；必须重新 `page_info()` 或重新跑 `js(...)`。

## 重要提醒

- **Session 管理**：详见[零、登录认证](#零登录认证)。任务开始前先确认 `auth-state.json` 存在；CLI 认证失败时先走恢复流程，不要立刻要求用户重新登录。
- **禁止连续失败循环**：同一命令最多重试 2 次；认证恢复流程只走一次，仍失败则提示用户手动重新登录。
  - **以下错误属于需要用户干预的基础设施问题，立即停止所有重试，直接告知用户处理**：
    - `ERR_CERT_AUTHORITY_INVALID`：站点证书不被本机信任，请求用户处理。
    - `ERR_NAME_NOT_RESOLVED`：域名无法解析，告知用户确认域名或检查 DNS / hosts 配置。