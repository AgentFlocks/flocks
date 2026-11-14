# Flocks Pro 2026.11.13 定制增强包

增强包版本：**1.15.0**。仅适用于 `2026.11.13-linux-amd64`、Pro `c16ed13499b3ca721e3549893d5507c506bc2c58`、Core `2bc02382874a226ef63d20cab3734220ad752225`，运行树保护策略必须为 `native`。

## 本次迁移

将 1.14.10 的全部增强合并到官方 11.13 配对 Core，保留新版上游修复。增强源码为 [f6ab71f1](https://github.com/AgentFlocks/flocksdev/commit/f6ab71f148367a83bf4c3d535dcbaf34f559317f)，开发分支 `codex/pro-1113-enhancements`；可复现差异保存在 `patches/core-1113.patch`。

保留以下能力：

- 首页与中文品牌文案、数字员工文案、知识分类、经验总结和工作流调用统计。
- 七个定制 SOC 工作流、安全运营监测、多 XDR 配置、模型恢复、邮件闭环与脱敏诊断。
- 监测流式对话、零事件结束、三色点缀、任务中心监测失败过滤和简洁错误详情。
- SOC 默认告警态势、执行记录默认展开、并行批次展示和页面会话缓存。
- Syslog ISO 时间头解析、定时任务详情滚动、报告路由与最近十天历史会话。
- Agent车间仅显示知识沉淀入口；保留知识库、记忆的直接路由与数据。
- 场景管理未开放 SOC 入口隐藏；设备巡检和情报分析两个 Agent 保持隐藏。

配对 Pro 前端使用原版扩展注册表和锁定依赖构建。Pro 原生任务、会话及工作流存储仍由发行版接管，增强通过适配层加载；保留 PostgreSQL 执行租约、代次校验、权限与原有 SQLite 回退，不迁移业务数据库。

## 安装与更新

交付仓库固定为 `AgentFlocks/flocks` 的 `fix/process_11.12_en` 分支。用户将该分支同步到 Gitee 的同名分支后，在远程机器原有 `update-flocks.sh` 所在目录执行：

```bash
bash update-flocks.sh
```

脚本下载固定 Gitee 分支 ZIP，根据 `delivery.json` 自动选择与当前 Pro 发行版匹配的补丁。无需修改下载地址、手动解压或先还原旧补丁。更新脚本不会把 11.12.2 基础发行版升级到 11.13；仓库仍保留旧版配套补丁。

请使用安装和运行 Pro 的系统账号执行。自定义安装位置沿用 `--pro-root`。下载、校验与预检在停服务前完成；相同包重复执行不重启，镜像旧版本不覆盖新补丁。未知手工修改会拒绝覆盖，失败自动回滚。

安装器修改当前受管发布中的 `runtime/flocks/`、`runtime/webui-dist/` 及配对 Pro SOC 文本模板；配置、账号、设备、授权、数据库、历史备份保持原样。Go 入口、Python、原生扩展和签名清单不覆盖。安装前后调用原版 `upgrade verify`；错误版本、符号链接冲突、未知改动、`all` 保护策略继续拒绝安装。

本次无需更新已安装的整个告警运营套件；既有用户插件不被直接覆盖。需要更新插件页面时，沿用待加载插件库的单项更新和备份流程。

## 查询与还原

```bash
"$HOME/.flocks/customizations/home-dashboard/apply.sh" --pro-root "$HOME/flockspro" status
"$HOME/.flocks/customizations/home-dashboard/apply.sh" --pro-root "$HOME/flockspro" inspect
```

正常更新无需手动还原。明确需要撤销时：

```bash
flocks stop && "$HOME/.flocks/customizations/home-dashboard/apply.sh" --pro-root "$HOME/flockspro" restore && flocks start --no-browser --webui-host 0.0.0.0
```

还原保留业务数据；安装后手工修改的代码不会被强制覆盖。

## 源码准备与打包

从本目录运行，选择两个尚不存在的源码目录：

```bash
python3 prepare_sources.py --core .work/core-1113 --pro .work/pro-1113
cd .work/core-1113/webui && npm ci && cd ../../..
FLOCKS_WEBUI_SRC="$PWD/.work/core-1113/webui" python3 .work/pro-1113/scripts/build_webui.py .work/pro-webui-candidate
python3 build_package.py --core .work/core-1113 --pro .work/pro-1113 --frontend .work/pro-webui-candidate
```

构建要求源码提交、Pro 注册表、输入哈希与生产前端清单一致，拒绝未提交改动。`runtime/` 与增强源码逐文件一致；SQLGlot 的 MIT 许可证随包保留。输出完整 tar.gz 与 SHA-256 文件。

## 验收边界

实际检查、代码及负载哈希见 `verification.json`。本机 5173 的基础发行仍是 11.12.2-darwin-arm64；本地验收使用临时本机基线清单和本包相同业务源码、前端，不替换原生文件。此验收与 Linux 11.13 原版安装器隔离验证分别记录，不将本机功能验证称为 Linux 实机验证。

远程服务器不由智能体部署。上传后仍需用户同步 Gitee、执行更新脚本并回传结果；真实 XDR、模型服务和邮件收发需远程业务验收。
