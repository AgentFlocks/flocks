# 深信服 EDR 浏览器工作流

> 本文件是深信服 EDR 的独立浏览器流程，基于 `browser-use` / `flocks browser` 的 `cdp-direct` 能力编写。
> 当任务进入浏览器模式时，优先执行本文件；不要再依赖旧的 socket 示例作为主流程。
> 对后台任务 / 定时任务，或系统不支持可视化，使用 `browser-use` 的 `cdp-headless` 模式。

## 适用范围

当任务涉及以下场景时，进入浏览器模式：

- EDR 首页仪表盘查看
- 终端状态、终端概况统计
- 失陷设备排查
- 页面详情、交互式筛选

## 环境信息

| 项目 | 值 |
|------|-----|
| **EDR 地址** | **需用户提供**（无默认值） |
| **目标页面 URL** | `{EDR_URL}/ui/#/index` |
| **浏览器入口** | `flocks browser` |

## 零、前置条件

### 1. 先确认 EDR URL

必须询问用户 EDR 地址，例如：

- `https://edr.example.com/`

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

### 3. 登录 EDR

如果页面需要登录、MFA 或人工确认，必须让用户在可见浏览器中完成，不要假设已有会话。

## 一、标准工作流

### Step 1：打开或创建目标页

优先创建自己的 tab：

```bash
flocks browser -c '
tid = new_tab("{EDR_URL}/ui/#/index", activate=True)
wait_for_load()
print(tid)
print(page_info())
'
```

如果当前任务已经创建过该 URL 的 tab，也可以复用：

```bash
flocks browser -c '
tid = open_or_attach_tab("{EDR_URL}/ui/#/index", activate=True)
wait_for_load()
print(tid)
print(page_info())
'
```

### Step 2：确认页面是否已登录且 attach 正确

```bash
flocks browser -c '
print(page_info())
print(js("document.body.innerText.slice(0, 2000)"))
'
```

如果出现登录框、未授权提示，或文本明显不属于 EDR 首页，让用户先完成登录，再重新读取。

### Step 3：如果主文档为空，检查 iframe

EDR 页面内容可能在 iframe 中。主文档文本为空、明显不完整，或只看到外层壳页面时，改用 iframe 读取：

```bash
flocks browser -c '
frame = iframe_target("/ui/")
print({"iframe_target": frame})
if frame:
    print(js("document.body.innerText.slice(0, 2000)", target_id=frame))
'
```

如果 iframe 目标不稳定，可把 `"/ui/"` 换成用户提供的 EDR 域名中的关键片段。

### Step 4：等待图表和统计卡片渲染

首页图表可能需要额外等待。默认做法：

```bash
flocks browser -c '
wait(3.0)
print(page_info())
print(js("document.body.innerText.slice(0, 2500)"))
'
```

若仍在加载，再增加到 `5` 秒，但不要无限等待。

## 二、核心数据提取

### 首页概览文本抓取

```bash
flocks browser -c '
print(js("document.body.innerText"))
'
```

如果需要结构化片段，优先在页面内拼装 JSON：

```bash
flocks browser -c '
text = js("document.body.innerText")
print({
    "page_text_preview": text[:3000],
    "has_compromised": "已失陷" in text,
    "has_cpu": "CPU" in text or "CPU：" in text,
    "has_memory": "内存" in text,
    "has_disk": "硬盘" in text,
})
'
```

### 重点字段关键词

从页面文本中重点关注：

| 数据 | 关键词 |
|------|--------|
| CPU使用率 | `CPU:` / `CPU：` |
| 内存使用率 | `内存:` / `内存：` |
| 硬盘使用率 | `硬盘:` / `硬盘：` |
| 终端总数 | `受管控终端` |
| 在线 / 离线 / 其它 | `在线:` / `离线:` / `其它:` |
| 服务器 / PC | `服务器:` / `PC:` |
| 已失陷 / 高可疑 / 低可疑 | `已失陷` / `高可疑` / `低可疑` |

## 三、失陷设备查询 SOP

### 目标

首页仪表盘显示“已失陷 N 台”时，进一步获取具体清单。

### 推荐流程

1. 先在首页确认“已失陷 N 台”
2. 进入威胁资产分析页面
3. 切换到“已失陷终端”标签页，而不是停留在默认“全部”

### 浏览器操作示例

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
js("""
Array.from(document.querySelectorAll("*"))
  .find(el => el.textContent?.trim() == "已失陷终端")
  ?.click()
""")
wait(1.5)
print(js("document.body.innerText.slice(0, 2500)"))
'
```

如果通过文字无法稳定点击，先打印候选元素再重新构造点击逻辑：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(js("""
Array.from(document.querySelectorAll("*"))
  .filter(el => el.textContent?.includes("已失陷"))
  .slice(0, 20)
  .map(el => ({
    tag: el.tagName,
    text: el.textContent?.trim(),
    cls: el.className,
  }))
"""))
'
```

> 必须避免直接读取默认“全部”筛选结果作为失陷设备清单。

## 四、排障顺序

### 1. `js(...)` 返回空文本

优先检查：

1. 当前 attach 的是不是正确 tab
2. 页面是否仍在登录页
3. 内容是否在 iframe

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

### 3. 页面数据为空

优先顺序：

1. `wait(3.0)` 后重读
2. 检查 iframe
3. 让用户确认当前页面确实是 `{EDR_URL}/ui/#/index`

### 4. 页面显示登录框

直接告知用户重新登录 EDR，不要尝试绕过登录。

## 五、执行规范

- 默认优先 `new_tab(..., activate=True)` 创建自己的 tab。
- 需要继续操作同一 tab 时，优先 `attach_tab("<TARGET_ID>")`，不要反复抢焦点。
- 每次点击、切 tab、等待、滚动之后，都重新读取 `page_info()` 或 `js(...)`。
- 需要截图时，使用 `capture_screenshot(...)`，不要把截图作为默认主流程。

## 六、可选调试动作

截图：

```bash
flocks browser -c '
attach_tab("<TARGET_ID>")
print(capture_screenshot("/tmp/sangfor-edr.png", max_dim=1800))
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
