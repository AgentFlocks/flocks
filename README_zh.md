<h1 align="center">Flocks</h1>

<p align="center">
  AI 原生 SecOps 平台
</p>

<p align="center">
  <a href="https://agentflocks.github.io/flocks-docs/"><img alt="文档" src="https://img.shields.io/badge/docs-agentflocks.github.io-555555?style=for-the-badge"></a>
  <a href="LICENSE.txt"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-52b100?style=for-the-badge"></a>
</p>

<p align="center">
  <a href="README.md"><img alt="English" src="https://img.shields.io/badge/lang-English-e33f44?style=for-the-badge"></a>
  <a href="README_zh.md"><img alt="中文" src="https://img.shields.io/badge/lang-%E4%B8%AD%E6%96%87-e33f44?style=for-the-badge"></a>
</p>

![Flocks Web](assets/flocks.webp)

## 1. 项目概览

Flocks 是一个以 Python 构建的 AI 驱动型 SecOps 平台，具备多智能体协作、HTTP API 服务与现代化终端用户界面，用于辅助完成各类 SecOps 任务。

## 2. 功能特性

- 🤖 **AI 智能体系统** — 多智能体协作（构建、规划、通用）
- 🔧 **丰富工具集** — bash、文件操作、代码搜索、LSP 集成等
- 🌐 **HTTP API 服务** — 基于 FastAPI 的高性能 API
- 💬 **会话管理** — 会话与上下文管理
- 🎯 **多模型支持** — 支持 Anthropic、OpenAI、Google 等 AI 模型
- 📝 **LSP 集成** — 语言服务器协议支持
- 🔌 **MCP 支持** — Model Context Protocol
- 🎨 **TUI 界面** — 现代化终端用户界面
- 🖼️ **WebUI** — 基于浏览器的 Web 用户界面

## 3. 安装与使用

Flocks 支持两种部署方式，请**任选其一**：

| 方式 | 说明 |
|---|---|
| 3.1 终端安装 | 推荐，适用于本地开发与生产部署 |
| 3.2 Docker 安装 | 开箱即用，但 agent-browser headed 模式暂不可用 |

### 3.1 方案 1：终端安装

#### 3.1.1 系统要求

- `uv`
- `Node.js` 与 `npm` `22.+`
- `agent-browser`
- `bun`（可选，用于 TUI 安装）

默认情况下，项目安装脚本会在可行时尽量自动满足上述要求。

如果安装过程中自动安装 `npm` 失败，请手动安装 `npm`，并使用 `22.+` 或更高版本。

#### 3.1.2 安装

> 支持以下安装方式，**选择其中一种**完成安装后，继续执行 3.1.3 启动服务。

---

**选项 A：快速安装（推荐）**

> **中国大陆用户**：默认推荐使用 Gitee 上的 `install_zh` 一键安装脚本；如果你希望先审查仓库内容，也可以先从 Gitee 克隆源码后再安装，见「选项 B：源码安装」。

macOS / Linux
```bash
curl -fsSL https://gitee.com/flocks/flocks/raw/main/install_zh.sh | bash
```
默认会在当前目录下创建 ./flocks

Windows PowerShell (Administrator)
```powershell
powershell -c "irm https://gitee.com/flocks/flocks/raw/main/install_zh.ps1 | iex"
```

---

**选项 B：源码安装**

克隆到本地后在工作区执行安装脚本：

```bash
git clone https://gitee.com/flocks/flocks.git flocks
cd flocks
```

macOS / Linux
```bash
sh ./scripts/install_zh.sh
```

Windows PowerShell (Administrator)
```powershell
powershell -ep Bypass -File .\scripts\install_zh.ps1
```


**选项 C: Windows 安装包（EXE，BETA）** 

Flocks 提供 **Windows x64** 下的 **Inno Setup 安装向导**（`.exe`）。请从 [GitHub Releases](https://github.com/AgentFlocks/flocks/releases) 页面下载对应版本的安装包。

| 平台 | 下载文件 |
| --- | --- |
| Windows (x64) | `FlocksSetup-<tag>.exe` |

安装完成后，可通过 **开始菜单** 或可选的 **桌面快捷方式** 启动；或在**新开**的终端中执行 `flocks start`，以便新的 `PATH` 等环境变量生效。更多说明见 [`packaging/README.md`](packaging/README.md)。


---

#### 3.1.3 启动服务

使用 `flocks` CLI 以守护进程方式同时管理后端与 WebUI。
`flocks start` 默认会先构建 WebUI 再启动；如果需要显式全量重启，请使用 `flocks restart`。

```bash
flocks start
flocks status
flocks logs
flocks restart
flocks stop
```

默认服务地址：
- Flocks WebUI：`http://127.0.0.1:5173`
- 远程访问可通过 `flocks start --host <ip>` 配置

更多 CLI 命令使用 `flocks --help`

### 3.2 方案 2：Docker 安装

> [!NOTE]
> docker 版本暂时 agent-browser headed 模式不可用

#### 3.2.1 拉取镜像

```bash
docker pull ghcr.io/agentflocks/flocks:latest
```

#### 3.2.2 启动服务

运行容器，并将宿主机用户的 `~/.flocks` 目录挂载到容器内：

macOS / Linux
```bash
docker run -d \
  --name flocks \
  -e TZ=Asia/Shanghai \
  -p 5173:5173 \
  --shm-size 4gb \
  -v "${HOME}/.flocks:/home/flocks/.flocks" \
  ghcr.io/agentflocks/flocks:latest
```

Windows PowerShell
```powershell
docker run -d `
  --name flocks `
  -e TZ=Asia/Shanghai `
  -p 5173:5173 `
  --shm-size 4gb `
  -v "${env:USERPROFILE}\.flocks:/home/flocks/.flocks" `
  ghcr.io/agentflocks/flocks:latest
```

镜像中的 `EXPOSE` 仅用于声明容器端口；要从宿主机浏览器访问服务，仍需使用 `-p 5173:5173` 映射端口。

## 4. 常见问题

### 4.1 中国用户：加速 Python 包安装

在中国大陆的机器上，可以将 `uv` 配置为使用本地 PyPI 镜像，以加快包下载。

创建 `~/.config/uv/uv.toml`，内容如下：

```toml
[[index]]
url = "https://pypi.tuna.tsinghua.edu.cn/simple"

[[index]]
url = "https://pypi.org/simple"
default = true
```

### 4.2 Docker 问题

Docker 国内镜像地址

- 1ms GHCR：`docker pull ghcr.1ms.run/agentflocks/flocks:latest`
- dockerproxy GHCR：`docker pull ghcr.dockerproxy.net/agentflocks/flocks:latest`
- gh-proxy prefix：`docker pull docker.gh-proxy.com/ghcr.io/agentflocks/flocks:latest`
- milu GHCR：`docker pull ghcr.milu.moe/agentflocks/flocks:latest`
- NJU GHCR：`docker pull ghcr.nju.edu.cn/agentflocks/flocks:latest`

启动后 `/home/flocks/.flocks` 权限问题

``` bash
-v "$HOME/.flocks:/home/flocks/.flocks:Z" \
```
或
```bash
docker run --rm --entrypoint id ghcr.io/agentflocks/flocks
# example result: uid=1001(flocks) gid=1001(flocks) 组=1001(flocks)
sudo chown -R <uid>:<gid> ~/.flocks
# example: sudo chown -R 1001:1001 ~/.flocks
```

### 4.3 远程访问 Flocks 服务
```bash
flocks start --host 0.0.0.0
```
若从虚拟机远程访问失败，请将 host 指定为虚拟机的 IP。

WebUI 与 API 共用同一个服务地址和端口，API 请求使用同源 `/api` 路径。这样浏览器 Cookie 与 SSE 保持在同一源，适用于局域网访问与反向代理场景。

仅在确实需要浏览器直连后端 URL 时，再显式启用：

```bash
FLOCKS_WEBUI_DIRECT_BACKEND_URLS=1 \
flocks start --server-host 0.0.0.0 --webui-host 0.0.0.0
```

### 4.4 鉴权与 API Token

启用本地账号体系后，所有 HTTP 路径默认要求鉴权，仅以下路径放行：WebUI 引导页（`/`、`/auth/*`）、静态资源、以及 IM 平台 webhook 回调（`/api/channel/{channel_id}/webhook`）。

初次部署：

1. 打开 WebUI，按提示完成 **bootstrap-admin**，创建唯一的 `admin` 账号。
2. WebUI 会自动写入 `flocks_session` Cookie，浏览器侧无需额外配置。

非浏览器客户端（TUI / SDK / 脚本）：

- 所有非浏览器客户端（包括本机回环调用）都必须携带 API Token。Token 存放于 `~/.flocks/config/.secret.json`，secret id 为 `server_api_token`。

  在 **服务端** 生成（或轮换）token，会持久化到服务端本机的 secret store：

  ```bash
  flocks admin generate-api-token        # 打印 token 并写入 server_api_token
  ```

  在 **每台远程客户端** 上把同一个 token 写入客户端自己的 secret 文件，让 SDK / TUI 自动携带：

  ```bash
  flocks admin set-api-token --token <服务端打印的 token>
  ```

  也可以按请求显式携带任一 Header：

  ```text
  Authorization: Bearer <token>
  X-Flocks-API-Token: <token>
  ```

  快速验证：

  ```bash
  curl -H "Authorization: Bearer <token>" https://flocks.example.com/api/health
  ```

  查询指定会话的实时运行状态：

  ```bash
  curl -H "Authorization: Bearer <token>" \
    https://flocks.example.com/api/session/<sessionID>/status
  ```

  `status.type` 可能为 `idle`、`queued`、`busy`、`retry`、`compacting` 或
  `dreaming`。自动化调用可通过 `isProcessing` 判断是否仍有已接收的工作正在执行或等待执行；
  `idle` 只表示当前空闲，不代表上一次执行成功。

反向代理部署：

- 反代必须主动注入 `X-Forwarded-For`。若缺失该头，且前方存在代理，中间件会拒绝信任回环地址，避免任何直连本机的请求被自动提升为 `admin`。
- 若反代终止 HTTPS，请同时透传 `X-Forwarded-Proto: https`，以便服务端正确设置安全 Cookie 标志。
- 浏览器流量优先使用同源反代：WebUI 保持在 `/`，后端流量经 `/api`（必要时还有 `/event`）转发。除非有意让浏览器绕过代理直连后端源站，否则不要在反向代理部署中设置 `VITE_API_BASE_URL`。
- 对于 SSE 端点，请关闭代理缓冲并保持 HTTP/1.1 启用。

忘记密码 / 应急恢复：

- 在宿主机上执行 `flocks admin generate-one-time-password`。`admin` 账号会被强制置为 `must_reset_password=true`；下次 WebUI 登录会跳转到改密页。**此状态下所有非浏览器端点均返回 403**，若该账号被自动化依赖，请先协调后再执行。

无主 session（CLI / 后台任务 / inbound 渠道）：

- 在无鉴权上下文中创建的 session（CLI 命令、后台任务、IM 渠道入站 dispatcher）`owner_user_id` 为空。bootstrap admin 仍可见，但之后新增的 member 账号不可见。可用以下命令回填归属：

  ```bash
  flocks admin reassign-orphan-sessions --username admin --dry-run   # 预览
  flocks admin reassign-orphan-sessions --username admin             # 实际写入
  ```

  命令会汇总 `scanned / orphaned / reassigned / failed` 计数；`failed` 非零时以退出码 2 结束，便于 CI / 脚本发现部分写入并在修复底层原因（通常为临时存储错误）后重试。

### 4.5 出站代理（HTTP / HTTPS / SOCKS）与直连白名单

后端所有出站请求——模型接口、IM 平台（Slack、Telegram、飞书、钉钉、企微、微信）、插件广场、更新检查、
远端 MCP、网页抓取 / 搜索、无头 Chrome，以及 `git` / `uv` / `npm` 这类子进程——都走同一套策略。
可以在 WebUI 的 **设置 → 网络代理**（仅管理员）里配，也可以写 `flocks.json` 或用环境变量：

```jsonc
"network": {
  "proxy": {
    "mode": "manual",                      // off | system（默认）| manual
    "http": "http://127.0.0.1:7890",       // http:// 目标
    "https": "socks5h://127.0.0.1:7891",   // https:// 与 wss:// 目标
    "all": "",                             // http / https 留空时的兜底
    "username": "",
    "password": "{secret:network_proxy_password}",   // 由密钥管理保存，不落明文
    "bypass": ["*.corp.example", "10.0.0.0/8", "gitlab.internal:8443"],
    "bypassPrivate": true
  }
}
```

- `system` 完全跟随启动时导出的 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `NO_PROXY`；`manual` 用上面填的地址，
  并同时写进进程环境，信任环境变量的库和子进程看到的是同一份；`off` 隐藏这些变量、全部直连。
- 支持的 scheme：`http`、`https`、`socks5`、`socks5h`（`socks5h` 由代理解析域名）；地址不带端口时按 80 / 443 / 1080 补。
- 白名单规则（在 DNS 解析之前判断）：`*.corp.example` / `.corp.example`（域名及其子域）、`host`、`host:port`、IP、
  CIDR（`10.0.0.0/8`、`fd00::/8`）、`<local>`（不带点的主机名）、`*`（全部直连）。`bypassPrivate` 打开时，
  链路本地、RFC1918、IPv6 ULA 以及 `*.local` / `*.internal` / `*.lan` / `*.home.arpa` 不用写也直连。
  `localhost` / `127.0.0.0/8` / `::1` 任何时候都直连（确实要代理回环地址时在列表里加 `<-loopback>`）。
- 给 systemd 单元和容器用的环境变量覆盖：`FLOCKS_PROXY_MODE`、`FLOCKS_PROXY_URL`（同时作用于 http / https / all）、
  `FLOCKS_PROXY_BYPASS`（逗号分隔）。这一段只从用户级 `flocks.json` 读（`FLOCKS_CONFIG` 指向的文件和 `FLOCKS_CONFIG_CONTENT` 里写了不生效）。
- `POST /api/config/network/proxy/test`（页面上的 **测试** 按钮）会告诉你某个地址是直连还是走了哪个代理，附 HTTP 状态和耗时。

已知边界：只认环境变量的库（litellm、lark-oapi、python-telegram-bot、`git`、`pip`）对 `NO_PROXY` 只按主机名 / 后缀匹配，不认 CIDR，
经它们访问的内网地址要把主机名（或 `.后缀`）写进白名单；flocks 自己的客户端在 DNS 之前按 CIDR 和端口规则判。WhatsApp 桥接的 websocket 和媒体上传按策略走（http、socks 都行），但媒体*下载*用的是 Node 原生 `fetch`，
只有 Node ≥ 24 才认 `HTTP_PROXY`，且只支持 http 代理——策略只配了 SOCKS 时下载会直连。TUI（Bun）同理：
认 `HTTP_PROXY` / `NO_PROXY` 但遇到 `socks://` 直接报错，所以启动它时不传 SOCKS 条目。
无头 Chrome 只接受不带账号密码的代理（`--proxy-server` 没有鉴权）。

## 5. 加入社区

请使用**微信**扫描下方二维码，加入官方交流群。  

![企业微信官方交流群二维码](assets/community-wecom-qr.png)

## 6. 参与贡献

开发环境、代码规范、测试要求和 Pull Request 流程请参考 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

## 7. 开源协议

Apache License 2.0
