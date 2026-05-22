# 深信服 XDR CDP 直连浏览器流程

## 环境信息

| 项目 | 值 |
|------|-----|
| **Browser daemon port 文件** | `~/.flocks/browser/bu.port`（固定路径，跨平台） |
| **XDR 地址** | **需用户提供**（无默认值） |
| **目标页面 URL** | `{XDR_URL}/#/apex-business/settings/run/state` |

## 零、前置条件

### 1. 确保浏览器 daemon 可用
```bash
flocks browser --doctor
```

如果 `active browser connections` 为 0，需用户开启浏览器 remote debugging。

### 2. 开启 Chrome Remote Debugging

**Windows**
```powershell
# Chrome
chrome.exe --remote-debugging-port=9222
# Edge
msedge.exe --remote-debugging-port=9222
```

**macOS**
```bash
# Chrome
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222
# Edge
"/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" --remote-debugging-port=9222
```

**Linux**
```bash
google-chrome --remote-debugging-port=9222
# 或
chromium --remote-debugging-port=9222
```

### 3. 登录 XDR
确保用户在 Chrome 中已登录 XDR（如需 MFA，完成认证）。

---

## 一、执行流程

### Step 1：确认 XDR URL
**必须询问用户 XDR 地址**，例如：
- `https://xdr.example.com/`
- `https://xdr.company.com/`

### Step 2：检查 daemon
```bash
flocks browser --doctor
```

如果 daemon 未运行，执行：
```bash
flocks browser --setup
```

### Step 3：用户打开目标页面
用户在 Chrome 中打开：
```
{XDR_URL}/#/apex-business/settings/run/state
```

### Step 4：执行抓取脚本

**工具脚本路径**（位于 skill references 目录）：
```
references/fetch_xdr_system_state.py
```

**执行命令（按平台选择）：**

```powershell
# Windows PowerShell
powershell -Command "& '<FLOCKS_VENV>\Scripts\python.exe' '<FLOCKS_PLUGINS>\skills\sangfor-xdr-use\references\fetch_xdr_system_state.py' --url '{XDR_URL}'"
```

```bash
# macOS / Linux
"<FLOCKS_VENV>/bin/python" "<FLOCKS_PLUGINS>/skills/sangfor-xdr-use/references/fetch_xdr_system_state.py" --url "{XDR_URL}"
```

占位符 `<FLOCKS_VENV>` / `<FLOCKS_PLUGINS>` 含义见 SKILL.md "执行示例"。

**参数说明：**
| 参数 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `--url` | 是 | - | XDR URL，如 `https://xdr.example.com/` |
| `--wait` | 否 | 5 | 等待页面渲染秒数 |
| `--raw` | 否 | False | 输出原始页面文本 |

---

## 二、手动 CDP Socket 方式

> 当脚本不可用时，使用此方式。

```python
import json
import socket
import time
from pathlib import Path

port_file = Path.home() / ".flocks" / "browser" / "bu.port"
port = int(port_file.read_text().strip())

def send_cmd(sock, cmd):
    sock.sendall((json.dumps(cmd) + "\n").encode())
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(8192)
        if not chunk:
            break
        data += chunk
    return json.loads(data)

sock = socket.socket()
sock.settimeout(15)
sock.connect(("127.0.0.1", port))

targets = send_cmd(sock, {"method": "Target.getTargets"})["result"]["targetInfos"]

xdr_url = "{XDR_URL}"
host = xdr_url.replace("https://", "").replace("http://", "").rstrip("/")

xdr_tab = next((t for t in targets if host in t.get("url", "") and "/apex-business/settings/run/state" in t.get("url", "")), None)

if not xdr_tab:
    xdr_tab = next((t for t in targets if host in t.get("url", "") and t.get("type") == "page"), None)

if not xdr_tab:
    print(f"XDR tab not found. Please open: {xdr_url}/#/apex-business/settings/run/state")
    exit(1)

attach = send_cmd(sock, {"method": "Target.attachToTarget", "params": {"targetId": xdr_tab["targetId"], "flatten": True}})
session_id = attach["result"]["sessionId"]

time.sleep(5)

text_result = send_cmd(sock, {"method": "Runtime.evaluate", "params": {"expression": "document.body.innerText"}, "session_id": session_id})
print(text_result["result"]["result"]["value"])
```

---

## 三、页面数据提取

从 `page_text` 中按关键词提取：

| 数据 | 关键词 |
|------|--------|
| 节点健康/异常/不可用 | `状态总览` 区块 |
| CPU/内存/磁盘使用率 | `CPU使用趋势` / `内存使用趋势` / `磁盘使用趋势` |
| 系统盘/数据盘/CPU温度 | `系统盘监控状况` / `数据盘监控状况` / `CPU最高温度` |
| 磁盘IO | `读取` / `写入` + `MiB/s` |
| 网口流量 | `接收` / `发送` + `MiB/s` |
| IO延迟 | `IO读取延迟` / `IO写入延迟` + `ms` |
| 数据接入指标 | `数据采集吞吐率` / `数据解析速率` / `授权日志上限` / `日志接入总量` |

---

## 四、关键坑点

| 坑 | 原因 | 解法 |
|---|---|---|
| `Target.getTargets` 返回空 | 浏览器未开启 remote debugging | 用户执行 `chrome.exe --remote-debugging-port=9222` |
| XDR tab 未找到 | 页面未打开或 URL 不匹配 | 确保 Chrome 中打开了系统运维页面 |
| 页面数据为空 | XDR 图表需时间渲染 | 增加 `--wait` 参数（默认 5 秒） |
| 页面显示登录框 | 会话已失效 | 告知用户重新登录 XDR |

---

## 五、执行规范

**必须使用 Flocks 虚拟环境（`.venv`）执行 Python 脚本，禁止使用系统 Python。**

- ✅ 正确：`<FLOCKS_VENV>/bin/python`（Unix）或 `<FLOCKS_VENV>\Scripts\python.exe`（Windows）
- ❌ 禁止：`python script.py` / `python3 script.py`

---

## 六、可用工具脚本

| 脚本路径 | 功能 | 必需参数 |
|---------|------|---------|
| `references/fetch_xdr_system_state.py` | 系统运行状态抓取 | `--url {XDR_URL}` |

### 执行示例

```powershell
# Windows
powershell -Command "& '<FLOCKS_VENV>\Scripts\python.exe' '<FLOCKS_PLUGINS>\skills\sangfor-xdr-use\references\fetch_xdr_system_state.py' --url 'https://xdr.example.com/'"
```

```bash
# macOS / Linux
"<FLOCKS_VENV>/bin/python" "<FLOCKS_PLUGINS>/skills/sangfor-xdr-use/references/fetch_xdr_system_state.py" --url "https://xdr.example.com/"
```