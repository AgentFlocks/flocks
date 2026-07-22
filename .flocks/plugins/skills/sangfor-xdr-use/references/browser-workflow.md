# 深信服 XDR 浏览器工作流

> 本文件是深信服 XDR 的独立浏览器流程，基于 `browser-use` / `flocks browser` 的 `cdp-direct` 能力编写。
> 当任务进入浏览器模式时，优先执行本文件；不要再依赖旧的 socket 示例作为主流程。
> 对后台任务 / 定时任务，或系统不支持可视化，使用 `browser-use` 的 `cdp-headless` 模式。

## 适用范围

当任务涉及以下场景时，进入浏览器模式：

- 系统运维页面查看
- 节点状态、CPU / 内存 / 磁盘 / IO 趋势查看
- 页面详情、交互式筛选、图表数据读取
- API 未覆盖或 API 当前不可用的场景

## 环境信息

| 项目 | 值 |
|------|-----|
| **XDR 地址** | **需用户提供**（无默认值） |
| **目标页面 URL** | `{XDR_URL}/#/apex-business/settings/run/state` |
| **浏览器入口** | `flocks browser` |

## 零、前置条件

### 1. 先确认 XDR URL

必须询问用户 XDR 地址，例如：

- `https://xdr.example.com/`

### 2. 确保 `browser-use` 可用

先执行：

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

### 3. 登录 XDR

如果页面需要登录、MFA 或人工确认，必须让用户在可见浏览器中完成。

## 一、标准工作流

### Step 1：打开或创建目标页

优先创建自己的 tab：

```bash
flocks browser -c '
tid = new_tab("{XDR_URL}/#/apex-business/settings/run/state", activate=True)
wait_for_load()
print(tid)
print(page_info())
'
```

如果当前任务已经创建过该 URL 的 tab，也可以复用：

```bash
flocks browser -c '
tid = open_or_attach_tab("{XDR_URL}/#/apex-business/settings/run/state", activate=True)
wait_for_load()
print(tid)
print(page_info())
'
```

### Step 2：确认页面已登录且 attach 正确

```bash
flocks browser -c '
print(page_info())
print(js("document.body.innerText.slice(0, 2000)"))
'
```

如果显示登录框、权限错误或明显不是系统运维页，让用户先完成登录，再重新读取。

### Step 3：等待图表和系统状态卡片渲染

XDR 系统运维页的趋势图和卡片通常需要额外等待。默认做法：

```bash
flocks browser -c '
wait(5.0)
print(page_info())
print(js("document.body.innerText.slice(0, 3000)"))
'
```

如果只需要快速探测页面是否已加载，可先等 `3` 秒；若文本仍不完整，再增量等待。

## 二、核心数据提取

### 系统运维页面文本抓取

```bash
flocks browser -c '
print(js("document.body.innerText"))
'
```

如果需要结构化探测，可先做轻量判断：

```bash
flocks browser -c '
text = js("document.body.innerText")
print({
    "has_status_overview": "状态总览" in text,
    "has_cpu": "CPU使用趋势" in text,
    "has_memory": "内存使用趋势" in text,
    "has_disk": "磁盘使用趋势" in text,
    "has_ingest": "数据采集吞吐率" in text,
})
'
```

### 重点字段关键词

从页面文本中重点关注：

| 数据 | 关键词 |
|------|--------|
| 节点健康 / 异常 / 不可用 | `状态总览` |
| CPU / 内存 / 磁盘使用率 | `CPU使用趋势` / `内存使用趋势` / `磁盘使用趋势` |
| 系统盘 / 数据盘 / CPU温度 | `系统盘监控状况` / `数据盘监控状况` / `CPU最高温度` |
| 磁盘 IO | `读取` / `写入` + `MiB/s` |
| 网口流量 | `接收` / `发送` + `MiB/s` |
| IO 延迟 | `IO读取延迟` / `IO写入延迟` + `ms` |
| 数据接入指标 | `数据采集吞吐率` / `数据解析速率` / `授权日志上限` / `日志接入总量` |

## 三、页面交互与观察

### 重新 attach 当前 tab

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(page_info())
'
```

### 读取更多页面文本

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(js("document.body.innerText.slice(0, 5000)"))
'
```

### 需要滚动时

XDR 页面上尽量少用滚动；若确实需要，先滚动再重新读取：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("window.scrollBy(0, 1000)")
wait(1.0)
print(js("document.body.innerText.slice(0, 4000)"))
'
```

## 四、排障顺序

### 1. `js(...)` 返回空文本

优先检查：

1. 当前 attach 的是不是正确 tab
2. 页面是否仍在登录页
3. 图表是否尚未渲染完成

推荐命令：

```bash
flocks browser -c '
print(current_tab())
print(list_tabs(include_chrome=False))
'
```

### 2. `new_tab()` 后后续命令无响应

说明 session 可能切到了错误上下文。处理方式：

```bash
flocks browser -c '
switch_tab("<TARGET_ID>")
print(page_info())
'
```

### 3. 页面数据为空或加载中

优先顺序：

1. `wait(5.0)` 后重读
2. 确认当前 URL 是否就是系统运维页
3. 让用户确认当前页面确实打开了 `{XDR_URL}/#/apex-business/settings/run/state`

### 4. 页面显示登录框

直接告知用户重新登录 XDR，不要尝试绕过登录。

## 五、执行规范

- 默认优先 `new_tab(..., activate=True)` 创建自己的 tab。
- 需要继续操作同一 tab 时，优先 `attach_tab("<TARGET_ID>")`。
- 每次等待、切 tab、滚动之后，都重新读取 `page_info()` 或 `js(...)`。
- 如果需要视觉证据，使用 `capture_screenshot(...)`；不要把截图作为默认主流程。

## 六、可选调试动作

截图：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(capture_screenshot("/tmp/sangfor-xdr.png", max_dim=1800))
'
```

打印按钮 / 链接候选：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(js("""
Array.from(document.querySelectorAll("a, button"))
  .slice(0, 100)
  .map(el => ({
    tag: el.tagName,
    text: el.textContent?.trim()?.slice(0, 40),
  }))
"""))
'
```
