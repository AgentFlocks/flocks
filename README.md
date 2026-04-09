# Flocks

**English** | [简体中文](README_zh.md)

AI-Native SecOps Platform

![Flocks WebUI](assets/flocks.webp)

## Project Overview

Flocks is an AI-driven SecOps platform built with Python, featuring multi-agent collaboration, HTTP API server, and modern terminal user interface designed to help you with your SecOps tasks.

## Features

- 🤖 **AI Agent System** - Multi-agent collaboration (build, plan, general)
- 🔧 **Rich Tool Set** - bash, file operations, code search, LSP integration, etc.
- 🌐 **HTTP API Server** - High-performance API service based on FastAPI
- 💬 **Session Management** - Session and context management
- 🎯 **Multiple Model Support** - Support for Anthropic, OpenAI, Google and other AI models
- 📝 **LSP Integration** - Language Server Protocol support
- 🔌 **MCP Support** - Model Context Protocol
- 🖼️ **WebUI** - Browser-based web user interface
- 🎨 **TUI Interface** - Modern terminal user interface

# Installation & Usage

Flocks supports two deployment methods:

- `Option 1: PC Installation` (recommended)
- `Option 2: Docker Installation`

Choose one method below.

## Option 1: PC Installation

### System Requirements

- `uv`
- `Node.js` with `npm` 22.+
- `agent-browser`
- `bun` for TUI installation (Optional)

By default, the project install scripts will try to ensure the requirements above are available automatically when possible.

If automatic `npm` installation fails during setup, please install `npm` manually and use version `22.+` or newer.

### Install with one command

> **Users in mainland China**: If GitHub or `raw.githubusercontent.com` is slow or unreachable, clone from a Gitee mirror and follow the Source install instructions below.

The recommended host installation entrypoint is the GitHub bootstrap installer. It downloads the repository source archive to a temporary directory, copies it into a `flocks/` subdirectory under your current working directory by default, then installs backend and WebUI dependencies and exposes the `flocks` CLI on your PATH. You can still override the destination with `FLOCKS_INSTALL_DIR`.

#### macOS / Linux

```bash
# One-click install backend + WebUI
curl -fsSL https://raw.githubusercontent.com/AgentFlocks/flocks/main/install.sh | bash
# Creates ./flocks under the current directory

# Optional: also install TUI dependencies
curl -fsSL https://raw.githubusercontent.com/AgentFlocks/flocks/main/install.sh | bash -s -- --with-tui
```

#### Windows PowerShell (Administrator)

```powershell
# One-click install backend + WebUI
powershell -c "irm https://raw.githubusercontent.com/AgentFlocks/flocks/main/install.ps1 | iex"

# Optional: also install TUI dependencies
powershell -c "& ([scriptblock]::Create((irm https://raw.githubusercontent.com/AgentFlocks/flocks/main/install.ps1))) -InstallTui"
```

### Install from source code

If you prefer to inspect the repository before installation, clone it locally and run the installer from the workspace:

```bash
git clone https://github.com/AgentFlocks/Flocks.git flocks

# Alternative for users in China (Gitee mirror)
# git clone https://gitee.com/flocks/flocks.git flocks

cd flocks
```

Macos/Linux
```bash
./scripts/install.sh # Macos/Linux
```

Windows powershell (Administrator)
```bash
powershell -ep Bypass -File .\scripts\install.ps1 # Windows powershell
```

### Start service

Use the `flocks` CLI to manage the backend and WebUI together in daemon mode.
The `start` command builds the WebUI before launch by default; use `flocks restart` when you want an explicit full restart.

#### macOS / Linux / Windows PowerShell

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

Flocks cli useage:  `flocks --help`

## Option 2: Docker Installation

> [!NOTE]
> docker 模式下暂时 agent-browser headed 模式不可用

### Pull image

```bash
docker pull ghcr.io/agentflocks/flocks:latest
```

## Start service

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

## FAQ

### For Users in China: Speed Up Python Package Installation

On machines in mainland China, you can configure `uv` to use a local PyPI mirror for faster package downloads.

Create `~/.config/uv/uv.toml` with:

```toml
[[index]]
url = "https://pypi.tuna.tsinghua.edu.cn/simple"

[[index]]
url = "https://pypi.org/simple"
default = true
```

### Docker Issues

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

### Remote Access to Flocks Service
```bash
__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=<your_domain> \
flocks start --server-host 127.0.0.1 --webui-host 0.0.0.0
```
If remote access from a virtual machine fails, please specify the host as the virtual machine's IP.

## Join our community

Scan the QR code with **WeChat** to join our official discussion group.  

![WeCom official community QR code](assets/community-wecom-qr.png)

## License

Apache License 2.0
