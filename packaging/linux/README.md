# Linux 离线安装包（flocks-offline.run）

给不能联网的 CentOS Stream 9 / RHEL 9 系机器用的单文件安装包。客户只需要 `scp` 上去、`sudo bash /var/tmp/flocks-offline.run`，装完浏览器开 `http://服务器IP:5173`。包里带齐 Python 3.12、Node.js、uv、预建好的 `.venv`、构建好的 WebUI，可选再带一份 Pro bundle；安装过程和之后的 Pro 升级都不需要 PyPI、npm、GitHub。

这套东西和 `install.sh` / Docker / Windows 包互不影响：装到机器上的形态还是"仓库根 + `.venv` + `webui/dist`"，只是放在 `/opt/flocks/flocks`、以 `flocks` 用户跑、由 `/etc/flocks/flocks.env` 提供环境变量。代码里为它加的逻辑全部由 `FLOCKS_PRO_BUNDLE_DIR`、`FLOCKS_UPDATE_CHANNEL` 和 bundle manifest 的 `prebuilt: true` 触发，这三样只有这个安装包会设置。

## 内容

- [文件](#文件)
- [怎么构建](#怎么构建)
- [装到机器上是什么样](#装到机器上是什么样)
- [旧数据怎么带过来](#旧数据怎么带过来)
- [Pro 升级在离线机器上怎么走](#pro-升级在离线机器上怎么走)
- [和原来的升级方式是什么关系](#和原来的升级方式是什么关系)
- [测试](#测试)

## 文件

| 文件 | 作用 |
|---|---|
| `build-in-docker.sh` | 一键打包：起 CentOS Stream 9 容器跑下面的构建脚本，`--arch x86_64|aarch64|all`，产物放 `Output/<arch>/` |
| `export-source.sh` | 从 git 导出干净源码树（`git archive HEAD` + 锚定在仓库根的 exclude pathspec，去掉 tests / packaging / docs 等顶层开发目录），核对导出文件数等于 git 列出的数，再对结果做敏感文件复检；`build-offline-run.sh` 与 `build-in-docker.sh` 都靠它拿源码 |
| `build-offline-run.sh` | 真正的构建脚本，在目标发行版的容器里跑（推荐 `quay.io/centos/centos:stream9`），产出 `flocks-offline.run` + `.sha256` + `versions.json` |
| `versions.manifest.json` | 锁定的 Python / Node / uv / makeself 版本与下载地址模板；升级工具链改这里 |
| `installer/install.sh` | 打进包里的安装脚本，`sudo bash xxx.run` 时由 makeself 自动执行，全程不提问 |
| `installer/flocks.env.template` | 渲染成 `/etc/flocks/flocks.env` |
| `installer/flocks.service` | 渲染成 `/etc/systemd/system/flocks.service` |
| `installer/flocks-cli-wrapper.sh` | 渲染成 `/usr/local/bin/flocks`，`sudo flocks status` 会以服务用户身份执行 |
| `smoke-test.sh` | 在无网络的一次性容器里装一遍：装在非默认端口（默认 5273，`SMOKE_PORT` 可改）、建管理员、本地 Pro 包安装、离线模式拒绝联网升级、不带变量重跑安装包要沿用端口、不带 Pro 的新包覆盖已激活实例（沿用 / 拒绝两种，拒绝那次核对没有新的 `.bak` 目录且解包目录被挪到 `.failed-*`）、`SMOKE_INIT=systemd` 时重启容器验 Pro 存活与真 firewalld 永久规则、降级到 OSS 的 prebuilt 重启（放在重启之后，因为重启那段要求 Pro 还在）、`FLOCKS_OFFLINE_SKIP_START=1` 只装不启动，输出 PASS/FAIL |
| `verify-install.sh` | 在装好的真机上跑的验收脚本（root）：布局、systemd、firewalld、建管理员/登录、本地 Pro 包安装、`--rerun` 重复执行安装包，输出 PASS/FAIL。`--port` 默认读机器上的 `FLOCKS_PORT`（`--rerun` 会把它作为显式端口传给安装脚本，写死 5173 会把 5273 的实例悄悄挪走）；不传 `--admin-pass` 时用包内 Python 的 `secrets` 生成随机密码（原来的 `tr </dev/urandom \| head -c 16` 在 `pipefail` 下必定 exit 141） |

CI：`.github/workflows/linux-offline-run.yml` 在 x86_64（`ubuntu-latest`）和 aarch64（`ubuntu-24.04-arm`）两个原生 runner 上各起一个 CentOS Stream 9 容器构建，手动触发上传两个 artifact（可填 Pro wheel 的 URL 和 release id），发布 Release 时把 `flocks-offline-x86_64.run`、`flocks-offline-aarch64.run` 挂到 Release 资产；交付给客户时改名成说明书写死的 `flocks-offline.run`。

## 怎么构建

一键（本机有 docker 即可）：

```bash
packaging/linux/build-in-docker.sh --arch all --flockspro-wheel ../flockspro/dist/flockspro-<ver>-py3-none-any.whl
# 产物：packaging/linux/Output/x86_64/flocks-offline.run、packaging/linux/Output/aarch64/flocks-offline.run
```

`--arch` 默认是本机架构；另一种架构要 Docker 里有 QEMU（`docker run --privileged --rm tonistiigi/binfmt --install all`），慢好几倍，同架构机器或 CI 矩阵更快。国内机器加 `--cn` 走 npmmirror。`--keep-container` 会留下容器，进去 `build-offline-run.sh --repack` 可以只换脚本快速重打。

源码取自 **`git archive HEAD`**，不是工作区：有未提交改动时脚本直接拒绝（`versions.json` 里的 `git_sha` 要能对上真实提交），本地调试可以加 `--allow-dirty`，这时会把工作区里已跟踪 + 未忽略的文件一起导出，`versions.json` 记 `source_dirty: true`。`build-in-docker.sh` 在本机导出到 `~/.cache/flocks-offline-build/<arch>/source/` 再只读挂进容器（上一次的导出改名为 `source.previous-<时间戳>` 留着，攒多了自己清），容器里没有工作区可 tar。排除用的是 git pathspec（`:(exclude)tests` 这种，只认仓库根那一层），不是 `tar --exclude`：bsdtar 的 exclude 不锚定，`./assets` 会把 hub 技能里嵌套的 `assets/` 一并吃掉——第一次在 macOS 上导出就丢了 279 个文件，包装上能跑，谁也没发现，所以现在导出后还会核对文件数（导出数 ≠ git 列出的数直接失败）。无论哪种模式，导出后都会复检一遍：`.env`、`.secret.json`、`*.pem` / `*.key`、`id_rsa*`、`*.log`、`.flocks/{config,data,run,logs,workspace}` 和本地的 `flocks.json` / `mcp_list.json` 只要出现就构建失败（`*.example` 除外）。之前是 `tar` 整个工作区，会把 `.flocks/.storage_migrated` 这类本地状态、甚至 `.env` 一起打进客户的包里。

venv 不能搬家（`pyvenv.cfg`、shebang、editable `.pth` 全是绝对路径），所以构建必须在目标路径 `/opt/flocks` 下进行，要求这个目录为空——这就是一定要在干净容器里跑的原因。手动等价命令：

```bash
docker run --rm -it -v "$PWD":/src -v ~/.cache/flocks-offline-build:/root/.cache/flocks-offline-build \
  quay.io/centos/centos:stream9 bash -c '
    dnf -y install tar gzip findutils which git python3 diffutils procps-ng iproute &&
    cd /src && bash packaging/linux/build-offline-run.sh --output-dir /root/.cache/flocks-offline-build/out'
```

带 Pro bundle：加 `--flockspro-wheel /path/flockspro-<ver>-py3-none-any.whl`，可选 `--pro-release-id`（console 上对应的 release）、`--pro-bundle-version`。wheel 必须是和这个 core 配对的版本。

国内构建机可以走镜像：`FLOCKS_NODE_BASE_URL=https://registry.npmmirror.com/-/binary/node`、`FLOCKS_NPM_REGISTRY=https://registry.npmmirror.com/`、`FLOCKS_UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple`。下载的工具链缓存在 `~/.cache/flocks-offline-build`，改 manifest 才会重新下。

构建做的事：下载工具链 → `export-source.sh` 导出源码（`git archive`，不含 `.git`、tests、packaging、docs、`node_modules`）并复检 → 建完包 `python3 ~/.claude/tools/artifact_scan.py Output/<arch>/flocks-offline.run --repo .` 再看一眼包里有没有不该有的东西 → 用包内 Python 跑 `uv sync --frozen --group dev` → 在 scratch 目录 `npm ci && npm run build` 后只把 `dist` 拷进来 → 写 Pro bundle 和 `versions.json` → makeself 打包（`--target /opt/flocks/.staging`，解包不占 `/tmp`）。x86_64 和 aarch64 都能构建，产物只能装到同架构机器。

## 装到机器上是什么样

```
/opt/flocks/tools/{python,node,uv}     运行时，root 所有
/opt/flocks/flocks                      仓库副本 + .venv + webui/dist，flocks 用户所有（升级要写它）
/opt/flocks/bundle                      Pro bundle（manifest.json + wheels/），有才装
/opt/flocks/cache/uv                    构建机的 uv 缓存，UV_OFFLINE=1 时兜底
/opt/flocks/installer, versions.json    这次安装用的脚本和版本信息
/etc/flocks/flocks.env                  服务环境变量；改 IP/端口/控制台地址就改它然后 systemctl restart flocks
/etc/systemd/system/flocks.service      oneshot + RemainAfterExit，ExecStart=flocks start，ExecStop=flocks stop
/var/lib/flocks/.flocks                 数据（配置、数据库、日志、授权）；flocks.env 里改过 FLOCKS_ROOT 就是改过的那个。已有数据时安装脚本不动它，第一次装会把旧实例的数据拷进来（见下一节）
/usr/local/bin/flocks                   CLI 包装
```

安装脚本会：先做预检（架构、磁盘、端口、已激活 Pro 与 bundle 的匹配），预检不过就报错退出、不动任何文件，并把已解包的安装文件（只挪 `versions.json`、`installer/`、`tools/`、`flocks/`、`bundle/`、`cache/` 这几项，解包目录本身不改名——`--target /var/tmp` 时那是共享的系统目录）收进 `<解包目录>/flocks-offline-failed-<时间戳>/`，makeself 失败时不清 `--target` 目录，不收起来的话下一个包会解在它上面、两个版本的文件混在一起；下次装成功时这些残留会被搬到 `/opt/flocks/` 下和 `*.bak-*` 放一起。任何没被显式处理的命令失败也走同一条路（ERR trap），日志里会写是哪条命令、第几行——`$(...)` 里出错只退那个子 shell，由父进程报外层命令，`fail()` 的输出全走 stderr，不会被命令替换当成变量值；架构核对在用到包内 Python 之前就做（异架构包的 Python 根本起不来，否则只能看到 Exec format error）；建 `flocks` 用户；已有安装先停服、把旧的 `flocks/`、`tools/`、`bundle/` 改名成 `*.bak-<时间戳>`（不删）；搬入新文件后 `restorecon -R`（从临时目录 mv 过来的文件会带 `tmp_t` 标签，SELinux enforcing 下 systemd 起不来）；合并 `flocks.env`（受管的几个键覆盖，其它保留）；如果之前激活过 Pro（`~/.flocks/run/pro-bundle-installed.json` 在），把包里的 Pro wheel 重新装进新 venv；新包不带 `bundle/` 时，先看机器上原来的 `/opt/flocks/bundle` 能不能沿用（`manifest.json` 的 `core_version` 等于新包 core、wheel sha256 对得上 manifest），能就原样搬回来继续装 Pro，不能就在动任何文件之前报错退出（“这台机器已激活 Flocks Pro，但新安装包不含 Pro 组件……本次未做任何改动”），不会把一台 Pro 实例静默装成 OSS；`systemctl enable --now`；firewalld 放行服务端口（默认 zone 加上所有绑了网卡/来源的 zone，永久规则和运行态规则分别核对补齐——之前只查运行态，运维手动 `--add-port` 过一次的机器永久规则就永远不会写，reload 或重启后端口关闭；firewalld 拒绝写入只打警告、给出手动命令，不会让已经换好文件的安装中途退出）；`chronyd` 打开；轮询 `/api/health` 直到 200；最后打印 `[flocks] 安装完成：http://<IP>:<端口>`。日志在 `/var/log/flocks-offline-install.log`。

端口只有一个来源：本次显式给的 `FLOCKS_OFFLINE_PORT` > 机器上 `/etc/flocks/flocks.env` 里已有的 `FLOCKS_PORT` > 5173，env 文件、firewalld、健康检查和最后那行 URL 用的都是它。所以装在 5273 上的实例，之后直接 `sudo bash 新包.run` 不带变量也还是 5273（之前会退回 5173 去查健康，等 180 秒后报“没有就绪”）；要换端口就显式传 `FLOCKS_OFFLINE_PORT`，这时 env 里的 `FLOCKS_PORT` 才会被改写（env 里那行本身是坏值时也会被改写成 5173，否则服务起不来）。装好后留在 `/opt/flocks/installer/` 的脚本副本只供查看，直接执行会被拒绝——它的解包目录就是安装目录，跑起来会把自己所在的安装挪成 `.bak`。

systemd 用 oneshot 而不是把 `service-daemon` 放前台，是因为在线升级的 `restart_handoff` 会先 `stop_all` 再自己拉起新的 `flocks start`；前台模式下主进程一退 systemd 会连 handoff 一起杀掉。oneshot 下 `systemctl status` 只能看到 active (exited)，看进程用 `sudo flocks status`。

调试用的环境变量（客户不需要）：`FLOCKS_OFFLINE_DRY_RUN=1` 只打印计划；`FLOCKS_OFFLINE_SKIP_SYSTEMD=1` 在容器里用 `flocks start` 直接起；`FLOCKS_OFFLINE_SKIP_FIREWALL=1`、`FLOCKS_OFFLINE_SKIP_START=1`（只装不启动，smoke 里有真跑；之前这条分支调了一个还没定义的函数，exit 127）、`FLOCKS_OFFLINE_PORT`、`FLOCKS_OFFLINE_HEALTH_TIMEOUT`。

## 旧数据怎么带过来

数据目录先看机器上已有的 `/etc/flocks/flocks.env`：里面的 `FLOCKS_ROOT` 是绝对路径就一直用它（运维把数据挪到数据盘的情况），没有才用 `/var/lib/flocks/.flocks`。Pro 标记、建目录、`install_profile.json` 都跟着这个目录走。只认 `FLOCKS_ROOT=` 这种写法，和 systemd、安装脚本合并 env 的读法一致：`export FLOCKS_ROOT=` 对服务不生效，安装脚本会打警告并按默认目录处理；写成相对路径直接报错退出。之前这里写死了默认路径，数据挪过盘的机器重装时读不到 Pro 标记，会把 Pro 实例装成开源版。

数据目录里还没有用户数据时（`config/flocks.json`、`config/flocks.jsonc`、`data/flocks.db` 都不在，一般就是这台机器第一次装离线包），安装脚本会找上一个实例的数据拷一份过来，按这个顺序找：

1. `FLOCKS_OFFLINE_MIGRATE_FROM=<目录>`，显式指定；写 `none` 表示不迁移。
2. 启动安装脚本时带的 `FLOCKS_ROOT`，对应在线版改过数据目录的情况。
3. 本机用户默认的 `~/.flocks`：执行 sudo 的用户、root、`/etc/passwd` 里每个用户的 home。只有一个有数据就用它；有好几个就列出来（带数据库最后写入时间）报错退出，让运维用第 1 条指定，脚本不替人猜。

sudo 默认会清掉调用方的环境变量，变量要写在 sudo 后面：`sudo FLOCKS_OFFLINE_MIGRATE_FROM=/home/admin/.flocks bash /var/tmp/flocks-offline.run`。

数据目录本身也先过一遍：不能是 `/`；已经存在但没有用户数据时，里面只能有 Flocks 自己的目录（config、data、run、logs、plugins、workspace 这类），混着别的东西（别的服务的文件之类；新 ext4 盘根目录的 `lost+found` 不算）就当作数据目录配错了，报错退出，安装脚本不会去建目录、改属主或改名。显式给了 `FLOCKS_OFFLINE_MIGRATE_FROM`、数据目录却已经有数据时，也报错退出，不会悄悄跳过。

动文件之前还要查三件事：有进程还开着源目录里的文件（旧实例没停，按 `/proc/*/fd` 查），就报出 PID 和命令行后退出；数据目录是挂载点时没法整目录替换，让运维把 FLOCKS_ROOT 改到挂载点下的子目录；数据目录真实所在的分区（软链按链接目标算）放不下一份拷贝（另留 200 MB），也退出；旧目录顶层的外部软链指向的目录如果包含新数据目录（比如 `workspace -> /data` 而新数据目录在 `/data/flocks`），照拷会把拷贝本身再拷进去，同样拒绝。旧实例还占着端口时，端口检查会先报错，并提示先 `flocks stop`。迁移的数据里有 Pro 标记（`run/pro-bundle-installed.json`）就按已激活 Pro 处理：新包带 Pro 组件就装进新 venv，不带就在动文件之前拒绝。

拷贝由 `installer/migrate_data.py` 用包内 Python 执行，源目录只读不写：
1. 整份拷到 `<数据目录>.migrating-<时间戳>`，跳过 socket、管道这类运行时文件。源目录顶层有指向别处的目录软链（比如 `data` 挪到了数据盘），拷成实体目录，新旧实例不共用同一份数据库。
2. 原来的数据目录如果已经存在（没有用户数据），把只有它有的文件并进拷贝，然后把它改名成 `.pre-migration-<时间戳>`。
3. 先把整份拷贝的属主交给 flocks，再把拷贝改名成数据目录。改名是最后一步，改名成功时数据已经是服务能用的状态。

这一步排在创建服务用户之后、停旧程序之前，拷失败时旧安装原样不动，已拷的部分留在 `.migrating-*` 里。中途断掉只有两种结果：没改名，目标原样，重跑会从头再迁一次；已改名，数据已经完整归 flocks 所有。改属主时软链只改链接本身，不会顺着它去改树外文件；已有实例的数据目录，安装脚本从不改它的属主。

旧实例里写死的绝对路径会跟着改：`config/flocks.json`、`config/flocks.jsonc`、`config/config.json`、`config/mcp_list.json`、`data/hub/**/*.json`（hub 安装记录的 `installPath`、`backupPath`）里以旧目录开头的字符串值，以及指向旧目录的软链，都换成新目录。改的时候只动字符串本身，注释、格式和键名原样保留；比较时先按 JSON 解码，`\uXXXX`、`\/` 这类转义写法也能匹配；读不了的文件（不是 UTF-8）会在安装日志里列出来。迁移记录写在 `run/offline-migration-<时间戳>.json`。旧目录不删，装完会提示确认后自己清。

有两处没有跟着改。工作流中心的注册表按工作流文件的绝对路径算 id，从 `/root/.flocks` 换到 `/var/lib/flocks/.flocks` 后，发布过的工作流 API 服务要重新发布一次，工作流定义本身已经拷过来了。在线版程序目录里的 `.env`（比如 `/root/flocks/.env`）不在数据目录下，不会带过来，里面的变量要搬进 `/etc/flocks/flocks.env`。

## Pro 升级在离线机器上怎么走

页面上"开始升级"调的还是 `perform_pro_bundle_install`；命令行是 `flocks update --pro-bundle [--no-restart]`，加 `--check` 只显示将安装的 bundle（来源、版本、release id、当前是否已装），不安装也不重启，也不碰 SQLite（session token 只读共享文件）；`FLOCKS_PRO_BUNDLE_DIR` 指着一个坏目录时直接报那个目录，不去问 Console。它先看 `FLOCKS_PRO_BUNDLE_DIR`（安装包写进 env 的 `/opt/flocks/bundle`）：目录里有 `manifest.json` 就用本地包，不下载；manifest 标了 `prebuilt: true` 且没带 `flocks/` 源码，就只做 `uv pip install --no-deps <wheel>`，跳过 `uv sync` 和 npm，写 marker 后走 handoff 的 Pro-only 重启。console 的 manifest 只是尽力拉一次：bundle 版本相同、且双方都带的 `core_version` / `flockspro_component_version` / `build_id` 都一致时，只把 `release_id` / `bundle_release_id` 合进 marker；其余字段一律以本地包为准（同一个 bundle 版本可以配不同 wheel 重打，不能因为版本号相同就把 console 那份的组件版本和 build id 记成已装）；拉不到、版本不同或身份冲突都不合并，也不影响安装。

`FLOCKS_UPDATE_CHANNEL` 被安装包固定成 `flockspro-offline-<core 版本>`，心跳和升级申请都会带上这个 `channel` 字段；console 给每个离线包版本开一个冻结渠道、发和包里同一个 release，在线客户的 `flockspro` 渠道不受影响。老客户端不带 `channel` 字段，console 按 `flockspro` 处理。

页面上“降级到 OSS”走 `perform_pro_bundle_downgrade`：卸掉 flockspro wheel 后的 handoff 重启在离线实例上（`FLOCKS_DEPLOY_MODE=offline`）带 `--prebuilt`，跳过 `uv sync` / `npm install` / `npm run build` 直接 `flocks start`；在线实例的降级 handoff 不变。

`prebuilt` 格式的 bundle 只能发到离线渠道。老版本客户端拿到没有 `flocks/` 目录的包会判成"不是 Pro bundle"，直接报"未找到 flockspro wheel"。

## 和原来的升级方式是什么关系

离线实例的升级机制和在线安装是同一套代码（备份到 `~/.flocks/version`、`_replace_install_dir` 保留 `.venv`、marker、handoff 重启），只是"包从哪来"不同：Pro 组件来自包内 `bundle/`，核心升级靠新版 `.run` 覆盖执行。env 里的 `FLOCKS_DEPLOY_MODE=offline` 会让页面"检查更新 → 升级"、`flocks update` 和 `/api/update/apply` 在动任何文件之前就拒绝联网升级并提示用新 `.run`（否则会先替换源码再在 `uv sync` 上被 `UV_OFFLINE=1` 卡住，服务停在半路）。页面上 `update_allowed=false` 时显示"离线安装包部署"的提示，不是 Docker 那条；这块提示不看 `has_update`。核心版本检查（`edition=flocks`）在离线实例上根本不出网：`check_update` 直接返回本地版本、`latest` 为空、无错误——之前会挨个等 GitHub/Gitee 超时（30–45 秒），最后一级兜底还会在当前目录跑 `git tag`，在别的 git 仓库里执行 CLI 甚至能凑出一个假的“有新版本”。页面这时显示“离线部署，未查询远端版本”而不是“已是最新”；`flocks update` / `--check` 先打提示、再打当前版本，退出码 0，不问“是否使用中国镜像”。Pro 那条（`edition=flockspro`，走 console manifest）不受影响，console 是这类机器唯一允许访问的端点。

机器如果后来能联网、想改回在线升级：从 `/etc/flocks/flocks.env` 删掉 `FLOCKS_DEPLOY_MODE` / `FLOCKS_OFFLINE_INSTALL` / `UV_OFFLINE` / `npm_config_offline` / `FLOCKS_PRO_BUNDLE_DIR` / `FLOCKS_UPDATE_CHANNEL`，`systemctl restart flocks`，之后就是普通源码安装的在线升级路径（GitHub/Gitee 源码包 + console `flockspro` 渠道），代码不用改。反过来，`install.sh` 装的实例改用 `.run`：`.run` 是另一种部署形态（`/opt/flocks`、`flocks` 用户、`/var/lib/flocks/.flocks`），旧实例的 `~/.flocks` 会按上面「旧数据怎么带过来」自动拷一份；旧实例要先停，端口还被它占着时安装脚本会报错退出，并提示先 `flocks stop`。

在线用户不受影响：新逻辑全部靠 `FLOCKS_PRO_BUNDLE_DIR`、`FLOCKS_DEPLOY_MODE=offline`、manifest 的 `prebuilt` 和 handoff 的 `--prebuilt` 参数触发，`service_manager` 在 `ps` 不可用时（没有这个命令，或 BusyBox 那种不认 `-p` / `-eo` 的 `ps`，总之调用失败）改读 `/proc`——最小化容器里 `flocks stop` 找不到守护进程会把旧后端留在端口上；`ps` 正常的机器走原来的路径，console 现在发的 bundle（带 `flocks/`）新代码照收，既有 updater 测试全部原样通过。

## 测试

```bash
uv run pytest tests/scripts/test_linux_offline_installer.py   # 语法、函数先定义后调用、无递归删除、模板键、dry-run（含端口沿用/覆盖）、源码导出不带本地密钥、已激活 Pro 的沿用/拒绝、firewalld 永久规则（假 firewall-cmd）、随机密码、旧数据迁移（选源顺序、多个候选时拒绝、源目录只读、路径改写、占用和空间检查、FLOCKS_ROOT 挪过盘时的 Pro 判断）
uv run pytest tests/updater/test_updater_prebuilt_bundle.py     # 本地 bundle / prebuilt 路径、离线降级 handoff 带 --prebuilt、console 身份只在完全匹配时借 release_id，以及默认路径不变的回归
uv run pytest tests/cli/test_update_command.py                  # 离线实例 flocks update 先给 .run 指引
cd webui && npx vitest run src/components/common/UpdateModal.test.tsx   # 离线提示不依赖 has_update
uv run pytest tests/cli/test_service_manager.py -k proc              # 没有 ps 时的 /proc 兜底
packaging/linux/smoke-test.sh Output/flocks-offline.run         # 无网络容器里真装一遍（需要 docker，镜像架构要和包一致）
sudo packaging/linux/verify-install.sh --port 5173 --rerun /var/tmp/flocks-offline.run   # 在装好的真机上验收
```

真机上 5173 已被别的服务占用时，安装和验收都可以换端口：`FLOCKS_OFFLINE_PORT=5273 sudo bash /var/tmp/flocks-offline.run`、`verify-install.sh --port 5273`。安装脚本发现端口被占且没有旧的 Flocks 安装会直接报错退出，不会去停别人的进程。

真机验收清单在 `webook_dev/docs/offline_install/` 的差距分析文档里（断外网只放行 portal/passport 的 CentOS Stream 9 x86_64，按客户说明书原文从 scp 跑到 Pro 激活、两次重启、重复执行 `.run`）。
