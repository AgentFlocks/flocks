# Flocks 网络与离线安装指南

## 摘要
本文整理中国大陆网络环境、镜像源配置、Docker 镜像替代地址以及离线安装的现实边界。当前最成熟的做法是：国内优先使用 Gitee 安装入口、为 `uv` 配置镜像源、Docker 场景优先使用国内镜像；完整离线安装流程目前并没有在官方文档中给出成体系说明。

## 适用场景
- GitHub 或 `raw.githubusercontent.com` 无法访问
- Python 或前端依赖下载很慢
- 需要在受限网络或半离线环境中部署
- 希望了解当前是否支持真正的离线安装

## 国内网络环境的推荐做法
### 1. 优先使用 Gitee 安装入口
对于中国大陆环境，中文 README 已明确推荐使用 Gitee 的 `install_zh` 脚本。

### 2. 配置 `uv` 镜像源
README 给出的推荐配置如下：

```toml
[[index]]
url = "https://pypi.tuna.tsinghua.edu.cn/simple"

[[index]]
url = "https://pypi.org/simple"
default = true
```

保存到：

```text
~/.config/uv/uv.toml
```

### 3. Docker 使用国内镜像
README 中给出的镜像地址为：

```text
ghcr.nju.edu.cn/agentflocks/flocks:latest
```

## 什么时候会表现为网络问题
常见症状包括：
- 一键安装卡在下载依赖阶段
- `npm` 或前端构建失败
- 安装时间远超预期
- 同样的命令在不同网络环境成功率差异很大

## 离线安装的现实情况
根据现有 README 和群聊记录，官方目前没有提供一份完整、可直接执行的离线安装文档。

这意味着：
- 完全离线环境下的部署仍需要你自己提前准备依赖和安装材料
- 更现实的方案通常是“受限网络安装”而不是“完全离线安装”

## 受限网络环境的过渡方案
- 提前在可联网环境拉取源码包或 Git 仓库
- 提前准备 Docker 镜像
- 提前配置内网可用的 PyPI / npm 镜像
- 优先采用源码安装或容器方式，减少动态依赖下载的不确定性


## 常见误区
### 有 Docker 镜像就等于完全离线
不一定。镜像能解决服务本体问题，但如果你的部署、升级或扩展流程依赖外部资源，仍然可能需要额外准备。

### 只配 Python 镜像就够了
Flocks 还依赖前端构建链路，因此 npm 相关下载也可能成为瓶颈。

## 相关文档
- [安装与快速开始指南](./01-installation-quick-start-guide.md)
- [安装环境准备指南](./05-installation-environment-guide.md)
- [安装失败排查指南](./07-installation-troubleshooting-guide.md)
- [远程部署指南](./09-remote-deployment-guide.md)
