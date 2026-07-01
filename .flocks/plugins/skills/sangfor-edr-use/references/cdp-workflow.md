# 深信服 EDR CDP 直连浏览器流程

## 环境信息

| 项目 | 值 |
|------|-----|
| **Browser daemon port 文件** | `~/.flocks/browser/bu.port`（固定路径，跨平台） |
| **EDR 地址** | **需用户提供**（无默认值） |
| **目标页面 URL** | `{EDR_URL}/ui/#/index`（首页仪表盘） |

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

### 3. 登录 EDR
登录态来源有两种，必须先向用户确认是否愿意提供 EDR 访问地址、用户名和密码：

- 用户不提供账密：由用户在 Chrome 中手动登录 EDR（如需 MFA，完成认证），登录成功后保存浏览器 state。
- 用户提供账密：调用 `sangfor_edr_auth` 保存账密，并通过 browser daemon / CDP 驱动真实 EDR 登录页自动登录。验证码图片在浏览器会话中获取，OCR 识别后填入页面；登录成功后保存浏览器 state。

---

## 一、执行流程

### Step 1：确认 EDR URL
**必须询问用户 EDR 地址**，例如：
- `https://edr.example.com/`
- `https://edr.company.com/`

### Step 2：检查 daemon
```bash
flocks browser --doctor
```

如果 daemon 未运行，执行：
```bash
flocks browser --setup
```

### Step 3：准备登录态
优先检查固定 state 文件：
```
~/.flocks/browser/sangfor-edr/auth-state.json
```

如果 state 不存在或已失效：

- 未保存账密：提示用户在 Chrome 中手动登录，登录成功后执行 `flocks browser state save ~/.flocks/browser/sangfor-edr/auth-state.json`。
- 已保存账密：调用 `sangfor_edr_auth`，使用 `action=ensure_auth_state` 或 `action=refresh_auth_state` 自动打开真实登录页、识别验证码、填入账密并保存新的 state。

### Step 4：打开目标页面
用户或工具在 Chrome 中打开：
```
{EDR_URL}/ui/#/index
```

### Step 5：执行抓取脚本

**工具脚本路径**（位于 skill references 目录）：
```
references/fetch_edr_system_state.py
```

**执行命令（按平台选择）：**

```powershell
# Windows PowerShell
powershell -Command "& '<FLOCKS_VENV>\Scripts\python.exe' '<FLOCKS_PLUGINS>\skills\sangfor-edr-use\references\fetch_edr_system_state.py' --url '{EDR_URL}'"
```

```bash
# macOS / Linux
"<FLOCKS_VENV>/bin/python" "<FLOCKS_PLUGINS>/skills/sangfor-edr-use/references/fetch_edr_system_state.py" --url "{EDR_URL}"
```

占位符 `<FLOCKS_VENV>` / `<FLOCKS_PLUGINS>` 含义见 SKILL.md "执行示例"。

**参数说明：**
| 参数 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `--url` | 是 | - | EDR URL，如 `https://edr.example.com/` |
| `--wait` | 否 | 3 | 等待页面渲染秒数 |
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

edr_url = "{EDR_URL}"
host = edr_url.replace("https://", "").replace("http://", "").rstrip("/")

edr_tab = next((t for t in targets if host in t.get("url", "") and "#/index" in t.get("url", "")), None)

if not edr_tab:
    edr_tab = next((t for t in targets if host in t.get("url", "") and t.get("type") == "page"), None)

if not edr_tab:
    print(f"EDR tab not found. Please open: {edr_url}/ui/#/index")
    exit(1)

attach = send_cmd(sock, {"method": "Target.attachToTarget", "params": {"targetId": edr_tab["targetId"], "flatten": True}})
session_id = attach["result"]["sessionId"]

time.sleep(3)

text_result = send_cmd(sock, {"method": "Runtime.evaluate", "params": {"expression": "document.body.innerText"}, "session_id": session_id})
print(text_result["result"]["result"]["value"])
```

---

## 三、页面数据提取

从 `page_text` 中按关键词提取：

| 数据 | 关键词 |
|------|--------|
| CPU使用率 | `CPU:` 或 `CPU：` |
| 内存使用率 | `内存:` 或 `内存：` |
| 硬盘使用率 | `硬盘:` 或 `硬盘：` |
| 终端总数 | `受管控终端` |
| 在线/离线/其它 | `在线:` / `离线:` / `其它:` |
| 服务器/PC | `服务器:` / `PC:` |
| 已失陷/高可疑/低可疑 | `已失陷 N 台` / `高可疑 N 台` / `低可疑 N 台` |

---

## 四、关键坑点

| 坑 | 原因 | 解法 |
|---|---|---|
| `Target.getTargets` 返回空 | 浏览器未开启 remote debugging | 用户执行 `chrome.exe --remote-debugging-port=9222` |
| EDR tab 未找到 | 页面未打开或 URL 不匹配 | 确保 Chrome 中打开了 EDR 首页 |
| 页面数据为空 | EDR 内容在跨域 iframe 中 | 用 CDP direct 方式 attach 到 EDR tab，在正确 frame context 执行 JS |
| 页面显示登录框 | 会话已失效 | 若已保存账密，调用 `sangfor_edr_auth` 自动刷新 state；否则提示用户手动登录 EDR |
| 自动登录失败 | 验证码识别失败、MFA、页面选择器变化或登录成功检测失败 | 回退到浏览器手动登录，登录后保存 state |

---

## 五、执行规范

**必须使用 Flocks 虚拟环境（`.venv`）执行 Python 脚本，禁止使用系统 Python。**

- ✅ 正确：`<FLOCKS_VENV>/bin/python`（Unix）或 `<FLOCKS_VENV>\Scripts\python.exe`（Windows）
- ❌ 禁止：`python script.py` / `python3 script.py`

---

## 六、可用工具脚本

| 脚本路径 | 功能 | 必需参数 |
|---------|------|---------|
| `references/fetch_edr_system_state.py` | 设备状态抓取 | `--url {EDR_URL}` |

### 执行示例

```powershell
# Windows
powershell -Command "& '<FLOCKS_VENV>\Scripts\python.exe' '<FLOCKS_PLUGINS>\skills\sangfor-edr-use\references\fetch_edr_system_state.py' --url 'https://edr.example.com/'"
```

```bash
# macOS / Linux
"<FLOCKS_VENV>/bin/python" "<FLOCKS_PLUGINS>/skills/sangfor-edr-use/references/fetch_edr_system_state.py" --url "https://edr.example.com/"
```
