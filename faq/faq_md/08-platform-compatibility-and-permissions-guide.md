# Flocks 平台兼容与权限指南

## 摘要
本文整理 Flocks 在 Windows、Linux、macOS、WSL 和 ARM 等不同平台上的常见兼容性差异，以及安装和运行时最容易踩到的权限问题。整体经验是：Linux 和 macOS 通常更稳定；Windows 更依赖管理员权限且升级问题更多；WSL 和 ARM 需要额外谨慎。

## 适用场景
- 想确认当前平台是否适合安装 Flocks
- Windows 安装报错或命令不可用
- WSL / ARM 环境下不确定是否值得继续排障

## Windows 的注意事项
### 优先考虑管理员权限
README 中已经明确 Windows PowerShell 安装应以管理员身份执行。群聊中也多次出现“非管理员安装后 CLI 不可用或运行异常”的情况。

## Linux 和 macOS 的特点
- 与 README 的主流程最一致
- 终端安装和源码安装成功率通常更高
- 页面升级在大多数版本上更稳定

## WSL / WSL2 的特点
群聊中的典型问题是：
- 更新流程可能误调用 Windows 侧 Node.js
- 支持度不如原生 Linux 稳定

如果你在 WSL 中频繁遇到安装、更新或 Node 环境问题，建议直接切换到原生 Linux、Windows Docker 或源码安装。


## 权限相关的常见问题
### 安装目录权限
如果安装目录、用户主目录或挂载目录权限不正确，可能导致：
- 安装过程写入失败
- `~/.flocks` 中日志和配置文件异常
- Docker 挂载目录权限错误

### Docker 挂载权限
两类处理方式：

- 使用 `:Z`
- 用容器内 uid/gid 对宿主机 `~/.flocks` 执行 `chown`


## 推荐策略
- Windows：管理员 PowerShell，优先源码安装或 Docker
- Linux / macOS：优先终端安装或源码安装
- WSL：仅在你能接受额外排障成本时使用
- ARM：优先 Docker

## 相关文档
- [安装方式选择指南](./06-installation-mode-selection-guide.md)
- [安装失败排查指南](./07-installation-troubleshooting-guide.md)
- [升级方式指南](./11-upgrade-methods-guide.md)
- [升级失败排查指南](./12-upgrade-troubleshooting-guide.md)
