---
name: sangfor-edr-use
description: 用于处理深信服 EDR（终端检测与响应）相关任务，通过浏览器（CDP 直连）进行以下任务：终端状态查询、终端概况统计、失陷设备排查、设备运行状态查看等。只要用户提到 深信服 EDR、EDR、sangfor EDR 等需求时，必须先加载本 skill。本 skill 是 EDR 平台操作的唯一决策入口：在未阅读本 skill 前，不要直接使用 browser-use skill。
---

# 深信服 EDR Use

## First

> ⚠️ **EDR 没有开放 API**，所有操作必须通过浏览器（CDP 直连）完成。

进入浏览器模式前，**必须询问用户 EDR URL**（如 `https://edr.example.com/`），然后阅读浏览器模式使用指南。

## 浏览器模式使用指南

请阅读以下文档获取完整流程：
- [references/cdp-workflow.md](references/cdp-workflow.md)

### CDP 模式适用场景

- **首页仪表盘**（`/ui/#/index`）：设备 CPU/内存/硬盘使用率、终端概况（在线/离线/服务器/PC）、失陷设备统计
- **威胁资产分析**：已失陷终端列表（需点击"已失陷终端"标签页，不是默认的"全部"）
- 页面详情、交互式筛选

### 可用工具脚本

| 脚本路径 | 功能 | 必需参数 |
|---------|------|---------|
| `references/fetch_edr_system_state.py` | 设备状态抓取 | `--url {EDR_URL}` |

脚本位于 skill 目录的 `references/` 下，无硬编码 URL 或敏感信息。

### 执行示例

脚本位于 `<flocks-plugins-root>/skills/sangfor-edr-use/references/fetch_edr_system_state.py`，请按当前平台选择对应命令。

**Windows（PowerShell）**

```powershell
powershell -Command "& '<FLOCKS_VENV>\Scripts\python.exe' '<FLOCKS_PLUGINS>\skills\sangfor-edr-use\references\fetch_edr_system_state.py' --url '{EDR_URL}'"
```

**macOS / Linux（bash / zsh）**

```bash
"<FLOCKS_VENV>/bin/python" "<FLOCKS_PLUGINS>/skills/sangfor-edr-use/references/fetch_edr_system_state.py" --url "{EDR_URL}"
```

**占位符说明**

| 占位符 | Windows 典型值 | macOS/Linux 典型值 |
|--------|---------------|-------------------|
| `<FLOCKS_VENV>` | `D:\Flocks Project\flocks\.venv` | `~/Flocks/flocks/.venv`（取决于实际安装位置） |
| `<FLOCKS_PLUGINS>` | `%USERPROFILE%\.flocks\plugins` | `~/.flocks/plugins` |

> 必须使用 Flocks 虚拟环境（`.venv`）执行；系统 Python 可能缺少依赖。如不确定 venv 位置，先执行 `flocks --version` 或检查 Flocks 安装目录。

## 关键坑点（必须避免）

| 坑 | 原因 | 解法 |
|---|---|---|
| `flocks browser -c js(...)` 返回空文本 | daemon session 指向错误的 tab | 用 Python socket 直连 daemon，通过 `Runtime.evaluate` 在正确 context 执行 |
| `flocks browser -c new_tab()` 后后续命令无响应 | tab 切换导致 session 错位 | 用 `switch_tab(targetId)` 明确切到 EDR tab |
| 多行代码转义失败 | PowerShell 引号嵌套 | 使用 `fetch_edr_system_state.py` 脚本，无需手动转义 |
| EDR 页面数据为空 | EDR 内容在跨域 iframe 中 | 用 CDP direct 方式 attach 到 EDR tab，在正确 frame context 执行 JS |
| 失陷设备数量不匹配 | 读取的是"全部"筛选而非"已失陷"筛选 | 需点击"已失陷终端"标签页获取准确数量 |

## 失陷设备查询 SOP

**问题**：首页仪表盘显示"已失陷 N 台"，但威胁资产分析页面默认只列出部分。

**成功路径**：
1. 在 EDR 首页仪表盘确认"已失陷 N 台"的数量
2. 如需具体清单，进入 `威胁资产分析` → 点击 `已失陷终端` 标签页（不是默认的"全部"）
3. 只有点击"已失陷终端"标签页，列表数量才会与仪表盘一致

**⚠️ 必须避免**：直接读取威胁资产分析的默认"全部"筛选结果作为失陷设备清单，这是错误的。

## 执行规范

**必须使用 Flocks 虚拟环境（`.venv`）执行 Python 脚本，禁止使用系统 Python。**

- ✅ 正确：`<FLOCKS_VENV>/bin/python`（Unix）或 `<FLOCKS_VENV>\Scripts\python.exe`（Windows）
- ❌ 禁止：`python script.py` / `python3 script.py`（直接调用 PATH 中的 Python）

**原因**：Flocks 虚拟环境包含了所有项目依赖，系统 Python 可能缺少必要的包。完整跨平台示例见上一节"执行示例"。