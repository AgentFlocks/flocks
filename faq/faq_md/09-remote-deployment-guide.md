# Flocks 远程部署指南

## 摘要
本文整理 Flocks 从“仅本机访问”切换到“局域网、虚拟机、云服务器或 Docker 远程访问”时的标准做法。核心原则是：默认只监听本机；远程访问通常只建议对外开放 WebUI；监听地址改对以后，还要继续检查防火墙、安全组、端口映射和 WebUI 实际请求的后端地址。

## 适用场景
- 希望局域网其他机器访问 Flocks
- 把 Flocks 部署在云服务器或远程 Linux 主机
- 在虚拟机中部署，宿主机需要访问
- 使用 Docker 部署并对外提供 WebUI

## 默认行为先说明
默认命令：

```bash
flocks start
```

默认监听：
- 后端 API：`127.0.0.1:8000`
- WebUI：`127.0.0.1:5173`

这意味着默认只允许当前机器访问，外部设备无法直接访问 WebUI。

## 推荐的远程部署方式
### 只开放 WebUI
这是最常见也最稳妥的做法：

```bash
flocks start --server-host 127.0.0.1 --webui-host 0.0.0.0
```

这样做的好处是：
- 后端 API 仍只对部署机本机开放
- WebUI 可以被外部浏览器访问
- 相比把前后端都暴露到公网，风险更低

### 同时开放 API 和 WebUI
只有在确实需要外部系统直接调用 API 时才建议使用：

```bash
flocks start --server-host 0.0.0.0 --webui-host 0.0.0.0
```

这会明显增加暴露面，不建议直接裸露到公网。

## 云服务器远程访问
如果你在远程 Linux 服务器上部署，推荐先按“只开放 WebUI”的方式启动，然后从本地浏览器访问：

```text
http://<远程机器IP>:5173
```

如果需要域名访问，可配合：

```bash
__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS=<your_domain> \
flocks start --server-host 127.0.0.1 --webui-host 0.0.0.0
```

## Docker 远程部署
标准命令示例：

```bash
docker run -d \
  --name flocks \
  -e TZ=Asia/Shanghai \
  -p 8000:8000 \
  -p 5173:5173 \
  --shm-size 2gb \
  -v "${HOME}/.flocks:/home/flocks/.flocks" \
  ghcr.io/agentflocks/flocks:latest
```

注意：
- `EXPOSE` 不是对外开放
- 必须显式写 `-p`
- 改成 `-p 8080:5173` 后，就要访问 `http://<宿主机IP>:8080`

## 虚拟机部署
虚拟机里最常见的问题不是 Flocks 启动失败，而是网络模型不对。

推荐先试：

```bash
flocks start --server-host 127.0.0.1 --webui-host 0.0.0.0
```

如果宿主机仍打不开，可尝试直接绑定虚拟机实际 IP：

```bash
flocks start --server-host 127.0.0.1 --webui-host <虚拟机IP>
```

## 最常见的排查顺序
1. 检查启动参数是否仍是 `127.0.0.1`
2. 检查浏览器实际访问的地址和端口
3. 检查 WebUI 实际请求的后端地址
4. 检查防火墙、安全组、端口映射
5. 检查虚拟机是 NAT、桥接还是 Host-Only

## 风险提示
- `0.0.0.0` 代表监听所有网卡
- 对公网开放 WebUI 或 API 前，建议至少做好防火墙白名单
- 若必须开放 API，优先只对可信网段开放

## 相关文档
- [服务启动与访问指南](./02-service-start-and-access-guide.md)
- [网络与离线安装指南](./10-network-and-offline-installation-guide.md)
- [浏览器自动化与网页登录指南](./14-browser-automation-and-login-guide.md)
- [remote-access-deployment-guide.md](./remote-access-deployment-guide.md)
