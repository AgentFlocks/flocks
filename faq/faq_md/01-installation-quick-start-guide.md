# Flocks 安装与快速开始指南

## 摘要
本文面向第一次接触 Flocks 的用户，给出从安装方式选择、执行安装命令到首次启动服务的最短路径。推荐优先使用终端安装；中国大陆环境优先使用 Gitee 安装入口；安装完成后使用 `flocks start`、`flocks status`、`flocks logs` 验证服务是否正常。

## 适用场景
- 第一次安装 Flocks
- 不确定应该使用一键安装、源码安装还是 Docker 安装
- 已经执行过安装脚本，但不知道后续该做什么

## 推荐安装路径
### 方案一：终端安装
这是 README 中的推荐方式，适合需要完整 WebUI、本机浏览器能力和后续调试排障的场景。

中国大陆环境建议优先使用 Gitee 安装脚本：

```bash
curl -fsSL https://gitee.com/flocks/flocks/raw/main/install_zh.sh | bash
```

如果需要同时安装 TUI 依赖：

```bash
curl -fsSL https://gitee.com/flocks/flocks/raw/main/install_zh.sh | bash -s -- --with-tui
```

国际网络环境可使用 GitHub 安装入口：

```bash
curl -fsSL https://raw.githubusercontent.com/AgentFlocks/flocks/main/install.sh | bash
```

Windows 建议以管理员 PowerShell 执行：

```powershell
powershell -c "irm https://gitee.com/flocks/flocks/raw/main/install_zh.ps1 | iex"
```

### 方案二：源码安装
如果你希望先查看源码、网络环境对一键脚本不友好，或者后续需要自己排查升级问题，更适合源码安装。

```bash
git clone https://gitee.com/flocks/flocks.git flocks
cd flocks
./scripts/install.sh
```

Windows：

```powershell
powershell -ep Bypass -File .\scripts\install.ps1
```

### 方案三：Docker 安装
适合快速起服务或隔离运行环境，但不适合依赖本机交互式浏览器登录的使用场景。

```bash
docker pull ghcr.io/agentflocks/flocks:latest
docker run -d \
  --name flocks \
  -e TZ=Asia/Shanghai \
  -p 8000:8000 \
  -p 5173:5173 \
  --shm-size 2gb \
  -v "${HOME}/.flocks:/home/flocks/.flocks" \
  ghcr.io/agentflocks/flocks:latest
```

## 安装完成后的第一步
安装成功后，不要直接假设服务已经启动。标准做法是：

```bash
flocks start
flocks status
flocks logs
```

默认访问地址为：
- WebUI：`http://127.0.0.1:5173`
- 后端 API：`http://127.0.0.1:8000`

## 首次快速检查清单
- `flocks` 命令可以正常执行
- `flocks status` 显示服务已启动
- 浏览器可以打开 `http://127.0.0.1:5173`
- 页面加载后可以进入模型配置或新手引导

## 频出现的问题
- 安装完成后不知道下一步做什么，通常是没有执行 `flocks start`
- Windows 环境下一键安装失败，社区中更常建议改为源码安装或 Docker 安装
- 中国大陆访问 GitHub 慢，使用 Gitee 安装入口成功率更高
- Docker 用户容易忽略端口映射和目录挂载，导致页面打不开或结果文件找不到

## 常见错误与排查
### 安装脚本执行完了，但 `flocks` 命令不可用
优先怀疑安装没有真正完成，尤其是浏览器依赖、Node.js 或后端安装步骤中断。建议改用源码安装重新执行。

### 页面能打开，但功能不完整
这通常不是安装本身的问题，而是还没有完成默认模型配置。安装后应继续完成首次配置。

### Docker 已启动但浏览器能力不可用
这是已知限制。README 明确说明 Docker 模式下 `agent-browser` 的 headed 模式暂不可用。

## 相关文档
- [服务启动与访问指南](./02-service-start-and-access-guide.md)
- [安装环境准备指南](./05-installation-environment-guide.md)
- [安装方式选择指南](./06-installation-mode-selection-guide.md)
- [安装失败排查指南](./07-installation-troubleshooting-guide.md)
