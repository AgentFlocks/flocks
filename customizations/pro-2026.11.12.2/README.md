# Flocks Pro 2026.11.12.2 定制增强包

增强包版本：1.14.1。仅适用于 `2026.11.12.2-linux-amd64`、Pro 构建 `2ee622b`、Core `730da689`，运行树保护策略必须为 `native`。

## 开发与部署方式

增强直接合入 `zw_dev` 的 Core 源码，以本发行版配对的 Core `730da689` 为上游基线。打包时根据该基线与增强提交之间的差异，收集 Python 源码、内置组件和配对的 Pro 前端产物。

本包源码提交：[`538ee022`](https://github.com/AgentFlocks/flocksdev/commit/538ee022defd7ef5b5969aaed7d6ac02037a5b58)，为原 `zw_dev` 与发行版基线的普通合并，保留开发历史。

交付形式仍是独立增强包。解压到 `~/.flocks/customizations/` 后，安装器校验并备份原文件，然后直接修改 `~/flockspro/current/runtime/flocks/` 中实际运行的 Core 源码，以及 `runtime/webui-dist/` 中的界面文件。`customizations` 用于保存安装器、部署文件和备份；服务启动时加载的是受管发布中的增强源码。

保留原有 `flocks` 命令、Pro Go 监督器、原生模块和 PostgreSQL。Pro 接管的存储及任务模块通过随源码部署的适配层扩展，保留原执行租约和代次校验；不能只覆盖同名 SQLite 模块。此方式不需要降级或退出 Pro。

## 安装

推荐把 `update-flocks.py` 放到服务器使用账号的家目录。首次安装和后续更新始终运行同一条命令：

```bash
python3 "$HOME/update-flocks.py"
```

脚本使用 Python 3 和标准 `curl` 客户端下载，不需要 Git、npm、uv 或额外的 Python 包。

脚本从固定的 Gitee 分支压缩包地址下载仓库 ZIP：

```text
https://gitee.com/flocks/flocks/repository/archive/fix/process_11.12_en
```

它通过仓库中的 `delivery.json` 自动选择匹配当前 Pro 发行版的最新补丁，校验内嵌压缩包、源码和界面文件，再部署到 `~/.flocks/customizations/`。不需要手动下载新包、修改版本号、解压或先还原旧补丁。下载与预检在停服务之前完成；相同补丁校验通过后直接退出，不重启服务。镜像版本早于已安装版本时保留当前补丁。

请使用安装和运行 Pro 的系统账号执行；账号名为 `pro` 也适用，脚本从受管 `flocks` 入口识别该账号的安装目录并保留用户服务参数。WebUI 中已登录 Pro 账号无需退出。脚本不更换账号、授权或 PostgreSQL，不自动提权。自定义安装位置可使用 `--pro-root /实际目录`。默认启动参数与原命令一致，为 `--no-browser --webui-host 0.0.0.0`；需要保留本地监听时可传 `--webui-host 127.0.0.1`。

已装补丁 A 时，脚本核对 A 的部署记录和当前文件，再直接写入补丁 B。B 删除的旧补丁文件会清理，不再修改的原版文件恢复发行基线；保留配置、状态及历史备份。部署失败自动退回 A；B 启动失败时也会回退本次代码更新，并尝试重新启动此前运行的服务。未知手工源码改动会被保留并报告。自动升级以补丁声明的 Pro 发行基线为准。

若继续使用手工压缩包方式，把 `flocks-customizations-1.14.1-pro-2026.11.12.2-full.tar.gz` 上传到服务器家目录，再整行执行：

```bash
flocks stop && mkdir -p "$HOME/.flocks/customizations" && tar -xzf "$HOME/flocks-customizations-1.14.1-pro-2026.11.12.2-full.tar.gz" -C "$HOME/.flocks/customizations" && "$HOME/.flocks/customizations/home-dashboard/apply.sh" --pro-root "$HOME/flockspro" apply --install-deps && flocks start --no-browser --webui-host 0.0.0.0
```

解压目录和安装器都在 `~/.flocks/customizations/`，不需要额外的家目录工作文件夹。补丁目标是当前受管 Pro 发布；旧的 `$HOME/flocks` 不参与安装。

前端已经使用原版 Pro 扩展注册表编译。`--install-deps` 保留原命令习惯，本包不需要在服务器上运行 npm、uv 或修改 Pro 的 Python 依赖环境。

若已安装的告警运营套件在“添加场景”中显示“有更新”，点击更新，以将该套件的已安装页面同步到新版统一存储接口。安全运营监测使用原生监测页面，`/report`、`/reports` 都进入报告页。

## 内容与存储

保留原有首页、中文品牌文案、知识分类和经验总结、工作流统计、七个定制 SOC 工作流、安全运营监测及邮件闭环，并包含定时任务详情滚动和每日报告路由修复。

监测数据通过 Pro 原有 Go 数据库入口写入 PostgreSQL；普通任务和监测任务继续使用 Pro 的执行租约、代次校验和权限检查。旧的 SQLite 连接锁修复不覆盖 Pro 任务引擎。Go 程序、原生扩展、授权文件、签名清单和 Python 解释器保持原样。界面来自精确配对的 Core 和 Pro 源码，不覆盖成普通开源版界面。

安装器只替换代码和界面文件，不修改现有数据库、设备配置、账号和授权。已有增强包的配置、状态和备份目录保留。历史 SQLite 监测数据不会被当作当前 PostgreSQL 数据使用。

## 校验与还原

安装前后均调用原版 Pro 的 `upgrade verify`。未知源码改动、错误版本、错误界面配对、符号链接冲突或 `all` 保护策略都会中止安装。安装记录和原文件备份保存在本目录的 `state/`、`backups/`。

```bash
"$HOME/.flocks/customizations/home-dashboard/apply.sh" --pro-root "$HOME/flockspro" status
```

`status` 显示实际源码、界面和 Pro 入口路径。部署清单记录 `zw_dev` 提交以及每个文件的安装前后哈希；可使用 `inspect` 输出文件校验明细：

```bash
"$HOME/.flocks/customizations/home-dashboard/apply.sh" --pro-root "$HOME/flockspro" inspect
```

如需撤销本包：

```bash
flocks stop && "$HOME/.flocks/customizations/home-dashboard/apply.sh" --pro-root "$HOME/flockspro" restore && flocks start --no-browser --webui-host 0.0.0.0
```

还原恢复未增强的代码和原 Pro 界面，保留业务数据与备份。若安装后又手工改过文件，还原会停止并保留改动；不会强制覆盖。安装被意外中断且自动恢复未完成时，服务保持停止，保留备份；使用 `rollback-update` 退回上一次补丁，或使用 `restore` 回到未增强原版，再重新运行更新脚本。正常 A→B 更新无需手动执行这两个命令。

## 开发与验证

`target-profile.json` 固定发行基线和源码配对。`zw_dev` 保存合入后的增强源码；`patches/core-11122.patch` 保存相对发行基线的可复现差异，`runtime/` 保存 Pro 存储适配代码及随包分发的 SQLGlot（MIT 许可）副本。打包时核对这些副本与分支源码一致，从干净的增强提交及配对的 Pro 前端产物生成增强包和 SHA-256 校验文件，并在部署清单中记录分支、提交和基线。

数据库回归使用隔离 PostgreSQL、真实测试版 Go 数据库入口和实际 Pro 2026.11.12.2 Python 原生模块，不连接用户业务数据库。安装器回归验证重复安装、文件冲突、失败回滚、权限恢复和还原。前端使用锁定依赖执行测试与生产构建。验证记录随交付保存，远程 Linux 机器仍需按上面的安装命令完成实际验收。

`prepare_sources.py --core <新源码目录> --pro <新Pro目录>` 按清单检出固定的 `zw_dev` 提交与 Pro 源码；不会在旧目录叠加修改。`build_package.py` 默认从 `.work/zw_dev` 构建，拒绝未提交改动或过期前端输入。`run_tests.py` 也默认测试该分支，可用 `FLOCKS_TEST_CORE_SOURCE` 指定另一个已准备的源码目录。

## 本次交付验收范围

本次按候选包交付。既有增强的前端 2968 项、原 SQLite 兼容回归 1574 项通过；PostgreSQL 已知失败经定点复核清零，完整运行和定点复核的对应关系、10 项测试夹具限制见 `pg-verification-summary.json`，没有累计重复用例冒称完整单轮全绿。12 并发会话预约、6 并发结束卡及中断恢复均已验证。提交到 `zw_dev` 后另通过源码更新保护 14 项及原生 PostgreSQL 冒烟 7 项，并逐文件核对既有运行源码与增强代码一致。

1.14.1 的安装器与一键更新脚本共 40 项回归通过，包括 A→B→C、旧安装记录迁移、删除旧文件、异常回滚、监听地址恢复、重复运行及镜像版本防倒退。Gitee 地址已通过真实匿名下载验证；5173 现有 Pro 实例已实际执行旧 1.14.0 首装、直接升级 1.14.1、重复运行不重启和全量还原。验收中使用适配本机架构的离线仓库快照，并以仅注释不同的 A 版源码确认 B 确实覆盖了 A 的文件。

5173 现有 Pro 实例已实际加载同源码增强包，验证首页统计、安全监测套件入口、成功和失败任务详情双向滚动，以及安装、重复安装、还原；最终源码部署复核还确认原 Pro Python 入口加载 `current/runtime/flocks/`，所有部署文件哈希匹配，启动包装脚本未改变；验收后已恢复原版。该实例原有安全监测组件不完整，因此没有自动补全或启用新监测任务，完整报告页与真实监测链路仍待验收。Linux 目标服务器也尚未实机部署。详细记录见 `verification.json`。
