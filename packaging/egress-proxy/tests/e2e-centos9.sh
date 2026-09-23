#!/usr/bin/env bash
# flocks-egress 端到端测试：Docker 里起 4 个容器模拟客户机房 ——
#   机器 A（fe-host，跑 systemd 的 CentOS Stream 9，装 setup-a.sh）
#   机器 B（fe-proxy，同样的镜像，扮演客户已有的代理：squid 3128 不认证 / 3129 basic 认证、socat 套 TLS 的 3443、
#           tests/fixtures/socks5-proxy-on-b.sh 用包里的 mihomo 起的 3130（HTTP+SOCKS5，认证）——不装本包的任何 A 侧东西）
#   一台内网 web（fe-intranet-web）、一台内网 DNS（fe-dns，dnsmasq）
# 流程：参数校验 / dry-run / 代理探测 → 安装（host 范围，auto→redir-host）→ 场景断言 → 引擎停掉时 fail-closed →
#       切 fake-ip → 代理四种写法（squid 认证、socks5、https 自签、https 导入 CA）→ nftables.service 清空规则后自愈 →
#       切 user 范围 → firewalld 共存 → systemd 单元校验 → rollback → 残留拒绝 → 重装。
#   tests/e2e-centos9.sh [--arch amd64|arm64] [--keep] [--bundle dist/flocks-egress-proxy-<ver>-linux-x86_64.tar.gz] [--run dist/...-x86_64.run]
#   交付目标是 x86_64：DOCKER_CONTEXT=colima-x86 tests/e2e-centos9.sh --arch amd64 --bundle dist/…-x86_64.tar.gz --run dist/…-x86_64.run（QEMU 虚拟机，约 35 分钟）
#   开发机快速迭代：DOCKER_CONTEXT=colima tests/e2e-centos9.sh --arch arm64 --bundle dist/dev-arm64/…-arm64.tar.gz --run dist/dev-arm64/…-arm64.run
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="$(uname -m)"; case "$ARCH" in x86_64) ARCH=amd64 ;; aarch64|arm64) ARCH=arm64 ;; esac
KEEP=0; BUNDLE=""; RUNFILE=""
while [ $# -gt 0 ]; do case "$1" in --arch) ARCH="$2"; shift 2 ;; --keep) KEEP=1; shift ;; --bundle) BUNDLE="$2"; shift 2 ;; --run) RUNFILE="$2"; shift 2 ;; *) echo "未知参数 $1" >&2; exit 1 ;; esac; done
[ -z "$BUNDLE" ] || [ -f "$BUNDLE" ] || { echo "交付包不存在: $BUNDLE" >&2; exit 1; }
[ -z "$RUNFILE" ] || [ -f "$RUNFILE" ] || { echo "自解压包不存在: $RUNFILE" >&2; exit 1; }
PLATFORM="linux/$ARCH"
DFHASH="$(shasum -a 256 "$HERE/tests/Dockerfile.centos9" 2>/dev/null | cut -c1-12 || sha256sum "$HERE/tests/Dockerfile.centos9" | cut -c1-12)"
IMG="flocks-egress-test:c9-$ARCH-$DFHASH"
NET=fe-e2e; HOST=fe-host; PROXY=fe-proxy; WEB=fe-intranet-web; DNS=fe-dns
HOST_IP=172.30.0.10; PROXY_IP=172.30.0.20; WEB_IP=172.30.0.30; DNS_IP=172.30.0.53
VER="$(cat "$HERE/VERSION")"

PASS=0; FAIL=0; SKIP=0; RESULTS=()
t_pass() { PASS=$((PASS+1)); RESULTS+=("PASS | $1 | $2"); printf '  PASS  %-52s %s\n' "$1" "$2"; }
t_fail() { FAIL=$((FAIL+1)); RESULTS+=("FAIL | $1 | $2"); printf '  FAIL  %-52s %s\n' "$1" "$2"; }
t_skip() { SKIP=$((SKIP+1)); RESULTS+=("SKIP | $1 | $2"); printf '  SKIP  %-52s %s\n' "$1" "$2"; }
hx()  { docker exec "$HOST" bash -c "$*"; }                        # 机器 A，root
hxu() { docker exec "$HOST" runuser -u "$1" -- bash -c "$2"; }      # 机器 A，指定用户
hb()  { docker exec "$PROXY" bash -c "$*"; }                       # 机器 B，root
blog() { hb 'cat /var/log/squid/access.log 2>/dev/null; journalctl -u fe-socks5 --no-pager -o cat 2>/dev/null'; }   # B 两种代理的日志
blog_count() { blog | grep -c -- "$1"; }
elog_lines() { hx 'journalctl -u flocks-egress --no-pager -o cat | wc -l'; }
elog_from()  { hx "journalctl -u flocks-egress --no-pager -o cat | tail -n +$(( $1 + 1 ))"; }
is_fake() { [[ "$1" =~ ^198\.1[89]\. ]]; }
fe() { hx "cd / && flocks-egress $*"; }                             # 装好之后的命令入口（/usr/sbin 软链）

cleanup() {
    if [ "$KEEP" = 1 ]; then echo "--keep：保留容器 $HOST $PROXY $WEB $DNS"; return; fi
    docker rm -f "$HOST" "$PROXY" "$WEB" "$DNS" "$DNS-nopub" >/dev/null 2>&1 || true
}
trap cleanup EXIT

start_systemd_container() {   # 名字 IP
    docker run -d --platform "$PLATFORM" --name "$1" --dns 8.8.8.8 --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
        --tmpfs /run --tmpfs /run/lock --tmpfs /tmp:rw,noexec,nosuid,size=256m --network "$NET" --ip "$2" --hostname "$1" "$IMG" >/dev/null
    local st
    for _ in $(seq 1 60); do st="$(docker exec "$1" systemctl is-system-running 2>/dev/null || true)"; case "$st" in running|degraded) return 0 ;; esac; sleep 2; done
    echo "$1 的 systemd 没起来: $st" >&2; return 1
}
put_pkg() {   # 把交付包放进容器 /root/pkg
    local c="$1"
    if [ -n "$BUNDLE" ]; then
        docker cp "$BUNDLE" "$c:/root/bundle.tar.gz"
        docker exec "$c" bash -c 'mkdir -p /root/pkg && tar -xzf /root/bundle.tar.gz -C /root/pkg --strip-components=1 && cd /root/pkg && sha256sum -c --quiet SHA256SUMS' \
            || { echo "交付包解压或校验失败" >&2; exit 1; }
    else
        docker exec "$c" mkdir -p /root/pkg /root/pkg/vendor
        for f in VERSION setup-a.py setup-a.sh egress.conf.example README.md THIRD_PARTY_NOTICES.md; do docker cp "$HERE/$f" "$c:/root/pkg/"; done
        local stage; stage="$(mktemp -d)"; cp "$HERE/vendor/mihomo-linux-$ARCH" "$HERE/vendor/SHA256SUMS" "$stage/"
        docker cp "$stage/." "$c:/root/pkg/vendor/" && rm -f "$stage/mihomo-linux-$ARCH" "$stage/SHA256SUMS" && rmdir "$stage"
    fi
}

echo "== 0. 准备环境 (arch=$ARCH platform=$PLATFORM 镜像=$IMG) =="
[ -f "$HERE/vendor/mihomo-linux-$ARCH" ] || { echo "缺少 vendor/mihomo-linux-$ARCH，先跑 tools/fetch-mihomo.sh --arch $ARCH" >&2; exit 1; }
docker rm -f "$HOST" "$PROXY" "$WEB" "$DNS" "$DNS-nopub" >/dev/null 2>&1 || true
docker network inspect "$NET" >/dev/null 2>&1 || docker network create --subnet 172.30.0.0/24 "$NET" >/dev/null
if [ "$(docker image inspect "$IMG" --format '{{.Architecture}}' 2>/dev/null)" != "$ARCH" ]; then
    echo "构建测试镜像 $IMG（首次较慢；amd64 在 ARM 机器上走 qemu 模拟）..."
    if docker buildx version >/dev/null 2>&1; then
        docker buildx build --platform "$PLATFORM" --load -t "$IMG" -f "$HERE/tests/Dockerfile.centos9" "$HERE/tests" >/tmp/fe-e2e-build.log 2>&1 \
            || { tail -20 /tmp/fe-e2e-build.log; echo "镜像构建失败" >&2; exit 1; }
    else
        docker build -t "$IMG" -f "$HERE/tests/Dockerfile.centos9" "$HERE/tests" >/tmp/fe-e2e-build.log 2>&1 || { tail -20 /tmp/fe-e2e-build.log; echo "镜像构建失败" >&2; exit 1; }
    fi
    [ "$(docker image inspect "$IMG" --format '{{.Architecture}}')" = "$ARCH" ] || { echo "镜像架构不对" >&2; exit 1; }
fi
[ -n "$BUNDLE" ] && echo "使用交付包: $BUNDLE"

# 内网 DNS（dnsmasq）：解析 corp.local 下的内网主机，其余转 8.8.8.8 —— A、B 的 resolv.conf 都指向它，和真实机房一样
docker run -d --platform "$PLATFORM" --name "$DNS" --dns 8.8.8.8 --network "$NET" --ip "$DNS_IP" "$IMG" \
    dnsmasq -k --no-resolv --no-hosts --server=8.8.8.8 --listen-address=$DNS_IP --bind-interfaces \
    --address=/web.corp.local/$WEB_IP --address=/proxy.corp.local/$PROXY_IP --address=/flocks-host.corp.local/$HOST_IP >/dev/null
docker run -d --platform "$PLATFORM" --name "$WEB" --dns 8.8.8.8 --network "$NET" --ip "$WEB_IP" "$IMG" python3 -m http.server 8080 --bind 0.0.0.0 >/dev/null
start_systemd_container "$PROXY" "$PROXY_IP" || exit 1
start_systemd_container "$HOST"  "$HOST_IP"  || exit 1
hb "printf 'nameserver $DNS_IP\nsearch corp.local\n' > /etc/resolv.conf"
hx "printf 'nameserver $DNS_IP\nsearch corp.local\n' > /etc/resolv.conf"
hx 'getent hosts web.corp.local proxy.corp.local www.baidu.com' | grep -q "$WEB_IP" && echo "内网 DNS 就绪" || { echo "内网 DNS 不工作" >&2; docker logs "$DNS" 2>&1 | tail -3; exit 1; }
put_pkg "$PROXY"; put_pkg "$HOST"
[ -z "$RUNFILE" ] || { docker cp "$RUNFILE" "$HOST:/root/flocks-egress.run"; echo "使用自解压包: $RUNFILE"; }
docker cp "$HERE/tests/pty_drive.py" "$HOST:/root/pty_drive.py"   # 模拟人在终端里回答提示（见文件头注释）
docker cp "$HERE/tests/fixtures/socks5-proxy-on-b.sh" "$PROXY:/root/socks5-proxy-on-b.sh"   # B 上的 SOCKS5 代理夹具（客户 B 上什么都不装，这只是测试用）
hx 'useradd -m flocks 2>/dev/null; id flocks' >/dev/null

echo; echo "== 1. 机器 B：客户已有的代理（squid 不认证 / squid 认证 / https 自签 / 认证 HTTP+SOCKS5） =="
hb 'htpasswd -bc /etc/squid/passwd corpuser corppass >/dev/null 2>&1 && cat > /etc/squid/squid.conf <<CONF
http_port 3128 name=plain
http_port 3129 name=authed
auth_param basic program /usr/lib64/squid/basic_ncsa_auth /etc/squid/passwd
auth_param basic realm flocks-test
acl authed_port myportname authed
acl authenticated proxy_auth REQUIRED
acl SSL_ports port 443 80
acl Safe_ports port 80 443 1025-65535
acl CONNECT method CONNECT
http_access deny !Safe_ports
http_access deny CONNECT !SSL_ports
http_access deny authed_port !authenticated
http_access allow all
cache deny all
access_log stdio:/var/log/squid/access.log
pid_filename /run/squid.pid
CONF
systemctl enable --now squid >/dev/null 2>&1; sleep 2
# B 上 firewalld 在跑：客户的代理端口本来就该是开着的，这里替 squid / TLS 前置开一下（socks5 夹具自己会开 3130）
if systemctl is-active --quiet firewalld; then for p in 3128 3129 3443; do firewall-cmd --add-port=$p/tcp >/dev/null 2>&1; done; fi
systemctl is-active --quiet squid'
hb 'systemctl is-active --quiet squid' && t_pass "B01 squid 起来了（3128 不认证、3129 认证）" "" || { t_fail "B01 squid" "$(hb 'journalctl -u squid --no-pager | tail -5')"; exit 1; }
# https 代理：socat 用自签证书在 3443 终结 TLS，转给 squid 3128
hb 'cd /etc/squid && openssl req -x509 -newkey rsa:2048 -nodes -keyout tls.key -out tls.crt -days 30 -subj /CN=proxy.corp.local -addext "subjectAltName=DNS:proxy.corp.local,IP:172.30.0.20" >/dev/null 2>&1 && cat tls.key tls.crt > tls.pem && chmod 600 tls.pem && systemd-run --unit fe-tlsfront -q socat OPENSSL-LISTEN:3443,reuseaddr,fork,cert=/etc/squid/tls.pem,verify=0 TCP:127.0.0.1:3128 && sleep 1 && systemctl is-active --quiet fe-tlsfront' \
    && t_pass "B02 https 代理（socat TLS → squid）起来了" "3443，自签证书 CN=proxy.corp.local" || t_fail "B02 https 代理" "$(hb 'journalctl -u fe-tlsfront --no-pager | tail -3')"
# SOCKS5 代理：包里的 mihomo 作 HTTP+SOCKS5 共用 3130，带认证（只是测试夹具）
out="$(hb "bash /root/socks5-proxy-on-b.sh /root/pkg/vendor/mihomo-linux-$ARCH 3130 corpuser corppass 2>&1"; echo "rc=$?")"   # secret-guard: allow（测试容器假账号）
grep -q "rc=0" <<<"$out" && grep -q "fe-socks5 就绪.*http=200" <<<"$out" && hb 'systemctl is-active --quiet fe-socks5' \
    && t_pass "B03 B 上的 SOCKS5 代理起来了（3130 认证）" "" || t_fail "B03 socks5 夹具" "$(tail -4 <<<"$out")"
for spec in "http://proxy.corp.local:3128|不认证 squid" "http://corpuser:corppass@proxy.corp.local:3129|认证 squid" "socks5://corpuser:corppass@proxy.corp.local:3130|socks5" "http://corpuser:corppass@proxy.corp.local:3130|http 3130"; do  # secret-guard: allow（测试容器假账号）
    url="${spec%%|*}"; name="${spec##*|}"
    code="$(hx "curl -sS -m 15 -x '$url' -o /dev/null -w '%{http_code}' https://www.baidu.com/" 2>/dev/null)"
    [ "$code" = 200 ] && t_pass "B04 A 用 curl -x 经 B 上网（$name）" "http=200" || t_fail "B04 A 用 curl -x 经 B 上网（$name）" "http=$code"
done
code="$(hx "curl -sS -m 15 --proxy-insecure -x https://proxy.corp.local:3443 -o /dev/null -w '%{http_code}' https://www.baidu.com/" 2>/dev/null)"
[ "$code" = 200 ] && t_pass "B04 A 用 curl -x 经 B 上网（https 自签）" "http=200" || t_fail "B04 https 自签" "http=$code"

echo; echo "== 2. 机器 A：参数校验 / dry-run / 代理探测 =="
SA='cd /root/pkg && bash setup-a.sh'
COMMON="--check-intranet-url http://web.corp.local:8080/ --check-public-url https://www.baidu.com/ --check-public-url http://www.baidu.com/"
out="$(hx "$SA install --proxy ftp://x:1 --dry-run 2>&1"; echo "rc=$?")"
grep -q "不支持的协议" <<<"$out" && grep -q "rc=1" <<<"$out" && ! grep -q Traceback <<<"$out" && t_pass "T01 非法代理协议一行报错" "ftp://" || t_fail "T01 非法代理协议" "$(tail -2 <<<"$out")"
out="$(hx "$SA install --proxy http://proxy.corp.local:3128 --scope user --flocks-user nobody-such-user --dry-run 2>&1"; echo "rc=$?")"
grep -q "系统里没有用户" <<<"$out" && grep -q "rc=1" <<<"$out" && t_pass "T02 SCOPE=user 且用户不存在被拒绝" "" || t_fail "T02 用户不存在" "$(tail -2 <<<"$out")"
out="$(hx "$SA install --proxy http://proxy.corp.local:3128 --scope user --flocks-user flocks-egress --dry-run 2>&1"; echo "rc=$?")"
grep -q "回环" <<<"$out" && t_pass "T03 FLOCKS_USER=引擎用户 被拒绝" "" || t_fail "T03 引擎用户" "$(tail -2 <<<"$out")"
out="$(hx "$SA install --proxy http://proxy.corp.local:3128 $COMMON --dry-run 2>&1"; echo "rc=$?")"
if grep -q "rc=0" <<<"$out" && grep -q "MATCH,corp-proxy" <<<"$out" && grep -q "meta skuid 65534 return" <<<"$out" && grep -q "DNS_MODE=auto → redir-host" <<<"$out" \
   && grep -q "enable: false" <<<"$out" && grep -q "代理可用" <<<"$out" && ! hx 'test -e /etc/flocks-egress || test -e /opt/flocks-egress || id flocks-egress >/dev/null 2>&1' && ! hx 'ls -d /tmp/flocks-egress.* 2>/dev/null | grep -q .'; then
    t_pass "T04 dry-run：探测 auto→redir-host、代理可用、渲染成功、不落盘、无临时残留" "占位 uid 65534"; else t_fail "T04 dry-run" "$(grep -E 'rc=|DNS_MODE|错误|代理' <<<"$out" | head -4)"; fi
grep -q "setup-a.py v$VER" <<<"$out" && t_pass "T05 渲染头里的版本号" "v$VER" || t_fail "T05 版本号" "$(grep -o 'setup-a.py v[^ ]*' <<<"$out" | head -1)"
grep -q "172.30.0.20/32" <<<"$out" && grep -q "代理主机名 proxy.corp.local 解析为 172.30.0.20" <<<"$out" && t_pass "T05a 代理主机名解析后自动进直连集合" "" || t_fail "T05a 代理地址直连" "$(grep -n '172.30.0.20\|代理主机名' <<<"$out" | head -3)"
out="$(hx "$SA install --proxy http://corpuser:corppass@proxy.corp.local:3129 --dry-run 2>&1"; echo "rc=$?")"  # secret-guard: allow（测试容器假账号）
grep -q "rc=0" <<<"$out" && grep -q 'password: <隐藏>' <<<"$out" && ! grep -q corppass <<<"$out" && grep -q 'corpuser:\*\*\*@' <<<"$out" && t_pass "T05b dry-run 输出隐藏密码（含代理地址回显）" "" || t_fail "T05b 隐藏密码" "$(grep -n 'corppass\|password' <<<"$out" | head -3)"
out="$(hx "$SA install --proxy http://proxy.corp.local:3128 --dns-mode fake-ip $COMMON --dry-run 2>&1"; echo "rc=$?")"
grep -q -- '- "+.corp.local"' <<<"$out" && grep -q "DOMAIN-SUFFIX,corp.local,DIRECT" <<<"$out" && grep -q "自动加入内网域名后缀" <<<"$out" && grep -q "redirect to :1053" <<<"$out" \
    && t_pass "T05c fake-ip：resolv.conf 的 search 域自动成为内网后缀，DNS 表有 redirect" "corp.local" || t_fail "T05c search 域" "$(grep -n 'corp.local\|自动加入\|1053' <<<"$out" | head -3)"
hx 'cat > /tmp/unk.conf <<CONF
PROXY_URL="http://proxy.corp.local:3128"
FOO_BAR="x"
PATH="/evil"
CONF'
out="$(hx "$SA install --config /tmp/unk.conf --dry-run 2>&1"; echo "rc=$?")"
grep -q "忽略未知参数 FOO_BAR" <<<"$out" && grep -q "忽略未知参数 PATH" <<<"$out" && grep -q "rc=0" <<<"$out" && t_pass "T05d 参数文件里的未知键只警告不生效" "" || t_fail "T05d 未知键" "$(grep -i '未知\|rc=' <<<"$out" | head -3)"
out="$(hx "$SA install --proxy http://proxy.corp.local:3128 --dns-mode fake-ip --dns 127.0.0.1 --dry-run 2>&1"; echo "rc=$?")"
grep -q "不能是本机地址" <<<"$out" && grep -q "rc=1" <<<"$out" && t_pass "T05e fake-ip + host 范围拒绝本机回环 DNS（会回环）" "" || t_fail "T05e 回环 DNS" "$(tail -2 <<<"$out")"
out="$(hx "$SA install --proxy http://proxy.corp.local:3128 --dns-mode redir-host --dns 127.0.0.1 --dry-run --skip-proxy-test 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && t_pass "T05e2 redir-host 不碰 DNS，本机 DNS 转发器可以接受" "" || t_fail "T05e2 redir-host 本机 DNS" "$(tail -2 <<<"$out")"
out="$(hx "$SA install --proxy http://proxy.corp.local:3128 --config /tmp/unk.conf --dry-run 2>&1"; echo "rc=$?")"
grep -q "二选一" <<<"$out" && grep -q "rc=1" <<<"$out" && t_pass "T05h --config 与 --proxy 不能同时给" "" || t_fail "T05h 二选一" "$(tail -2 <<<"$out")"
out="$(hx "$SA install http://proxy.corp.local:3128 --dry-run 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && grep -q "代理可用" <<<"$out" && t_pass "T05i 代理地址可以直接当位置参数给（不写 --proxy）" "" || t_fail "T05i 位置参数" "$(grep -E 'rc=|错误' <<<"$out" | head -2)"
out="$(hx "$SA install --dry-run 2>&1"; echo "rc=$?")"
grep -q "机器上也没有现成的代理设置" <<<"$out" && grep -q "rc=1" <<<"$out" && ! grep -q "请按提示输入" <<<"$out" && t_pass "T05j 不给地址、机器上也没有代理设置、不是终端 → 明确报错（不卡在提问上）" "" || t_fail "T05j 无地址报错" "$(tail -2 <<<"$out")"
out="$(hx "$SA install proxy.corp.local:3128 --dry-run 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && grep -q "测试代理 http://proxy.corp.local:3128" <<<"$out" && grep -q "代理可用" <<<"$out" && t_pass "T05k 只写 主机:端口 按 HTTP 代理算" "" || t_fail "T05k 主机:端口" "$(grep -E 'rc=|错误|代理可用' <<<"$out" | head -3)"
# 小白路径：不带参数、机器上没有代理设置、在终端里跑 → 问地址和账号。pty_drive.py 像人一样等提示出来再答：先答一个错的再答对的，账号回车
out="$(hx 'cd /root/pkg && python3 /root/pty_drive.py "代理地址（=ftp://x:1" "代理地址（=proxy.corp.local:3128" "代理账号（=" -- bash setup-a.sh install --dry-run 2>&1'; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && grep -q "请按提示输入" <<<"$out" && grep -q "地址不对：PROXY_URL 不支持的协议 ftp" <<<"$out" && grep -q "参数来源: 终端输入" <<<"$out" && grep -q "测试代理 http://proxy.corp.local:3128" <<<"$out" && grep -q "代理可用" <<<"$out" && ! grep -q "代理密码" <<<"$out" \
    && t_pass "T05l 终端里不带参数 → 问地址（答错重问）、账号回车不问密码 → 装" "" || t_fail "T05l 终端提问" "$(grep -E 'rc=|提示输入|地址不对|参数来源|代理可用|Traceback|Error|没等到' <<<"$out" | head -6 | tr '\n' ' ')"
# 要账号密码的代理：第三问 getpass 不回显；密码不能出现在任何输出里
out="$(hx 'cd /root/pkg && python3 /root/pty_drive.py "代理地址（=proxy.corp.local:3129" "代理账号（=corpuser" "代理密码（=corppass" -- bash setup-a.sh install --dry-run 2>&1'; echo "rc=$?")"   # secret-guard: allow（测试容器假账号）
grep -q "rc=0" <<<"$out" && grep -q "参数来源: 终端输入" <<<"$out" && grep -q "测试代理 http://corpuser:\*\*\*@proxy.corp.local:3129" <<<"$out" && grep -q "代理可用" <<<"$out" && grep -q 'username: "corpuser"' <<<"$out" && grep -q 'password: <隐藏>' <<<"$out" && ! grep -q corppass <<<"$out" \
    && t_pass "T05m 终端里答账号密码 → 认证代理可用，密码不回显、不出现在输出里" "" || t_fail "T05m 终端答密码" "$(grep -E 'rc=|参数来源|测试代理|代理可用|corppass|Traceback|Error|没等到' <<<"$out" | head -6 | tr '\n' ' ')"
out="$(hx "$SA help 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && grep -q "^usage:" <<<"$out" && t_pass "T05n setup-a.sh help 等于 --help" "" || t_fail "T05n help" "$(head -2 <<<"$out")"
docker run -d --platform "$PLATFORM" --name "$DNS-nopub" --dns 8.8.8.8 --network "$NET" --ip 172.30.0.54 "$IMG" \
    dnsmasq -k --no-resolv --no-hosts --listen-address=172.30.0.54 --bind-interfaces --address=/web.corp.local/$WEB_IP >/dev/null && sleep 2
out="$(hx "$SA install --proxy http://proxy.corp.local:3128 --dns 172.30.0.54 --dry-run 2>&1"; echo "rc=$?")"
grep -q "DNS_MODE=auto → fake-ip" <<<"$out" && grep -q "enhanced-mode: fake-ip" <<<"$out" && grep -q "rc=0" <<<"$out" \
    && t_pass "T05g DNS_MODE=auto：内网 DNS 解析不了公网 → fake-ip" "" || t_fail "T05g auto → fake-ip" "$(grep -E 'DNS_MODE|rc=' <<<"$out" | head -3)"
docker rm -f "$DNS-nopub" >/dev/null 2>&1
# 代理探测：装之前就把地址 / 账号错误挡下来
out="$(hx "$SA install --proxy http://proxy.corp.local:3999 --dry-run 2>&1"; echo "rc=$?")"
grep -q "代理测试失败" <<<"$out" && grep -q "连不上代理" <<<"$out" && grep -q "rc=1" <<<"$out" && t_pass "P01 代理端口不通 → 安装前报错" "" || t_fail "P01 端口不通" "$(tail -3 <<<"$out")"
out="$(hx "$SA install --proxy http://proxy.corp.local:3129 --proxy-user corpuser --proxy-password wrongpass --dry-run 2>&1"; echo "rc=$?")"
grep -q "认证" <<<"$out" && grep -q "407" <<<"$out" && grep -q "rc=1" <<<"$out" && ! grep -q wrongpass <<<"$out" && t_pass "P02 squid 认证失败（407）→ 安装前报错，不回显密码" "" || t_fail "P02 407" "$(tail -3 <<<"$out")"
out="$(hx "$SA install --proxy http://proxy.corp.local:3129 --dry-run 2>&1"; echo "rc=$?")"
grep -q -- "--proxy-user" <<<"$out" && grep -q "rc=1" <<<"$out" && t_pass "P03 该认证却没给账号 → 提示加 --proxy-user" "" || t_fail "P03 缺账号提示" "$(tail -3 <<<"$out")"
out="$(hx "$SA install --proxy https://proxy.corp.local:3443 --dry-run 2>&1"; echo "rc=$?")"
grep -q "证书校验失败" <<<"$out" && grep -q "rc=1" <<<"$out" && t_pass "P04 https 自签证书未信任 → 报错并给出两种解法" "" || t_fail "P04 自签证书" "$(tail -3 <<<"$out")"
out="$(hx "$SA install --proxy socks5://corpuser:wrong@proxy.corp.local:3130 --dry-run 2>&1"; echo "rc=$?")"
grep -q "SOCKS5 账号密码不对" <<<"$out" && grep -q "rc=1" <<<"$out" && t_pass "P05 socks5 认证失败 → 安装前报错" "" || t_fail "P05 socks5 认证" "$(tail -3 <<<"$out")"
out="$(hx "$SA install --proxy http://proxy.corp.local:3999 --dry-run --skip-proxy-test 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && grep -q "没有测试代理" <<<"$out" && t_pass "P06 --skip-proxy-test 跳过探测" "" || t_fail "P06 skip" "$(tail -2 <<<"$out")"

echo; echo "== 3. 机器 A：安装（host 范围，auto→redir-host，客户 squid 不认证） =="
out="$(hx "$SA install --proxy http://proxy.corp.local:3128 $COMMON 2>&1"; echo "rc=$?")"
echo "$out" | sed 's/^/    | /' | tail -28
grep -q "rc=0" <<<"$out" && t_pass "I01 install 成功" "" || t_fail "I01 install" "$(grep -E '错误|FAIL' <<<"$out" | head -3)"
hx 'systemctl is-active --quiet flocks-egress-rules && systemctl is-active --quiet flocks-egress && [ "$(systemctl is-enabled flocks-egress)" = enabled ] && [ "$(systemctl is-enabled flocks-egress-rules)" = enabled ]' \
    && t_pass "I02 两个服务 active + enabled" "" || t_fail "I02 服务状态" "$(hx 'systemctl status flocks-egress-rules flocks-egress --no-pager | head -20')"
hx 'stat -c "%U:%G %a" /etc/flocks-egress /etc/flocks-egress/egress.conf /etc/flocks-egress/settings.json /etc/flocks-egress/config.yaml /var/lib/flocks-egress /opt/flocks-egress/bin/flocks-egress' | tr '\n' ' ' | grep -q "root:flocks-egress 750 root:root 600 root:root 600 root:flocks-egress 640 flocks-egress:flocks-egress 750 root:root 755" \
    && t_pass "I03 文件权限" "/etc/flocks-egress 750 root:flocks-egress、egress.conf/settings.json 600、config.yaml 640、状态目录属引擎用户" || t_fail "I03 文件权限" "$(hx 'stat -c "%n %U:%G %a" /etc/flocks-egress /etc/flocks-egress/* /var/lib/flocks-egress /opt/flocks-egress/bin/*')"
hx 'grep -q corppass /etc/flocks-egress/settings.json && exit 1; grep -q "\"has_password\": false" /etc/flocks-egress/settings.json' && t_pass "I03b settings.json 不含密码" "" || t_fail "I03b settings.json" "$(hx 'grep -n password /etc/flocks-egress/settings.json')"
caps="$(hx 'grep CapEff /proc/$(systemctl show -p MainPID --value flocks-egress)/status')"
grep -q "0000000000001000" <<<"$caps" && t_pass "I04 引擎以非 root 运行，仅 CAP_NET_ADMIN" "$caps" || t_fail "I04 引擎权限" "$caps"
hx 'journalctl -u flocks-egress --no-pager -o cat | grep -qi "geo\(ip\|site\).*download\|downloading"' && t_fail "I05 引擎不联网下载 geo 数据" "日志里有下载动作" || t_pass "I05 引擎不联网下载 geo 数据" ""
grep -q "PASS 13 / FAIL 0" <<<"$out" && t_pass "I06 安装末尾自动验收 13 项全过（含容器流量接管项）" "" || t_fail "I06 自动验收" "$(grep -E '^  (FAIL|WARN)|PASS [0-9]+ /' <<<"$out" | head -4)"
hx 'test -s /var/lib/flocks-egress/last-check.json && python3 -c "import json,sys; d=json.load(open(\"/var/lib/flocks-egress/last-check.json\")); sys.exit(0 if d[\"fail\"]==0 and d[\"pass\"]>=13 and d[\"dns_mode\"]==\"redir-host\" else 1)"' \
    && t_pass "I07 验收记录 last-check.json（fail=0, redir-host）" "" || t_fail "I07 验收记录" "$(hx 'head -c 300 /var/lib/flocks-egress/last-check.json')"
out="$(fe status 2>&1; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && grep -q "flocks-egress-rules.service *active" <<<"$out" && grep -q "上次验收" <<<"$out" && t_pass "I08 flocks-egress status（/usr/sbin 软链）" "" || t_fail "I08 status" "$(tail -4 <<<"$out")"
hx 'nft list table inet flocks_egress_engine | grep -q "redirect to :7892" && ! nft list table inet flocks_egress | grep -q "redirect to :7892" && ! nft list table inet flocks_egress_engine | grep -q dns_nat' \
    && t_pass "I09 默认 ENGINE_DOWN=direct：分流在随引擎启停的表里，常驻表不 redirect；redir-host 不接管 DNS" "" || t_fail "I09 默认 direct 布局" "$(hx 'nft list tables; nft list table inet flocks_egress | grep -c redirect')"
hx 'test -f /etc/systemd/system/nftables.service.d/flocks-egress.conf' && t_pass "I10 nftables.service drop-in 已装" "" || t_fail "I10 drop-in" ""
# 透明代理已在跑时再做 dry-run / auto 探测：探测以引擎用户发出，不被自己接管
out="$(fe "reconfigure --dry-run 2>&1"; echo "rc=$?")"
grep -q "DNS_MODE=auto → redir-host" <<<"$out" && grep -q "代理可用" <<<"$out" && grep -q "rc=0" <<<"$out" && ! hx 'ls -d /tmp/flocks-egress.* 2>/dev/null | grep -q .' \
    && t_pass "I11 运行中 reconfigure --dry-run：探测不被自己接管、无临时残留" "" || t_fail "I11 运行中 dry-run" "$(grep -E 'DNS_MODE|rc=|错误' <<<"$out" | head -3)"

# ------------------------------------------------------------------ 场景断言（host/user × redir-host/fake-ip 各跑）
scenario_suite() {   # $1 场景前缀; $2 被接管方用户; $3 例外用户; $4 dns 模式
    local tag="$1" client="$2" other="$3" mode="$4" n1 out code ip oip newl b0 b1
    b0="$(blog_count 'www.baidu.com:443')"
    code="$(hxu "$client" 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"; sleep 1
    b1="$(blog_count 'www.baidu.com:443')"
    if [ "$code" = 200 ] && [ "$b1" -gt "$b0" ] && blog | tail -n 3 | grep -q "$HOST_IP\|www.baidu.com"; then
        t_pass "$tag HTTPS 经 B 转发" "$client http=200，B 日志 baidu:443 $b0 → $b1"
    else t_fail "$tag HTTPS 经 B 转发" "http=$code; B 日志 $b0 → $b1"; fi
    b0="$(blog_count 'www.baidu.com:80')"
    code="$(hxu "$client" 'curl -sS -m 20 -o /dev/null -w "%{http_code}" http://www.baidu.com/')"; sleep 1
    b1="$(blog_count 'www.baidu.com:80')"
    [ "$code" = 200 ] && [ "$b1" -gt "$b0" ] && t_pass "$tag 明文 HTTP(80) 也经 B（CONNECT :80）" "http=200，B 日志 baidu:80 $b0 → $b1" || t_fail "$tag 明文 HTTP 经 B" "http=$code B 日志 $b0 → $b1"
    ip="$(hxu "$client" 'getent ahostsv4 www.baidu.com | head -1 | cut -d" " -f1')"
    if [ "$mode" = fake-ip ]; then
        is_fake "$ip" && t_pass "$tag 被接管方 DNS 得到 fake-ip" "$client: www.baidu.com → $ip" || t_fail "$tag 被接管方 DNS 得到 fake-ip" "$ip"
    else
        [ -n "$ip" ] && ! is_fake "$ip" && t_pass "$tag DNS 不接管，被接管方拿真实 IP" "$client: www.baidu.com → $ip" || t_fail "$tag DNS 真实 IP" "$ip"
    fi
    oip="$(hxu "$other" 'getent ahostsv4 www.baidu.com | head -1 | cut -d" " -f1')"
    [ -n "$oip" ] && ! is_fake "$oip" && t_pass "$tag 例外用户 DNS 真实" "$other: www.baidu.com → $oip" || t_fail "$tag 例外用户 DNS 真实" "$other → '$oip'"
    n1="$(elog_lines)"
    code="$(hxu "$other" 'curl -sS -m 15 -o /dev/null -w "%{http_code}" https://www.baidu.com/' 2>/dev/null || true)"; sleep 1
    if grep -q "www.baidu.com:443" <<<"$(elog_from "$n1")"; then t_fail "$tag 例外用户 HTTPS 不进引擎" "$other 的连接进了引擎"
    elif [ "$code" = 200 ]; then t_pass "$tag 例外用户 HTTPS 不进引擎" "$other 直连 http=200，引擎无新连接"
    else t_fail "$tag 例外用户 HTTPS 不进引擎" "$other 的 curl 没成功（http=$code），断言无效"; fi
    b0="$(blog_count 'web.corp.local')"
    code="$(hxu "$client" 'curl -sS -m 8 -o /dev/null -w "%{http_code}" http://web.corp.local:8080/')"; sleep 1
    [ "$code" = 200 ] && [ "$(blog_count 'web.corp.local')" = "$b0" ] && [ "$(blog_count "$WEB_IP:8080")" = 0 ] && t_pass "$tag 内网域名直连" "http=200，B 日志无 web.corp.local" || t_fail "$tag 内网域名直连" "http=$code"
    code="$(hxu "$client" "curl -sS -m 8 -o /dev/null -w '%{http_code}' http://$WEB_IP:8080/")"
    [ "$code" = 200 ] && [ "$(blog_count "$WEB_IP:8080")" = 0 ] && t_pass "$tag 内网 IP 直连" "http=200" || t_fail "$tag 内网 IP 直连" "http=$code"
    out="$(hxu "$client" 'echo x > /dev/udp/1.1.1.1/9999' 2>&1 || true)"
    grep -qi "not permitted\|refused" <<<"$out" && t_pass "$tag 被接管方公网 UDP 被拒绝" "" || t_fail "$tag 被接管方公网 UDP 被拒绝" "$out"
    out="$(hxu "$other" 'echo x > /dev/udp/1.1.1.1/9999 && echo sent' 2>&1 || true)"
    grep -q sent <<<"$out" && t_pass "$tag 例外用户公网 UDP 不受影响" "" || t_fail "$tag 例外用户公网 UDP 不受影响" "$out"
    n1="$(elog_lines)"
    code="$(hxu "$client" "curl -sS -m 20 -o /dev/null -w '%{http_code}' --resolve www.baidu.com:443:$oip https://www.baidu.com/")"; sleep 1
    newl="$(elog_from "$n1")"
    [ "$code" = 200 ] && grep -q -- "--> www.baidu.com:443 match .* using corp-proxy" <<<"$newl" \
        && t_pass "$tag 纯 IP 目标经 SNI 嗅探以域名交给 B" "连 $oip:443 → 识别为 www.baidu.com:443" || t_fail "$tag 纯 IP 目标 SNI 嗅探" "http=$code $(grep TCP <<<"$newl" | tail -1)"
}

echo; echo "== 4. host 范围 / redir-host 场景（被接管方 root，例外 flocks-egress） =="
scenario_suite "H" root flocks-egress redir-host
if hx 'python3 -m pip install -q aiohttp httpx >/tmp/pip.log 2>&1'; then
    n1="$(elog_lines)"
    out="$(hx 'python3 - <<PYEOF
import asyncio, aiohttp, httpx
async def main():
    async with aiohttp.ClientSession() as s:
        async with s.get("https://www.baidu.com/", timeout=aiohttp.ClientTimeout(total=20)) as r:
            print("aiohttp", r.status)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://www.baidu.com/")
        print("httpx", r.status_code)
asyncio.run(main())
PYEOF' 2>&1)"; sleep 1
    hits="$(grep -c -- "--> www.baidu.com:443 match .* using corp-proxy" <<<"$(elog_from "$n1")")"
    grep -q "aiohttp 200" <<<"$out" && grep -q "httpx 200" <<<"$out" && [ "$hits" -ge 2 ] \
        && t_pass "H aiohttp / httpx 两库都进引擎（pip 安装本身也经 B）" "均 200，引擎日志新增 $hits 条" || t_fail "H aiohttp / httpx" "$(tr '\n' ' ' <<<"$out" | head -c 160) hits=$hits"
else t_skip "H aiohttp / httpx 两库都进引擎" "pip 装不上（经 B 出网失败？见 /tmp/pip.log）"; fi
hx 'nohup python3 -m http.server 9000 --bind 0.0.0.0 >/tmp/l.log 2>&1 & sleep 1; systemctl is-active --quiet firewalld && firewall-cmd --add-port=9000/tcp >/dev/null 2>&1; true'
code="$(docker exec "$WEB" curl -sS -m 5 -o /dev/null -w '%{http_code}' "http://$HOST_IP:9000/" 2>/dev/null || true)"
[ "$code" = 200 ] && t_pass "H 别的机器连 A 的监听端口不受影响" "http=200" || t_fail "H 别的机器连 A 的监听端口不受影响" "http=$code"

echo; echo "== 4b. 容器流量：网桥 + 网络命名空间模拟 Docker bridge 网络（host 范围默认连容器一起接管） =="
# 和 Docker 一样：一个网桥、一个 veth 进独立网络命名空间、对外 MASQUERADE、网桥进 firewalld 的信任 zone（容器重启后要重建）
fake_container() {
    hx 'ip netns del c1 2>/dev/null; ip link del br-test 2>/dev/null; nft delete table ip fe_test_nat 2>/dev/null; true'
    hx 'ip link add br-test type bridge && ip addr add 172.31.0.1/24 dev br-test && ip link set br-test up \
     && ip netns add c1 && ip link add veth0 type veth peer name veth1 && ip link set veth1 netns c1 && ip link set veth0 master br-test up \
     && ip netns exec c1 ip addr add 172.31.0.2/24 dev veth1 && ip netns exec c1 ip link set veth1 up && ip netns exec c1 ip link set lo up \
     && ip netns exec c1 ip route add default via 172.31.0.1 && sysctl -qw net.ipv4.ip_forward=1 \
     && nft add table ip fe_test_nat && nft add chain ip fe_test_nat post "{ type nat hook postrouting priority srcnat; }" \
     && nft add rule ip fe_test_nat post ip saddr 172.31.0.0/24 oifname != "br-test" masquerade; \
     systemctl is-active --quiet firewalld && firewall-cmd --zone=trusted --add-interface=br-test >/dev/null 2>&1; true'
}
cx() { hx "ip netns exec c1 $*"; }     # 「容器」里执行
fake_container
b0="$(blog_count 'www.baidu.com:443')"; n1="$(elog_lines)"
code="$(cx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"; sleep 1
if [ "$code" = 200 ] && [ "$(blog_count 'www.baidu.com:443')" -gt "$b0" ] && elog_from "$n1" | grep -q -- "172.31.0.2:.* --> www.baidu.com:443 match .* using corp-proxy"; then
    t_pass "C01 容器 HTTPS 经 B（prerouting 接管，引擎日志源地址是容器 IP）" "http=200"; else t_fail "C01 容器 HTTPS 经 B" "http=$code B日志 $b0→$(blog_count 'www.baidu.com:443') $(elog_from "$n1" | grep 172.31 | tail -1)"; fi
ip="$(cx 'getent ahostsv4 www.baidu.com | head -1 | cut -d" " -f1')"
[ -n "$ip" ] && ! is_fake "$ip" && t_pass "C02 redir-host 下容器 DNS 不接管、拿真实 IP（经转发到内网 DNS）" "$ip" || t_fail "C02 容器 DNS" "'$ip'"
code="$(cx "curl -sS -m 8 -o /dev/null -w '%{http_code}' http://$WEB_IP:8080/")"
[ "$code" = 200 ] && [ "$(blog_count "$WEB_IP:8080")" = 0 ] && t_pass "C03 容器访问内网 IP 直连（转发出去，不进引擎）" "http=200" || t_fail "C03 容器内网直连" "http=$code"
write_udp_probe() {   # 容器重启会清掉 /tmp，用到前再写一次
hx 'cat > /tmp/udp_probe.py <<PY
import socket
# connect 过的 UDP socket 才会把 ICMP 端口不可达报成 ECONNREFUSED（未 connect 的 sendto 内核不上报）
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3); s.connect(("1.1.1.1", 9999)); s.send(b"x")
try:
    s.recv(1); print("no error")
except ConnectionRefusedError:
    print("ConnectionRefused")
except socket.timeout:
    print("timeout")
PY'
}
write_udp_probe
# forward 链的 reject 计数：分流链在哪张表取决于 ENGINE_DOWN，两张表都问一遍
fwd_reject_count() { hx 'for tb in flocks_egress flocks_egress_engine; do nft -j list chain inet $tb egress_forward 2>/dev/null; echo; done' | python3 -c 'import json,sys
total = 0
for line in sys.stdin:            # nft -j 一次输出一行 JSON，两张表各一行
    line = line.strip()
    if not line: continue
    for i in json.loads(line)["nftables"]:
        if "rule" in i and any("reject" in x for x in i["rule"]["expr"]):
            total += sum(e["counter"]["packets"] for e in i["rule"]["expr"] if "counter" in e)
print(total)' 2>/dev/null || echo 0; }
u0="$(fwd_reject_count)"
out="$(cx 'python3 /tmp/udp_probe.py' 2>&1 || true)"
u1="$(fwd_reject_count)"
grep -q "ConnectionRefused" <<<"$out" && [ "$u1" -gt "$u0" ] && t_pass "C04 容器公网 UDP 被拒（forward 链，reject 计数 $u0 → $u1）" "" || t_fail "C04 容器公网 UDP 被拒" "$(tail -1 <<<"$out") reject 计数 $u0 → $u1"
out_lan="$(docker exec "$WEB" curl -sS -m 5 -x http://$HOST_IP:7890 -o /dev/null -w '%{http_code}' https://www.baidu.com/ 2>&1 || true)"
out_c="$(cx 'curl -sS -m 5 -x http://172.31.0.1:7890 -o /dev/null -w "%{http_code}" https://www.baidu.com/' 2>&1 || true)"
out_redir="$(docker exec "$WEB" bash -c "curl -sS -m 3 -o /dev/null -w '%{http_code}' http://$HOST_IP:7892/ 2>&1; echo rc=\$?" || true)"
hx 'ss -Hlnt "sport = :7890"' | grep -q "127.0.0.1:7890" && ! hx 'ss -Hlnt "sport = :7890"' | grep -qE "(0.0.0.0|\*|\[::\]):7890" && hx 'ss -Hlnt "sport = :7892"' | grep -qE "(0.0.0.0|\*|\[::\]):7892" \
    && ! grep -q "^200" <<<"$out_lan" && ! grep -q "^200" <<<"$out_c" && grep -q "rc=28" <<<"$out_redir" \
    && t_pass "C05 HTTP 代理口 7890 只在回环（局域网、容器都用不了）；透明口 7892 在所有接口但局域网来的被 input 链丢掉" "LAN 7890: $(head -c 40 <<<"$out_lan" | tr '\n' ' ') / 容器 7890: $(head -c 40 <<<"$out_c" | tr '\n' ' ') / LAN 7892: 超时" \
    || t_fail "C05 引擎端口暴露面" "ss: $(hx 'ss -Hlnt "( sport = :7890 or sport = :7892 )"' | tr '\n' ' ') LAN7890=$out_lan 容器7890=$out_c LAN7892=$out_redir"

echo; echo "== 5a. 默认 ENGINE_DOWN=direct：引擎不在时直连兜底（redir-host） =="
hx 'systemctl stop flocks-egress'
b0="$(blog_count 'www.baidu.com:443')"
code="$(hx 'curl -sS -m 15 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"; sleep 1
ip="$(hx 'getent ahostsv4 www.baidu.com | head -1 | cut -d" " -f1')"
if [ "$code" = 200 ] && [ "$(blog_count 'www.baidu.com:443')" = "$b0" ] && ! is_fake "$ip" && ! hx 'nft list table inet flocks_egress_engine >/dev/null 2>&1' && hx 'nft list table inet flocks_egress >/dev/null 2>&1'; then
    t_pass "E01 停引擎：分流表随之卸掉，本机公网直连兜底（B 日志无新增），常驻表还在" "http=200 直连，DNS → $ip"; else t_fail "E01 默认直连兜底" "http=$code B日志 $b0→$(blog_count 'www.baidu.com:443') ip=$ip"; fi
code="$(cx 'curl -sS -m 15 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"
[ "$code" = 200 ] && t_pass "E02 停引擎：容器也直连兜底" "http=200" || t_fail "E02 容器直连兜底" "http=$code"
out="$(cx 'python3 /tmp/udp_probe.py' 2>&1 || true)"
grep -q "timeout\|no error" <<<"$out" && t_pass "E03 停引擎：公网 UDP 不再被拒" "$(tail -1 <<<"$out")" || t_fail "E03 UDP 兜底" "$(tail -1 <<<"$out")"
code="$(hx 'curl -sS -m 8 -o /dev/null -w "%{http_code}" http://web.corp.local:8080/')"
[ "$code" = 200 ] && t_pass "E04 停引擎：内网照常" "http=200" || t_fail "E04 内网照常" "http=$code"
hx 'systemctl start flocks-egress'; sleep 2
b0="$(blog_count 'www.baidu.com:443')"
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"; sleep 1
[ "$code" = 200 ] && [ "$(blog_count 'www.baidu.com:443')" -gt "$b0" ] && hx 'nft list table inet flocks_egress_engine | grep -q "redirect to :7892"' && t_pass "E05 引擎回来后重新经 B" "http=200" || t_fail "E05 恢复经 B" "http=$code"
pid1="$(hx 'systemctl show -p MainPID --value flocks-egress')"; hx "kill -9 $pid1"; sleep 4
pid2="$(hx 'systemctl show -p MainPID --value flocks-egress')"
b0="$(blog_count 'www.baidu.com:443')"
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"; sleep 1
[ "$pid2" != "$pid1" ] && [ "$pid2" != 0 ] && [ "$code" = 200 ] && [ "$(blog_count 'www.baidu.com:443')" -gt "$b0" ] && t_pass "E06 引擎被 kill -9 后自动拉起、分流表回来、重新经 B" "pid $pid1 → $pid2" || t_fail "E06 kill -9 自愈" "pid $pid1 → $pid2 http=$code"

echo; echo "== 5. ENGINE_DOWN=reject：引擎不在时 fail-closed（规则常驻） =="
out="$(fe "install --engine-down reject 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && grep -q "FAIL 0" <<<"$out" && grep -q "引擎不在时: 公网立刻被拒" <<<"$out" && hx 'nft list table inet flocks_egress | grep -q "redirect to :7892"' \
    && t_pass "F00 切到 reject：分流规则回到常驻表" "" || t_fail "F00 切 reject" "$(grep -E '^  FAIL|错误|rc=' <<<"$out" | head -3)"
hx 'systemctl stop flocks-egress'
t0="$(date +%s)"; out="$(cx 'curl -sS -m 5 -o /dev/null https://www.baidu.com/ 2>&1; echo rc=$?')"; t1="$(date +%s)"
grep -q "rc=7" <<<"$out" && [ $((t1 - t0)) -le 3 ] && t_pass "C06 reject 下停引擎：容器公网也立刻被拒（fail-closed 同样覆盖容器）" "rc=7 用时 $((t1 - t0))s" || t_fail "C06 容器 fail-closed" "$(tr '\n' ' ' <<<"$out")"
hx 'nft list table inet flocks_egress >/dev/null 2>&1' && t_pass "F01 停引擎后常驻规则表还在" "" || t_fail "F01 停引擎后常驻规则表还在" "表没了"
hx 'nft list table inet flocks_egress_engine >/dev/null 2>&1' && t_fail "F02 停引擎后 DNS 表随之移除" "还在" || t_pass "F02 停引擎后 DNS 表随之移除" ""
t0="$(date +%s)"; out="$(hx 'curl -sS -m 5 -o /dev/null https://www.baidu.com/ 2>&1; echo rc=$?')"; t1="$(date +%s)"
grep -q "rc=7" <<<"$out" && [ $((t1 - t0)) -le 3 ] && t_pass "F03 停引擎后公网 TCP 立刻被拒（不漏成直连）" "curl rc=7 用时 $((t1 - t0))s" || t_fail "F03 fail-closed" "$(tr '\n' ' ' <<<"$out") 用时 $((t1 - t0))s"
code="$(hx 'curl -sS -m 8 -o /dev/null -w "%{http_code}" http://web.corp.local:8080/')"
[ "$code" = 200 ] && t_pass "F04 停引擎后内网照常" "http=200" || t_fail "F04 停引擎后内网照常" "http=$code"
ip="$(hx 'getent ahostsv4 web.corp.local | head -1 | cut -d" " -f1')"
[ "$ip" = "$WEB_IP" ] && t_pass "F05 停引擎后 DNS 照常" "web.corp.local → $ip" || t_fail "F05 停引擎后 DNS 照常" "$ip"
hx 'systemctl start flocks-egress'; sleep 2
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"
[ "$code" = 200 ] && hx 'nft list table inet flocks_egress_engine >/dev/null 2>&1' && t_pass "F06 引擎重新启动后恢复" "http=200" || t_fail "F06 恢复" "http=$code"
pid1="$(hx 'systemctl show -p MainPID --value flocks-egress')"; hx "kill -9 $pid1"; sleep 4
pid2="$(hx 'systemctl show -p MainPID --value flocks-egress')"
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"
[ "$pid2" != "$pid1" ] && [ "$pid2" != 0 ] && [ "$code" = 200 ] && t_pass "F07 引擎被 kill -9 后自动拉起" "pid $pid1 → $pid2，http=200" || t_fail "F07 kill -9 自愈" "pid $pid1 → $pid2 http=$code"
hx 'systemctl stop flocks-egress-rules'; sleep 1
if ! hx 'systemctl is-active --quiet flocks-egress' && ! hx 'nft list table inet flocks_egress >/dev/null 2>&1' && ! hx 'nft list table inet flocks_egress_engine >/dev/null 2>&1'; then
    t_pass "F08 停规则服务 = 引擎一起停、两张表都删（整机恢复直连）" ""; else t_fail "F08 停规则服务" "$(hx 'systemctl is-active flocks-egress; nft list tables')"; fi
hx 'systemctl start flocks-egress-rules flocks-egress'; sleep 2
hx 'systemctl is-active --quiet flocks-egress && nft list table inet flocks_egress >/dev/null 2>&1' && t_pass "F09 重新启动两个服务后恢复" "" || t_fail "F09 恢复" ""

echo; echo "== 6. 切 fake-ip（命令行改参数 = 重写 egress.conf） =="
out="$(fe "install --dns-mode fake-ip 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && hx 'grep -q "^DNS_MODE=\"fake-ip\"" /etc/flocks-egress/egress.conf && grep -q "^PROXY_URL=\"http://proxy.corp.local:3128\"" /etc/flocks-egress/egress.conf && grep -q "^CHECK_INTRANET_URL=" /etc/flocks-egress/egress.conf && test -f /etc/flocks-egress/egress.conf.prev' \
    && t_pass "K01 install --dns-mode fake-ip：沿用其余参数改写 egress.conf，留 .prev" "" || t_fail "K01 改参数" "$(tail -3 <<<"$out"); $(hx 'cat /etc/flocks-egress/egress.conf')"
grep -q "PASS 15 / FAIL 0" <<<"$out" && t_pass "K02 fake-ip 模式自动验收 15 项全过（多了 DNS 接管表、例外用户 DNS 两项）" "" || t_fail "K02 自动验收" "$(grep -E '^  (FAIL|WARN)|PASS [0-9]+ /' <<<"$out" | head -4)"
hx 'nft list table inet flocks_egress_engine | grep -q "redirect to :1053"' && t_pass "K03 DNS 表有 53 → 1053 的 redirect" "" || t_fail "K03 DNS 表" ""
scenario_suite "K" root flocks-egress fake-ip
ip="$(cx 'getent ahostsv4 www.baidu.com | head -1 | cut -d" " -f1')"
is_fake "$ip" && t_pass "C07 fake-ip 下容器 DNS 也被接管（dns_prerouting）" "www.baidu.com → $ip" || t_fail "C07 容器 DNS 接管" "'$ip'"
b0="$(blog_count 'www.baidu.com:443')"
code="$(cx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"; sleep 1
[ "$code" = 200 ] && [ "$(blog_count 'www.baidu.com:443')" -gt "$b0" ] && t_pass "C08 fake-ip 下容器 HTTPS 经 B" "http=200" || t_fail "C08 fake-ip 容器 HTTPS" "http=$code"
hx 'systemctl stop flocks-egress'
ip="$(hx 'getent ahostsv4 web.corp.local | head -1 | cut -d" " -f1')"; ip2="$(hx 'getent ahostsv4 www.baidu.com | head -1 | cut -d" " -f1')"
[ "$ip" = "$WEB_IP" ] && ! is_fake "$ip2" && ! hx 'nft list table inet flocks_egress_engine >/dev/null 2>&1' && t_pass "K04 fake-ip 模式停引擎：DNS 表移除，内网 / 公网域名解析恢复直连" "web → $ip, baidu → $ip2" || t_fail "K04 停引擎 DNS" "web=$ip baidu=$ip2"
out="$(hx 'curl -sS -m 5 -o /dev/null https://www.baidu.com/ 2>&1; echo rc=$?')"
grep -q "rc=7" <<<"$out" && t_pass "K05 fake-ip 模式停引擎：公网 TCP 仍被拒" "" || t_fail "K05 公网 TCP 拒绝" "$(tr '\n' ' ' <<<"$out")"
hx 'systemctl start flocks-egress'; sleep 2

echo; echo "== 6b. 严格环境：内网 DNS 解析不了公网域名（auto → fake-ip，公网域名由代理解析） =="
docker run -d --platform "$PLATFORM" --name "$DNS-nopub" --dns 8.8.8.8 --network "$NET" --ip 172.30.0.54 "$IMG" \
    dnsmasq -k --no-resolv --no-hosts --listen-address=172.30.0.54 --bind-interfaces --address=/web.corp.local/$WEB_IP --address=/proxy.corp.local/$PROXY_IP >/dev/null && sleep 2
out="$(fe "install --dns 172.30.0.54 --dns-mode auto 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && grep -q "DNS_MODE=auto → fake-ip" <<<"$out" && grep -q "FAIL 0" <<<"$out" \
    && t_pass "S01 内网 DNS 只认内网名：auto 选 fake-ip，安装 + 自动验收全过" "$(grep -o 'PASS [0-9]* / FAIL [0-9]* / WARN [0-9]*' <<<"$out")" || t_fail "S01 严格 DNS 环境安装" "$(grep -E '^  (FAIL|WARN)|DNS_MODE|错误|PASS [0-9]+ /' <<<"$out" | head -5)"
grep -q "PASS  例外用户 DNS 不接管 .*rcode=" <<<"$out" && t_pass "S02 例外用户直问内网 DNS：真实 DNS 的 rcode 应答，不是 fake-ip，判 PASS" "" || t_fail "S02 例外用户 DNS 判据" "$(grep '例外用户 DNS' <<<"$out")"
b0="$(blog_count 'www.baidu.com:443')"
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"; sleep 1
[ "$code" = 200 ] && [ "$(blog_count 'www.baidu.com:443')" -gt "$b0" ] && t_pass "S03 公网域名本机解析不了也能经 B 访问（fake-ip 占位，代理解析）" "http=200" || t_fail "S03 严格环境公网访问" "http=$code"
ip="$(hx 'getent ahostsv4 web.corp.local | head -1 | cut -d" " -f1')"
[ "$ip" = "$WEB_IP" ] && t_pass "S04 内网域名仍由内网 DNS 真实解析" "web.corp.local → $ip" || t_fail "S04 内网域名解析" "$ip"
out="$(fe "install --dns 172.30.0.53 --dns-mode fake-ip 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && grep -q "FAIL 0" <<<"$out" && t_pass "S05 切回能解析公网的内网 DNS（后续场景用）" "" || t_fail "S05 切回" "$(grep -E '^  FAIL|错误' <<<"$out" | head -3)"
docker rm -f "$DNS-nopub" >/dev/null 2>&1

echo; echo "== 7. 代理的四种写法（客户现成 squid 认证 / socks5 / https 自签 / https 导入 CA） =="
out="$(fe "install --proxy http://proxy.corp.local:3129 --proxy-user corpuser --proxy-password corppass 2>&1"; echo "rc=$?")"
b0="$(blog_count ' corpuser ')"
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"; sleep 1
if grep -q "rc=0" <<<"$out" && hx 'grep -q "^PROXY_USER=\"corpuser\"" /etc/flocks-egress/egress.conf && grep -q "^PROXY_PASSWORD=\"corppass\"" /etc/flocks-egress/egress.conf' && [ "$code" = 200 ] && [ "$(blog_count ' corpuser ')" -gt "$b0" ]; then
    t_pass "V01 squid basic 认证（--proxy-user/--proxy-password）" "http=200，squid 日志出现用户 corpuser"; else t_fail "V01 squid 认证" "rc: $(tail -1 <<<"$out") http=$code $(hx 'grep PROXY_ /etc/flocks-egress/egress.conf')"; fi
grep -q "FAIL 0" <<<"$out" && t_pass "V01b 认证 squid 下自动验收全过" "" || t_fail "V01b 自动验收" "$(grep -E '^  FAIL' <<<"$out" | head -3)"
out="$(fe "install --proxy http://proxy.corp.local:3129 --proxy-user corpuser --proxy-password wrongpass 2>&1"; echo "rc=$?")"
grep -q "rc=1" <<<"$out" && grep -q "认证" <<<"$out" && hx 'grep -q "^PROXY_PASSWORD=\"corppass\"" /etc/flocks-egress/egress.conf && systemctl is-active --quiet flocks-egress' \
    && t_pass "V02 账号错误的重装在探测阶段被拒，原配置和服务不受影响" "" || t_fail "V02 错误账号" "$(tail -3 <<<"$out")"
out="$(fe "install --proxy socks5://corpuser:corppass@proxy.corp.local:3130 2>&1"; echo "rc=$?")"  # secret-guard: allow（测试容器假账号）
r0="$(hb 'journalctl -u fe-socks5 --no-pager -o cat | grep -c "www.baidu.com:443"')"
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"; sleep 1
r1="$(hb 'journalctl -u fe-socks5 --no-pager -o cat | grep -c "www.baidu.com:443"')"
if grep -q "rc=0" <<<"$out" && grep -q "FAIL 0" <<<"$out" && [ "$code" = 200 ] && [ "$r1" -gt "$r0" ] && hx 'grep -q "type: socks5" /etc/flocks-egress/config.yaml && ! grep -q "^PROXY_USER" /etc/flocks-egress/egress.conf'; then
    t_pass "V03 socks5 代理（账号写在 URL 里）" "http=200，B 上 socks5 日志 $r0 → $r1"; else t_fail "V03 socks5" "rc: $(tail -1 <<<"$out") http=$code socks5 日志 $r0 → $r1"; fi
out="$(fe "install --proxy https://proxy.corp.local:3443 --proxy-tls-insecure 2>&1"; echo "rc=$?")"
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"
grep -q "rc=0" <<<"$out" && grep -q "FAIL 0" <<<"$out" && [ "$code" = 200 ] && hx 'grep -q "tls: true" /etc/flocks-egress/config.yaml && grep -q "skip-cert-verify: true" /etc/flocks-egress/config.yaml && grep -q "^PROXY_TLS_INSECURE=\"yes\"" /etc/flocks-egress/egress.conf' \
    && t_pass "V04 https 代理 + --proxy-tls-insecure（自签证书）" "http=200" || t_fail "V04 https insecure" "rc: $(tail -1 <<<"$out") http=$code"
out="$(fe "install --proxy https://proxy.corp.local:3443 2>&1"; echo "rc=$?")"
grep -q "rc=1" <<<"$out" && grep -q "证书校验失败" <<<"$out" && hx 'systemctl is-active --quiet flocks-egress && grep -q "skip-cert-verify: true" /etc/flocks-egress/config.yaml' \
    && t_pass "V05 https 不带 insecure、CA 未信任 → 探测拒绝，原配置不动" "" || t_fail "V05 未信任 CA" "$(tail -3 <<<"$out")"
docker cp "$PROXY:/etc/squid/tls.crt" /tmp/fe-tls.crt >/dev/null && docker cp /tmp/fe-tls.crt "$HOST:/etc/pki/ca-trust/source/anchors/fe-proxy.crt" && rm -f /tmp/fe-tls.crt
hx 'update-ca-trust'
out="$(fe "install --proxy https://proxy.corp.local:3443 2>&1"; echo "rc=$?")"
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"
grep -q "rc=0" <<<"$out" && grep -q "FAIL 0" <<<"$out" && [ "$code" = 200 ] && hx 'grep -q "skip-cert-verify: false" /etc/flocks-egress/config.yaml && ! grep -q "^PROXY_TLS_INSECURE" /etc/flocks-egress/egress.conf' \
    && t_pass "V06 把代理证书导入系统 CA 后，https 代理正常校验（探测与引擎都认系统 CA）" "http=200" || t_fail "V06 导入 CA" "rc: $(tail -1 <<<"$out") http=$code"
out="$(fe "install --proxy http://proxy.corp.local:3128 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && grep -q "FAIL 0" <<<"$out" && hx '! grep -q "^PROXY_USER\|^PROXY_TLS" /etc/flocks-egress/egress.conf && grep -q "^DNS_MODE=\"fake-ip\"" /etc/flocks-egress/egress.conf' \
    && t_pass "V07 换回不认证 squid：旧账号 / TLS 开关清掉，DNS_MODE 等沿用" "" || t_fail "V07 换回" "$(tail -2 <<<"$out")"

echo; echo "== 7b. ENGINE_LOG_LEVEL=warning：引擎不再逐条记连接，验收改看 nft redirect 计数 =="
hx 'echo ENGINE_LOG_LEVEL="warning" >> /etc/flocks-egress/egress.conf'
out="$(fe "reconfigure 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && grep -q "FAIL 0" <<<"$out" && grep -q "redirect 计数" <<<"$out" && grep -q "WARN  例外用户不被接管" <<<"$out" \
    && t_pass "W01 warning 级别下自动验收仍全过：公网走代理凭 redirect 计数，例外用户项降为 WARN" "$(grep -o 'PASS [0-9]* / FAIL [0-9]* / WARN [0-9]*' <<<"$out")" || t_fail "W01 warning 级别验收" "$(grep -E '^  (FAIL|WARN)|PASS [0-9]+ /|错误' <<<"$out" | head -5)"
hx 'sed -i "/^ENGINE_LOG_LEVEL=/d" /etc/flocks-egress/egress.conf'
out="$(fe "reconfigure 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && grep -q "FAIL 0" <<<"$out" && grep -q "引擎日志: www.baidu.com:443" <<<"$out" && t_pass "W02 改回 info 后验收重新用引擎日志作证据" "" || t_fail "W02 改回 info" "$(grep -E '^  FAIL|PASS [0-9]+ /|错误' <<<"$out" | head -4)"

echo; echo "== 8. nftables.service 清空 ruleset 后自愈（drop-in） =="
hx 'printf "flush ruleset\n" > /etc/sysconfig/nftables.conf; systemctl start nftables; sleep 3'
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"
hx 'nft list table inet flocks_egress >/dev/null 2>&1 && nft list table inet flocks_egress_engine | grep -q "redirect to :1053"' && [ "$code" = 200 ] && t_pass "N01 systemctl start nftables（flush ruleset）后两张表自动恢复" "http=200" || t_fail "N01 start nftables" "http=$code $(hx 'nft list tables')"
hx 'systemctl reload nftables; sleep 3'
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"
hx 'nft list table inet flocks_egress >/dev/null 2>&1 && nft list table inet flocks_egress_engine | grep -q "redirect to :1053"' && [ "$code" = 200 ] && t_pass "N02 systemctl reload nftables 后恢复" "http=200" || t_fail "N02 reload nftables" "http=$code $(hx 'nft list tables')"
hx 'systemctl restart nftables; sleep 3'
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"
hx 'nft list table inet flocks_egress >/dev/null 2>&1 && nft list table inet flocks_egress_engine | grep -q "redirect to :1053"' && [ "$code" = 200 ] && t_pass "N03 systemctl restart nftables 后恢复" "http=200" || t_fail "N03 restart nftables" "http=$code $(hx 'nft list tables')"
out="$(fe "check --quick 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && t_pass "N04 之后 check --quick 通过" "$(grep -o 'PASS [0-9]* / FAIL [0-9]* / WARN [0-9]*' <<<"$out")" || t_fail "N04 check --quick" "$(grep -E '^  FAIL' <<<"$out")"
hx 'systemctl disable --now nftables >/dev/null 2>&1; systemctl restart flocks-egress-rules; sleep 2'

echo; echo "== 8b. 整机重启（容器重启 = systemd 重新走一遍开机） =="
docker restart "$HOST" >/dev/null; sleep 3
for _ in $(seq 1 30); do st="$(docker exec "$HOST" systemctl is-system-running 2>/dev/null || true)"; case "$st" in running|degraded) break ;; esac; sleep 2; done
hx "printf 'nameserver $DNS_IP\nsearch corp.local\n' > /etc/resolv.conf"    # docker 每次启动都会重写 resolv.conf，真机上这是静态文件
sleep 2
hx 'systemctl is-active --quiet flocks-egress-rules && systemctl is-active --quiet flocks-egress && nft list table inet flocks_egress >/dev/null 2>&1 && nft list table inet flocks_egress_engine | grep -q "redirect to :1053"' \
    && t_pass "R01 重启后两个服务自启、两张表都在" "" || t_fail "R01 重启自启" "$(hx 'systemctl is-active flocks-egress-rules flocks-egress; nft list tables; journalctl -u flocks-egress-rules --no-pager | tail -3')"
# 规则要在网络起来之前就位（fail-closed 从开机第一秒起）：比较两个单元的单调时间戳
# 容器里没有网络管理服务，network.target 可能根本没被 reach；规则单元 Wants+Before 的 network-pre.target 一定有时间戳
t_rules="$(hx 'systemctl show -p ActiveEnterTimestampMonotonic --value flocks-egress-rules.service')"
t_net="$(hx 'systemctl show -p ActiveEnterTimestampMonotonic --value network-pre.target')"
t_engine="$(hx 'systemctl show -p ActiveEnterTimestampMonotonic --value flocks-egress.service')"
if [ "${t_rules:-0}" -gt 0 ] && [ "${t_net:-0}" -gt 0 ] && [ "${t_engine:-0}" -gt 0 ]; then
    [ "$t_rules" -lt "$t_net" ] && [ "$t_rules" -lt "$t_engine" ] && t_pass "R01b 开机时规则表先于 network-pre.target 和引擎就位" "rules=${t_rules}us network-pre=${t_net}us engine=${t_engine}us" || t_fail "R01b 规则先于网络" "rules=${t_rules} network-pre=${t_net} engine=${t_engine}"
else t_skip "R01b 规则先于网络" "拿不到时间戳 rules='$t_rules' network-pre='$t_net' engine='$t_engine'"; fi
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"
[ "$code" = 200 ] && t_pass "R02 重启后公网仍经 B" "http=200" || t_fail "R02 重启后公网" "http=$code"
out="$(fe "check --quick 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && t_pass "R03 重启后 check --quick 通过" "$(grep -o 'PASS [0-9]* / FAIL [0-9]* / WARN [0-9]*' <<<"$out")" || t_fail "R03 check --quick" "$(grep -E '^  FAIL' <<<"$out")"

echo; echo "== 8c. 上联口本身是网桥（虚机宿主常见）：不能把局域网当容器 =="
fake_container
GW="$(hx 'ip -4 route show default | awk "{print \$3}"')"
hx "ip link add br-up type bridge && ip link set br-up up && ip addr flush dev eth0 && ip link set eth0 master br-up && ip addr add $HOST_IP/24 dev br-up && ip route replace default via $GW dev br-up" \
    && hx "ping -c1 -W2 $PROXY_IP >/dev/null" && echo "    | eth0 已挂进 br-up，IP 与默认路由搬到网桥上" || t_fail "8c 环境准备" "eth0 进网桥失败"
# 网卡刚挪进网桥，慢机器（QEMU x86）上头几秒经 B 的连接会失败：等到经引擎访问公网通了再 reconfigure，否则 reconfigure 末尾的自动验收会误报
for _ in $(seq 1 15); do [ "$(hx 'curl -sS -m 5 -o /dev/null -w "%{http_code}" https://www.baidu.com/' 2>/dev/null)" = 200 ] && break; sleep 1; done
out="$(fe "reconfigure 2>&1"; echo "rc=$?")"
tbl=""; for _ in 1 2 3; do tbl="$(hx 'nft list table inet flocks_egress' 2>/dev/null)"; [ "$(grep -c 'iifname "br-up" return' <<<"$tbl")" -ge 2 ] && break; sleep 1; done
grep -q "rc=0" <<<"$out" && grep -q "上联网桥 br-up 除外" <<<"$out" && [ "$(grep -c 'iifname "br-up" return' <<<"$tbl")" -ge 2 ] && grep -q 'meta iifkind "bridge" iifname != "br-up" return' <<<"$tbl" \
    && t_pass "B-UP1 reconfigure 识别出上联网桥（默认路由在它上面）并在四条容器链里排除" "" || t_fail "B-UP1 上联网桥识别" "$(grep -E '^  (FAIL|WARN)|错误|rc=' <<<"$out" | head -4 | tr '\n' ' '); nft 里含 br-up / iifkind 的行: $(grep -n 'br-up\|iifkind' <<<"$tbl" | tr '\n' ' ')"
grep -q "FAIL 0" <<<"$out" && grep -q "上联网桥不算容器: br-up" <<<"$out" && t_pass "B-UP2 自动验收仍全过，「容器流量接管」项列出上联网桥" "" || t_fail "B-UP2 上联网桥验收" "$(grep -E '^  (FAIL|WARN)|PASS [0-9]+ /' <<<"$out" | tr '\n' ' ')"
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"
[ "$code" = 200 ] && t_pass "B-UP3 本机自己的公网访问照常经 B" "http=200" || t_fail "B-UP3 本机公网" "http=$code"
out_redir="$(docker exec "$WEB" bash -c "curl -sS -m 3 -o /dev/null http://$HOST_IP:7892/ 2>&1; echo rc=\$?" || true)"
grep -q "rc=28" <<<"$out_redir" && t_pass "B-UP4 局域网主机从上联网桥进来连 7892 仍被丢（没被当成容器）" "" || t_fail "B-UP4 上联网桥 input 保护" "$out_redir"
b0="$(blog_count 'www.baidu.com:443')"
code="$(cx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"; sleep 1
[ "$code" = 200 ] && [ "$(blog_count 'www.baidu.com:443')" -gt "$b0" ] && t_pass "B-UP5 真正的容器网桥（br-test）不受影响，容器仍经 B" "http=200" || t_fail "B-UP5 容器网桥" "http=$code"
hx "ip route del default; ip addr flush dev br-up; ip link set eth0 nomaster; ip link del br-up; ip addr add $HOST_IP/24 dev eth0; ip route replace default via $GW dev eth0" && hx "ping -c1 -W2 $PROXY_IP >/dev/null" || t_fail "8c 环境还原" "eth0 还原失败"
out="$(fe "reconfigure 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && ! hx 'nft list table inet flocks_egress | grep -q br-up' && t_pass "B-UP6 网桥拆掉后 reconfigure 不再排除任何网桥" "" || t_fail "B-UP6 还原" "$(grep -E '上联|rc=' <<<"$out" | head -2)"

echo; echo "== 8d. ENGINE_DOWN=direct：引擎不在时直连兜底 =="
out="$(fe "install --engine-down direct 2>&1"; echo "rc=$?")"
ok=1
grep -q "rc=0" <<<"$out" && grep -q "FAIL 0" <<<"$out" && grep -q "引擎不在时: 直连兜底" <<<"$out" || ok=0
hx 'nft list table inet flocks_egress | grep -q "redirect to :7892"' && ok=0                      # 常驻表不该再有分流
hx 'nft list table inet flocks_egress_engine | grep -q "redirect to :7892" && nft list table inet flocks_egress_engine | grep -q "chain egress_prerouting"' || ok=0
[ "$ok" = 1 ] && t_pass "D01 切到 direct：分流规则（含容器链）搬进随引擎启停的表，常驻表不再 redirect，验收全过" "$(grep -o 'PASS [0-9]* / FAIL [0-9]* / WARN [0-9]*' <<<"$out")" || t_fail "D01 切 direct" "$(grep -E '^  FAIL|错误|rc=|引擎不在时' <<<"$out" | head -4)"
b0="$(blog_count 'www.baidu.com:443')"
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"; sleep 1
[ "$code" = 200 ] && [ "$(blog_count 'www.baidu.com:443')" -gt "$b0" ] && t_pass "D02 direct 模式引擎在跑时照常经 B" "http=200" || t_fail "D02 direct 模式经 B" "http=$code"
hx 'systemctl stop flocks-egress'
b0="$(blog_count 'www.baidu.com:443')"
code="$(hx 'curl -sS -m 15 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"; sleep 1
ip="$(hx 'getent ahostsv4 www.baidu.com | head -1 | cut -d" " -f1')"
if [ "$code" = 200 ] && [ "$(blog_count 'www.baidu.com:443')" = "$b0" ] && ! is_fake "$ip" && ! hx 'nft list table inet flocks_egress_engine >/dev/null 2>&1' && hx 'nft list table inet flocks_egress >/dev/null 2>&1'; then
    t_pass "D03 停引擎：分流表随之卸掉，本机公网直连兜底（B 日志无新增），DNS 真实" "http=200 直连，DNS → $ip"; else t_fail "D03 停引擎直连兜底" "http=$code B日志 $b0→$(blog_count 'www.baidu.com:443') ip=$ip"; fi
t0="$(date +%s)"; out="$(hx 'curl -sS -m 8 -o /dev/null --resolve www.baidu.com:443:198.18.0.4 https://www.baidu.com/ 2>&1; echo rc=$?')"; t1="$(date +%s)"
grep -q "rc=7" <<<"$out" && [ $((t1 - t0)) -le 3 ] && t_pass "D03b 停引擎：程序手里缓存的 fake-ip 占位地址立刻被拒，不挂到超时" "rc=7 用时 $((t1 - t0))s" || t_fail "D03b 占位地址快速拒绝" "$(tr '\n' ' ' <<<"$out") 用时 $((t1 - t0))s"
code="$(cx 'curl -sS -m 15 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"
[ "$code" = 200 ] && t_pass "D04 停引擎：容器也直连兜底" "http=200" || t_fail "D04 容器直连兜底" "http=$code"
write_udp_probe
out="$(cx 'python3 /tmp/udp_probe.py' 2>&1 || true)"
grep -q "timeout\|no error" <<<"$out" && t_pass "D05 停引擎：公网 UDP 也不再被拒（没有可达的目标就是超时）" "$(tail -1 <<<"$out")" || t_fail "D05 UDP 兜底" "$(tail -1 <<<"$out")"
hx 'systemctl start flocks-egress'; sleep 2
b0="$(blog_count 'www.baidu.com:443')"
code="$(hx 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"; sleep 1
[ "$code" = 200 ] && [ "$(blog_count 'www.baidu.com:443')" -gt "$b0" ] && hx 'nft list table inet flocks_egress_engine | grep -q "redirect to :7892"' && t_pass "D06 引擎回来后重新经 B" "http=200" || t_fail "D06 恢复经 B" "http=$code"
out="$(fe "check --quick 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && grep -q "引擎不在时=直连兜底" <<<"$out" && t_pass "D07 direct 模式 check --quick 通过" "$(grep -o 'PASS [0-9]* / FAIL [0-9]* / WARN [0-9]*' <<<"$out")" || t_fail "D07 check" "$(grep -E '^  FAIL' <<<"$out" | head -3)"
out="$(fe "install --engine-down reject 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && grep -q "FAIL 0" <<<"$out" && hx 'nft list table inet flocks_egress | grep -q "redirect to :7892"' && t_pass "D08 切回 reject（默认 fail-closed）" "" || t_fail "D08 切回 reject" "$(grep -E '^  FAIL|错误' <<<"$out" | head -3)"

echo; echo "== 9. 切到 user 范围（只接管 flocks 用户） =="
out="$(fe "install --scope user --flocks-user flocks 2>&1"; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && hx "nft list table inet flocks_egress | grep -q 'meta skuid != $(hx 'id -u flocks') return' && nft list table inet flocks_egress_engine | grep -q 'meta skuid != $(hx 'id -u flocks') return'" \
    && t_pass "U01 切到 user 范围，两张表的范围规则都改为 uid $(hx 'id -u flocks')" "" || t_fail "U01 切 user 范围" "$(grep -E '错误|rc=' <<<"$out" | tail -3)"
grep -q "FAIL 0" <<<"$out" && t_pass "U02 user 范围自动验收全过" "$(grep -o 'PASS [0-9]* / FAIL [0-9]* / WARN [0-9]*' <<<"$out")" || t_fail "U02 自动验收" "$(grep -E '^  FAIL' <<<"$out" | head -3)"
scenario_suite "U" flocks root fake-ip
fake_container      # 8b 的容器重启把网桥 / 命名空间都清掉了，重建一个
b0="$(blog_count 'www.baidu.com:443')"
code="$(cx 'curl -sS -m 15 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"; sleep 1
! hx 'nft list table inet flocks_egress | grep -q egress_prerouting' && [ "$code" = 200 ] && [ "$(blog_count 'www.baidu.com:443')" = "$b0" ] \
    && t_pass "C09 user 范围默认不接管容器：容器流量直连，B 日志无新增" "http=200 直连" || t_fail "C09 user 范围容器不接管" "http=$code B日志 $b0→$(blog_count 'www.baidu.com:443')"

echo; echo "== 10. firewalld 共存 / systemd 单元 =="
if hx 'systemctl start firewalld 2>/dev/null && sleep 2 && systemctl is-active --quiet firewalld'; then
    hx 'firewall-cmd --reload >/dev/null 2>&1'; sleep 1
    code="$(hxu flocks 'curl -sS -m 20 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"
    hx 'nft list table inet flocks_egress >/dev/null 2>&1' && [ "$code" = 200 ] && t_pass "FW firewalld 运行 + reload 后规则仍在、访问正常" "http=200" || t_fail "FW firewalld" "http=$code"
else t_skip "FW firewalld 共存" "容器里 firewalld 起不来"; fi
out="$(hx 'systemd-analyze verify /etc/systemd/system/flocks-egress.service /etc/systemd/system/flocks-egress-rules.service 2>&1'; echo "rc=$?")"
grep -q "rc=0" <<<"$out" && t_pass "S systemd-analyze verify（两个单元）" "" || t_fail "S systemd-analyze verify" "$out"
score="$(hx 'systemd-analyze security flocks-egress --no-pager 2>/dev/null | tail -1')"
[ -n "$score" ] && t_pass "S systemd-analyze security（信息）" "$score" || t_skip "S systemd-analyze security" "不可用"
out="$(hx 'python3 -m pip install -q pyyaml >/dev/null 2>&1; python3 -c "import yaml; yaml.safe_load(open(\"/etc/flocks-egress/config.yaml\")); print(\"yaml ok\")"' 2>&1)"
grep -q "yaml ok" <<<"$out" && t_pass "S 渲染出的 config.yaml 是合法 YAML" "" || t_skip "S config.yaml YAML 校验" "$out"

echo; echo "== 11. rollback / 残留拒绝 / 重装 =="
out="$(fe "rollback 2>&1"; echo "rc=$?")"
echo "$out" | sed 's/^/    | /' | tail -5
if grep -q "rc=0" <<<"$out" && ! hx 'test -e /etc/systemd/system/flocks-egress.service || test -e /etc/systemd/system/flocks-egress-rules.service || test -e /etc/systemd/system/nftables.service.d/flocks-egress.conf' \
   && ! hx 'nft list table inet flocks_egress >/dev/null 2>&1 || nft list table inet flocks_egress_engine >/dev/null 2>&1' \
   && ! hx 'test -e /opt/flocks-egress || test -e /var/lib/flocks-egress || id flocks-egress >/dev/null 2>&1 || test -e /etc/flocks-egress || test -L /usr/sbin/flocks-egress || test -L /usr/local/sbin/flocks-egress' \
   && ! hx 'ls /etc/systemd/system/flocks-egress*.prev /etc/systemd/system/nftables.service.d 2>/dev/null | grep -q .' \
   && hx 'ls -d /etc/flocks-egress.retired-* >/dev/null 2>&1'; then
    t_pass "X01 rollback 干净、配置目录改名保留" "$(hx 'ls -d /etc/flocks-egress.retired-*')"
else t_fail "X01 rollback" "$(hx 'ls -la /opt/flocks-egress /etc/flocks-egress* /etc/systemd/system/flocks-egress* 2>&1 | head; id flocks-egress 2>&1; nft list tables')"; fi
ip="$(hxu flocks 'getent ahostsv4 www.baidu.com | head -1 | cut -d" " -f1')"
code="$(hxu flocks 'curl -sS -m 15 -o /dev/null -w "%{http_code}" https://www.baidu.com/')"
! is_fake "$ip" && [ "$code" = 200 ] && t_pass "X02 rollback 后流量恢复直连" "DNS → $ip，直连 http=200" || t_fail "X02 rollback 后直连" "ip=$ip http=$code"
hx 'nft add table inet flocks_egress'
out="$(hx "$SA install --proxy http://proxy.corp.local:3128 --dry-run 2>&1"; echo "rc=$?")"
grep -q "残留" <<<"$out" && grep -q "rc=1" <<<"$out" && t_pass "X03 有残留表却没有 settings.json → 拒绝安装并指向 rollback" "" || t_fail "X03 残留拒绝" "$(tail -2 <<<"$out")"
hx 'nft delete table inet flocks_egress'
# 按客户 README 那一行装、不带任何参数：机器 dnf.conf 里已有 proxy= 时自动用它（内网机器为了 dnf 通常早配好了）；
# 有自解压包就用它（sudo bash xxx.run），验收应是 10 项 PASS + 1 条「内网直连未设置」WARN
hx 'printf "[main]\nproxy=http://proxy.corp.local:3128\n" >> /etc/dnf/dnf.conf'
# 没有自解压包时，从挂了 noexec 的 /tmp 里装（客户常见的加固配置）：vendor 里的二进制跑不了，要先拷进 /opt 再自检
if [ -n "$RUNFILE" ]; then out="$(hx 'cd / && bash /root/flocks-egress.run 2>&1'; echo "rc=$?")"; how="自解压包不带参数"
else out="$(hx 'cp -a /root/pkg /tmp/pkg-noexec && cd /tmp/pkg-noexec && bash setup-a.sh install 2>&1'; echo "rc=$?")"; how="noexec 的 /tmp 里 setup-a.sh install 不带参数"
     grep -q "禁止执行程序（noexec），改用拷到 /opt/flocks-egress/bin/mihomo.tmp 的副本自检" <<<"$out" && t_pass "X04a 包在 noexec 目录时引擎先拷进 /opt 再自检" "" || t_fail "X04a noexec 自检" "$(grep -E 'noexec|自检' <<<"$out" | head -3)"; fi
grep -q "rc=0" <<<"$out" && grep -q "用机器上已有的代理设置.*/etc/dnf/dnf.conf" <<<"$out" && grep -q "PASS 10 / FAIL 0 / WARN 1" <<<"$out" && grep -q "WARN  内网直连 .*未设置 CHECK_INTRANET_URL" <<<"$out" && hx 'systemctl is-active --quiet flocks-egress && grep -q "^PROXY_URL=\"http://proxy.corp.local:3128\"" /etc/flocks-egress/egress.conf' \
    && t_pass "X04 rollback 后零参数安装（$how，代理地址取自 dnf.conf）：PASS 10 / FAIL 0 / WARN 1" "" || t_fail "X04 零参数重装（$how）" "$(grep -E '错误|FAIL|WARN|PASS [0-9]+ /|代理设置|rc=|Traceback|Error' <<<"$out" | head -6 | tr '\n' ' ') ／ 末尾: $(tail -4 <<<"$out" | tr '\n' ' ')"
[ -z "$RUNFILE" ] || { ! hx 'ls -d /opt/.flocks-egress-run.* /var/tmp/flocks-egress-run.* /tmp/flocks-egress-run.* 2>/dev/null | grep -q .' && t_pass "X04b 自解压包跑完不留临时文件" "" || t_fail "X04b 自解压包临时文件" "$(hx 'ls -d /opt/.flocks-egress-run.* /var/tmp/flocks-egress-run.* /tmp/flocks-egress-run.* 2>/dev/null')"; }
if [ -n "$RUNFILE" ]; then out="$(hx 'cd / && bash /root/flocks-egress.run uninstall 2>&1'; echo "rc=$?")"; else out="$(fe "uninstall 2>&1"; echo "rc=$?")"; fi
grep -q "rc=0" <<<"$out" && ! hx 'test -e /opt/flocks-egress' && t_pass "X05 uninstall（rollback 别名${RUNFILE:+，经自解压包透传}）" "" || t_fail "X05 uninstall" "$(tail -2 <<<"$out")"
if [ -n "$RUNFILE" ]; then   # 自解压包：第一个参数是地址（不是子命令）就当 install；--dry-run 不落盘
    out="$(hx 'cd / && bash /root/flocks-egress.run 172.30.0.20:3128 --dry-run 2>&1'; echo "rc=$?")"
    grep -q "rc=0" <<<"$out" && grep -q "测试代理 http://172.30.0.20:3128" <<<"$out" && grep -q "代理可用" <<<"$out" && ! hx 'test -e /opt/flocks-egress || test -e /etc/flocks-egress' \
        && t_pass "X04c 自解压包直接跟 IP:端口 → 当 install（dry-run 不落盘）" "" || t_fail "X04c 自解压包位置参数" "$(grep -E 'rc=|错误|代理可用|usage|用法' <<<"$out" | head -3 | tr '\n' ' ')"
    # README 第 1 节的小白路径整条走一遍：机器上没有代理设置，sudo bash xxx.run 什么都不带 → 终端提问 → 认证代理。终端 stdin 要能穿过 .run 头部 → setup-a.sh → python3
    hx 'sed -i "/^proxy=/d" /etc/dnf/dnf.conf'
    out="$(hx 'cd / && python3 /root/pty_drive.py "代理地址（=proxy.corp.local:3129" "代理账号（=corpuser" "代理密码（=corppass" -- bash /root/flocks-egress.run --dry-run 2>&1'; echo "rc=$?")"   # secret-guard: allow（测试容器假账号）
    grep -q "rc=0" <<<"$out" && grep -q "请按提示输入" <<<"$out" && grep -q "参数来源: 终端输入" <<<"$out" && grep -q "代理可用" <<<"$out" && ! grep -q corppass <<<"$out" && ! hx 'test -e /etc/flocks-egress || ls -d /opt/.flocks-egress-run.* 2>/dev/null | grep -q .' \
        && t_pass "X04d 自解压包不带参数、机器无代理设置 → 终端提问三句 → 认证代理可用（dry-run 不落盘、不留临时目录）" "" || t_fail "X04d 自解压包终端提问" "$(grep -E 'rc=|提示输入|参数来源|代理可用|Traceback|Error|没等到' <<<"$out" | head -6 | tr '\n' ' ')"
    out="$(hx 'bash /root/flocks-egress.run help 2>&1'; echo "rc=$?")"   # 小白最可能敲的词，不能被当成代理地址
    grep -q "rc=0" <<<"$out" && grep -q "^用法:" <<<"$out" && ! hx 'ls -d /opt/.flocks-egress-run.* 2>/dev/null | grep -q .' && t_pass "X04e 自解压包 help 打用法（不解压、不装）" "" || t_fail "X04e 自解压包 help" "$(head -2 <<<"$out" | tr '\n' ' ')"
fi

echo; echo "================ 结果 (arch=$ARCH) ================"
printf '%s\n' "${RESULTS[@]}"
printf 'PASS %d / FAIL %d / SKIP %d\n' "$PASS" "$FAIL" "$SKIP"
[ "$FAIL" -eq 0 ]
