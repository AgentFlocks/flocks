# Flocks

**English** | [简体中文](README_zh.md)

AI-Native SecOps Platform

![Flocks WebUI](assets/flocks.webp)

## 1. Project Overview

Flocks is an AI-driven SecOps platform built with Python, featuring multi-agent collaboration, HTTP API server, and modern terminal user interface designed to help you with your SecOps tasks.

## 2. Features

- 🤖 **AI Agent System** - Multi-agent collaboration (build, plan, general)
- 🔧 **Rich Tool Set** - bash, file operations, code search, LSP integration, etc.
- 🌐 **HTTP API Server** - High-performance API service based on FastAPI
- 💬 **Session Management** - Session and context management
- 🎯 **Multiple Model Support** - Support for Anthropic, OpenAI, Google and other AI models
- 📝 **LSP Integration** - Language Server Protocol support
- 🔌 **MCP Support** - Model Context Protocol
- 🖼️ **WebUI** - Browser-based web user interface
- 🎨 **TUI Interface** - Modern terminal user interface

## 3. Installation & Usage

Flocks supports two deployment methods — **choose one**:

| Method | Description |
|---|---|
| 3.1 PC Installation | Recommended for local development and production deployment |
| 3.2 Docker Installation | Out-of-the-box, but agent-browser headed mode is currently unavailable |

### 3.1 Option 1: PC Installation

#### 3.1.1 System Requirements

- `uv`
- `Node.js` with `npm` 22.+
- `agent-browser`
- `bun` for TUI installation (Optional)

By default, the project install scripts will try to ensure the requirements above are available automatically when possible.

If automatic `npm` installation fails during setup, please install `npm` manually and use version `22.+` or newer.

#### 3.1.2 Install

> The following installation options are supported. **Choose one** to complete the installation, then proceed to 3.1.3 Start service.

---

**Option A: Install with one command (recommended)**

> [!NOTE]
> **Users in mainland China**: Please follow the installation instructions in the [简体中文](README_zh.md), which provides a mirror-accelerated installation method specifically optimized for users in China.

macOS / Linux
```bash
curl -fsSL https://raw.githubusercontent.com/AgentFlocks/flocks/main/install.sh | bash
```
Creates ./flocks under the current directory

Windows PowerShell (Administrator)
```powershell
powershell -c "irm https://raw.githubusercontent.com/AgentFlocks/flocks/main/install.ps1 | iex"
```

---

**Option B: Install from source code**

If you prefer to inspect the repository before installation, clone it locally and run the installer from the workspace:

```bash
git clone https://github.com/AgentFlocks/Flocks.git flocks
cd flocks
```

macOS / Linux
```bash
sh ./scripts/install.sh
```

Windows PowerShell (Administrator)
```powershell
powershell -ep Bypass -File .\scripts\install.ps1
```

---

**Option C: Windows installer (EXE, BETA)**

Flocks provides an **Inno Setup** wizard (`.exe`) for **Windows x64**. Download the installer for your version from the [GitHub Releases](https://github.com/AgentFlocks/flocks/releases) page.

| Platform | Download |
| --- | --- |
| Windows (x64) | `FlocksSetup-<tag>.exe` |

After installation, use the **Start menu** or optional **desktop** shortcut, or open a **new** terminal and run `flocks start` so updated `PATH` and related environment variables take effect. For more details, see [`packaging/README.md`](packaging/README.md).

---

#### 3.1.3 Start service

Use the `flocks` CLI to manage the backend and WebUI together in daemon mode.
The `start` command builds the WebUI before launch by default; use `flocks restart` when you want an explicit full restart.

```bash
flocks start
flocks status
flocks logs
flocks restart
flocks stop
```

The default service URLs are:
- Backend API: `http://127.0.0.1:8000` by default
- WebUI: `http://127.0.0.1:5173` by default
- Remote access configurable via `flocks start --server-host <ip> --webui-host <ip>`

Flocks CLI usage: `flocks --help`

### 3.2 Option 2: Docker Installation

> [!NOTE]
> In the Docker installation, the agent-browser headed mode is currently unavailable.

#### 3.2.1 Pull image

```bash
docker pull ghcr.io/agentflocks/flocks:latest
```

#### 3.2.2 Start service

Run the container and mount the host user's `~/.flocks` directory into the container:

macOS / Linux
```bash
docker run -d \
  --name flocks \
  -p 8000:8000 \
  -p 5173:5173 \
  --shm-size 2gb \
  -v "${HOME}/.flocks:/home/flocks/.flocks" \
  ghcr.io/agentflocks/flocks:latest
```

Windows PowerShell
```powershell
docker run -d `
  --name flocks `
  -p 8000:8000 `
  -p 5173:5173 `
  --shm-size 2gb `
  -v "${env:USERPROFILE}\.flocks:/home/flocks/.flocks" `
  ghcr.io/agentflocks/flocks:latest
```

`EXPOSE` in the image only documents container ports. You still need `-p 8000:8000 -p 5173:5173` to access the service from the host browser.

## 4. FAQ

### 4.1 For Users in China: Speed Up Python Package Installation

On machines in mainland China, you can configure `uv` to use a local PyPI mirror for faster package downloads.

Create `~/.config/uv/uv.toml` with:

```toml
[[index]]
url = "https://pypi.tuna.tsinghua.edu.cn/simple"

[[index]]
url = "https://pypi.org/simple"
default = true
```

### 4.2 Docker Issues

Docker registry mirror in China
``` bash
ghcr.nju.edu.cn/agentflocks/flocks:latest
```

Permission issues for `/home/flocks/.flocks` after startup:

``` bash
-v "$HOME/.flocks:/home/flocks/.flocks:Z" \
```
OR
```bash
docker run --rm --entrypoint id ghcr.io/agentflocks/flocks
# example result: uid=1001(flocks) gid=1001(flocks) 组=1001(flocks)
sudo chown -R <uid>:<gid> ~/.flocks
# example: sudo chown -R 1001:1001 ~/.flocks
```

### 4.3 Remote Access to Flocks Service
```bash
__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=<your_domain> \
flocks start --server-host 127.0.0.1 --webui-host 0.0.0.0
```
If remote access from a virtual machine fails, please specify the host as the virtual machine's IP.

### 4.4 Authentication & API Token

Since the local-account update, every HTTP path is protected by default — only
the WebUI bootstrap pages (`/`, `/auth/*`), static assets, and IM platform
webhooks (`/api/channel/{channel_id}/webhook`) are public.

Initial setup:

1. Open the WebUI and complete the **bootstrap-admin** flow to create the
   single `admin` account.
2. The browser session cookie (`flocks_session`) is enough for the WebUI;
   no extra steps are required.

Non-browser clients (TUI, SDKs, scripts):

- **Local loopback** (`127.0.0.1` / `::1` / `localhost`, no
  `x-forwarded-for` header) is auto-trusted as `local-service` admin. This
  covers TUI, plugin sub-processes, and CLI calls running on the same host.
- **Remote** clients must present an API token. The token lives in
  `~/.flocks/config/.secret.json` under the secret id `server_api_token`.

  On the **server**, generate (or rotate) the token — it is persisted on
  the server's local secret store:

  ```bash
  flocks admin generate-api-token        # prints token; stores under server_api_token
  ```

  On each **remote client**, store the same token value into the client's
  own secret file (so the client SDK / TUI can attach it automatically):

  ```bash
  flocks admin set-api-token --token <token-from-server>
  ```

  Or attach it directly per request via either header:

  ```text
  Authorization: Bearer <token>
  X-Flocks-API-Token: <token>
  ```

  Smoke test:

  ```bash
  curl -H "Authorization: Bearer <token>" https://flocks.example.com/api/health
  ```

Reverse-proxy deployments:

- Always set `X-Forwarded-For` on the proxy. Without it, any direct
  loopback request would be auto-elevated to `admin`. The middleware
  intentionally refuses to trust loopback when this header is absent and a
  proxy is in front.
- For HTTPS termination, also forward `X-Forwarded-Proto: https` so that
  the secure-cookie flag is set correctly.

Recovery / lost password:

- Run `flocks admin generate-one-time-password` on the host. The admin
  account is then forced into `must_reset_password=true`; the next WebUI
  login is redirected to the change-password page. **All non-browser
  endpoints return 403 in that state**, so do not run this against an
  account that automation depends on without coordination.

Orphan sessions (CLI / background / inbound channels):

- Sessions created without an auth context (CLI commands, background
  tasks, inbound IM-channel dispatchers) leave `owner_user_id` empty.
  The bootstrap admin still sees them, but a later-added member account
  would not. Backfill ownership with:

  ```bash
  flocks admin reassign-orphan-sessions --username admin --dry-run   # preview
  flocks admin reassign-orphan-sessions --username admin             # apply
  ```

  The command summarises `scanned / orphaned / reassigned / failed`
  counts; a non-zero `failed` exits with code 2 so CI / scripts can
  detect partial-write situations and re-run after fixing the underlying
  cause (typically a transient storage error).

## 5. Join our community

Scan the QR code with **WeChat** to join our official discussion group.  

![WeCom official community QR code](assets/community-wecom-qr.png)

## 6. License

Apache License 2.0
