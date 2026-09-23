跑过什么、结果如何（v2.0.0，开发机 Apple Silicon Mac + colima）。原始日志（*.log）只留在跑测试的那台机器上，不进仓库；
要重新拿一份就照下面的命令再跑一遍，日志会落回这个目录。下面的命令都以 packaging/egress-proxy/ 为工作目录。

交付目标是 x86_64，以 x86_64 那轮为准；arm64 只是开发机上容器跑得快（10 分钟 vs 35 分钟）用来快速迭代，包在 dist/dev-arm64/，不交付。

2026-09-22（「安装做到小白也能装」之后的最终验证，用的就是最终交付包）
  x86_64  PASS 160 / FAIL 0 / SKIP 0   DOCKER_CONTEXT=colima-x86 tests/e2e-centos9.sh --arch amd64 \
                                         --bundle dist/flocks-egress-proxy-2.0.0-linux-x86_64.tar.gz \
                                         --run    dist/flocks-egress-proxy-2.0.0-linux-x86_64.run
          CentOS Stream 9 容器（systemd 作 PID 1，/tmp 挂 noexec），跑在 QEMU 全系统模拟的 Linux 6.8 虚拟机里。四容器：
          机器 A、机器 B（客户现成代理：squid 3128 / 3129 认证、socat TLS 3443、tests/fixtures 起的 mihomo 3130 认证 HTTP+SOCKS5）、
          内网 web、内网 DNS（dnsmasq；auto 探测的反例另起一台没有上游的 dnsmasq）。
          小白路径那几条：T05k 只写 主机:端口、T05l/T05m 终端提问（pty_drive.py 模拟人答题，含答错重问、账号回车、认证代理密码不回显）、
          T05n help、X04 零参数 .run 取 dnf.conf 里的代理、X04c .run 直接跟 IP:端口、X04d .run 不带参数 → 终端三问、X04e .run help
  arm64   PASS 160 / FAIL 0 / SKIP 0   DOCKER_CONTEXT=colima tests/e2e-centos9.sh --arch arm64 \
                                         --bundle dist/dev-arm64/…-arm64.tar.gz --run dist/dev-arm64/…-arm64.run
  pytest  42 / 42                      <flocks 仓库根>/.venv/bin/python -m pytest tests/test_setup_a.py -q
                                       （这个目录没有自己的 pyproject.toml，别在这儿 uv run，它会去用仓库根那个项目）
          （主机:端口 补 http://、wgetrc 解析、终端提问的五种分支与 Ctrl-D、机器现成代理设置解析不了时报来源并遮账号后忽略）

2026-09-21（上一版）：x86_64 与 arm64 各 150 / 150、pytest 37 / 37。当时 B 上的 SOCKS5 代理还是用后来删掉的 extras/relay 装的，
现在是 tests/fixtures/socks5-proxy-on-b.sh。

上面两轮 e2e 用的包就是最终交付包（setup-a.py、setup-a.sh、README、vendor 二进制逐字相同）：
  41f2e8a9a0389c048cdd8e6462ebbdcc5a5bea56c00fac64c9fe0c7beb7ba556  flocks-egress-proxy-2.0.0-linux-x86_64.tar.gz      ← 交付
  82c7ece07fdaf27752b4a11f79e55030d0c144d22d2881aa1e2001367f7aa18b  flocks-egress-proxy-2.0.0-linux-x86_64.run         ← 交付（给客户就这一个文件）
  8b49ce24045a438fd4d0df29f11e0a7107cb06d9b415cae98635bf65aaca39a9  dev-arm64/flocks-egress-proxy-2.0.0-linux-arm64.tar.gz
  0060b11ade34d8e73fd37c10b443653c44cb23008a97ebc81434818b8a8a8072  dev-arm64/flocks-egress-proxy-2.0.0-linux-arm64.run
.run = 一段 shell 头 + __ARCHIVE_BELOW__ + 同一个 tar.gz（payload 的 sha256 与 tar.gz 相同，可用
tail -n +$(awk '/^__ARCHIVE_BELOW__$/{print NR+1; exit}' x.run) x.run | sha256sum 核对）。

自己跑的时候会踩的几个坑：
- docker context 一律显式写（DOCKER_CONTEXT=colima / colima-x86）：colima start 另一个 profile 会把当前 context 切走，正在跑的测试会丢容器。
- x86_64 那轮约 35 分钟，超过一次工具调用的上限：nohup 起脚本把日志落到本目录，再 until grep -q "^e2e rc=" 等它。
- 测试进行中不要改 tests/e2e-centos9.sh：bash 边读边执行，改了文件正在跑的那次会在半路报语法错误。
- x86_64（QEMU 虚拟机，很慢）上 8c「上联口是网桥」那段偶发失败过：把 eth0 挪进网桥后马上 reconfigure，reconfigure 末尾的自动验收
  有一项 FAIL、返回 1，B-UP1 / B-UP2 跟着 FAIL，其余全过；同一份包重跑就过，arm64 一直稳定。原因是网卡刚挪进网桥、头几秒经 B 的
  连接不通。现在测试在 reconfigure 前先等经引擎访问公网通了（最多 15 秒）再做；失败时会打印验收里的 FAIL 行。
- 终端提问的用例不能一次性把答案灌进 stdin：getpass 读密码前 tcsetattr(TCSAFLUSH) 会清输入队列，提前敲的密码丢了进程就一直等着。
  tests/pty_drive.py 等提示出来再敲，超时 300 秒自杀。
