# Flocks 升级方式指南

## 摘要
本文说明 Flocks 常见的升级路径，包括页面一键升级、源码手动升级和 Docker 重新拉镜像升级。总体经验是：Linux 和 macOS 在多数版本上可以页面升级；Windows 更常需要手动升级；Docker 部署则以重新拉取镜像和重建容器为主。

## 适用场景
- 想升级到最新版本但不知道应该点页面升级还是手动升级
- 正在使用 Docker，不确定升级方式
- Windows 环境下担心升级不稳定

## 三种升级方式
### 页面一键升级
适合：
- 当前版本支持页面升级
- 平台为 Linux 或 macOS
- 运行环境和安装方式比较标准

优点：
- 操作最直接
- 对普通用户最友好

注意：
- 某些历史版本升级存在已知问题，不能一概而论

### 源码手动升级
适合：
- Windows 环境
- 页面升级失败
- 当前版本存在已知升级兼容问题
- 需要确认升级过程中的每一步

常见步骤：

```bash
flocks stop
git pull
./scripts/install.sh
flocks restart
```

Windows 对应使用 `install.ps1`。

### Docker 升级
适合：
- Docker 部署用户

基本思路是：
- 重新拉取最新镜像
- 重建或重启容器


## 如何选择
- 你是 Docker 用户：优先镜像升级
- 你是 Windows 用户：优先手动升级
- 你是 Linux / macOS 用户且当前版本没有已知问题：可先尝试页面升级
- 页面升级失败：改为源码手动升级

## 相关文档
- [升级失败排查指南](./12-upgrade-troubleshooting-guide.md)
- [升级后异常处理指南](./13-post-upgrade-issues-guide.md)
- [平台兼容与权限指南](./08-platform-compatibility-and-permissions-guide.md)
